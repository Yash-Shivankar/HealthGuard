"""Bounded contexts.

One app per context, each layered the same way (AUDIT 7.1):

    routes/         URL -> controller
    controllers/    HTTP only: parse, authorise, respond
    services/       business rules; no ORM, no HTTP
    repositories/   the ONLY place ORM queries live
    serializers/    DRF validation in and out
    admin/          one ModelAdmin per file
    models.py       Django-mandated

The rule that keeps it honest: **controllers never touch the ORM, services
never touch HTTP, repositories never contain business logic.**  Violate it once
and you are back to the 193-line views.py the audit found (2.14).
"""
