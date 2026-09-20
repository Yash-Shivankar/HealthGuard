"""The only place ORM queries against Patient live (AUDIT 7.1).

Services call these; they never build a QuerySet themselves.  The point is not
ceremony - it is that when someone asks "where do we read patient rows?", the
answer is one file, which is also the file you add scoping to when
multi-tenancy arrives in Phase 4.
"""

from __future__ import annotations

from apps.patients.models import Patient


class PatientRepository:
    @staticmethod
    def create(
        *,
        name: str,
        contact: str,
        age: int,
        height_cm: float,
        weight_kg: float,
    ) -> Patient:
        return Patient.objects.create(
            name=name,
            contact=contact,
            age=age,
            height_cm=height_cm,
            weight_kg=weight_kg,
        )

    @staticmethod
    def get_by_id(patient_id: int) -> Patient | None:
        return Patient.objects.filter(pk=patient_id).first()

    @staticmethod
    def find_by_contact(contact: str) -> Patient | None:
        return Patient.objects.filter(contact=contact).order_by("-created_at").first()

    @staticmethod
    def delete(patient: Patient) -> None:
        """Hard delete, used only by the consent-withdrawal path.

        Everywhere else deletion must go through retention with the notice
        period the DPDP Rules require (AUDIT 4.1, 8.8).
        """
        patient.delete()

    @staticmethod
    def exists(patient_id: int) -> bool:
        return Patient.objects.filter(pk=patient_id).exists()
