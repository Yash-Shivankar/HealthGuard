"""HTTP handling for the triage flow.

Views parse the request, call a service, and render.  They contain no CSV
reads, no model training and no business rules - all of which lived here
before, in a single 193-line module inside the project config package
(AUDIT 2.14).

Every crash path the audit reproduced is closed:

* ``GET /predict/`` used to fall off the end of the function and return
  ``None``, so refreshing or bookmarking a result 500'd (2.6).  It now
  redirects to the symptom form.
* an empty symptom selection divided by zero (2.7) - the form rejects it.
* an unknown symptom name raised ``KeyError`` (2.7) - the form rejects it and
  the predictor reports it.
* a missing ``days`` value raised ``TypeError`` (2.7) - the form rejects it.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from prediction_app.forms import IntakeForm, SymptomCheckForm
from prediction_app.models import UserInfo
from prediction_app.services import content, predictor, triage

logger = logging.getLogger(__name__)

INTAKE_SESSION_KEY = "intake_record_id"


@require_http_methods(["GET"])
def home(request):
    return render(request, "home.html")


@require_http_methods(["GET"])
def privacy(request):
    """The notice the consent tick on the intake form refers to."""
    return render(request, "privacy.html")


@require_http_methods(["GET", "POST"])
def register(request):
    """Collect intake details and consent.

    The record goes to the database.  The old handler appended it to
    ``data.csv`` in the repository root, which is the direct cause of the
    privacy incident in AUDIT 2.1.
    """
    if request.method == "POST":
        form = IntakeForm(request.POST)
        if form.is_valid():
            record = form.save()
            # Only the row id goes into the session - never patient data.
            request.session[INTAKE_SESSION_KEY] = record.pk
            logger.info("Intake record %s created", record.pk)
            return redirect("symptoms")
        logger.info("Intake form rejected: %s", form.errors.as_json())
    else:
        form = IntakeForm()

    return render(request, "register.html", {"form": form})


@require_http_methods(["GET"])
def symptoms(request):
    return render(request, "symptom.html", {"form": SymptomCheckForm()})


@require_http_methods(["GET", "POST"])
def predict(request):
    """Run the triage model and render the result.

    A GET lands here whenever someone refreshes the result page or follows a
    shared link, which is an ordinary thing to do - so it redirects to the form
    rather than erroring.
    """
    if request.method == "GET":
        return redirect("symptoms")

    form = SymptomCheckForm(request.POST)
    if not form.is_valid():
        logger.info("Symptom form rejected: %s", form.errors.as_json())
        return render(request, "symptom.html", {"form": form}, status=400)

    selected = form.cleaned_data["symptoms"]
    days = form.cleaned_data["days"]

    try:
        differential = predictor.predict(selected)
    except predictor.ModelUnavailable:
        logger.exception("Triage model unavailable")
        return render(request, "unavailable.html", status=503)

    if differential.abstained:
        context = {
            "abstained": True,
            "urgency": triage.abstention_urgency(),
            "symptoms": [form.humanise(s) for s in selected],
            "days": days,
        }
        return render(request, "result.html", context)

    top = differential.top
    context = {
        "abstained": False,
        "candidates": differential.candidates,
        "top": top,
        "description": content.description_for(top.disease),
        "precautions": content.precautions_for(top.disease),
        "remedies": content.remedies_for(top.disease),
        "urgency": triage.assess(selected, days),
        "symptoms": [form.humanise(s) for s in selected],
        "days": days,
        "differential_size": settings.TRIAGE_DIFFERENTIAL_SIZE,
    }
    return render(request, "result.html", context)


@require_http_methods(["GET", "POST"])
def withdraw_consent(request):
    """Let someone withdraw the consent they gave earlier in this session.

    Withdrawal must be as easy as giving consent (AUDIT 4.1).  This is the
    session-scoped minimum; a full data-subject-request workflow is Phase 4.
    """
    record_id = request.session.get(INTAKE_SESSION_KEY)
    record = UserInfo.objects.filter(pk=record_id).first() if record_id else None

    if request.method == "POST" and record is not None:
        record.withdraw_consent()
        request.session.pop(INTAKE_SESSION_KEY, None)
        logger.info("Consent withdrawn for intake record %s", record.pk)
        return render(request, "consent_withdrawn.html")

    return render(request, "withdraw_consent.html", {"record": record})
