"""Create the role groups and their permissions (AUDIT 4 Phase, RBAC).

    python manage.py seed_roles
    python manage.py seed_roles --prune   # also remove permissions not listed

Idempotent, so it is safe to run on every deploy - which is the point. Roles
defined in a migration drift the moment someone edits a group in the admin;
roles defined here are re-asserted each time and the file is the source of
truth.

## The shape of the grants

The rule behind every line below is AUDIT 8.8's: **do not hand out
``is_superuser``**, and give each role the narrowest set that lets it do its
job.

* **Front desk** creates and updates patients. It cannot see the prediction log
  and cannot see consent artefacts - it does not need either to book someone in.
* **Doctor** reads patients and reads *and reviews* assessments. It cannot
  create or delete patients, because a clinician correcting the register is a
  front-desk task and mixing them blurs the audit trail.
* **Clinic owner** is the front desk plus the doctor plus staff management.
* **Platform ops** is read-only across clinics, plus the compliance queues. It
  can see that a record exists and act on a data-subject request; it cannot
  edit a clinical record.

Nobody gets ``delete_patient``. Deletion runs through retention with its notice
period, or through the reviewed right-to-erasure action - never a delete button
(AUDIT 8.8).
"""

from __future__ import annotations

from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.models import Role

# Role -> the permissions it holds, as "app_label.codename".
ROLE_PERMISSIONS: dict[str, tuple[str, ...]] = {
    Role.FRONT_DESK: (
        "patients.view_patient",
        "patients.add_patient",
        "patients.change_patient",
        # ABHA linkage happens at registration, which is this desk.
        "abdm.view_abhalink",
    ),
    Role.DOCTOR: (
        "patients.view_patient",
        "triage.view_triageassessment",
        # Sign-off. The only clinical write a doctor needs in the admin.
        "triage.change_triageassessment",
        "consent.view_consentartefact",
        # A doctor sees whether a patient has an ABHA; linking one is a front-
        # desk task at registration, which is where M1 puts it.
        "abdm.view_abhalink",
    ),
    Role.OWNER: (
        "patients.view_patient",
        "patients.add_patient",
        "patients.change_patient",
        "triage.view_triageassessment",
        "triage.change_triageassessment",
        "consent.view_consentartefact",
        "accounts.view_clinic",
        "accounts.view_membership",
        "accounts.add_membership",
        "accounts.change_membership",
        "audit.view_accesslogentry",
        "abdm.view_abhalink",
        "abdm.view_facilityregistration",
        "abdm.view_practitionerregistration",
    ),
    Role.PLATFORM_OPS: (
        # Read-only on clinical data, across clinics.
        "patients.view_patient",
        "triage.view_triageassessment",
        "consent.view_consentartefact",
        "accounts.view_clinic",
        "accounts.view_membership",
        "audit.view_accesslogentry",
        # The compliance queues are ops work, so these are writable.
        "compliance.view_retentionnotice",
        "compliance.view_datasubjectrequest",
        "compliance.change_datasubjectrequest",
        "compliance.add_datasubjectrequest",
        "compliance.view_breachincident",
        "compliance.add_breachincident",
        "compliance.change_breachincident",
        # Registry onboarding and gateway health are ops work.
        "abdm.view_abhalink",
        "abdm.view_facilityregistration",
        "abdm.change_facilityregistration",
        "abdm.view_practitionerregistration",
        "abdm.change_practitionerregistration",
        "abdm.view_gatewaycall",
    ),
}


class Command(BaseCommand):
    help = "Create or update the HealthGuard role groups and their permissions."

    def add_arguments(self, parser):
        parser.add_argument(
            "--prune",
            action="store_true",
            help="Remove permissions from a group that are not listed here. "
            "Off by default so a manual grant is not silently revoked.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        prune = options["prune"]
        available = self._permission_index()

        for role, codenames in ROLE_PERMISSIONS.items():
            group, created = Group.objects.get_or_create(
                name=Role.group_name(role)
            )
            wanted, missing = self._resolve(codenames, available)

            group.permissions.add(*wanted)

            removed = 0
            if prune:
                extra = set(group.permissions.all()) - set(wanted)
                group.permissions.remove(*extra)
                removed = len(extra)

            self.stdout.write(
                f"{'Created' if created else 'Updated'} {group.name}: "
                f"{len(wanted)} permission(s)"
                + (f", removed {removed}" if removed else "")
            )
            for codename in missing:
                # A typo here would silently under-grant, so say so loudly
                # rather than leaving a role quietly unable to do its job.
                self.stdout.write(
                    self.style.WARNING(f"  unknown permission {codename!r} - skipped")
                )

        self.stdout.write(self.style.SUCCESS("Roles seeded."))

    @staticmethod
    def _permission_index() -> dict[str, Permission]:
        return {
            f"{p.content_type.app_label}.{p.codename}": p
            for p in Permission.objects.select_related("content_type")
        }

    @staticmethod
    def _resolve(codenames, available) -> tuple[list[Permission], list[str]]:
        found, missing = [], []
        for codename in codenames:
            permission = available.get(codename)
            if permission is None:
                missing.append(codename)
            else:
                found.append(permission)
        return found, missing
