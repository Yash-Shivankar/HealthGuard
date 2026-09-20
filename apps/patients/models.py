"""The patient master record.

Split out of the old ``prediction_app.UserInfo``, which carried both the
person's details and their consent state on one row.  Consent is now its own
app (``apps.consent``), because the DPDP Rules 2025 want a consent *artefact*
per person per purpose, versioned and independently withdrawable - which a
handful of boolean columns on the patient row cannot express (AUDIT 4.1).

None of these fields are model inputs.  The triage model uses symptoms only;
these are collected so a clinician reviewing an intake has context.
"""

from __future__ import annotations

from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models

from utils.validators.phone import mask_mobile

mobile_validator = RegexValidator(
    regex=r"^[6-9]\d{9}$",
    message="Enter a valid 10-digit Indian mobile number.",
)


class Patient(models.Model):
    """A person who has used, or is about to use, the symptom checker."""

    name = models.CharField(max_length=100)
    contact = models.CharField(
        max_length=10,
        validators=[mobile_validator],
        help_text="Canonical 10-digit mobile number.",
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

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Patient"
        verbose_name_plural = "Patients"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["-created_at"]),
            # Lookup by number is how support finds a record; without this it
            # is a table scan once the table is real.
            models.Index(fields=["contact"]),
        ]

    def __str__(self) -> str:
        # Never the full number: __str__ lands in admin listings, log lines and
        # error reports (AUDIT 8.8).
        return f"{self.name} ({self.masked_contact})"

    @property
    def masked_contact(self) -> str:
        return mask_mobile(self.contact)

    @property
    def bmi(self) -> float | None:
        """Body mass index, or None when the inputs cannot produce one.

        Computed rather than stored so it cannot drift out of step with the
        height and weight it derives from.
        """
        if not self.height_cm or not self.weight_kg:
            return None
        metres = self.height_cm / 100
        if metres <= 0:
            return None
        return round(self.weight_kg / (metres * metres), 1)
