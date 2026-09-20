"""Database models for the triage flow.

Why this file matters more than its size suggests: ``UserInfo`` was previously
defined in the project config package, which owns no migrations, so **no table
was ever created**.  That silent failure is why intake data was appended to a
flat ``data.csv`` instead - which is how 40 real names and mobile numbers ended
up committed to git (AUDIT 1, 2.1).  The model now lives in a real app with a
real migration, and the CSV write is gone.

Consent is recorded as part of the record rather than assumed.  Under the DPDP
Act 2023 and the DPDP Rules 2025 a data fiduciary must be able to show what a
person was told and when they agreed to it, and must support withdrawal
(AUDIT 4.1).  A boolean alone cannot do that, so the notice version and
timestamp are stored alongside it.  This is the minimum honest version, not a
full consent-artefact implementation - that is Phase 4 work.
"""

from __future__ import annotations

from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.utils import timezone

# Bump this whenever the wording of the consent notice changes, so existing
# records keep pointing at the text their owner actually agreed to.
CONSENT_NOTICE_VERSION = "2026-09-20.1"

phone_validator = RegexValidator(
    regex=r"^[6-9]\d{9}$",
    message="Enter a valid 10-digit Indian mobile number.",
)


class UserInfo(models.Model):
    """A person's self-reported details, captured before a symptom check.

    None of these fields are model inputs - the triage model uses symptoms
    only.  They are collected so a clinician reviewing an intake has context.
    """

    name = models.CharField(max_length=100)
    contact = models.CharField(
        max_length=10,
        validators=[phone_validator],
        help_text="10-digit mobile number.",
    )
    age = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(0), MaxValueValidator(120)]
    )
    height_cm = models.FloatField(
        validators=[MinValueValidator(30), MaxValueValidator(275)]
    )
    weight_kg = models.FloatField(
        validators=[MinValueValidator(1), MaxValueValidator(650)]
    )

    # --- Consent (DPDP Act 2023 / DPDP Rules 2025) ---
    consent_given = models.BooleanField(default=False)
    consent_notice_version = models.CharField(max_length=32, blank=True)
    consent_at = models.DateTimeField(null=True, blank=True)
    consent_withdrawn_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Intake record"
        verbose_name_plural = "Intake records"
        ordering = ("-created_at",)
        indexes = [models.Index(fields=["-created_at"])]

    def __str__(self) -> str:
        # Never the full contact number: __str__ lands in admin listings, log
        # lines and error reports (AUDIT 8.8).
        return f"{self.name} ({self.masked_contact})"

    @property
    def masked_contact(self) -> str:
        """Contact number with all but the last four digits hidden."""
        if len(self.contact) <= 4:
            return "•" * len(self.contact)
        return f"{'•' * (len(self.contact) - 4)} {self.contact[-4:]}"

    @property
    def has_active_consent(self) -> bool:
        return self.consent_given and self.consent_withdrawn_at is None

    def record_consent(self, *, version: str = CONSENT_NOTICE_VERSION) -> None:
        self.consent_given = True
        self.consent_notice_version = version
        self.consent_at = timezone.now()
        self.consent_withdrawn_at = None

    def withdraw_consent(self) -> None:
        """Withdrawal must be as easy as giving consent (AUDIT 4.1)."""
        self.consent_withdrawn_at = timezone.now()
        self.save(update_fields=["consent_withdrawn_at", "updated_at"])
