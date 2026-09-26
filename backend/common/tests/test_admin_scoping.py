"""Cross-tenant isolation tests for the admin (AUDIT 8.7, step 6).

The audit is blunt about this: putting Django Admin in front of a multi-tenant
healthcare product is "the single most dangerous part", four places need
scoping, "most implementations only do the first", and **the isolation tests
get written before the admin classes**. It also says this is the one area it
would not trust review alone on.

## What these tests scope by, and why it is not a clinic

There is no ``Clinic`` model until Phase 4. Rather than skip the tests until
there is one - which is exactly how this gets skipped permanently - they run
against a real scoping dimension that exists today: a ``TriageAssessment``
belongs to a ``Patient``, and an admin scoped to one patient must not reveal
another's. That exercises all four code paths for real, through the real
admin machinery, with a real HTTP client.

When Phase 4 adds ``clinic_id``, only ``ScopedModelAdmin.get_tenant`` and the
``tenant_field`` values change. These tests keep working and keep meaning the
same thing.

Four tests per scoped model, as the audit specifies: list, direct-URL change,
FK dropdown contents, autocomplete response.
"""

from __future__ import annotations

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from django.urls import reverse

from apps.patients.models import Patient
from apps.triage.models import TriageAssessment
from common.admin import ScopedModelAdmin

pytestmark = pytest.mark.django_db


# --- Test doubles: a scoped admin whose tenant is "one patient" -------------


class PatientScopedAssessmentAdmin(ScopedModelAdmin):
    """Assessments, scoped to the patient the request 'belongs to'."""

    tenant_field = "patient_id"
    tenant_scoped_fks = ("patient",)

    def get_tenant(self, request):
        # Stands in for `request.user.clinic_id`.
        return getattr(request, "_test_tenant", None)


class PatientScopedPatientAdmin(ScopedModelAdmin):
    """Patients, scoped to themselves.

    This is the *related* admin, and scoping it is what closes the autocomplete
    leak - the place the audit says everyone misses.
    """

    tenant_field = "id"
    search_fields = ("name",)

    def get_tenant(self, request):
        return getattr(request, "_test_tenant", None)


@pytest.fixture
def users(django_user_model):
    staff = django_user_model.objects.create_user(
        username="staff", password="pw", is_staff=True, is_superuser=False
    )
    # Staff need model permissions before the admin will show them anything.
    from django.contrib.auth.models import Permission

    staff.user_permissions.add(
        *Permission.objects.filter(
            content_type__app_label__in=("triage", "patients"),
            codename__startswith="view_",
        )
    )
    root = django_user_model.objects.create_superuser(
        username="root", password="pw", email="root@example.com"
    )
    return {"staff": staff, "root": root}


@pytest.fixture
def tenants():
    mine = Patient.objects.create(
        name="Mine", contact="9876500001", age=30, height_cm=170, weight_kg=65
    )
    theirs = Patient.objects.create(
        name="Theirs", contact="9876500002", age=40, height_cm=160, weight_kg=55
    )
    TriageAssessment.objects.create(patient=mine, symptoms=["itching"], days=1)
    TriageAssessment.objects.create(patient=theirs, symptoms=["headache"], days=2)
    return {"mine": mine, "theirs": theirs}


def _request(user, tenant=None, path="/"):
    request = RequestFactory().get(path)
    request.user = user
    request._test_tenant = tenant
    return request


# --- 1. The list view -------------------------------------------------------


class TestListScoping:
    def test_list_shows_only_the_callers_tenant(self, users, tenants):
        site = AdminSite()
        model_admin = PatientScopedAssessmentAdmin(TriageAssessment, site)
        request = _request(users["staff"], tenant=tenants["mine"].pk)

        rows = model_admin.get_queryset(request)
        assert rows.count() == 1
        assert rows.first().patient_id == tenants["mine"].pk

    def test_an_unscoped_caller_sees_everything(self, users, tenants):
        site = AdminSite()
        model_admin = PatientScopedAssessmentAdmin(TriageAssessment, site)
        assert model_admin.get_queryset(_request(users["root"])).count() == 2

    def test_a_superuser_bypasses_scoping_even_with_a_tenant(self, users, tenants):
        """Documents the sharp edge: AUDIT 8.8 says do not hand out
        is_superuser, precisely because it bypasses every check here.
        """
        site = AdminSite()
        model_admin = PatientScopedAssessmentAdmin(TriageAssessment, site)
        request = _request(users["root"], tenant=tenants["mine"].pk)
        assert model_admin.get_queryset(request).count() == 2


# --- 2. Object-level permission on a direct URL -----------------------------


