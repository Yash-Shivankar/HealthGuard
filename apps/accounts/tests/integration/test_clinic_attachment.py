"""Intake taken by staff belongs to their clinic; public intake belongs to none.

The clinic is read from the resolved tenant, never from the request payload.
A client-supplied tenant id is a cross-tenant write waiting to happen, so the
test that matters here is the last one.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.accounts.models import Clinic, Role
from apps.accounts.services.membership_service import MembershipService
from apps.patients.models import Patient
from apps.triage.models import TriageAssessment

pytestmark = pytest.mark.django_db


@pytest.fixture
def clinic() -> Clinic:
    return Clinic.objects.create(name="Attaching Clinic")


@pytest.fixture
def other_clinic() -> Clinic:
    return Clinic.objects.create(name="Someone Else")


@pytest.fixture
def desk(django_user_model, clinic):
    from django.core.management import call_command

    call_command("seed_roles", verbosity=0)
    user = django_user_model.objects.create_user(username="desk", password="pw")
    MembershipService().grant(user=user, clinic=clinic, role=Role.FRONT_DESK)
    user.refresh_from_db()
    return user


@pytest.fixture
def payload() -> dict:
    return {
        "name": "Walk In",
        "contact": "9876500077",
        "age": 44,
        "height_cm": 168,
        "weight_kg": 72,
        "consent": True,
    }


class TestIntakeAttachment:
    def test_public_intake_has_no_clinic(self, client, payload):
        """Self-service intake from the public page. Forcing a clinic here
        would mean inventing one.
        """
        client.post(
            reverse("patients:intake"), payload, content_type="application/json"
        )
        assert Patient.objects.get().clinic_id is None

    def test_staff_intake_lands_in_their_clinic(self, client, desk, clinic, payload):
        client.force_login(desk)
        client.post(
            reverse("patients:intake"), payload, content_type="application/json"
        )
        assert Patient.objects.get().clinic_id == clinic.pk

    def test_a_clinic_in_the_payload_is_ignored(
        self, client, desk, clinic, other_clinic, payload
    ):
        """The clinic comes from the resolved tenant, not the request body."""
        client.force_login(desk)
        payload["clinic"] = other_clinic.pk
        client.post(
            reverse("patients:intake"), payload, content_type="application/json"
        )
        assert Patient.objects.get().clinic_id == clinic.pk


class TestAssessmentAttachment:
    KNOWN = ["itching", "skin_rash", "nodal_skin_eruptions", "dischromic _patches"]

    def test_an_anonymous_check_has_no_clinic(self, client):
        client.post(
            reverse("triage:check"),
            {"symptoms": self.KNOWN, "days": 3},
            content_type="application/json",
        )
        assert TriageAssessment.objects.get().clinic_id is None

    def test_a_check_run_by_staff_is_attributed_to_their_clinic(
        self, client, desk, clinic
    ):
        client.force_login(desk)
        client.post(
            reverse("triage:check"),
            {"symptoms": self.KNOWN, "days": 3},
            content_type="application/json",
        )
        assert TriageAssessment.objects.get().clinic_id == clinic.pk

    def test_the_clinic_is_denormalised_from_the_patient(
        self, client, desk, clinic, payload
    ):
        """So a scoped query never has to join to find out whose check it was."""
        client.force_login(desk)
        client.post(
            reverse("patients:intake"), payload, content_type="application/json"
        )
        client.post(
            reverse("triage:check"),
            {"symptoms": self.KNOWN, "days": 3},
            content_type="application/json",
        )
        assessment = TriageAssessment.objects.get()
        assert assessment.patient_id == Patient.objects.get().pk
        assert assessment.clinic_id == clinic.pk
