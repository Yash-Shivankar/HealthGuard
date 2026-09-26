"""Append-only access log.

AUDIT 8.9: Django's ``LogEntry`` records *changes* only.  It does not record
reads - and reading a patient record is exactly the event that matters under
the DPDP Act.  This is the second layer: who looked at what, when, from where.

**Nothing in this table is personal data about the subject.**  It stores the
actor, the object's type and id, and the action.  It deliberately does *not*
store the object's contents, because an audit log that copies the record it is
auditing survives the erasure of that record and quietly defeats it.

Append-only is enforced three ways: no update path in the repository, a
read-only admin, and :meth:`save` refusing to modify an existing row.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models


class AuditAction(models.TextChoices):
    VIEW_LIST = "view_list", "Viewed a list"
    VIEW_OBJECT = "view_object", "Viewed a record"
    UNMASK = "unmask", "Revealed a masked field"
    CREATE = "create", "Created a record"
    UPDATE = "update", "Changed a record"
    DELETE = "delete", "Deleted a record"
    EXPORT = "export", "Exported data"
    CONSENT_WITHDRAWN = "consent_withdrawn", "Consent withdrawn"
    ADMIN_LOGIN = "admin_login", "Signed in to the admin"


class AccessLogEntry(models.Model):
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="access_log_entries",
        help_text="Null when the actor's account is later removed; "
        "actor_label keeps the record readable.",
    )
    # Denormalised so the entry stays meaningful after the account is gone.
    actor_label = models.CharField(max_length=150, blank=True)

    action = models.CharField(max_length=32, choices=AuditAction.choices)

    # Recorded as strings rather than a GenericForeignKey: this table must
    # keep reading correctly after the object it refers to has been erased,
    # and a real FK would either block the erasure or vanish with it.
    object_type = models.CharField(max_length=100, blank=True)
    object_id = models.CharField(max_length=64, blank=True)

    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True)
    request_id = models.CharField(max_length=64, blank=True)

    # Some jurisdictions and some internal policies require a stated reason for
    # accessing a record. Free text, optional today.
    reason = models.CharField(max_length=300, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Access log entry"
        verbose_name_plural = "Access log"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["-created_at"]),
            models.Index(fields=["actor", "-created_at"]),
            models.Index(fields=["object_type", "object_id", "-created_at"]),
            models.Index(fields=["action", "-created_at"]),
        ]

    def __str__(self) -> str:
        target = f"{self.object_type}#{self.object_id}" if self.object_type else "-"
        return f"{self.actor_label or 'system'} {self.action} {target}"

    def save(self, *args, **kwargs):
        """Append-only: an existing entry can never be modified.

        A log that can be edited is not evidence. This is the last line of
        defence behind the read-only admin and the repository having no update
        method - it catches code that reaches past both.
        """
        if self.pk is not None:
            raise ValueError(
                "AccessLogEntry is append-only; an existing entry cannot be modified."
            )
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError(
            "AccessLogEntry is append-only; entries cannot be deleted individually. "
            "Retention trimming, when it exists, must be a reviewed bulk operation."
        )
