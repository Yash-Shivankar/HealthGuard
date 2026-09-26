"""Retention enforced in code, not in a document (AUDIT 4.1).

The audit's phrasing is the requirement:

    Erase after 3 years of no interaction (certain fiduciaries)
        -> Retention policy enforced in code, not in a doc
    Notify 48 hours before erasure
        -> Scheduled-deletion job with advance notification

So this is two passes, deliberately separate:

``issue_notices``  finds records past the retention horizon and opens a notice
                   for each. Erases nothing.
``run_erasures``   erases only records whose notice period has elapsed.

Splitting them is what makes the 48 hours real. A single pass that noticed and
erased in one go would satisfy the letter of "give notice" and none of its
purpose, because the person would have no window in which to object.

Both are idempotent, because they run on a schedule and a re-run after a
partial failure must not double-notify or re-erase.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.audit.models import AuditAction
from apps.audit.services.audit_service import AuditService
from apps.compliance.models import RETENTION_YEARS, RetentionNotice
from apps.compliance.repositories.compliance_repository import RetentionRepository
from apps.patients.repositories.patient_repository import PatientRepository
from apps.patients.services.patient_service import PatientService

logger = logging.getLogger(__name__)


class RetentionService:
    def __init__(
        self,
        retention: RetentionRepository | None = None,
        patients: PatientRepository | None = None,
        patient_service: PatientService | None = None,
        audit: AuditService | None = None,
    ):
        self.retention = retention or RetentionRepository()
        self.patients = patients or PatientRepository()
        self.patient_service = patient_service or PatientService()
        self.audit = audit or AuditService()

    # --- Pass 1: notice -----------------------------------------------------
    def issue_notices(self, *, dry_run: bool = False) -> dict:
        """Open a retention notice for every record past the horizon.

        Erases nothing. The notice starts a clock that ``run_erasures`` reads.
        """
        cutoff = timezone.now() - timedelta(days=365 * RETENTION_YEARS)
        candidates = self.patients.created_before(cutoff)

        issued = skipped = 0
        for patient in candidates:
            # Idempotent: a record already under notice is left alone, so a
            # re-run after a partial failure does not double-notify.
            if self.retention.open_notice_for(patient.pk) is not None:
                skipped += 1
                continue

            if dry_run:
                issued += 1
                continue

            notice = self.retention.create_notice(
                patient_ref=patient.pk,
                clinic=patient.clinic,
                reason=f"No interaction for {RETENTION_YEARS} years",
            )
            logger.info(
                "Retention notice issued",
                extra={
                    "event": "retention_notice_issued",
                    "patient_ref": patient.pk,
                    "due_at": notice.due_at.isoformat(),
                },
            )
            issued += 1

        return {"issued": issued, "already_under_notice": skipped, "dry_run": dry_run}

    # --- Pass 2: erasure ----------------------------------------------------
    def run_erasures(self, *, dry_run: bool = False) -> dict:
        """Erase records whose notice period has elapsed.

        Each erasure is its own transaction. One bad row must not roll back the
        rest of the run - a retention pass that fails atomically is a retention
        pass that never completes.
        """
        due = list(self.retention.due_notices())
        erased = cancelled = missing = 0

        for notice in due:
            patient = self.patients.get_by_id(notice.patient_ref)

            if patient is None:
                # Already gone - erased by a consent withdrawal, most likely.
                if not dry_run:
                    self._close(notice, erased=True)
                missing += 1
                continue

            # A person who came back inside the notice window is no longer
            # dormant, so the notice is cancelled rather than executed.
            if self._interacted_since(patient, notice):
                if not dry_run:
                    self._close(notice, cancelled=True)
                cancelled += 1
                continue

            if dry_run:
                erased += 1
                continue

            with transaction.atomic():
                self.audit.record(
                    action=AuditAction.DELETE,
                    actor=None,
                    object_type="patients.Patient",
                    object_id=str(patient.pk),
                    reason=f"retention: {notice.reason}",
                )
                self.patient_service.forget(patient)
                self._close(notice, erased=True)

            logger.info(
                "Record erased under retention",
                extra={"event": "retention_erased", "patient_ref": notice.patient_ref},
            )
            erased += 1

        return {
            "erased": erased,
            "cancelled": cancelled,
            "already_absent": missing,
            "dry_run": dry_run,
        }

    # --- Helpers ------------------------------------------------------------
    @staticmethod
    def _interacted_since(patient, notice: RetentionNotice) -> bool:
        """Did this person do anything after the notice was issued?

        ``updated_at`` covers an edit; a newer assessment covers a fresh check.
        Either means they are no longer dormant.
        """
        if patient.updated_at > notice.issued_at:
            return True
        latest = patient.assessments.order_by("-created_at").first()
        return latest is not None and latest.created_at > notice.issued_at

    @staticmethod
    def _close(notice: RetentionNotice, *, erased: bool = False, cancelled: bool = False):
        now = timezone.now()
        if erased:
            notice.erased_at = now
        if cancelled:
            notice.cancelled_at = now
        notice.save(update_fields=["erased_at", "cancelled_at"])

    def cancel_for_patient(self, patient) -> int:
        """Cancel any open notice because the person interacted.

        Called from the intake and triage paths, so returning to use the service
        is itself enough to stop the clock.
        """
        notice = self.retention.open_notice_for(patient.pk)
        if notice is None:
            return 0
        self._close(notice, cancelled=True)
        logger.info(
            "Retention notice cancelled after interaction",
            extra={"event": "retention_cancelled", "patient_ref": patient.pk},
        )
        return 1
