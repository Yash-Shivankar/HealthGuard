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
        clinic=None,
    ) -> Patient:
        return Patient.objects.create(
            name=name,
            contact=contact,
            age=age,
            height_cm=height_cm,
            weight_kg=weight_kg,
            clinic=clinic,
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

    # --- Aggregates for the operations dashboard (AUDIT 8.5) ---------------

    @staticmethod
    def count() -> int:
        return Patient.objects.count()

    @staticmethod
    def all_with_clinic():
        """Base queryset for the clinic-facing list and detail endpoints.

        select_related because both render the clinic, and the default N+1 on a
        list page is the same problem AUDIT 8.6 names for the admin.
        """
        return Patient.objects.select_related("clinic").all()

    @staticmethod
    def created_before(cutoff):
        """Records past a retention horizon, oldest first.

        Returns rows rather than a count because the retention pass has to act
        on each one. select_related on clinic because the notice records it.
        """
        return (
            Patient.objects.filter(created_at__lt=cutoff)
            .select_related("clinic")
            .order_by("created_at")
        )

    @staticmethod
    def count_created_before(cutoff) -> int:
        """Patients whose records are approaching or past the retention limit.

        Covered by the ``-created_at`` index, so this stays cheap as the table
        grows. The DPDP Rules require notice *before* erasure, so these have to
        be visible ahead of the deadline, not after it.
        """
        return Patient.objects.filter(created_at__lt=cutoff).count()
