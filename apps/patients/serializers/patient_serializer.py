"""DRF validation in and out for Patient.

Validation rules are shared with the HTML form via utils.validators, so the
two surfaces cannot disagree - which is the failure mode AUDIT 2.7 describes.
"""

from __future__ import annotations

from rest_framework import serializers

from apps.patients.models import Patient
from utils.exceptions.domain import ValidationError as DomainValidationError
from utils.validators.phone import normalise_mobile


class PatientReadSerializer(serializers.ModelSerializer):
    """Outbound representation.

    The contact number is **masked**, never returned in full. An API that
    hands back complete mobile numbers is a bulk-extraction endpoint waiting
    to happen (AUDIT 8.8).
    """

    contact = serializers.CharField(source="masked_contact", read_only=True)
    bmi = serializers.FloatField(read_only=True)

    class Meta:
        model = Patient
        fields = ("id", "name", "contact", "age", "height_cm", "weight_kg", "bmi", "created_at")
        read_only_fields = fields


class PatientIntakeSerializer(serializers.Serializer):
    """Inbound intake payload, including the consent tick."""

    name = serializers.CharField(max_length=100, trim_whitespace=True)
    # Wide enough to accept "+91 98765 43210"; normalised below.
    contact = serializers.CharField(max_length=20)
    age = serializers.IntegerField(min_value=0, max_value=120)
    height_cm = serializers.FloatField(min_value=30, max_value=275)
    weight_kg = serializers.FloatField(min_value=1, max_value=650)
    consent = serializers.BooleanField()

    def validate_name(self, value: str) -> str:
        if not value.strip():
            raise serializers.ValidationError("Please enter your name.")
        return value.strip()

    def validate_contact(self, value: str) -> str:
        try:
            return normalise_mobile(value)
        except DomainValidationError as exc:
            raise serializers.ValidationError(exc.message) from exc

    def validate_consent(self, value: bool) -> bool:
        if not value:
            raise serializers.ValidationError(
                "We cannot continue without your consent."
            )
        return value
