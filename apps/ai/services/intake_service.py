"""Clinician sign-off on an intake draft, and the hand-off to triage.

This is the only path by which anything this app produced becomes part of a
patient's record, and it is the whole reason the app is allowed to exist in a
clinical system. TPG 2020 requires a clinician between an AI output and a record;
concentrating that requirement in one service means there is one place to read,
one place to test, and nowhere else it could be bypassed.

## Two sign-offs, for two different claims

Easy to conflate and important not to:

**Confirming an intake draft** asserts *these are the symptoms this patient
has*. A clinician is reading a normalisation of what the patient said and
agreeing it is accurate. They may add symptoms the extractor missed and remove
ones it invented - a correction is the most valuable thing this step produces,
and a flow that only allowed acceptance would be a rubber stamp.

**Reviewing the resulting assessment** asserts something else entirely: *the
model's ranked differential is clinically sensible*. That is a separate judgement
about a separate artefact, so the assessment this service creates still starts
``UNREVIEWED`` and still needs its own sign-off in the review queue. Treating the
first sign-off as covering the second would let a machine differential into a
record on the strength of someone agreeing that the patient said "headache".

## What is refused

- a draft that is not in ``draft`` state - a second confirmation is not an edit
- a **simulated** draft, always, whatever the caller asks for
- confirmation with no actor: an unattributed sign-off is not a sign-off
- a symptom outside the model's vocabulary, reported rather than dropped, so a
  clinician's typo does not silently vanish from what they thought they recorded
"""

from __future__ import annotations

import logging

from apps.ai.models import DraftState, IntakeDraft, stamp_confirmation
from apps.ai.repositories.ai_repository import IntakeDraftRepository
from apps.ai.services import lexicon
from apps.audit.models import AuditAction
from apps.audit.services.audit_service import AuditService
from apps.triage.services import predictor
from apps.triage.services.assessment_service import AssessmentService
from utils.exceptions.domain import DomainError, ValidationError

logger = logging.getLogger(__name__)

#: Used when neither the clinician nor the patient's own words gave a duration.
#: One day rather than zero: the field is required, and "today" is the least
#: wrong assumption about someone who has just described a problem.
DEFAULT_DAYS = 1


