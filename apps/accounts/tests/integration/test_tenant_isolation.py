"""Cross-tenant isolation, with real clinics (AUDIT 8.7).

``common/tests/test_admin_scoping.py`` proved the machinery in Phase 3 against
a stand-in scope, before ``Clinic`` existed. These are the same four checks
against the real thing, through the real admin, with two clinics and staff who
belong to one each.

The audit's line about this area is worth keeping in view: *"This is the one
area where I would not trust review alone."*

## The fail-closed case

The test that matters most is :class:`TestUnassignedStaffSeeNothing`. While
there were no tenants, ``get_tenant()`` returned ``None`` for everyone and that
harmlessly meant "nothing to scope by". With tenants real, a staff account
nobody has assigned a clinic to must see **nothing** - not everything. That
inversion is the kind of bug that ships quietly, so it is asserted directly.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse

from apps.accounts.models import Clinic, Role
from apps.accounts.services.membership_service import MembershipService
from apps.patients.models import Patient
from apps.triage.models import TriageAssessment

pytestmark = pytest.mark.django_db

ADMIN_LOGIN = "/internal-ops/login/"


@pytest.fixture(autouse=True)
def _no_2fa(settings):
    """2FA is covered in its own suite; these tests are about scoping."""
    settings.ADMIN_REQUIRE_2FA = False


@pytest.fixture
def roles():
    """Seed the permission groups the way a deploy would."""
    from django.core.management import call_command

    call_command("seed_roles", verbosity=0)


@pytest.fixture
def clinics():
    north = Clinic.objects.create(name="North Clinic")
    south = Clinic.objects.create(name="South Clinic")
    return {"north": north, "south": south}


@pytest.fixture
def records(clinics):
    north_patient = Patient.objects.create(
        clinic=clinics["north"],
        name="North Patient",
        contact="9876500001",
        age=30,
        height_cm=170,
        weight_kg=65,
    )
    south_patient = Patient.objects.create(
        clinic=clinics["south"],
        name="South Patient",
        contact="9876500002",
        age=40,
        height_cm=160,
        weight_kg=55,
    )
    TriageAssessment.objects.create(
        clinic=clinics["north"], patient=north_patient, symptoms=["itching"], days=1
    )
    TriageAssessment.objects.create(
        clinic=clinics["south"], patient=south_patient, symptoms=["headache"], days=2
    )
    return {"north": north_patient, "south": south_patient}


def _staff(django_user_model, username: str, clinic, role: str, roles_seeded):
    user = django_user_model.objects.create_user(username=username, password="pw")
    MembershipService().grant(user=user, clinic=clinic, role=role)
    user.refresh_from_db()
    return user


@pytest.fixture
def north_staff(django_user_model, clinics, roles):
    return _staff(django_user_model, "north-desk", clinics["north"], Role.FRONT_DESK, roles)


@pytest.fixture
def south_staff(django_user_model, clinics, roles):
    return _staff(django_user_model, "south-desk", clinics["south"], Role.FRONT_DESK, roles)


@pytest.fixture
def platform_ops(django_user_model, roles):
    user = django_user_model.objects.create_user(username="ops", password="pw")
    MembershipService().grant(user=user, clinic=None, role=Role.PLATFORM_OPS)
    user.refresh_from_db()
    return user


# --- 1. The list view -------------------------------------------------------


class TestListIsolation:
    def test_staff_see_only_their_own_clinics_patients(
        self, client, north_staff, records
    ):
        client.force_login(north_staff)
        body = client.get(reverse("admin:patients_patient_changelist")).content.decode()
        assert "North Patient" in body
        assert "South Patient" not in body

    def test_the_other_clinic_sees_the_mirror_image(
        self, client, south_staff, records
    ):
        client.force_login(south_staff)
        body = client.get(reverse("admin:patients_patient_changelist")).content.decode()
        assert "South Patient" in body
        assert "North Patient" not in body

    def test_assessments_are_scoped_too(self, client, north_staff, records, roles):
        north_staff.user_permissions.add(
            Permission.objects.get(codename="view_triageassessment")
        )
        client.force_login(north_staff)
        response = client.get(reverse("admin:triage_triageassessment_changelist"))
        assert response.status_code == 200
        assert response.context["cl"].result_count == 1

    def test_platform_ops_sees_across_clinics(self, client, platform_ops, records):
        """Cross-tenant access through an explicit role, not is_superuser
        (AUDIT 8.8).
        """
        client.force_login(platform_ops)
        body = client.get(reverse("admin:patients_patient_changelist")).content.decode()
        assert "North Patient" in body
        assert "South Patient" in body


# --- 2. Object-level permission on a direct URL -----------------------------


class TestDirectURLIsolation:
    def test_another_clinics_record_is_not_reachable_by_url(
        self, client, north_staff, records
    ):
        """``get_queryset`` does not protect this path - the object-level
        permission check does.
        """
        client.force_login(north_staff)
        response = client.get(
            reverse("admin:patients_patient_change", args=[records["south"].pk])
        )
        assert response.status_code in (302, 403), response.status_code

    def test_its_own_record_is_reachable(self, client, north_staff, records):
        client.force_login(north_staff)
        response = client.get(
            reverse("admin:patients_patient_change", args=[records["north"].pk])
        )
        assert response.status_code == 200

    def test_a_crafted_post_cannot_move_a_record_between_clinics(
        self, client, north_staff, records, clinics
    ):
        """The FK dropdown is scoped, so a hand-rolled POST naming another
        clinic must be rejected rather than honoured.
        """
        client.force_login(north_staff)
        client.post(
            reverse("admin:patients_patient_change", args=[records["north"].pk]),
            {
                "clinic": str(clinics["south"].pk),
                "name": "North Patient",
                "contact": "9876500001",
                "age": 30,
                "height_cm": 170,
                "weight_kg": 65,
                "consent_artefacts-TOTAL_FORMS": "0",
                "consent_artefacts-INITIAL_FORMS": "0",
            },
        )
        records["north"].refresh_from_db()
        assert records["north"].clinic_id == clinics["north"].pk


# --- 3. FK dropdown contents ------------------------------------------------


class TestForeignKeyIsolation:
    def test_the_clinic_dropdown_offers_only_the_callers_clinic(
        self, client, north_staff, clinics, records
    ):
        client.force_login(north_staff)
        response = client.get(
            reverse("admin:patients_patient_change", args=[records["north"].pk])
        )
        field = response.context["adminform"].form.fields["clinic"]
        assert list(field.queryset) == [clinics["north"]]


# --- 4. The autocomplete endpoint -------------------------------------------


class TestAutocompleteIsolation:
    def test_autocomplete_does_not_leak_other_clinics_patients(
        self, client, north_staff, records, roles
    ):
        """The leak AUDIT 8.7 says everyone misses: the endpoint resolves
        through the *related* model's admin, so PatientAdmin's scoping is what
        protects it.
        """
        north_staff.user_permissions.add(
            Permission.objects.get(codename="view_triageassessment")
        )
        client.force_login(north_staff)

        response = client.get(
            reverse("admin:autocomplete"),
            {
                "app_label": "triage",
                "model_name": "triageassessment",
                "field_name": "patient",
                "term": "Patient",
            },
        )
        assert response.status_code == 200
        names = [row["text"] for row in response.json()["results"]]
        assert any("North" in n for n in names)
        assert not any("South" in n for n in names), names

    def test_searching_for_the_other_clinic_by_name_returns_nothing(
        self, client, north_staff, records, roles
    ):
        north_staff.user_permissions.add(
            Permission.objects.get(codename="view_triageassessment")
        )
        client.force_login(north_staff)
        response = client.get(
            reverse("admin:autocomplete"),
            {
                "app_label": "triage",
                "model_name": "triageassessment",
                "field_name": "patient",
                "term": "South Patient",
            },
        )
        assert response.json()["results"] == []


# --- The fail-closed case ---------------------------------------------------


class TestUnassignedStaffSeeNothing:
    """The inversion that would ship quietly.

    Before tenants existed, "no clinic" meant "nothing to scope by" and every
    query passed through. Now it must mean "refused".
    """

    @pytest.fixture
    def unassigned(self, django_user_model, roles):
        # Staff flag and model permission, but no membership at all - the state
        # an account is in between being created and being assigned.
        user = django_user_model.objects.create_user(
            username="nobody", password="pw", is_staff=True
        )
        user.user_permissions.add(Permission.objects.get(codename="view_patient"))
        return user

    def test_an_unassigned_staff_account_sees_no_patients(
        self, client, unassigned, records
    ):
        client.force_login(unassigned)
        response = client.get(reverse("admin:patients_patient_changelist"))
        assert response.status_code == 200
        assert response.context["cl"].result_count == 0
        body = response.content.decode()
        assert "North Patient" not in body
        assert "South Patient" not in body

    def test_an_unassigned_staff_account_cannot_open_a_record(
        self, client, unassigned, records
    ):
        client.force_login(unassigned)
        response = client.get(
            reverse("admin:patients_patient_change", args=[records["north"].pk])
        )
        assert response.status_code in (302, 403)

    def test_a_revoked_membership_loses_access(
        self, client, north_staff, records, clinics
    ):
        """Revoking must actually remove the grants, not just the row."""
        client.force_login(north_staff)
        assert (
            client.get(reverse("admin:patients_patient_changelist")).status_code == 200
        )

        membership = north_staff.memberships.get()
        MembershipService().revoke(membership=membership)

        north_staff.refresh_from_db()
        assert not north_staff.is_staff, "losing every role should remove staff access"
        assert north_staff.groups.count() == 0
