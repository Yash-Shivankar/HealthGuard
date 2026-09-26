"""Report ABDM readiness without leaving the terminal.

    python manage.py abdm_status

Exists because the first question anyone asks when a linkage fails is "are we
even pointed at a real gateway?", and the honest answer is not obvious from the
screens - a stub call and a live call both succeed and both look the same in the
UI, which is exactly why every record carries `simulated`.
"""

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.abdm.models import GatewayCall, RegistrationState
from apps.abdm.repositories.abdm_repository import (
    AbhaRepository,
    FacilityRepository,
    PractitionerRepository,
)


class Command(BaseCommand):
    help = "Show ABDM configuration and M1 registration status."

    def handle(self, *args, **options):
        enabled = getattr(settings, "ABDM_ENABLED", False)
        has_credentials = bool(
            getattr(settings, "ABDM_CLIENT_ID", "")
            and getattr(settings, "ABDM_CLIENT_SECRET", "")
        )

        self.stdout.write(self.style.MIGRATE_HEADING("Configuration"))
        self.stdout.write(f"  ABDM_ENABLED   : {enabled}")
        self.stdout.write(f"  Base URL       : {getattr(settings, 'ABDM_BASE_URL', '') or '(unset)'}")
        self.stdout.write(f"  Credentials    : {'configured' if has_credentials else 'MISSING'}")

        if enabled and has_credentials:
            self.stdout.write(self.style.WARNING("  Mode           : LIVE"))
            self.stdout.write(
                self.style.WARNING(
                    "  Check apps/abdm/services/endpoints.py against the current NHA\n"
                    "  documentation before trusting this: those paths and payloads\n"
                    "  have not been verified against a live sandbox."
                )
            )
        else:
            self.stdout.write("  Mode           : SIMULATED (StubTransport)")

        self.stdout.write(self.style.MIGRATE_HEADING("\nM1 registrations"))
        self.stdout.write(f"  Facilities (HFR) verified    : {FacilityRepository.count_verified()}")
        self.stdout.write(f"  Practitioners (HPR) verified : {PractitionerRepository.count_verified()}")
        self.stdout.write(f"  ABHA links verified          : {AbhaRepository.count_verified()}")
        self.stdout.write(f"  ABHA links pending           : {AbhaRepository.count_pending()}")

        total = GatewayCall.objects.count()
        simulated = GatewayCall.objects.filter(simulated=True).count()
        failed = GatewayCall.objects.filter(succeeded=False).count()

        self.stdout.write(self.style.MIGRATE_HEADING("\nGateway calls"))
        self.stdout.write(f"  Total     : {total}")
        self.stdout.write(f"  Simulated : {simulated}")
        self.stdout.write(f"  Failed    : {failed}")

        if simulated and simulated == total and total:
            self.stdout.write(
                self.style.NOTICE(
                    "\n  Every recorded call is simulated. Nothing has reached a real\n"
                    "  gateway, so M1 is unverified end to end (AUDIT 11.3)."
                )
            )