class IntakeService:
    def __init__(
        self,
        drafts: IntakeDraftRepository | None = None,
        assessments: AssessmentService | None = None,
        audit: AuditService | None = None,
    ):
        self.drafts = drafts or IntakeDraftRepository()
        self.assessments = assessments or AssessmentService()
        self.audit = audit or AuditService()

    # --- The gate -----------------------------------------------------------
    @staticmethod
    def check_confirmable(draft: IntakeDraft, *, actor) -> None:
        """Raise unless this draft may be signed off by this actor.

        Separate from :meth:`confirm` so a UI can grey out a button without
        causing the hand-off, and so the rules are readable in one place.
        """
        if actor is None or not getattr(actor, "pk", None):
            raise DomainError(
                "A clinician must be signed in to confirm an intake draft.",
                code="ai_confirmation_needs_actor",
            )

        if draft.state != DraftState.DRAFT:
            raise DomainError(
                f"That draft has already been {draft.get_state_display().lower()}.",
                code="ai_draft_already_reviewed",
            )

        if draft.simulated:
            # The hard one. A canned transcript or a stubbed provider produced
            # words the patient never said, and no amount of clinician
            # agreement makes fabricated content safe to file.
            raise DomainError(
                "This draft was produced by a simulated provider and cannot be "
                "confirmed into a record. Configure a real provider, or enter "
                "the symptoms directly.",
                code="ai_simulated_cannot_be_confirmed",
            )

    # --- Sign-off -----------------------------------------------------------
    def confirm(
        self,
        *,
        draft: IntakeDraft,
        symptoms: list[str] | None = None,
        days: int | None = None,
        actor=None,
        request=None,
    ):
        """Sign off a draft and run the confirmed symptoms through triage.

        ``symptoms`` is what the clinician accepted. Omitting it accepts the
        proposal as it stands, which is a convenience for the common case and not
        a different code path - it still records what was confirmed, so the
        history shows an explicit list either way.
        """
        self.check_confirmable(draft, actor=actor)

        confirmed = self._validate_symptoms(
            symptoms if symptoms is not None else draft.proposed_symptom_names
        )
        if not confirmed:
            raise ValidationError(
                "Confirm at least one symptom, or discard the draft.",
                code="ai_no_symptoms_confirmed",
            )

        resolved_days = self._resolve_days(draft, days)

        result = self.assessments.assess(
            symptoms=confirmed,
            days=resolved_days,
            patient=draft.patient,
            clinic=draft.clinic,
        )

        draft.confirmed_symptoms = confirmed
        draft.assessment = result.assessment
        stamp_confirmation(draft, actor=actor)
        self.drafts.save(
            draft,
            ["confirmed_symptoms", "assessment", "state", "confirmed_by", "confirmed_at"],
        )

        self.audit.record(
            action=AuditAction.UPDATE,
            actor=actor,
            obj=draft,
            request=request,
            reason="confirmed AI intake draft",
        )

        added = sorted(set(confirmed) - set(draft.proposed_symptom_names))
        removed = sorted(set(draft.proposed_symptom_names) - set(confirmed))
        logger.info(
            "Intake draft confirmed",
            extra={
                "event": "ai_draft_confirmed",
                "draft_id": draft.pk,
                "assessment_id": result.assessment.pk,
                # The correction, which is the signal worth having: an extractor
                # whose proposals are always edited the same way is telling you
                # what to add to the lexicon.
                "symptoms_added": len(added),
                "symptoms_removed": len(removed),
            },
        )
        return result

    def discard(self, *, draft: IntakeDraft, actor=None, reason: str = "", request=None):
        """Reject a draft.

        Allowed on a simulated draft, unlike confirmation: throwing away
        something fabricated is always safe, and a queue that cannot be cleared
        of stub rows would fill with them.
        """
        if draft.state != DraftState.DRAFT:
            raise DomainError(
                f"That draft has already been {draft.get_state_display().lower()}.",
                code="ai_draft_already_reviewed",
            )

        draft.state = DraftState.DISCARDED
        self.drafts.save(draft, ["state"])

        self.audit.record(
            action=AuditAction.UPDATE,
            actor=actor,
            obj=draft,
            request=request,
            reason=f"discarded AI intake draft: {reason}"[:300] if reason
            else "discarded AI intake draft",
        )
        logger.info(
            "Intake draft discarded",
            extra={"event": "ai_draft_discarded", "draft_id": draft.pk},
        )
        return draft

    # --- Helpers ------------------------------------------------------------
    @staticmethod
    def _validate_symptoms(symptoms) -> list[str]:
        """Keep the order, drop duplicates, reject anything the model rejects.

        Reported rather than silently filtered: a clinician who typed a symptom
        the model does not know should be told, not left believing they recorded
        something.
        """
        known = set(predictor.available_symptoms())
        unique = list(dict.fromkeys(str(s).strip() for s in (symptoms or []) if str(s).strip()))

        unknown = [s for s in unique if s not in known]
        if unknown:
            raise ValidationError(
                "These are not symptoms the model recognises: "
                f"{', '.join(unknown[:5])}.",
                code="ai_unknown_symptom",
            )
        return unique

    @staticmethod
    def _resolve_days(draft: IntakeDraft, days: int | None) -> int:
        """Duration, preferring what the clinician said over what we inferred.

        Order matters: a clinician's number is a clinical judgement, the parsed
        one is a guess about a sentence, and the default is an admission that
        neither was available.
        """
        if days is not None and int(days) > 0:
            return int(days)
        parsed = lexicon.extract_duration(draft.source_text)
        return parsed if parsed else DEFAULT_DAYS
