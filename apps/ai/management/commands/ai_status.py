"""Report which AI providers this deployment is actually using.

The counterpart to ``abdm_status``, and it exists for the same reason: the
difference between "the AI features are on" and "the AI features are running
against stubs" is invisible from the UI unless something says so out loud.

It also prints the external-processing decision and, when data is not allowed to
leave, *which condition refused*. "AI is off" is a support ticket; "the processor
agreement reference is empty" is a five-minute fix.
"""

from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import DatabaseError

from apps.ai.repositories.ai_repository import AiCallRepository
from apps.ai.services import lexicon, providers


class Command(BaseCommand):
    help = "Show which AI providers are in use and whether data may leave."

    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING("AI Tier 1 status"))

        self._providers()
        self._external_gate()
        self._coverage()
        self._calls()
        self._caveats()

    # --- Sections -----------------------------------------------------------
    def _providers(self) -> None:
        self.stdout.write("")
        self.stdout.write("Providers in use:")

        for label, instance in (
            ("transcription", providers.build_transcriber()),
            ("extraction", providers.build_extractor()),
            ("summarisation", providers.build_summariser()),
        ):
            name = getattr(instance, "name", "unknown")
            simulated = getattr(instance, "simulated", True)
            note = (
                self.style.WARNING("simulated - drafts cannot be confirmed")
                if simulated
                else self.style.SUCCESS("real")
            )
            self.stdout.write(f"  {label:<14} {name:<12} {note}")

    def _external_gate(self) -> None:
        self.stdout.write("")
        self.stdout.write("Third-party processing:")

        # Asked with credentials present so the answer describes the policy
        # gates rather than whichever provider happens to be unconfigured.
        decision = providers.external_processing(has_credentials=True)
        if decision.allowed:
            self.stdout.write(
                self.style.WARNING(
                    "  ALLOWED. Patient data may leave this machine when a "
                    "provider is configured."
                )
            )
            self.stdout.write(
                f"  Processor agreement: {settings.AI_PROCESSOR_AGREEMENT_REF}"
            )
            self.stdout.write(
                "  Per-patient consent under ConsentPurpose.AI_ASSISTANCE is "
                "still checked on every call."
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(f"  Refused: {decision.reason}")
            )
            self.stdout.write("  Nothing leaves this machine.")

    def _coverage(self) -> None:
        stats = lexicon.coverage()
        self.stdout.write("")
        self.stdout.write(
            f"Lexicon {lexicon.VERSION}: {stats['symptoms_covered']} symptoms, "
            f"{stats['phrases']} phrases (en/hi/mr)."
        )

        try:
            from apps.triage.services import predictor

            total = len(predictor.available_symptoms())
        except Exception:  # noqa: BLE001 - a missing artefact is not this command's problem
            return

        covered = stats["symptoms_covered"]
        self.stdout.write(
            f"  Covers {covered} of {total} canonical symptoms "
            f"({covered * 100 // total}%). The rest are findings nobody "
            "volunteers in conversation."
        )

    def _calls(self) -> None:
        self.stdout.write("")
        try:
            external = AiCallRepository.count_external()
        except DatabaseError:
            # A status command that cannot reach the database should still report
            # everything it learned without one. Unmigrated is a normal state to
            # run this in - it is one of the things you would run it to find out.
            self.stdout.write(
                self.style.WARNING(
                    "AI call log unavailable: the database is unreachable or "
                    "apps.ai is not migrated yet."
                )
            )
            return

        if external:
            self.stdout.write(
                self.style.WARNING(f"AI calls that sent data to a third party: {external}")
            )
        else:
            self.stdout.write("AI calls that sent data to a third party: 0")

    def _caveats(self) -> None:
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Before trusting any of this"))
        self.stdout.write(
            "  - No speech vendor is contracted (AUDIT 11.5 unanswered). The\n"
            "    request shapes in apps/ai/services/providers.py are placeholders:\n"
            "    grep -rn 'AI-VENDOR-SPECIFIC: verify'\n"
            "  - The lexicon has not been reviewed by a clinician (AUDIT 11.6):\n"
            "    grep -rn 'CLINICAL-REVIEW-NEEDED'\n"
            "  - Fairness across age, sex and language is not measurable with the\n"
            "    current dataset. Input language is now recorded on every draft,\n"
            "    which is what makes it measurable later - not now."
        )
