"""The operations dashboard (AUDIT 8.5).

The audit's point about this screen is worth restating, because it is the
reason to build it rather than accept Unfold's default "recent actions" list:

    Read that list against 4.1 and note what it is: the DPDP obligations
    rendered as a screen you actually look at. A compliance obligation nobody
    can see is a compliance obligation nobody meets.

Three constraints, all honoured here:

**Service-backed.** No ORM in a view or a template. This service composes
aggregate methods from each app's own repository - it does not query itself.
The first version of this file did query directly and the Phase 2 architecture
test caught it, which is what that test is for.

**Cached.** "The dashboard must not run twelve unindexed aggregate queries on
every page load." One short-TTL cache entry covers the whole summary, and every
underlying query is covered by an index declared on the model.

**Honest about what is missing.** Tiles the audit lists that depend on models
from Phases 4-6 are named in ``PENDING_TILES`` rather than faked. A tile
showing a hardcoded zero is worse than no tile: it reads as "nothing to worry
about" when it means "not measured".
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone

from apps.abdm.repositories.abdm_repository import (
    AbhaRepository,
    GatewayCallRepository,
    HieConsentRepository,
    TransferRepository,
)
from apps.accounts.models import Role
from apps.accounts.repositories.membership_repository import ClinicRepository
from apps.compliance.repositories.compliance_repository import (
    BreachRepository,
    DSRRepository,
    RetentionRepository,
)
from apps.consent.repositories.consent_repository import ConsentRepository
from apps.patients.repositories.patient_repository import PatientRepository
from apps.triage.models import UrgencyLevel
from apps.triage.repositories.assessment_repository import AssessmentRepository

logger = logging.getLogger(__name__)

CACHE_KEY = "admin_dashboard_v1"
CACHE_TTL_SECONDS = 60

# Anything unreviewed beyond this is the number that matters (AUDIT 8.5).
REVIEW_SLA_HOURS = 24

# DPDP: erase after three years without interaction, with notice beforehand.
RETENTION_YEARS = 3
RETENTION_WARNING_DAYS = 30

#: Every tile the audit lists is now measured. What remains unverified is not
#: the measurement but the integration underneath it: the ABDM wire format has
#: never round-tripped against a live gateway (AUDIT 11.3), so these numbers
#: are real counts of what this system did, not proof that ABDM received it.
PENDING_TILES = (
    "Nothing. Every tile AUDIT 8.5 asks for is measured - but the ABDM figures "
    "count what we sent, and the gateway wire format is still unverified "
    "against a live sandbox.",
)


class DashboardService:
    """Aggregates for the admin index. Read-only, cached, index-covered."""

    def __init__(
        self,
        patients: PatientRepository | None = None,
        consent: ConsentRepository | None = None,
        assessments: AssessmentRepository | None = None,
        clinics: ClinicRepository | None = None,
        retention: RetentionRepository | None = None,
        dsr: DSRRepository | None = None,
        breaches: BreachRepository | None = None,
        abha: AbhaRepository | None = None,
        gateway: GatewayCallRepository | None = None,
        hie_consent: HieConsentRepository | None = None,
        transfers: TransferRepository | None = None,
        intake_drafts=None,
        notes=None,
    ):
        self.patients = patients or PatientRepository()
        self.consent = consent or ConsentRepository()
        self.assessments = assessments or AssessmentRepository()
        self.clinics = clinics or ClinicRepository()
        self.retention = retention or RetentionRepository()
        self.dsr = dsr or DSRRepository()
        self.breaches = breaches or BreachRepository()
        self.abha = abha or AbhaRepository()
        self.gateway = gateway or GatewayCallRepository()
        self.hie_consent = hie_consent or HieConsentRepository()
        self.transfers = transfers or TransferRepository()

        # Imported here rather than at module scope: apps.ai is the newest app
        # and the dashboard is imported during admin autodiscovery, so a
        # top-level import puts the two in a load-order race for no benefit.
        from apps.ai.repositories.ai_repository import (
            ConsultationNoteRepository,
            IntakeDraftRepository,
        )

        self.intake_drafts = intake_drafts or IntakeDraftRepository()
        self.notes = notes or ConsultationNoteRepository()

    def summary(self, *, use_cache: bool = True) -> dict:
        if use_cache:
            cached = cache.get(CACHE_KEY)
            if cached is not None:
                return cached

        data = {
            "operations": self.operations(),
            "clinical_safety": self.clinical_safety(),
            "compliance": self.compliance(),
            "abdm": self.abdm(),
            "ai": self.ai(),
            "charts": {
                "assessments_per_day": self.assessments_per_day(),
                "urgency_mix": self.urgency_mix(),
                "top_conditions": self.top_conditions(),
            },
            "pending_tiles": PENDING_TILES,
            "generated_at": timezone.now(),
        }

        if use_cache:
            cache.set(CACHE_KEY, data, CACHE_TTL_SECONDS)
        return data

    # --- Row 1: operations -------------------------------------------------
    def operations(self) -> dict:
        since = timezone.now() - timedelta(days=1)
        total = self.assessments.count()
        return {
            # The DHIS incentive is paid on record volume, so clinic and
            # clinician counts are the numbers that scale revenue (AUDIT 8.5).
            "clinics": self.clinics.count_active(),
            "doctors": self.clinics.count_active_staff_by_role(Role.DOCTOR),
            "patients": self.patients.count(),
            "assessments_today": self.assessments.count_since(since),
            "assessments_total": total,
            "anonymous_share": _percentage(self.assessments.count_anonymous(), total),
        }

    # --- Row 2: clinical safety --------------------------------------------
    def clinical_safety(self) -> dict:
        now = timezone.now()
        week_ago = now - timedelta(days=7)
        sla_cutoff = now - timedelta(hours=REVIEW_SLA_HOURS)
        recent_total = self.assessments.count_since(week_ago)

        return {
            "awaiting_review": self.assessments.count_unreviewed(),
            "overdue_review": self.assessments.count_unreviewed_older_than(sla_cutoff),
            "review_sla_hours": REVIEW_SLA_HOURS,
            "urgent_this_week": self.assessments.count_since_by_urgency(
                week_ago, [UrgencyLevel.URGENT]
            ),
            "abstained_this_week": self.assessments.count_abstained_since(week_ago),
            # Is the model over-triaging? The audit says over-triage is the
            # correct safety posture, so this is a number to watch rather than
            # one to minimise.
            "escalation_rate": _percentage(
                self.assessments.count_since_by_urgency(
                    week_ago, [UrgencyLevel.URGENT, UrgencyLevel.CONSULT]
                ),
                recent_total,
            ),
        }

    # --- Row 3: compliance, all with a countdown ---------------------------
    def compliance(self) -> dict:
        now = timezone.now()
        retention_cutoff = now - timedelta(days=365 * RETENTION_YEARS)
        warning_cutoff = retention_cutoff + timedelta(days=RETENTION_WARNING_DAYS)
        states = self.consent.state_counts()

        breach = self.breaches.most_urgent_open()

        return {
            "consent_active": states["active"],
            "consent_withdrawn": states["withdrawn"],
            "approaching_retention": self.patients.count_created_before(warning_cutoff),
            "past_retention": self.patients.count_created_before(retention_cutoff),
            "retention_years": RETENTION_YEARS,
            # Notices already issued and waiting out their 48 hours.
            "retention_notices_open": self.retention.count_open(),
            # 90-day grievance and rights SLA.
            "dsr_open": self.dsr.count_open(),
            "dsr_overdue": self.dsr.count_overdue(),
            # The 72-hour breach clock. None when nothing is open, which is the
            # state you want it in - the tile should be absent, not zero.
            "breach_open": self.breaches.count_open(),
            "breach_hours_remaining": breach.hours_remaining if breach else None,
            "breach_overdue": bool(breach and breach.is_overdue),
        }

    # --- ABDM (AUDIT 4.2 M1) -----------------------------------------------
    def abdm(self) -> dict:
        """ABHA linkage and gateway health.

        The gateway failure count is the number that matters day to day: a
        failing integration is invisible from the clinical screens, and the
        first sign is usually a patient being told their ABHA "did not work".
        """
        now = timezone.now()
        day_ago = now - timedelta(days=1)
        month_ago = now - timedelta(days=30)

        return {
            "abha_verified": self.abha.count_verified(),
            "abha_pending": self.abha.count_pending(),
            "gateway_calls_today": self.gateway.count_since(day_ago),
            "gateway_failures_today": self.gateway.count_failed_since(day_ago),
            # The DHIS incentive is paid on record volume, so this tile is
            # money (AUDIT 8.5). It counts what *we sent* - the gateway wire
            # format is unverified, so it is not proof of receipt.
            "records_shared_month": self.transfers.count_sent_since(month_ago),
            "transfers_failed_month": self.transfers.count_failed_since(month_ago),
            "hie_consents_usable": self.hie_consent.count_usable(),
        }

    def ai(self) -> dict:
        """AI drafts waiting for a clinician, and where data went.

        ``external_calls`` is the number a DPDP audit asks for, and it should read
        zero until someone deliberately configures a vendor - which is exactly
        what makes it worth a tile rather than a log search.

        ``languages`` is the one fairness-adjacent figure this project can
        honestly produce. It says which languages patients are using. It does
        **not** say whether they are served equally well: AUDIT 6 requires
        fairness measured across age, sex and language, and the current dataset
        cannot support that. Recording the label is what makes it possible later.
        """
        from apps.ai.repositories.ai_repository import AiCallRepository

        return {
            "intake_drafts_pending": self.intake_drafts.count_pending(),
            "notes_pending": self.notes.count_pending(),
            "external_calls": AiCallRepository.count_external(),
            "languages": self.intake_drafts.language_breakdown(),
        }

    # --- Charts ------------------------------------------------------------
    def assessments_per_day(self, days: int = 30) -> list[dict]:
        since = timezone.now() - timedelta(days=days)
        rows = self.assessments.daily_counts_since(since)
        return [{"day": str(r["day"]), "count": r["count"]} for r in rows]

    def urgency_mix(self) -> list[dict]:
        labels = dict(UrgencyLevel.choices)
        return [
            {
                "level": r["urgency_level"],
                "label": labels.get(r["urgency_level"], r["urgency_level"]),
                "count": r["count"],
            }
            for r in self.assessments.counts_by_urgency()
        ]

    def top_conditions(self, limit: int = 10) -> list[dict]:
        """Most frequently suggested conditions.

        Read from the stored differential rather than a denormalised column,
        because the differential is the immutable snapshot of what was actually
        returned to someone.
        """
        counts: dict[str, int] = {}
        for differential in self.assessments.named_differentials():
            if differential:
                disease = differential[0].get("disease")
                if disease:
                    counts[disease] = counts.get(disease, 0) + 1

        ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:limit]
        return [{"disease": d, "count": c} for d, c in ranked]


def _percentage(part: int, whole: int) -> float:
    return round((part / whole) * 100, 1) if whole else 0.0


# --- Unfold hooks -----------------------------------------------------------


def dashboard_callback(request, context: dict) -> dict:
    """Injected into the admin index by UNFOLD["DASHBOARD_CALLBACK"]."""
    try:
        context["dashboard"] = DashboardService().summary()
    except Exception:  # noqa: BLE001
        # A broken tile must not take down the whole admin: staff still need to
        # reach the records.
        logger.exception("Dashboard aggregation failed")
        context["dashboard"] = None
    return context


def unreviewed_badge(request) -> int | None:
    """Sidebar badge: AI drafts awaiting clinician review.

    Returns None rather than 0 so the badge disappears when nothing is waiting,
    instead of showing a zero that reads like a broken counter.
    """
    if not request.user.has_perm("triage.view_triageassessment"):
        return None
    return AssessmentRepository().count_unreviewed() or None


def ai_drafts_badge(request) -> int | None:
    """Intake drafts waiting for a clinician.

    Scoped to the caller's clinic where they have one, like the other badges: a
    count that includes another clinic's queue is not actionable.
    """
    from apps.ai.repositories.ai_repository import IntakeDraftRepository

    clinic_id = getattr(request.user, "clinic_id", None)
    count = IntakeDraftRepository.count_pending(clinic_id)
    return count or None


def open_dsr_badge(request) -> int | None:
    """Sidebar badge: open data-subject requests on the 90-day clock."""
    if not request.user.has_perm("compliance.view_datasubjectrequest"):
        return None
    return DSRRepository().count_open() or None


def open_breach_badge(request) -> int | None:
    """Sidebar badge: unreported breaches on the 72-hour clock.

    None when nothing is open, which is the state you want - an absent badge
    rather than a zero that reads like a broken counter.
    """
    if not request.user.has_perm("compliance.view_breachincident"):
        return None
    return BreachRepository().count_open() or None
