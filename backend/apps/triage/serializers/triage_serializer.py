"""DRF contracts for the triage endpoints.

The response shape here is the API's version of the promise the HTML page
makes: ranked possibilities with a match band, an urgency assessment, and an
explicit abstention path - never a single disease presented as fact
(AUDIT 4.3, 6 Tier 0.3).
"""

from __future__ import annotations

from rest_framework import serializers

from apps.triage.services import predictor

MAX_SYMPTOMS = 20
MAX_DAYS = 365


class SymptomSerializer(serializers.Serializer):
    value = serializers.CharField(read_only=True)
    label = serializers.CharField(read_only=True)


class TriageRequestSerializer(serializers.Serializer):
    """Inbound symptom check.

    Every field the old view read straight out of ``request.POST`` and fed to
    ``int()`` or a dict lookup is validated here instead - the three unhandled
    exceptions in AUDIT 2.7.
    """

    symptoms = serializers.ListField(
        child=serializers.CharField(),
        allow_empty=False,
        error_messages={
            "empty": "Select at least one symptom so we have something to check.",
            "required": "Select at least one symptom so we have something to check.",
        },
    )
    days = serializers.IntegerField(
        min_value=1,
        max_value=MAX_DAYS,
        error_messages={
            "required": "Tell us how many days you have had these symptoms.",
            "invalid": "Enter the number of days as a whole number.",
        },
    )

    def validate_symptoms(self, value: list[str]) -> list[str]:
        unique = list(dict.fromkeys(value))

        if len(unique) > MAX_SYMPTOMS:
            raise serializers.ValidationError(
                f"Please select no more than {MAX_SYMPTOMS} symptoms."
            )

        known = set(predictor.available_symptoms())
        unknown = [s for s in unique if s not in known]
        if unknown:
            # Named rather than silently dropped: a client sending a symptom
            # the model does not know has a bug, and hiding it helps nobody.
            raise serializers.ValidationError(
                f"Unrecognised symptom(s): {', '.join(sorted(unknown))}."
            )
        return unique


class CandidateSerializer(serializers.Serializer):
    disease = serializers.CharField(read_only=True)
    confidence = serializers.FloatField(read_only=True)
    confidence_percent = serializers.IntegerField(read_only=True)
    match_band = serializers.CharField(read_only=True)


class UrgencySerializer(serializers.Serializer):
    level = serializers.CharField(read_only=True)
    label = serializers.CharField(read_only=True)
    advice = serializers.CharField(read_only=True)


class TriageResponseSerializer(serializers.Serializer):
    """Outbound result.

    ``disclaimer`` is part of the contract, not decoration: an API client
    rendering this data has the same obligation not to present it as a
    diagnosis as our own pages do.
    """

    assessment_id = serializers.IntegerField(read_only=True, allow_null=True)
    abstained = serializers.BooleanField(read_only=True)
    candidates = CandidateSerializer(many=True, read_only=True)
    urgency = UrgencySerializer(read_only=True)
    description = serializers.CharField(read_only=True, allow_blank=True)
    precautions = serializers.ListField(child=serializers.CharField(), read_only=True)
    remedies = serializers.ListField(child=serializers.CharField(), read_only=True)
    symptoms = serializers.ListField(child=serializers.CharField(), read_only=True)
    days = serializers.IntegerField(read_only=True)
    review_state = serializers.CharField(read_only=True)
    model_fingerprint = serializers.CharField(read_only=True)
    disclaimer = serializers.CharField(read_only=True)

    @staticmethod
    def from_result(result) -> dict:
        return {
            "assessment_id": result.assessment.pk,
            "abstained": result.abstained,
            "candidates": [] if result.abstained else result.differential.candidates,
            "urgency": result.urgency,
            "description": result.description,
            "precautions": result.precautions,
            "remedies": result.remedies,
            "symptoms": result.assessment.symptoms,
            "days": result.assessment.days,
            "review_state": result.assessment.review_state,
            "model_fingerprint": result.assessment.model_fingerprint,
            "disclaimer": (
                "This is not a diagnosis. These are possible conditions ranked "
                "by how closely the reported symptoms match a known pattern. "
                "It is not a substitute for professional medical advice, and it "
                "does not recommend medicines."
            ),
        }
