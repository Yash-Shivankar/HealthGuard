"""ABDM registration and linkage records (AUDIT 4.2 M1).

M1 is four things: create or verify a 14-digit ABHA at registration, register
the facility in the HFR, register each clinician in the HPR, and support
Scan-&-Share. This module holds the records those produce.

## The ABHA number is the most sensitive identifier in this system

It is a national health identifier. Everything the audit says about the mobile
number in 8.8 applies to it more strongly, so:

* it is **masked everywhere except the detail view of someone entitled to see
  it**, exactly like the contact number;
* it **cascades with the patient**, so a DPDP erasure removes it - an ABHA left
  behind after an erasure is a permanent, nationally-resolvable pointer to the
  person whose data you just promised to delete;
* it never enters the access log, which stores type and id only.

## Linking needs its own consent

Consent to a symptom check is not consent to link a national health identifier
and share records through the ABDM network. The DPDP Rules require notice to be
itemised per purpose, so ``ConsentPurpose.ABDM_LINKAGE`` is separate and the
service refuses to link without it.

## Every outbound call is recorded

``GatewayCall`` exists from M1 rather than M3, even though the audit only names
audit trails under M3. Adding it later would mean a gap in the record for
exactly the period when the integration was least stable, and NHA's production
exit audit asks what was called and when.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone


class RegistrationState(models.TextChoices):
    """Where a registration has got to.

    PENDING is a real state, not a transient one: ABDM flows involve an OTP
    round trip and a gateway that can be slow or down, so a record can sit here
    across requests and needs to be visible while it does.
    """

    UNREGISTERED = "unregistered", "Not registered"
    PENDING = "pending", "Awaiting verification"
    VERIFIED = "verified", "Verified"
    FAILED = "failed", "Failed"


class FacilityRegistration(models.Model):
    """A clinic's Health Facility Registry entry.

    ``Clinic.hfr_facility_id`` stays as the denormalised current value, because
    the clinic record should be readable without joining here. This model owns
    the lifecycle, and the service keeps the two in step - it is the only writer
    of either.
    """

    clinic = models.OneToOneField(
        "accounts.Clinic", on_delete=models.CASCADE, related_name="hfr_registration"
    )
    hfr_id = models.CharField(max_length=64, blank=True)
    state = models.CharField(
        max_length=16, choices=RegistrationState.choices, default=RegistrationState.UNREGISTERED
    )

    requested_at = models.DateTimeField(null=True, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Facility registration (HFR)"
        verbose_name_plural = "Facility registrations (HFR)"

    def __str__(self) -> str:
        return f"{self.clinic.name}: {self.get_state_display()}"

    @property
    def is_verified(self) -> bool:
        return self.state == RegistrationState.VERIFIED


class PractitionerRegistration(models.Model):
    """A clinician's Healthcare Professional Registry entry.

    Scoped to a clinic as well as a user: the same practitioner may work at more
    than one facility, and ABDM treats the HPR id as the person while the
    facility linkage is per-place. That mirrors ``accounts.Membership``, which
    exists for the same reason.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="hpr_registrations"
    )
    clinic = models.ForeignKey(
        "accounts.Clinic", on_delete=models.CASCADE, related_name="hpr_registrations"
    )
    hpr_id = models.CharField(max_length=64, blank=True)
    state = models.CharField(
        max_length=16, choices=RegistrationState.choices, default=RegistrationState.UNREGISTERED
    )

    verified_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Practitioner registration (HPR)"
        verbose_name_plural = "Practitioner registrations (HPR)"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "clinic"], name="one_hpr_registration_per_user_per_clinic"
            )
        ]
        indexes = [models.Index(fields=["clinic", "state"])]

    def __str__(self) -> str:
        return f"{self.user} at {self.clinic.name}: {self.get_state_display()}"

    @property
    def is_verified(self) -> bool:
        return self.state == RegistrationState.VERIFIED