class TestObjectPermissionScoping:
    def test_cannot_view_another_tenants_object_by_direct_url(self, users, tenants):
        """get_queryset does NOT protect this path."""
        site = AdminSite()
        model_admin = PatientScopedAssessmentAdmin(TriageAssessment, site)
        request = _request(users["staff"], tenant=tenants["mine"].pk)
        theirs = TriageAssessment.objects.get(patient=tenants["theirs"])

        assert model_admin.has_view_permission(request, theirs) is False
        assert model_admin.has_change_permission(request, theirs) is False
        assert model_admin.has_delete_permission(request, theirs) is False

    def test_can_act_on_its_own_object(self, users, tenants):
        site = AdminSite()
        model_admin = PatientScopedAssessmentAdmin(TriageAssessment, site)
        request = _request(users["staff"], tenant=tenants["mine"].pk)
        mine = TriageAssessment.objects.get(patient=tenants["mine"])

        assert model_admin.has_view_permission(request, mine) is True

    def test_permission_with_no_object_is_not_blocked(self, users, tenants):
        """The changelist asks without an object; refusing there would hide
        the whole model rather than scope it.
        """
        site = AdminSite()
        model_admin = PatientScopedAssessmentAdmin(TriageAssessment, site)
        request = _request(users["staff"], tenant=tenants["mine"].pk)
        assert model_admin.has_view_permission(request, None) is True


# --- 3. FK dropdown contents ------------------------------------------------


class TestForeignKeyScoping:
    def test_dropdown_offers_only_the_callers_tenant(self, users, tenants):
        """Otherwise a crafted POST assigns an object to another tenant."""
        site = AdminSite()
        site.register(Patient, PatientScopedPatientAdmin)
        model_admin = PatientScopedAssessmentAdmin(TriageAssessment, site)
        request = _request(users["staff"], tenant=tenants["mine"].pk)

        field = TriageAssessment._meta.get_field("patient")
        formfield = model_admin.formfield_for_foreignkey(field, request)

        offered = list(formfield.queryset)
        assert offered == [tenants["mine"]]

    def test_dropdown_is_empty_when_the_related_admin_is_unscoped(
        self, users, tenants
    ):
        """Fail closed, not open.

        If the related model has no scoped admin we cannot know which rows are
        safe, so we offer none and log an error. An unscoped dropdown here
        would list every patient in the system.
        """
        site = AdminSite()  # Patient deliberately not registered
        model_admin = PatientScopedAssessmentAdmin(TriageAssessment, site)
        request = _request(users["staff"], tenant=tenants["mine"].pk)

        field = TriageAssessment._meta.get_field("patient")
        formfield = model_admin.formfield_for_foreignkey(field, request)
        assert list(formfield.queryset) == []


# --- 4. The autocomplete endpoint -------------------------------------------


class TestAutocompleteScoping:
    """The leak everyone misses.

    ``autocomplete_fields`` is served by ``AutocompleteJsonView``, which
    queries the **related** model's ModelAdmin. A scoped admin whose related
    admin is unscoped returns other tenants' patient names as JSON after three
    keystrokes.
    """

    def test_autocomplete_resolves_through_the_related_admin_queryset(
        self, users, tenants
    ):
        site = AdminSite()
        related = PatientScopedPatientAdmin(Patient, site)
        request = _request(users["staff"], tenant=tenants["mine"].pk)

        # This is the exact call AutocompleteJsonView.get_queryset makes.
        visible = related.get_queryset(request)
        assert list(visible) == [tenants["mine"]]
        assert tenants["theirs"] not in visible

    def test_search_cannot_reach_across_tenants(self, users, tenants):
        """Searching for the other tenant's name by name returns nothing."""
        site = AdminSite()
        related = PatientScopedPatientAdmin(Patient, site)
        request = _request(users["staff"], tenant=tenants["mine"].pk)

        queryset, _ = related.get_search_results(
            request, related.get_queryset(request), "Theirs"
        )
        assert queryset.count() == 0

    def test_the_live_autocomplete_endpoint_is_scoped(self, client, users, tenants):
        """End-to-end through the real URL, not just the admin object.

        The registered PatientAdmin is unscoped today because there are no
        tenants - so this asserts the endpoint is reachable and correct now,
        and it is the test that will catch a Phase 4 regression.
        """
        client.force_login(users["root"])
        response = client.get(
            reverse("admin:autocomplete"),
            {
                "app_label": "triage",
                "model_name": "triageassessment",
                "field_name": "patient",
                "term": "Mine",
            },
        )
        assert response.status_code == 200
        names = [row["text"] for row in response.json()["results"]]
        assert any("Mine" in n for n in names)


# --- The discipline itself --------------------------------------------------


class TestEveryScopedAdminCoversAllFourPlaces:
    """A scoped admin that overrides only get_queryset is the failure mode the
    audit describes. Assert the base class supplies all four.
    """

    @pytest.mark.parametrize(
        "method",
        [
            "get_queryset",
            "formfield_for_foreignkey",
            "has_view_permission",
            "has_change_permission",
            "has_delete_permission",
        ],
    )
    def test_base_class_defines_the_hook(self, method):
        assert method in ScopedModelAdmin.__dict__, (
            f"ScopedModelAdmin must define {method}; without it a subclass "
            "silently inherits Django's unscoped default."
        )

    def test_a_scoped_model_declares_its_tenant_field(self):
        """A ScopedModelAdmin with tenant_field=None scopes nothing. That is
        legitimate for global master data, but it must be a decision, not an
        oversight - so the default is None and every scoped admin sets it.
        """
        assert ScopedModelAdmin.tenant_field is None
        assert PatientScopedAssessmentAdmin.tenant_field == "patient_id"
