"""Cross-tenant isolation for the API (AUDIT 8.7, over HTTP).

**The admin tests do not cover these endpoints.** ``ScopedModelAdmin`` overrides
``ModelAdmin`` hooks and a DRF view shares none of them, so the same four checks
have to be made again against the JSON surface. A paged JSON list is arguably a
worse leak than a changelist, because it is easier to walk.

The other thing asserted here is the shape of the refusal: a record at another
clinic returns **404, not 403**. A 403 confirms the record exists, which is
itself a disclosure when the id is a patient.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.accounts.models import Clinic, Role
from apps.accounts.services.membership_service import MembershipService
from apps.patients.models import Patient
from apps.triage.models import ReviewState, TriageAssessment

pytestmark = pytest.mark.django_db

PASSWORD = "api-isolation-pw"


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
def records(clinics):
    north = Patient.objects.create(
        clinic=clinics["north"],
        name="North Patient",
        contact="9876500001",
        age=30,
        height_cm=170,
        weight_kg=65,
    )
    south = Patient.objects.create(
        clinic=clinics["south"],
        name="South Patient",
        contact="9876500002",
        age=40,
        height_cm=160,
        weight_kg=55,
    )
    return {
        "north": north,
        "south": south,
        "north_assessment": TriageAssessment.objects.create(
            clinic=clinics["north"], patient=north, symptoms=["itching"], days=1
        ),
        "south_assessment": TriageAssessment.objects.create(
            clinic=clinics["south"], patient=south, symptoms=["headache"], days=2
        ),
    }


def _doctor(django_user_model, username, clinic):
    user = django_user_model.objects.create_user(username=username, password=PASSWORD)
    MembershipService().grant(user=user, clinic=clinic, role=Role.DOCTOR)
    user.refresh_from_db()
    return user


@pytest.fixture
def north_doctor(django_user_model, clinics):
    return _doctor(django_user_model, "north-doc", clinics["north"])


@pytest.fixture
def south_doctor(django_user_model, clinics):
    return _doctor(django_user_model, "south-doc", clinics["south"])


@pytest.fixture
def ops(django_user_model):
    user = django_user_model.objects.create_user(username="ops", password=PASSWORD)
    MembershipService().grant(user=user, clinic=None, role=Role.PLATFORM_OPS)
    user.refresh_from_db()
    return user


class TestSessionEndpoint:
    def test_sign_in_returns_the_caller_context(self, client, north_doctor, clinics):
        response = client.post(
            reverse("accounts:login"),
            {"username": "north-doc", "password": PASSWORD},
            content_type="application/json",
        )
        assert response.status_code == 200
        body = response.json()
        assert body["username"] == "north-doc"
        assert body["role"] == Role.DOCTOR
        assert body["clinic"]["name"] == "North Clinic"
        assert body["unrestricted"] is False

    def test_bad_credentials_do_not_say_which_part_was_wrong(self, client, north_doctor):
        """Distinguishing them is an account enumeration oracle."""
        wrong_user = client.post(
            reverse("accounts:login"),
            {"username": "nobody-here", "password": PASSWORD},
            content_type="application/json",
        )
        wrong_pass = client.post(
            reverse("accounts:login"),
            {"username": "north-doc", "password": "wrong"},
            content_type="application/json",
        )
        assert wrong_user.status_code == wrong_pass.status_code == 401
        assert wrong_user.json() == wrong_pass.json()

    def test_the_session_endpoint_requires_a_session(self, client):
        assert client.get(reverse("accounts:session")).status_code in (401, 403)

    def test_an_api_session_does_not_open_the_admin(self, client, north_doctor, settings):
        """Different surfaces, different risk.

        The admin gate wants a verified OTP device; a session made through the
        API has none. Conflating them would let an API login walk into the
        admin.
        """
        settings.ADMIN_REQUIRE_2FA = True
        client.post(
            reverse("accounts:login"),
            {"username": "north-doc", "password": PASSWORD},
            content_type="application/json",
        )
        assert client.get(reverse("admin:index")).status_code == 302


class TestPatientListIsolation:
    def test_a_doctor_sees_only_their_clinics_patients(
        self, client, north_doctor, records
    ):
        client.force_login(north_doctor)
        names = [p["name"] for p in client.get(reverse("patients:list")).json()["results"]]
        assert names == ["North Patient"]

    def test_the_other_clinic_sees_the_mirror_image(
        self, client, south_doctor, records
    ):
        client.force_login(south_doctor)
        names = [p["name"] for p in client.get(reverse("patients:list")).json()["results"]]
        assert names == ["South Patient"]

    def test_platform_ops_sees_across_clinics(self, client, ops, records):
        client.force_login(ops)
        names = {p["name"] for p in client.get(reverse("patients:list")).json()["results"]}
        assert names == {"North Patient", "South Patient"}

    def test_the_list_never_returns_a_full_contact_number(
        self, client, north_doctor, records
    ):
        client.force_login(north_doctor)
        body = client.get(reverse("patients:list")).content.decode()
        assert "9876500001" not in body
        assert "0001" in body

    def test_search_cannot_reach_the_other_clinic(self, client, north_doctor, records):
        client.force_login(north_doctor)
        response = client.get(reverse("patients:list"), {"search": "South"})
        assert response.json()["results"] == []

    def test_search_is_not_available_on_the_contact_number(
        self, client, north_doctor, records
    ):
        """Searching by number would make this a way to look someone up from a
        number, which is the extraction path AUDIT 8.8 warns about.
        """
        client.force_login(north_doctor)
        response = client.get(reverse("patients:list"), {"search": "9876500001"})
        assert response.json()["results"] == []


class TestPatientDetailIsolation:
    def test_another_clinics_patient_is_404_not_403(
        self, client, north_doctor, records
    ):
        """403 would confirm the record exists."""
        client.force_login(north_doctor)
        response = client.get(
            reverse("patients:detail", args=[records["south"].pk])
        )
        assert response.status_code == 404

    def test_its_own_patient_is_readable(self, client, north_doctor, records):
        client.force_login(north_doctor)
        response = client.get(
            reverse("patients:detail", args=[records["north"].pk])
        )
        assert response.status_code == 200
        assert response.json()["name"] == "North Patient"


class TestAssessmentQueueIsolation:
    def test_the_queue_is_scoped(self, client, north_doctor, records):
        client.force_login(north_doctor)
        rows = client.get(reverse("triage:assessments")).json()["results"]
        assert [r["patient_name"] for r in rows] == ["North Patient"]

    def test_sign_off_works_on_its_own_clinics_assessment(
        self, client, north_doctor, records
    ):
        client.force_login(north_doctor)
        response = client.patch(
            reverse("triage:assessment-review", args=[records["north_assessment"].pk])
        )
        assert response.status_code == 200

        records["north_assessment"].refresh_from_db()
        assert records["north_assessment"].review_state == ReviewState.REVIEWED

    def test_sign_off_on_another_clinics_assessment_is_404(
        self, client, north_doctor, records
    ):
        client.force_login(north_doctor)
        response = client.patch(
            reverse("triage:assessment-review", args=[records["south_assessment"].pk])
        )
        assert response.status_code == 404

        records["south_assessment"].refresh_from_db()
        assert records["south_assessment"].review_state == ReviewState.UNREVIEWED

    def test_signing_off_twice_is_a_conflict_not_a_silent_success(
        self, client, north_doctor, records
    ):
        client.force_login(north_doctor)
        url = reverse("triage:assessment-review", args=[records["north_assessment"].pk])
        assert client.patch(url).status_code == 200
        assert client.patch(url).status_code == 409

    def test_sign_off_leaves_a_history_entry(self, client, north_doctor, records):
        """A sign-off with no trail of who signed it is not a sign-off."""
        client.force_login(north_doctor)
        client.patch(
            reverse("triage:assessment-review", args=[records["north_assessment"].pk])
        )
        assert records["north_assessment"].history.count() >= 2

    def test_a_front_desk_account_cannot_sign_off(
        self, client, django_user_model, clinics, records
    ):
        """Clinician sign-off is a clinical act; the front desk does not hold
        the permission for it.
        """
        desk = django_user_model.objects.create_user(username="desk", password=PASSWORD)
        MembershipService().grant(
            user=desk, clinic=clinics["north"], role=Role.FRONT_DESK
        )
        desk.refresh_from_db()

        client.force_login(desk)
        response = client.patch(
            reverse("triage:assessment-review", args=[records["north_assessment"].pk])
        )
        assert response.status_code == 403


class TestUnassignedAccountsAreRefused:
    """An empty list would read as "no patients registered" when it means
    "your account has no clinic". Saying which is the point.
    """

    @pytest.fixture
    def unassigned(self, django_user_model):
        from django.contrib.auth.models import Permission

        user = django_user_model.objects.create_user(
            username="nobody", password=PASSWORD, is_staff=True
        )
        user.user_permissions.add(
            Permission.objects.get(codename="view_patient"),
            Permission.objects.get(codename="view_triageassessment"),
        )
        return user

    def test_the_patient_list_is_refused_rather_than_empty(
        self, client, unassigned, records
    ):
        client.force_login(unassigned)
        response = client.get(reverse("patients:list"))
        assert response.status_code == 403
        assert "not attached to a clinic" in response.content.decode()

    def test_the_review_queue_is_refused_too(self, client, unassigned, records):
        client.force_login(unassigned)
        assert client.get(reverse("triage:assessments")).status_code == 403

    def test_a_named_patient_is_still_not_reachable(
        self, client, unassigned, records
    ):
        client.force_login(unassigned)
        response = client.get(reverse("patients:detail", args=[records["north"].pk]))
        assert response.status_code in (403, 404)


class TestClinicSwitching:
    def test_a_locum_can_switch_between_their_clinics(
        self, client, django_user_model, clinics, records
    ):
        locum = django_user_model.objects.create_user(
            username="locum", password=PASSWORD
        )
        grants = MembershipService()
        grants.grant(user=locum, clinic=clinics["north"], role=Role.DOCTOR)
        grants.grant(user=locum, clinic=clinics["south"], role=Role.DOCTOR)
        locum.refresh_from_db()

        client.force_login(locum)
        response = client.post(
            reverse("accounts:switch-clinic"),
            {"clinic_id": clinics["south"].pk},
            content_type="application/json",
        )
        assert response.status_code == 200
        assert response.json()["clinic"]["name"] == "South Clinic"

        names = [p["name"] for p in client.get(reverse("patients:list")).json()["results"]]
        assert names == ["South Patient"]

    def test_switching_to_a_clinic_you_do_not_belong_to_is_refused(
        self, client, north_doctor, clinics
    ):
        client.force_login(north_doctor)
        response = client.post(
            reverse("accounts:switch-clinic"),
            {"clinic_id": clinics["south"].pk},
            content_type="application/json",
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "not_a_member"
