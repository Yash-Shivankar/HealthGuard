"""Tests for the ABDM API.

These repeat the isolation checks rather than trusting the ones in
``apps/accounts``: the mixin is shared, but a view that forgets to use it is
exactly the bug those tests cannot catch, and a JSON endpoint returning ABHA
numbers is the worst version of a cross-tenant leak in this project.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.abdm.models import AbhaLink, RegistrationState
from apps.abdm.services.gateway import StubTransport
from apps.abdm.services.registry_service import FacilityService
from apps.accounts.models import Clinic, Role
from apps.accounts.services.membership_service import MembershipService
from apps.consent.models import ConsentPurpose
from apps.consent.services.consent_service import ConsentService
from apps.patients.models import Patient

pytestmark = pytest.mark.django_db

PASSWORD = "abdm-api-pw"


@pytest.fixture(autouse=True)
def roles():
    from django.core.management import call_command

    call_command("seed_roles", verbosity=0)


@pytest.fixture
def clinics():
    return {
        "north": Clinic.objects.create(name="North Clinic"),
        "south": Clinic.objects.create(name="South Clinic"),
    }


@pytest.fixture
def patients(clinics):
    made = {}
    for key, contact in (("north", "9876500001"), ("south", "9876500002")):
        patient = Patient.objects.create(
            clinic=clinics[key], name=f"{key.title()} Patient", contact=contact,
            age=30, height_cm=170, weight_kg=65,
        )
        ConsentService().grant(patient=patient, purpose=ConsentPurpose.ABDM_LINKAGE)
        made[key] = patient
    return made


def _desk(django_user_model, username, clinic):
    user = django_user_model.objects.create_user(username=username, password=PASSWORD)
    MembershipService().grant(user=user, clinic=clinic, role=Role.FRONT_DESK)
    user.refresh_from_db()
    return user


@pytest.fixture
def north_desk(django_user_model, clinics):
    return _desk(django_user_model, "north-desk", clinics["north"])


class TestAbhaEndpointIsolation:
    def test_creating_for_your_own_patient_works(self, client, north_desk, patients):
        client.force_login(north_desk)
        response = client.post(
            reverse("abdm:abha", args=[patients["north"].pk])
        )
        assert response.status_code == 201
        assert AbhaLink.objects.get().patient_id == patients["north"].pk

    def test_another_clinics_patient_is_404_not_403(
        self, client, north_desk, patients
    ):
        """403 would confirm the record exists."""
        client.force_login(north_desk)
        response = client.post(reverse("abdm:abha", args=[patients["south"].pk]))
        assert response.status_code == 404
        assert AbhaLink.objects.count() == 0

    def test_reading_another_clinics_link_is_404(self, client, north_desk, patients):
        AbhaLink.objects.create(
            patient=patients["south"], clinic=patients["south"].clinic,
            abha_number="12345678901234", state=RegistrationState.VERIFIED,
        )
        client.force_login(north_desk)
        assert client.get(
            reverse("abdm:abha", args=[patients["south"].pk])
        ).status_code == 404

    def test_an_unassigned_account_is_refused(
        self, client, django_user_model, patients
    ):
        from django.contrib.auth.models import Permission

        nobody = django_user_model.objects.create_user(
            username="nobody", password=PASSWORD, is_staff=True
        )
        nobody.user_permissions.add(Permission.objects.get(codename="view_patient"))

        client.force_login(nobody)
        assert client.get(
            reverse("abdm:abha", args=[patients["north"].pk])
        ).status_code == 403

    def test_anonymous_callers_are_refused(self, client, patients):
        """There is no public ABHA endpoint. Linking happens at a registered
        facility by registered staff, which is what M1 describes.
        """
        assert client.post(
            reverse("abdm:abha", args=[patients["north"].pk])
        ).status_code in (401, 403)


class TestTheApiNeverReturnsAFullAbha:
    def test_the_number_is_masked_in_the_response(
        self, client, north_desk, patients
    ):
        client.force_login(north_desk)
        body = client.post(reverse("abdm:abha", args=[patients["north"].pk])).json()

        link = AbhaLink.objects.get()
        assert link.abha_number not in str(body)
        assert body["abha_number"].endswith(link.abha_number[-4:])

    def test_the_read_endpoint_masks_too(self, client, north_desk, patients):
        client.force_login(north_desk)
        client.post(reverse("abdm:abha", args=[patients["north"].pk]))

        body = client.get(reverse("abdm:abha", args=[patients["north"].pk])).json()
        assert AbhaLink.objects.get().abha_number not in str(body)


class TestConsentIsEnforcedAtTheApi:
    def test_a_patient_without_linkage_consent_is_refused(
        self, client, north_desk, clinics
    ):
        patient = Patient.objects.create(
            clinic=clinics["north"], name="No Consent", contact="9876500009",
            age=30, height_cm=170, weight_kg=65,
        )
        # Symptom-check consent only.
        ConsentService().grant(patient=patient, purpose=ConsentPurpose.SYMPTOM_CHECK)

        client.force_login(north_desk)
        response = client.post(reverse("abdm:abha", args=[patient.pk]))
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "abdm_consent_required"
        assert AbhaLink.objects.count() == 0


class TestVerifyEndpoint:
    def test_links_an_existing_abha(self, client, north_desk, patients):
        client.force_login(north_desk)
        response = client.post(
            reverse("abdm:abha-verify", args=[patients["north"].pk]),
            {"abha_number": "12-3456-7890-1234"},
            content_type="application/json",
        )
        assert response.status_code == 200
        assert AbhaLink.objects.get().abha_number == "12345678901234"

    def test_a_malformed_number_is_a_400(self, client, north_desk, patients):
        client.force_login(north_desk)
        response = client.post(
            reverse("abdm:abha-verify", args=[patients["north"].pk]),
            {"abha_number": "123"},
            content_type="application/json",
        )
        assert response.status_code == 400


class TestUnlink:
    def test_removes_the_local_link_only(self, client, north_desk, patients):
        client.force_login(north_desk)
        client.post(reverse("abdm:abha", args=[patients["north"].pk]))

        assert client.delete(
            reverse("abdm:abha", args=[patients["north"].pk])
        ).status_code == 204
        assert AbhaLink.objects.count() == 0
        # The patient survives: unlinking is not erasure.
        assert Patient.objects.filter(pk=patients["north"].pk).exists()


class TestScanShareEndpoint:
    def test_returns_a_code_for_a_registered_facility(
        self, client, north_desk, clinics
    ):
        FacilityService(transport=StubTransport()).register(clinic=clinics["north"])

        client.force_login(north_desk)
        response = client.get(reverse("abdm:scan-share"))
        assert response.status_code == 200

        body = response.json()
        assert body["hfr_id"]
        assert body["svg"].lstrip().startswith(("<?xml", "<svg"))
        assert body["data_uri"].startswith("data:image/svg+xml;base64,")

    def test_an_unregistered_facility_is_refused_clearly(
        self, client, north_desk
    ):
        client.force_login(north_desk)
        response = client.get(reverse("abdm:scan-share"))
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "facility_not_registered"

    def test_the_code_carries_no_patient_data(
        self, client, north_desk, clinics, patients
    ):
        """A QR is a broadcast and cannot be un-shared."""
        FacilityService(transport=StubTransport()).register(clinic=clinics["north"])

        client.force_login(north_desk)
        payload = client.get(reverse("abdm:scan-share")).json()["payload"]

        assert patients["north"].name not in payload
        assert patients["north"].contact not in payload

    def test_the_code_is_for_the_callers_own_clinic(
        self, client, north_desk, clinics
    ):
        """There is no clinic parameter to tamper with - it comes from the
        resolved tenant.
        """
        FacilityService(transport=StubTransport()).register(clinic=clinics["north"])
        FacilityService(transport=StubTransport()).register(clinic=clinics["south"])

        client.force_login(north_desk)
        body = client.get(reverse("abdm:scan-share")).json()
        assert body["clinic_name"] == "North Clinic"
