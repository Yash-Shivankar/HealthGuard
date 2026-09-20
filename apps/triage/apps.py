"""App configuration for the triage surface."""

import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class TriageConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.triage"
    label = "triage"
    verbose_name = "Symptom triage"

    def ready(self) -> None:
        """Warm the clinical content caches once, at startup, not per request.

        A failure is logged rather than raised: `migrate` and `collectstatic`
        must still run on a machine where the model artefact has not been built
        yet. The first request surfaces the real error through the normal path.
        """
        from apps.triage.services import content

        try:
            content.warm_cache()
        except Exception:  # noqa: BLE001 - startup must not hard-fail here
            logger.exception("Could not warm clinical content caches at startup")
