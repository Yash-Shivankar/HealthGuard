"""Clinics, staff and roles - the tenant boundary (AUDIT 7.1, 8.7).

## Why Membership rather than a `clinic` field on the user

AUDIT 8.7's sample code reads ``request.user.clinic_id``, which implies one
clinic per user. That is the wrong shape for this market and for this codebase:

* **A locum doctor works at more than one clinic.** Single-FK models force
  duplicate accounts, and duplicate accounts defeat an audit trail - you can no
  longer say who did something, only which of their accounts did.
* **Platform ops belong to no clinic.** A nullable FK makes "no clinic" and
  "not yet assigned" the same state, and the difference matters because one
  should see everything and the other nothing.
* **Swapping ``AUTH_USER_MODEL`` now is expensive.** Django's own docs warn
  against it once migrations exist, and ``audit``, ``axes`` and ``django_otp``
  already hold foreign keys to ``auth.User``.

So the join is explicit, and ``request.user.clinic_id`` is still what the admin
scoping reads - resolved from the active membership by middleware. The Phase 3
scoping machinery is unchanged.

## Roles

Permissions are Django ``Group`` permissions, seeded by
``manage.py seed_roles``. The role on a Membership says which group a person
holds *at that clinic*; the groups themselves carry the permissions. Keeping
them separate means a role can be re-scoped without touching every membership
row.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.text import slugify


class Role(models.TextChoices):
    """What someone may do at a clinic.

    PLATFORM_OPS is deliberately a role and not a flag. AUDIT 8.8 says do not
    hand out ``is_superuser``, because it bypasses every scoping check - so
    cross-tenant support access needs to be something narrower that can itself
    be audited and withdrawn.
    """

    OWNER = "owner", "Clinic owner"
    DOCTOR = "doctor", "Doctor"
    FRONT_DESK = "front_desk", "Front desk"
    PLATFORM_OPS = "platform_ops", "Platform operations"

    @classmethod
    def group_name(cls, value: str) -> str:
        return f"HealthGuard: {cls(value).label}"


#: Roles that may see across clinics. Everything else is confined to its own.
UNRESTRICTED_ROLES = frozenset({Role.PLATFORM_OPS})


class Clinic(models.Model):
    """The tenant. Every patient record belongs to exactly one."""

    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True, blank=True)

    # ABDM registration identifiers. Held here rather than in apps.abdm so the
    # clinic record is complete before the integration exists (Phases 5-6):
    # HFR is the facility registry, HPR the practitioner registry.
    hfr_facility_id = models.CharField(
        max_length=64, blank=True, help_text="ABDM Health Facility Registry id."
    )

    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=15, blank=True)

    is_active = models.BooleanField(
        default=True,
        help_text="Inactive clinics keep their records but cannot be used. "
        "Deactivating is not deletion - erasure runs through retention.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Clinic"
        verbose_name_plural = "Clinics"
        ordering = ("name",)
        indexes = [models.Index(fields=["is_active", "name"])]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)[:220]
        return super().save(*args, **kwargs)


class Membership(models.Model):
    """A person's role at one clinic.

    Unique on (user, clinic): one role per person per clinic. A doctor who is
    also the owner holds the higher role, not two rows - two rows would make
    "what may this person do here" ambiguous.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships"
    )
    clinic = models.ForeignKey(
        Clinic,
        on_delete=models.CASCADE,
        related_name="memberships",
        null=True,
        blank=True,
        help_text="Null only for platform operations, which is not clinic-scoped.",
    )
    role = models.CharField(max_length=32, choices=Role.choices)

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Membership"
        verbose_name_plural = "Memberships"
        ordering = ("clinic__name", "user__username")
        constraints = [
            models.UniqueConstraint(
                fields=["user", "clinic"], name="one_role_per_user_per_clinic"
            ),
            # Platform ops has no clinic; every other role must have one.
            # Enforced in the database because a clinic-less doctor would be
            # invisible to every scoped query and hard to notice.
            models.CheckConstraint(
                condition=models.Q(role="platform_ops", clinic__isnull=True)
                | (~models.Q(role="platform_ops") & models.Q(clinic__isnull=False)),
                name="clinic_required_unless_platform_ops",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "is_active"]),
            models.Index(fields=["clinic", "role", "is_active"]),
        ]

    def __str__(self) -> str:
        where = self.clinic.name if self.clinic_id else "platform"
        return f"{self.user} - {self.get_role_display()} at {where}"

    @property
    def is_unrestricted(self) -> bool:
        """True when this membership may see across clinics."""
        return self.role in UNRESTRICTED_ROLES

    def revoke(self) -> None:
        """Revoke rather than delete.

        A deleted membership leaves an access-log entry pointing at a role
        nobody can explain. Keeping the row, marked revoked, keeps the trail
        readable.
        """
        self.is_active = False
        self.revoked_at = timezone.now()
        self.save(update_fields=["is_active", "revoked_at"])
