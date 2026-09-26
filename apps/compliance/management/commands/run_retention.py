"""Retention pass (AUDIT 4.1: "enforced in code, not in a doc").

    python manage.py run_retention --notices        # pass 1: open notices
    python manage.py run_retention --erase          # pass 2: erase what is due
    python manage.py run_retention --notices --erase --dry-run

Two passes, deliberately separate: the second only touches records whose
48-hour notice period has elapsed. Running both in one invocation is still
safe, because a notice issued in this run is not yet due.

**Scheduling.** These are management commands rather than Celery tasks. There
is no broker in this project yet, and adding one to run two daily jobs would be
infrastructure to maintain for no benefit. Run them from cron, systemd timers or
Windows Task Scheduler:

    0 2 * * *  manage.py run_retention --notices
    0 3 * * *  manage.py run_retention --erase

If Celery arrives for something that genuinely needs it (ABDM callbacks, voice
transcription), move these to Celery Beat then.
"""

from django.core.management.base import BaseCommand, CommandError

from apps.compliance.services.retention_service import RetentionService


class Command(BaseCommand):
    help = "Issue retention notices and erase records whose notice has elapsed."

    def add_arguments(self, parser):
        parser.add_argument(
            "--notices", action="store_true", help="Pass 1: open retention notices."
        )
        parser.add_argument(
            "--erase", action="store_true", help="Pass 2: erase records that are due."
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would happen without writing anything.",
        )

    def handle(self, *args, **options):
        if not (options["notices"] or options["erase"]):
            # Refusing beats guessing: defaulting to --erase on a bare
            # invocation would make an accidental run destructive.
            raise CommandError(
                "Choose at least one pass: --notices, --erase, or both."
            )

        service = RetentionService()
        dry_run = options["dry_run"]

        if options["notices"]:
            result = service.issue_notices(dry_run=dry_run)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Notices: issued {result['issued']}, "
                    f"already under notice {result['already_under_notice']}"
                    f"{' (dry run)' if dry_run else ''}"
                )
            )

        if options["erase"]:
            result = service.run_erasures(dry_run=dry_run)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Erasures: erased {result['erased']}, "
                    f"cancelled {result['cancelled']} (person returned), "
                    f"already absent {result['already_absent']}"
                    f"{' (dry run)' if dry_run else ''}"
                )
            )
