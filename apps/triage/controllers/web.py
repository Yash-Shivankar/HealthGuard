"""HTML controllers for the triage flow.

These render the server-side pages.  They exist alongside the JSON API because
AUDIT 11.2 - Django templates or a React SPA - is still an open decision, and
deleting a working surface to bet on an unmade decision would be the wrong
order of operations.

Both surfaces call the same ``AssessmentService``, so there is one
implementation of the rules and two ways to reach it.
"""

from __future__ import annotations

import logging

from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from apps.patients.services.patient_service import PatientService
from apps.triage.forms import SymptomCheckForm
from apps.triage.services import predictor
from apps.triage.services.assessment_service import AssessmentService, humanise

logger = logging.getLogger(__name__)

INTAKE_SESSION_KEY = "patient_id"


@require_http_methods(["GET"])
def home(request):
    return render(request, "home.html")


@require_http_methods(["GET"])
def symptoms(request):
    return render(request, "symptom.html", {"form": SymptomCheckForm()})


@require_http_methods(["GET", "POST"])
def predict(request):
    """Run a check and render the result.

    A GET lands here whenever someone refreshes the result page or follows a
    shared link, which is an ordinary thing to do - so it redirects to the form
    rather than returning None and 500ing, which is what it used to do
    (AUDIT 2.6).
    """
    if request.method == "GET":
        return redirect("symptoms")

    form = SymptomCheckForm(request.POST)
    if not form.is_valid():
        logger.info("Symptom form rejected: %s", form.errors.as_json())
        return render(request, "symptom.html", {"form": form}, status=400)

    selected = form.cleaned_data["symptoms"]
    days = form.cleaned_data["days"]
    patient = PatientService().find(request.session.get(INTAKE_SESSION_KEY))

    try:
        result = AssessmentService().assess(
            symptoms=selected, days=days, patient=patient
        )
    except predictor.ModelUnavailable:
        logger.exception("Triage model unavailable")
        return render(request, "unavailable.html", status=503)

    context = {
        "abstained": result.abstained,
        "candidates": result.differential.candidates,
        "top": result.top,
        "description": result.description,
        "precautions": result.precautions,
        "remedies": result.remedies,
        "urgency": result.urgency,
        "symptoms": [humanise(s) for s in selected],
        "days": days,
    }
    return render(request, "result.html", context)
