"""DPDP obligations that have deadlines (AUDIT 4.1, 8.5).

The audit's table of obligations is mostly things that cannot be bolted on
later, because each one is a clock that has to be started, recorded and shown:

    Detailed breach report to the Board within 72 hours
    Notify 48 hours before erasure
    Erase after 3 years of no interaction
    Grievance redressal within 90 days

Each is a model here rather than a process note, for one reason: **a compliance
obligation nobody can see is a compliance obligation nobody meets** (8.5). A
deadline in a policy document has no countdown. A row with a due date does.

Nothing in this module stores the *content* of a patient record. A retention
notice references the patient it is about; if that patient is later erased, the
reference survives and the details do not - the same rule the access log
follows.
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

# DPDP Rules 2025: advance notice before erasure, and the retention horizon
# certain fiduciaries must apply after a period of no interaction. Re-verify
# against the MeitY notification before relying on these commercially - the
# audit is explicit that its dates are directional (AUDIT 4.1).
ERASURE_NOTICE_HOURS = 48
RETENTION_YEARS = 3

#: Grievance redressal deadline.
DSR_SLA_DAYS = 90

#: Breach reporting deadline to the Data Protection Board.
BREACH_REPORT_HOURS = 72


class DeadlineMixin(models.Model):
    """Shared countdown behaviour for anything with a due date."""

    class Meta:
        abstract = True

    @property
    def is_overdue(self) -> bool:
        return self.due_at is not None and timezone.now() > self.due_at

    @property
    def hours_remaining(self) -> float | None:
        """Negative once overdue, which is deliberate.

        Clamping at zero hides how late something is, and how late matters more
        than the fact of being late.
        """
        if self.due_at is None:
            return None
        return round((self.due_at - timezone.now()).total_seconds() / 3600, 1)


class RetentionNotice(DeadlineMixin):
    """Advance notice that a record is about to be erased.

    The notice exists as a record because the obligation is to *give* notice,
    not to intend to. Without a row there is nothing to show that 48 hours
    elapsed before the erasure ran, and the erasure itself destroys the
    evidence that would otherwise date it.
    """

    patient_ref = models.PositiveBigIntegerField(
        help_text="Patient row id. Kept as a bare integer so the notice "
        "survives the erasure it precedes."
    )
    clinic = models.ForeignKey(
        "accounts.Clinic",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="retention_notices",
    )

    reason = models.CharField(
        max_length=200,
        default=f"No interaction for {RETENTION_YEARS} years",
    )
    issued_at = models.DateTimeField(default=timezone.now)
    due_at = models.DateTimeField(help_text="Erasure may not run before this.")

    erased_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Set when the person interacts again before the deadline.",
    )

    class Meta:
        verbose_name = "Retention notice"
        verbose_name_plural = "Retention notices"
        ordering = ("due_at",)
        indexes = [
            models.Index(fields=["due_at"]),
            models.Index(fields=["patient_ref"]),
            models.Index(fields=["erased_at", "cancelled_at"]),
        ]

    def __str__(self) -> str:
        return f"Erasure notice for patient #{self.patient_ref}, due {self.due_at:%Y-%m-%d}"

    def save(self, *args, **kwargs):
        if not self.due_at:
            self.due_at = self.issued_at + timedelta(hours=ERASURE_NOTICE_HOURS)
        return super().save(*args, **kwargs)

    @property
    def is_open(self) -> bool:
        return self.erased_at is None and self.cancelled_at is None

    @property
    def is_due(self) -> bool:
        """Open, and the notice period has elapsed."""
        return self.is_open and timezone.now() >= self.due_at


class DSRKind(models.TextChoices):
    ACCESS = "access", "Access a copy of my data"
    CORRECTION = "correction", "Correct my data"
    ERASURE = "erasure", "Erase my data"
    GRIEVANCE = "grievance", "Grievance"


class DSRStatus(models.TextChoices):
    OPEN = "open", "Open"
    IN_PROGRESS = "in_progress", "In progress"
    RESOLVED = "resolved", "Resolved"
    REJECTED = "rejected", "Rejected"


class DataSubjectRequest(DeadlineMixin):
    """A request from a person about their own data, on a 90-day clock.

    The contact details here are the requester's own, given so they can be
    replied to. They are **not** copied from a patient record: a request may
    arrive from someone whose record cannot be found, and matching it is part of
    handling the request rather than a precondition for accepting it.
    """

    kind = models.CharField(max_length=32, choices=DSRKind.choices)
    status = models.CharField(
        max_length=32, choices=DSRStatus.choices, default=DSRStatus.OPEN
    )

    requester_name = models.CharField(max_length=150)
    requester_contact = models.CharField(
        max_length=254, help_text="Email or phone the person asked to be reached on."
    )

    clinic = models.ForeignKey(
        "accounts.Clinic",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="data_subject_requests",
    )
    patient_ref = models.PositiveBigIntegerField(
        null=True, blank=True, help_text="Matched patient row, once identified."
    )

    detail = models.TextField(blank=True)
    resolution = models.TextField(blank=True)

    received_at = models.DateTimeField(default=timezone.now)
    due_at = models.DateTimeField()
    resolved_at = models.DateTimeField(null=True, blank=True)

    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_dsrs",
    )

    class Meta:
        verbose_name = "Data subject request"
        verbose_name_plural = "Data subject requests"
        ordering = ("due_at",)
        indexes = [
            models.Index(fields=["status", "due_at"]),
            models.Index(fields=["clinic", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} from {self.requester_name}"

    def save(self, *args, **kwargs):
        if not self.due_at:
            self.due_at = self.received_at + timedelta(days=DSR_SLA_DAYS)
        return super().save(*args, **kwargs)

    @property
    def is_open(self) -> bool:
        return self.status in {DSRStatus.OPEN, DSRStatus.IN_PROGRESS}

    def resolve(self, *, resolution: str, status: str = DSRStatus.RESOLVED) -> None:
        self.status = status
        self.resolution = resolution
        self.resolved_at = timezone.now()
        self.save(update_fields=["status", "resolution", "resolved_at"])


class BreachStatus(models.TextChoices):
    OPEN = "open", "Open, not yet reported"
    REPORTED = "reported", "Reported to the Board"
    CLOSED = "closed", "Closed"


class BreachIncident(DeadlineMixin):
    """A suspected or confirmed personal data breach, on a 72-hour clock.

    The clock starts at ``detected_at``, not at ``created_at``: the obligation
    runs from when the breach became known, and a row created three days later
    does not reset it. Backdating ``detected_at`` honestly is the point.
    """

    summary = models.CharField(max_length=300)
    detail = models.TextField(blank=True)

    status = models.CharField(
        max_length=32, choices=BreachStatus.choices, default=BreachStatus.OPEN
    )
    clinic = models.ForeignKey(
        "accounts.Clinic",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="breach_incidents",
        help_text="Null when the incident is platform-wide.",
    )

    records_affected = models.PositiveIntegerField(null=True, blank=True)

    detected_at = models.DateTimeField(
        default=timezone.now, help_text="When the breach became known. Starts the clock."
    )
    due_at = models.DateTimeField()
    reported_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    raised_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="raised_breaches",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Breach incident"
        verbose_name_plural = "Breach incidents"
        ordering = ("due_at",)
        indexes = [
            models.Index(fields=["status", "due_at"]),
            models.Index(fields=["clinic", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.summary} ({self.get_status_display()})"

    def save(self, *args, **kwargs):
        if not self.due_at:
            self.due_at = self.detected_at + timedelta(hours=BREACH_REPORT_HOURS)
        return super().save(*args, **kwargs)

    @property
    def is_open(self) -> bool:
        return self.status == BreachStatus.OPEN

    def mark_reported(self) -> None:
        self.status = BreachStatus.REPORTED
        self.reported_at = timezone.now()
        self.save(update_fields=["status", "reported_at"])
