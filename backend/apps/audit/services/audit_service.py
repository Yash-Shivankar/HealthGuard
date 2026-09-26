"""Recording access to records.

Every call goes through here so there is one place to audit when someone asks
how the DPDP obligations are met (AUDIT 4.1, 8.9).

A deliberate design decision: **recording must never break the thing it is
recording.**  If the audit write fails, the request that triggered it should
still succeed, with a loud error in the logs - a patient must not be denied
care because a log table was full.  That is a trade-off, and the honest
statement of it is: this log is best-effort for availability and authoritative
for what it does contain.  A regime that needs guaranteed capture needs the
write in the same transaction as the read it records, and needs the read to
fail when the write does.
"""

from __future__ import annotations

import logging

from apps.audit.models import AccessLogEntry, AuditAction
from apps.audit.repositories.access_log_repository import AccessLogRepository
from utils.logging.context import get_request_id

logger = logging.getLogger(__name__)

# Trimmed hard: a user agent is attacker-controlled and the column is indexed
# alongside others. 300 chars is plenty to identify a browser.
MAX_USER_AGENT = 300


class AuditService:
    def __init__(self, repository: AccessLogRepository | None = None):
        self.repository = repository or AccessLogRepository()

    def record(
        self,
        *,
        action: str,
        actor=None,
        obj=None,
        object_type: str = "",
        object_id: str = "",
        request=None,
        reason: str = "",
    ) -> AccessLogEntry | None:
        """Write one entry. Returns None if the write failed."""
        if obj is not None:
            object_type = object_type or obj._meta.label
            object_id = object_id or str(obj.pk)

        fields = {
            "action": action,
            "actor": actor if _is_saved_user(actor) else None,
            "actor_label": _label_for(actor),
            "object_type": object_type,
            "object_id": str(object_id),
            "reason": reason[:300],
            "request_id": get_request_id()[:64],
        }
        fields.update(_request_metadata(request))

        try:
            return self.repository.create(**fields)
        except Exception:  # noqa: BLE001 - see the module docstring
            logger.exception(
                "Failed to write access log entry",
                extra={"audit_action": action, "object_type": object_type},
            )
            return None

    # --- Convenience wrappers, so callers do not pass raw strings around ----

    def record_list_view(self, *, actor, model, request=None):
        return self.record(
            action=AuditAction.VIEW_LIST,
            actor=actor,
            object_type=model._meta.label,
            request=request,
        )

    def record_object_view(self, *, actor, obj, request=None):
        return self.record(
            action=AuditAction.VIEW_OBJECT, actor=actor, obj=obj, request=request
        )

    def record_unmask(self, *, actor, obj, request=None):
        """Revealing a masked field is itself a logged access event (8.8)."""
        return self.record(
            action=AuditAction.UNMASK, actor=actor, obj=obj, request=request
        )

    def record_change(self, *, actor, obj, created: bool, request=None):
        return self.record(
            action=AuditAction.CREATE if created else AuditAction.UPDATE,
            actor=actor,
            obj=obj,
            request=request,
        )

    def record_delete(self, *, actor, obj, request=None):
        return self.record(
            action=AuditAction.DELETE, actor=actor, obj=obj, request=request
        )

    def record_consent_withdrawal(self, *, patient_ref, actor=None, request=None):
        """Recorded by reference, never by content.

        The patient row is about to be erased; writing their details into the
        audit log would preserve exactly what the erasure is meant to remove.
        """
        return self.record(
            action=AuditAction.CONSENT_WITHDRAWN,
            actor=actor,
            object_type="patients.Patient",
            object_id=str(patient_ref),
            request=request,
        )


def _is_saved_user(actor) -> bool:
    return bool(actor and getattr(actor, "pk", None) and actor.is_authenticated)


def _label_for(actor) -> str:
    if not actor or not getattr(actor, "is_authenticated", False):
        return "anonymous"
    return (getattr(actor, "get_username", lambda: "")() or str(actor))[:150]


def _request_metadata(request) -> dict:
    if request is None:
        return {}

    # X-Forwarded-For is only trustworthy behind a proxy that overwrites it.
    # Taking the leftmost value blindly lets a client spoof its own address,
    # so REMOTE_ADDR is preferred and the header is a fallback.
    ip = request.META.get("REMOTE_ADDR") or None
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if not ip and forwarded:
        ip = forwarded.split(",")[0].strip() or None

    return {
        "ip_address": ip,
        "user_agent": request.META.get("HTTP_USER_AGENT", "")[:MAX_USER_AGENT],
    }
