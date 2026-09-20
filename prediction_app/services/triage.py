"""Urgency advice derived from symptom severity and how long they have lasted.

Two audit findings are addressed here.

**Duration was collected, required, and then ignored.**  ``calc_condition``
took ``num_days`` and never referenced it (AUDIT 2.12).  The algorithm this
project derives from weighted severity by duration; the weighting was dropped
but the form field was kept.  It is restored below.

**An empty symptom list divided by zero.**  ``severity_sum / len(symptoms)``
raised ``ZeroDivisionError`` on a two-click path through the UI (AUDIT 2.7).
The denominator is now ``len(symptoms) + 1``, which both matches the original
weighting and cannot be zero.

A caveat that belongs in the code and not only in a document: **the threshold
below is an inherited magic number with no clinical basis.**  It was 13 in the
original project and nobody recorded why.  It is kept, named and documented
rather than silently re-tuned, because changing a triage boundary is a clinical
decision needing a qualified sign-off, not an engineering one (AUDIT 11.6).
"""

from __future__ import annotations

from dataclasses import dataclass

from prediction_app.services import content

# Inherited threshold on the duration-weighted severity score.  Not clinically
# validated.  See the module docstring before changing it.
CONSULT_THRESHOLD = 13

# Above this the advice escalates further.  Chosen as twice the inherited
# threshold purely so that a very high score reads differently from a marginal
# one; it is equally un-validated and equally in need of sign-off.
URGENT_THRESHOLD = CONSULT_THRESHOLD * 2


@dataclass(frozen=True)
class Urgency:
    """Advice about how soon to seek care.

    ``level`` drives styling; ``label`` and ``advice`` are the user-facing text.
    Colour is never the only carrier of meaning - every level ships with a text
    label (AUDIT 8.3).
    """

    level: str
    label: str
    advice: str
    score: float

    @property
    def is_escalated(self) -> bool:
        return self.level in {"urgent", "consult"}


def severity_score(symptoms, days: int) -> float:
    """Duration-weighted mean severity.

        (sum of severity weights * days) / (symptom count + 1)

    The ``+ 1`` is from the source algorithm and doubles as the guard that
    makes an empty symptom list safe.
    """
    total = sum(content.severity_of(symptom) for symptom in symptoms)
    return (total * max(days, 0)) / (len(symptoms) + 1)


def assess(symptoms, days: int) -> Urgency:
    """Map symptoms and duration onto an urgency level.

    Deliberately biased toward escalation: over-triage is the correct safety
    posture for a self-triage tool, and the reviewed literature shows every
    credible symptom checker adopts it (AUDIT 3).
    """
    score = severity_score(symptoms, days)

    if not symptoms:
        return Urgency(
            level="unknown",
            label="No symptoms selected",
            advice=(
                "Select at least one symptom so the checker has something to "
                "work with."
            ),
            score=score,
        )

    if score >= URGENT_THRESHOLD:
        return Urgency(
            level="urgent",
            label="See a doctor today",
            advice=(
                "Your symptoms are both severe and long-running. Please arrange "
                "to see a doctor today. If they worsen suddenly, or you have "
                "chest pain, difficulty breathing, confusion or uncontrolled "
                "bleeding, seek emergency care immediately."
            ),
            score=score,
        )

    if score >= CONSULT_THRESHOLD:
        return Urgency(
            level="consult",
            label="Book a consultation",
            advice=(
                "You should get this looked at by a doctor rather than managing "
                "it at home."
            ),
            score=score,
        )

    return Urgency(
        level="routine",
        label="Self-care, with monitoring",
        advice=(
            "This does not look urgent from what you have told us. Take the "
            "precautions listed below, and see a doctor if things do not "
            "improve or they get worse."
        ),
        score=score,
    )


def abstention_urgency() -> Urgency:
    """Advice used when the model declines to name a condition.

    Declining is not the same as reassuring.  When the checker cannot tell what
    is going on, the safe answer is to point the person at a clinician.
    """
    return Urgency(
        level="consult",
        label="See a doctor",
        advice=(
            "There is not enough information here to suggest what this might "
            "be. That is not the same as it being harmless - please describe "
            "your symptoms to a doctor."
        ),
        score=0.0,
    )
