"""HTML controllers for intake and consent withdrawal.

The form is validated here, but every rule it enforces lives in
``PatientService`` - the controller's job is to turn a domain error back into
a field error the page can render.
"""

from __future__ import annotations

import logging

from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from apps.consent.services.consent_service import ConsentService
from apps.patients.forms import IntakeForm
from apps.patients.services.patient_service import PatientService
from utils.exceptions.domain import DomainError

logger = logging.getLogger(__name__)

INTAKE_SESSION_KEY = "patient_id"


@require_http_methods(["GET"])
def privacy(request):
    """The notice the consent tick on the intake form refers to."""
    return render(request, "privacy.html")


@require_http_methods(["GET", "POST"])
def register(request):
    """Collect intake details and consent.

    The record goes to the database.  The original handler appended it to
    ``data.csv`` in the repository root, which is the direct cause of the
    privacy incident in AUDIT 2.1.
    """
    if request.method == "POST":
        form = IntakeForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            try:
                patient = PatientService().register(
                    name=data["name"],
                    contact=data["contact"],
                    age=data["age"],
                    height_cm=data["height_cm"],
                    weight_kg=data["weight_kg"],
                    consent_given=data["consent"],
                )
            except DomainError as exc:
                # A rule the form could not express on its own; surface it as a
                # form error rather than a 500.
                form.add_error(None, exc.message)
            else:
                request.session[INTAKE_SESSION_KEY] = patient.pk
                return redirect("symptoms")

        logger.info("Intake form rejected: %s", form.errors.as_json())
    else:
        form = IntakeForm()

    return render(request, "register.html", {"form": form})


@require_http_methods(["GET", "POST"])
def withdraw_consent(request):
    """Withdraw consent given earlier in this session.

    Withdrawal must be as easy as giving consent (AUDIT 4.1).  This is the
    session-scoped minimum; a full data-subject-request workflow, reachable
    without the original browser session, is Phase 4.
    """
    service = PatientService()
    patient = service.find(request.session.get(INTAKE_SESSION_KEY))

    if request.method == "POST" and patient is not None:
        service.forget(patient)
        request.session.pop(INTAKE_SESSION_KEY, None)
        return render(request, "consent_withdrawn.html")

    artefacts = (
        ConsentService().artefacts_for(patient=patient) if patient is not None else []
    )
    return render(
        request,
        "withdraw_consent.html",
        {"record": patient, "artefacts": artefacts},
    )
