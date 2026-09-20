"""Copy rows from the pre-Phase-2 ``prediction_app_userinfo`` table.

Phase 2 split that single table into ``patients.Patient`` plus
``consent.ConsentArtefact``.  The legacy table is not part of the migration
graph any more - the app that owned it is gone - so this is an explicit,
idempotent command rather than a data migration, which keeps the graph clean
and makes the copy re-runnable and reviewable.

    python manage.py import_legacy_userinfo --dry-run
    python manage.py import_legacy_userinfo

Does nothing at all when the legacy table is absent, which is the normal case
on a fresh database.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import connection, transaction

from apps.consent.models import CURRENT_NOTICE_VERSION, ConsentArtefact, ConsentPurpose
from apps.patients.models import Patient

LEGACY_TABLE = "prediction_app_userinfo"


class Command(BaseCommand):
    help = "Copy pre-Phase-2 UserInfo rows into Patient + ConsentArtefact."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be copied without writing anything.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        if LEGACY_TABLE not in connection.introspection.table_names():
            self.stdout.write(
                self.style.SUCCESS(f"No {LEGACY_TABLE} table; nothing to import.")
            )
            return

        with connection.cursor() as cursor:
            cursor.execute(
                f"""SELECT id, name, contact, age, height_cm, weight_kg,
                           consent_given, consent_notice_version,
                           consent_at, consent_withdrawn_at
                    FROM {LEGACY_TABLE} ORDER BY id"""
            )
            rows = cursor.fetchall()

        if not rows:
            self.stdout.write(self.style.SUCCESS(f"{LEGACY_TABLE} is empty."))
            return

        imported = skipped = 0
        with transaction.atomic():
            for row in rows:
                (
                    _legacy_id,
                    name,
                    contact,
                    age,
                    height_cm,
                    weight_kg,
                    consent_given,
                    notice_version,
                    consent_at,
                    withdrawn_at,
                ) = row

                # Idempotent: re-running must not duplicate people.
                if Patient.objects.filter(name=name, contact=contact).exists():
                    skipped += 1
                    continue

                if dry_run:
                    imported += 1
                    continue

                patient = Patient.objects.create(
                    name=name,
                    contact=contact,
                    age=age,
                    height_cm=height_cm,
                    weight_kg=weight_kg,
                )
                # Only create an artefact where consent was actually recorded.
                # Manufacturing one for a row that never had it would be
                # inventing evidence.
                if consent_given:
                    artefact = ConsentArtefact(
                        patient=patient,
                        patient_ref=patient.pk,
                        purpose=ConsentPurpose.SYMPTOM_CHECK,
                        notice_version=notice_version or CURRENT_NOTICE_VERSION,
                        withdrawn_at=withdrawn_at,
                    )
                    if consent_at:
                        artefact.granted_at = consent_at
                    artefact.save()
                imported += 1

            if dry_run:
                transaction.set_rollback(True)

        verb = "would import" if dry_run else "imported"
        self.stdout.write(
            self.style.SUCCESS(
                f"{verb} {imported} row(s); skipped {skipped} already present."
            )
        )