class AbhaLink(models.Model):
    """A patient's ABHA, once they have chosen to link one.

    Nullable nowhere and optional everywhere: a patient without an ABHA is a
    perfectly normal patient, and the triage flow never requires one. Linking is
    something a person opts into, with its own consent.
    """

    patient = models.OneToOneField(
        "patients.Patient",
        on_delete=models.CASCADE,
        related_name="abha_link",
        help_text="Cascades: a DPDP erasure must take the ABHA with it.",
    )
    clinic = models.ForeignKey(
        "accounts.Clinic",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="abha_links",
        help_text="The clinic that performed the linkage.",
    )

    # 14 digits, conventionally displayed in 4-4-4-2 groups.
    abha_number = models.CharField(max_length=17, blank=True)
    # The human-friendly handle, e.g. someone@abdm.
    abha_address = models.CharField(max_length=128, blank=True)

    state = models.CharField(
        max_length=16, choices=RegistrationState.choices, default=RegistrationState.PENDING
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)

    linked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="abha_links_created",
    )
    # The artefact that permitted this. Kept as a reference rather than a
    # boolean so the specific notice version the person agreed to is
    # recoverable (AUDIT 4.1).
    consent_artefact = models.ForeignKey(
        "consent.ConsentArtefact",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="abha_links",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "ABHA link"
        verbose_name_plural = "ABHA links"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["clinic", "state"]),
            models.Index(fields=["abha_number"]),
        ]

    def __str__(self) -> str:
        # Masked: __str__ lands in admin listings, log lines and error reports.
        return f"ABHA {self.masked_number}"

    @property
    def masked_number(self) -> str:
        """All but the last four digits hidden.

        The same rule as the contact number, for a stronger reason: an ABHA
        resolves nationally.
        """
        digits = "".join(ch for ch in self.abha_number if ch.isdigit())
        if not digits:
            return ""
        if len(digits) <= 4:
            return "•" * len(digits)
        return f"{'•' * (len(digits) - 4)} {digits[-4:]}"

    @property
    def formatted_number(self) -> str:
        """14 digits in the conventional 4-4-4-2 grouping."""
        digits = "".join(ch for ch in self.abha_number if ch.isdigit())
        if len(digits) != 14:
            return self.abha_number
        return f"{digits[:2]}-{digits[2:6]}-{digits[6:10]}-{digits[10:]}"

    @property
    def is_verified(self) -> bool:
        return self.state == RegistrationState.VERIFIED

    def mark_verified(self) -> None:
        self.state = RegistrationState.VERIFIED
        self.verified_at = timezone.now()
        self.last_error = ""


class GatewayCall(models.Model):
    """One outbound call to the ABDM gateway.

    Recorded for the same reason the access log exists: when NHA asks what was
    called during the production exit audit, the answer should be a screen
    rather than a grep.

    **No request or response body is stored.** Those carry ABHA numbers, OTPs
    and, later, clinical payloads; keeping them would build a second copy of
    every record outside the erasure path. What is stored is the shape of the
    call - operation, status, timing, correlation id - which is what an audit
    asks for.
    """

    operation = models.CharField(max_length=64)
    clinic = models.ForeignKey(
        "accounts.Clinic",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="gateway_calls",
    )

    # ABDM correlates asynchronous callbacks by request id, so this is the only
    # way to tie a later callback to the call that caused it.
    request_id = models.CharField(max_length=64, blank=True)
    correlation_id = models.CharField(
        max_length=64, blank=True, help_text="Our own request id, from the logging middleware."
    )

    status_code = models.PositiveSmallIntegerField(null=True, blank=True)
    succeeded = models.BooleanField(default=False)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)
    # Message only, never a payload.
    error_message = models.CharField(max_length=300, blank=True)

    #: True when the call went to the stub rather than a real gateway. Without
    #: this a sandbox-era record is indistinguishable from a production one.
    simulated = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Gateway call"
        verbose_name_plural = "Gateway calls"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["-created_at"]),
            models.Index(fields=["operation", "-created_at"]),
            models.Index(fields=["request_id"]),
        ]

    def __str__(self) -> str:
        outcome = "ok" if self.succeeded else "failed"
        return f"{self.operation} ({outcome})"

    def save(self, *args, **kwargs):
        """Append-only, like the access log: a call record is evidence."""
        if self.pk is not None:
            raise ValueError("GatewayCall is append-only and cannot be modified.")
        return super().save(*args, **kwargs)
