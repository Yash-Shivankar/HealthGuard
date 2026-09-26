"""DPDP consent artefacts.

The DPDP Rules 2025 require a data fiduciary to show *what* a person was told,
*when* they agreed, *for which purpose*, and that withdrawal was available and
honoured (AUDIT 4.1).  Booleans on the patient row cannot carry that, so
consent is a record in its own right: one artefact per person per purpose,
stamped with the version of the notice text they actually saw.

**The artefact outlives the patient row on purpose.**  When someone withdraws
consent we erase their details, but the artefact must survive - it is the
evidence that the erasure was requested and lawful. Destroying it along with
the data would leave nothing to show the Board. So the foreign key is
``SET_NULL`` and the original row id is kept separately in ``patient_ref``,
which is a bare integer and identifies nobody on its own.
"""

from __future__ import annotations

from django.db import models
from django.utils import timezone

# Bump when the wording of a notice changes, so existing artefacts keep
# pointing at the text their owner actually agreed to.
CURRENT_NOTICE_VERSION = "2026-09-20.1"


class ConsentPurpose(models.TextChoices):
    """Purposes are itemised, never bundled.

    The DPDP Rules require notice to be specific and itemised; a single
    "I agree to everything" tick is the pattern they exist to stop.
    """

    SYMPTOM_CHECK = "symptom_check", "Symptom check and clinician review"
    # Separate on purpose. Agreeing to a symptom check is not agreeing to link
    # a national health identifier and share records through ABDM, and the
    # DPDP Rules require notice to be itemised per purpose (AUDIT 4.1, 4.2).
    ABDM_LINKAGE = "abdm_linkage", "Link an ABHA and share records via ABDM"
    # Separate again, and for the same reason. Agreeing to a symptom check is
    # not agreeing that a recording of your voice, or your description of your
    # illness, may be sent to a third-party AI processor. AUDIT 6 states the
    # guardrail: no patient-identifiable data to third-party LLM APIs without a
    # consent basis and a processor agreement. This is that basis, and it is
    # itemised because the DPDP Rules require notice to be.
    #
    # Only needed for *external* processing. The default extractor runs locally
    # on data we already hold under SYMPTOM_CHECK, and demanding a second
    # consent for that would train people to click through the one that matters.
    AI_ASSISTANCE = "ai_assistance", "AI assistance using a third-party processor"


class ConsentArtefact(models.Model):
    patient = models.ForeignKey(
        "patients.Patient",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="consent_artefacts",
    )
    # Survives erasure of the patient row; see the module docstring.
    patient_ref = models.PositiveBigIntegerField(
        help_text="Id of the patient row this artefact was granted for, kept "
        "after erasure so the artefact remains traceable."
    )

    purpose = models.CharField(
        max_length=64,
        choices=ConsentPurpose.choices,
        default=ConsentPurpose.SYMPTOM_CHECK,
    )
    notice_version = models.CharField(
        max_length=32,
        help_text="Version of the notice text the person was shown.",
    )

    granted_at = models.DateTimeField(default=timezone.now)
    withdrawn_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Consent artefact"
        verbose_name_plural = "Consent artefacts"
        ordering = ("-granted_at",)
        indexes = [
            models.Index(fields=["patient_ref"]),
            models.Index(fields=["purpose", "withdrawn_at"]),
        ]
        constraints = [
            # An artefact cannot be withdrawn before it was granted.
            models.CheckConstraint(
                condition=models.Q(withdrawn_at__isnull=True)
                | models.Q(withdrawn_at__gte=models.F("granted_at")),
                name="consent_withdrawn_after_granted",
            ),
        ]

    def __str__(self) -> str:
        state = "withdrawn" if self.is_withdrawn else "active"
        return f"{self.get_purpose_display()} ({state}, v{self.notice_version})"

    @property
    def is_withdrawn(self) -> bool:
        return self.withdrawn_at is not None

    @property
    def is_active(self) -> bool:
        return self.withdrawn_at is None

    def withdraw(self) -> None:
        if self.withdrawn_at is None:
            self.withdrawn_at = timezone.now()
