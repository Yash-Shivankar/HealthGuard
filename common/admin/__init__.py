"""Base admin classes. Every ModelAdmin in this project inherits from one.

See ``common/admin/base.py`` for the four-place scoping discipline (AUDIT 8.7)
and why it exists before there are tenants to scope to.
"""

from common.admin.base import (
    AuditedModelAdmin,
    ReadOnlyModelAdmin,
    ScopedModelAdmin,
    masked_contact,
)

__all__ = [
    "AuditedModelAdmin",
    "ReadOnlyModelAdmin",
    "ScopedModelAdmin",
    "masked_contact",
]
