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


# ---------------------------------------------------------------------------
# M2: HIE-CM consent and health information transfer
# ---------------------------------------------------------------------------
#
# **HIE-CM consent is not DPDP consent, and the two must not be conflated.**
#
# ``apps.consent.ConsentArtefact`` is our lawful basis under the DPDP Act to
# process someone's data at all. It is between the patient and us.
#
# ``HieConsentArtefact`` below is a different object entirely: it is the
# patient's instruction, issued through the ABDM Consent Manager, authorising a
# named Health Information User to fetch specified categories of their records
# from a named Health Information Provider, for a specified period. It is
# between the patient and the network, and we merely hold and honour it.
#
# Holding DPDP consent does not permit an ABDM disclosure, and an ABDM consent
# artefact is not a lawful basis for anything else we do. Treating either as
# the other would be a disclosure without a basis - which is precisely the kind
# of error the audit's 4.1 table exists to prevent.


class HiType(models.TextChoices):
    """Categories of health information a consent can cover.

    ABDM-SPECIFIC: verify. The set and the exact spellings come from the
    published specification and have not been confirmed against a sandbox.
    """

    DIAGNOSTIC_REPORT = "DiagnosticReport", "Diagnostic report"
    OP_CONSULTATION = "OPConsultation", "Outpatient consultation"
    PRESCRIPTION = "Prescription", "Prescription"
    DISCHARGE_SUMMARY = "DischargeSummary", "Discharge summary"
    IMMUNIZATION_RECORD = "ImmunizationRecord", "Immunisation record"
    HEALTH_DOCUMENT = "HealthDocumentRecord", "Health document"
    WELLNESS_RECORD = "WellnessRecord", "Wellness record"


class ConsentState(models.TextChoices):
    REQUESTED = "requested", "Requested"
    GRANTED = "granted", "Granted"
    DENIED = "denied", "Denied"
    REVOKED = "revoked", "Revoked"
    EXPIRED = "expired", "Expired"


class HieConsentArtefact(models.Model):
    """A patient's ABDM consent for record sharing.

    Scope is explicit and narrow by construction: which categories, which date
    range, and until when. A consent with no expiry is a consent nobody can
    withdraw by doing nothing, so ``expires_at`` is required.
    """

    patient = models.ForeignKey(
        "patients.Patient",
        on_delete=models.CASCADE,
        related_name="hie_consents",
        help_text="Cascades: an erased patient has no consents to honour.",
    )
    clinic = models.ForeignKey(
        "accounts.Clinic",
        on_delete=models.PROTECT,
        related_name="hie_consents",
        help_text="The HIP this consent is addressed to - us.",
    )

    # Issued by the Consent Manager; we do not mint these.
    consent_id = models.CharField(max_length=128, blank=True, db_index=True)
    #: Who is allowed to fetch. Free text because a HIU id is opaque to us.
    requester_id = models.CharField(max_length=128)
    requester_name = models.CharField(max_length=200, blank=True)

    state = models.CharField(
        max_length=16, choices=ConsentState.choices, default=ConsentState.REQUESTED
    )
    #: Which categories. A list rather than a single value: a consent commonly
    #: covers several, and storing them separately would misrepresent it as
    #: several consents.
    hi_types = models.JSONField(default=list)

    #: The clinical date range the consent covers - which records, not when the
    #: consent is valid.
    records_from = models.DateTimeField(null=True, blank=True)
    records_to = models.DateTimeField(null=True, blank=True)

    granted_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(
        help_text="Required. A consent with no expiry cannot lapse on its own."
    )
    revoked_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "ABDM consent artefact (HIE-CM)"
        verbose_name_plural = "ABDM consent artefacts (HIE-CM)"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["clinic", "state"]),
            models.Index(fields=["state", "expires_at"]),
        ]

    def __str__(self) -> str:
        return f"ABDM consent {self.consent_id or self.pk} ({self.get_state_display()})"

    @property
    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at

    @property
    def is_usable(self) -> bool:
        """Granted, not revoked, and not past its expiry.

        Expiry is checked live rather than trusting ``state``: a consent that
        lapsed five minutes ago is still ``granted`` in the database until some
        job notices, and honouring it in the meantime would be a disclosure
        without a basis.
        """
        return (
            self.state == ConsentState.GRANTED
            and self.revoked_at is None
            and not self.is_expired
        )

    def covers(self, hi_type: str) -> bool:
        return hi_type in (self.hi_types or [])

    def covers_date(self, moment) -> bool:
        if self.records_from and moment < self.records_from:
            return False
        if self.records_to and moment > self.records_to:
            return False
        return True


class TransferState(models.TextChoices):
    PENDING = "pending", "Pending"
    SENT = "sent", "Sent"
    FAILED = "failed", "Failed"


class HealthInformationTransfer(models.Model):
    """One push of records to a HIU under a consent.

    The bundle itself is **not stored here.** It is assembled, encrypted and
    sent; what remains is the fact of the transfer, the consent that permitted
    it, and a hash of what was sent. Keeping the bundle would be a second copy
    of clinical data outside the erasure path - the same mistake the access log
    and the gateway call record both avoid.

    The hash is what makes the record useful without the content: it answers
    "was this exact document sent, and under which consent?" without being the
    document.
    """

    consent = models.ForeignKey(
        HieConsentArtefact, on_delete=models.CASCADE, related_name="transfers"
    )
    clinic = models.ForeignKey(
        "accounts.Clinic", on_delete=models.PROTECT, related_name="hi_transfers"
    )

    transaction_id = models.CharField(max_length=128, blank=True)
    state = models.CharField(
        max_length=16, choices=TransferState.choices, default=TransferState.PENDING
    )

    entry_count = models.PositiveIntegerField(default=0)
    #: SHA-256 of the canonical bundle JSON.
    bundle_sha256 = models.CharField(max_length=64, blank=True)

    sent_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Health information transfer"
        verbose_name_plural = "Health information transfers"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["clinic", "-created_at"]),
            models.Index(fields=["state", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"Transfer {self.transaction_id or self.pk} ({self.get_state_display()})"
