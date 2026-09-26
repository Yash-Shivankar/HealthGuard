"""The only place ORM queries against the ABDM models live."""

from __future__ import annotations

from django.db.models import QuerySet

from apps.abdm.models import (
    AbhaLink,
    ConsentState,
    FacilityRegistration,
    GatewayCall,
    HealthInformationTransfer,
    HieConsentArtefact,
    PractitionerRegistration,
    RegistrationState,
    TransferState,
)


class AbhaRepository:
    @staticmethod
    def for_patient(patient) -> AbhaLink | None:
        return AbhaLink.objects.filter(patient=patient).select_related("clinic").first()

    @staticmethod
    def create(**fields) -> AbhaLink:
        return AbhaLink.objects.create(**fields)

    @staticmethod
    def save(link: AbhaLink, fields: list[str]) -> None:
        link.save(update_fields=[*fields, "updated_at"])

    @staticmethod
    def find_by_number(abha_number: str) -> AbhaLink | None:
        digits = "".join(ch for ch in abha_number if ch.isdigit())
        return AbhaLink.objects.filter(abha_number=digits).first()

    @staticmethod
    def for_clinic(clinic) -> QuerySet[AbhaLink]:
        return AbhaLink.objects.filter(clinic=clinic).select_related("patient")

    # --- Dashboard aggregates ---------------------------------------------

    @staticmethod
    def count_verified() -> int:
        return AbhaLink.objects.filter(state=RegistrationState.VERIFIED).count()

    @staticmethod
    def count_pending() -> int:
        return AbhaLink.objects.filter(state=RegistrationState.PENDING).count()


class FacilityRepository:
    @staticmethod
    def for_clinic(clinic) -> FacilityRegistration | None:
        return FacilityRegistration.objects.filter(clinic=clinic).first()

    @staticmethod
    def get_or_create(clinic) -> tuple[FacilityRegistration, bool]:
        return FacilityRegistration.objects.get_or_create(clinic=clinic)

    @staticmethod
    def save(registration: FacilityRegistration, fields: list[str]) -> None:
        registration.save(update_fields=[*fields, "updated_at"])

    @staticmethod
    def count_verified() -> int:
        return FacilityRegistration.objects.filter(state=RegistrationState.VERIFIED).count()


class PractitionerRepository:
    @staticmethod
    def for_user_and_clinic(user, clinic) -> PractitionerRegistration | None:
        return PractitionerRegistration.objects.filter(user=user, clinic=clinic).first()

    @staticmethod
    def get_or_create(user, clinic) -> tuple[PractitionerRegistration, bool]:
        return PractitionerRegistration.objects.get_or_create(user=user, clinic=clinic)

    @staticmethod
    def save(registration: PractitionerRegistration, fields: list[str]) -> None:
        registration.save(update_fields=[*fields, "updated_at"])

    @staticmethod
    def count_verified() -> int:
        return PractitionerRegistration.objects.filter(
            state=RegistrationState.VERIFIED
        ).count()


class GatewayCallRepository:
    """Append-only, like the access log - note the absence of an update."""

    @staticmethod
    def create(**fields) -> GatewayCall:
        return GatewayCall.objects.create(**fields)

    @staticmethod
    def recent(limit: int = 100) -> QuerySet[GatewayCall]:
        return GatewayCall.objects.select_related("clinic")[:limit]

    @staticmethod
    def count_since(cutoff) -> int:
        return GatewayCall.objects.filter(created_at__gte=cutoff).count()

    @staticmethod
    def count_failed_since(cutoff) -> int:
        return GatewayCall.objects.filter(created_at__gte=cutoff, succeeded=False).count()


class HieConsentRepository:
    """ABDM consent artefacts. Distinct from apps.consent, which is DPDP."""

    @staticmethod
    def usable_for_patient(patient) -> QuerySet[HieConsentArtefact]:
        """Granted, unrevoked and unexpired.

        Expiry is applied in the query rather than trusted from ``state``: a
        consent that lapsed reads ``granted`` until some job notices it.
        """
        from django.utils import timezone

        return HieConsentArtefact.objects.filter(
            patient=patient,
            state=ConsentState.GRANTED,
            revoked_at__isnull=True,
            expires_at__gt=timezone.now(),
        )

    @staticmethod
    def for_clinic(clinic) -> QuerySet[HieConsentArtefact]:
        return HieConsentArtefact.objects.filter(clinic=clinic).select_related("patient")

    @staticmethod
    def count_usable() -> int:
        from django.utils import timezone

        return HieConsentArtefact.objects.filter(
            state=ConsentState.GRANTED,
            revoked_at__isnull=True,
            expires_at__gt=timezone.now(),
        ).count()


class TransferRepository:
    @staticmethod
    def create(**fields) -> HealthInformationTransfer:
        return HealthInformationTransfer.objects.create(**fields)

    @staticmethod
    def save(transfer: HealthInformationTransfer, fields: list[str]) -> None:
        transfer.save(update_fields=fields)

    @staticmethod
    def for_clinic(clinic) -> QuerySet[HealthInformationTransfer]:
        return HealthInformationTransfer.objects.filter(clinic=clinic)

    @staticmethod
    def count_sent_since(cutoff) -> int:
        """Feeds the "records pushed" dashboard tile the audit asks for - the
        DHIS incentive is paid on record volume (AUDIT 8.5).
        """
        return HealthInformationTransfer.objects.filter(
            state=TransferState.SENT, sent_at__gte=cutoff
        ).count()

    @staticmethod
    def count_failed_since(cutoff) -> int:
        return HealthInformationTransfer.objects.filter(
            state=TransferState.FAILED, created_at__gte=cutoff
        ).count()
