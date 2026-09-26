"""Unfold configuration (AUDIT 8.2, 8.3, 8.4).

Kept in its own module because the UNFOLD dict is large and mostly design
decisions, which do not belong in the middle of database and security
settings. ``base.py`` imports it.

## Colour

The brand green ``#7cc576`` measures **2.08:1 on white and fails WCAG 2.1 AA
for every text size** (8.3). The scale below steps it down for anything
readable; the brand value survives only at weight 400, for decorative fills.

Unfold emits these verbatim as ``--color-primary-<weight>`` CSS variables, so
plain hex is fine - no need for its oklch defaults.

**Red is reserved for clinical danger and nothing else.** Never for delete or
cancel. If red is spent on destructive UI it is worthless when a red-flag
symptom appears, so destructive actions are neutral with a confirmation step.
"""

from django.templatetags.static import static
from django.urls import reverse_lazy

# Brand scale, corrected for contrast. Contrast figures are against white.
PRIMARY = {
    "50": "#f0f9f0",
    "100": "#dcf0db",
    "200": "#bbe2b9",
    "300": "#9bd497",
    "400": "#7cc576",  # brand; 2.08:1 - decorative fills only, never text
    "500": "#4caf50",
    "600": "#16a34a",  # 3.30:1 - borders, icons, large text (>= 24px)
    "700": "#15803d",  # 5.02:1 - passes AA for body text and filled buttons
    "800": "#116632",
    "900": "#0d4f27",
    "950": "#062e16",
}

UNFOLD = {
    "SITE_TITLE": "HealthGuard Operations",
    "SITE_HEADER": "HealthGuard Operations",
    "SITE_SUBHEADER": "Triage intake, consent and clinical content",
    "SITE_URL": "/",
    "SITE_SYMBOL": "health_and_safety",
    "SHOW_HISTORY": True,
    "SHOW_VIEW_ON_SITE": False,
    "SHOW_BACK_BUTTON": True,
    # Dark mode ships with Unfold. Every status colour below was re-checked on
    # a dark surface: amber-600 loses contrast there and steps up to amber-400
    # (8.3).
    "COLORS": {"primary": PRIMARY},
    "DASHBOARD_CALLBACK": "apps.audit.services.dashboard_service.dashboard_callback",
    "LOGIN": {
        "image": lambda request: static("media/Hero-back.jpeg"),
    },
    "SIDEBAR": {
        "show_search": True,
        "show_all_applications": False,
        # Grouped by job to be done, not by Django app. Unfold's SIDEBAR
        # replaces Django's alphabetical app list, which is the single biggest
        # usability win available here (8.4).
        #
        # ENCOUNTERS, ABDM and the model registry are still absent because the
        # models behind them do not exist yet (Phases 5-6). Listing empty groups
        # would make the admin look broken rather than unfinished.
        "navigation": [
            {
                "title": "Clinics",
                "separator": False,
                "items": [
                    {
                        "title": "Clinics",
                        "icon": "local_hospital",
                        "link": reverse_lazy("admin:accounts_clinic_changelist"),
                        "permission": "config.settings.unfold.can_view_clinics",
                    },
                    {
                        "title": "Staff",
                        "icon": "badge",
                        "link": reverse_lazy("admin:accounts_membership_changelist"),
                        "permission": "config.settings.unfold.can_view_staff",
                    },
                ],
            },
            {
                "title": "Patients",
                "separator": False,
                "items": [
                    {
                        "title": "Patients",
                        "icon": "personal_injury",
                        "link": reverse_lazy("admin:patients_patient_changelist"),
                        "permission": "config.settings.unfold.can_view_patients",
                    },
                    {
                        "title": "Triage assessments",
                        "icon": "clinical_notes",
                        "link": reverse_lazy("admin:triage_triageassessment_changelist"),
                        "permission": "config.settings.unfold.can_view_assessments",
                        "badge": "apps.audit.services.dashboard_service.unreviewed_badge",
                    },
                ],
            },
            {
                "title": "ABDM",
                "separator": True,
                "items": [
                    {
                        "title": "ABHA links",
                        "icon": "badge",
                        "link": reverse_lazy("admin:abdm_abhalink_changelist"),
                        "permission": "config.settings.unfold.can_view_abdm",
                    },
                    {
                        "title": "Facility registry",
                        "icon": "domain_verification",
                        "link": reverse_lazy("admin:abdm_facilityregistration_changelist"),
                        "permission": "config.settings.unfold.can_view_abdm_registry",
                    },
                    {
                        "title": "Practitioner registry",
                        "icon": "how_to_reg",
                        "link": reverse_lazy(
                            "admin:abdm_practitionerregistration_changelist"
                        ),
                        "permission": "config.settings.unfold.can_view_abdm_registry",
                    },
                    {
                        "title": "ABDM consents",
                        "icon": "handshake",
                        "link": reverse_lazy("admin:abdm_hieconsentartefact_changelist"),
                        "permission": "config.settings.unfold.can_view_hie_consent",
                    },
                    {
                        "title": "Records shared",
                        "icon": "outbox",
                        "link": reverse_lazy(
                            "admin:abdm_healthinformationtransfer_changelist"
                        ),
                        "permission": "config.settings.unfold.can_view_transfers",
                    },
                    {
                        "title": "Gateway calls",
                        "icon": "swap_horiz",
                        "link": reverse_lazy("admin:abdm_gatewaycall_changelist"),
                        "permission": "config.settings.unfold.can_view_gateway_calls",
                    },
                ],
            },
            {
                "title": "Compliance",
                "separator": True,
                "items": [
                    {
                        "title": "Consent artefacts",
                        "icon": "verified_user",
                        "link": reverse_lazy("admin:consent_consentartefact_changelist"),
                        "permission": "config.settings.unfold.can_view_consent",
                    },
                    {
                        "title": "Data requests",
                        "icon": "contact_support",
                        "link": reverse_lazy(
                            "admin:compliance_datasubjectrequest_changelist"
                        ),
                        "permission": "config.settings.unfold.can_view_dsr",
                        "badge": "apps.audit.services.dashboard_service.open_dsr_badge",
                    },
                    {
                        "title": "Breach incidents",
                        "icon": "gpp_maybe",
                        "link": reverse_lazy(
                            "admin:compliance_breachincident_changelist"
                        ),
                        "permission": "config.settings.unfold.can_view_breaches",
                        "badge": "apps.audit.services.dashboard_service.open_breach_badge",
                    },
                    {
                        "title": "Retention notices",
                        "icon": "auto_delete",
                        "link": reverse_lazy(
                            "admin:compliance_retentionnotice_changelist"
                        ),
                        "permission": "config.settings.unfold.can_view_retention",
                    },
                    {
                        "title": "Access log",
                        "icon": "policy",
                        "link": reverse_lazy("admin:audit_accesslogentry_changelist"),
                        "permission": "config.settings.unfold.can_view_audit",
                    },
                ],
            },
            {
                "title": "System",
                "separator": True,
                "items": [
                    {
                        "title": "Users",
                        "icon": "person",
                        "link": reverse_lazy("admin:auth_user_changelist"),
                        "permission": "config.settings.unfold.is_superuser",
                    },
                    {
                        "title": "Groups",
                        "icon": "group",
                        "link": reverse_lazy("admin:auth_group_changelist"),
                        "permission": "config.settings.unfold.is_superuser",
                    },
                ],
            },
        ],
    },
}


