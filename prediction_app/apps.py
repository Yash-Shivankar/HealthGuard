"""App configuration for the triage surface."""

import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class PredictionAppConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "prediction_app"
    verbose_name = "Symptom triage"

    def ready(self) -> None:
        """Warm the content caches once, at startup, not per request.

        A failure here is logged rather than raised: management commands such
        as `migrate` and `collectstatic` must still run on a machine where the
        model artefact has not been built yet.  The first request will surface
        the real error through the normal error path.
        """
        from prediction_app.services import content

        try:
            content.warm_cache()
        except Exception:  # noqa: BLE001 - startup must not hard-fail here
            logger.exception("Could not warm clinical content caches at startup")