# --- Sidebar permission callbacks ------------------------------------------
# Unfold imports these by dotted path. They mirror Django's model permissions
# so a link is not shown to someone who would only get a 403 from it.


def _can(request, perm: str) -> bool:
    return request.user.is_active and request.user.has_perm(perm)


def can_view_patients(request) -> bool:
    return _can(request, "patients.view_patient")


def can_view_assessments(request) -> bool:
    return _can(request, "triage.view_triageassessment")


def can_view_consent(request) -> bool:
    return _can(request, "consent.view_consentartefact")


def can_view_audit(request) -> bool:
    return _can(request, "audit.view_accesslogentry")


def can_view_abdm(request) -> bool:
    return _can(request, "abdm.view_abhalink")


def can_view_abdm_registry(request) -> bool:
    return _can(request, "abdm.view_facilityregistration")


def can_view_gateway_calls(request) -> bool:
    return _can(request, "abdm.view_gatewaycall")


def can_view_hie_consent(request) -> bool:
    return _can(request, "abdm.view_hieconsentartefact")


def can_view_transfers(request) -> bool:
    return _can(request, "abdm.view_healthinformationtransfer")


def can_view_clinics(request) -> bool:
    return _can(request, "accounts.view_clinic")


def can_view_staff(request) -> bool:
    return _can(request, "accounts.view_membership")


def can_view_dsr(request) -> bool:
    return _can(request, "compliance.view_datasubjectrequest")


def can_view_breaches(request) -> bool:
    return _can(request, "compliance.view_breachincident")


def can_view_retention(request) -> bool:
    return _can(request, "compliance.view_retentionnotice")


def is_superuser(request) -> bool:
    return request.user.is_active and request.user.is_superuser
