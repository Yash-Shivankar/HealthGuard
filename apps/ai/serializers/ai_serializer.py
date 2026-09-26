"""Request and response shapes for the AI endpoints.

Validation lives here so a controller never inspects raw input, and so the
schema drf-spectacular publishes describes what the endpoints actually accept.
"""

from __future__ import annotations

from rest_framework import serializers

from apps.ai.models import ConsultationNote, InputLanguage, IntakeDraft
from apps.ai.services.extraction_service import MAX_TEXT_CHARS
from apps.triage.serializers.triage_serializer import MAX_DAYS, MAX_SYMPTOMS


class DescribeRequestSerializer(serializers.Serializer):
    """A typed description of the problem, in any of the three languages."""

    text = serializers.CharField(
        max_length=MAX_TEXT_CHARS,
        trim_whitespace=True,
        help_text="What the patient says is wrong, in their own words.",
    )
    language = serializers.ChoiceField(
        choices=InputLanguage.choices,
        default=InputLanguage.UNKNOWN,
        help_text="The language the description is written in.",
    )


class VoiceRequestSerializer(serializers.Serializer):
    """An audio recording of the patient describing the problem."""

    audio = serializers.FileField(
        help_text="A short recording. Not stored: it is transcribed and dropped."
    )
    language = serializers.ChoiceField(
        choices=InputLanguage.choices, default=InputLanguage.UNKNOWN
    )


class ProposedSymptomSerializer(serializers.Serializer):
    """One proposed symptom, with the phrase that produced it.

    ``evidence`` is not decoration. A clinician asked to accept a machine's
    reading of a sentence needs to see which words it read, or the review is a
    rubber stamp - and ``confidence`` here is how specific the matched phrase
    was, not a probability of anything.
    """

    symptom = serializers.CharField()
    label = serializers.CharField(required=False)
    evidence = serializers.CharField(allow_blank=True)
    confidence = serializers.FloatField()


class IntakeDraftSerializer(serializers.ModelSerializer):
    proposed_symptoms = serializers.SerializerMethodField()
    language_label = serializers.CharField(source="get_language_display", read_only=True)
    state_label = serializers.CharField(source="get_state_display", read_only=True)
    needs_review = serializers.BooleanField(read_only=True)
    can_be_confirmed = serializers.BooleanField(read_only=True)
    understood_nothing = serializers.BooleanField(read_only=True)
    suggested_days = serializers.SerializerMethodField()

    class Meta:
        model = IntakeDraft
        fields = (
            "id",
            "patient",
            "language",
            "language_label",
            "source_text",
            "from_audio",
            "proposed_symptoms",
            "unmapped_phrases",
            "confirmed_symptoms",
            "suggested_days",
            "state",
            "state_label",
            "needs_review",
            "can_be_confirmed",
            "understood_nothing",
            "simulated",
            "externally_processed",
            "provider",
            "model_version",
            "assessment",
            "created_at",
        )
        read_only_fields = fields

    def get_proposed_symptoms(self, obj) -> list[dict]:
        from apps.triage.services.assessment_service import humanise

        return [
            {
                "symptom": entry.get("symptom", ""),
                "label": humanise(entry.get("symptom", "")),
                "evidence": entry.get("evidence", ""),
                "confidence": entry.get("confidence", 0.0),
            }
            for entry in (obj.proposed_symptoms or [])
        ]

    def get_suggested_days(self, obj) -> int | None:
        """Duration read out of what the patient said, so it is not asked twice."""
        from apps.ai.services import lexicon

        return lexicon.extract_duration(obj.source_text)


class ConfirmRequestSerializer(serializers.Serializer):
    """The clinician's decision.

    ``symptoms`` may add terms the extractor missed and drop ones it invented -
    that correction is the most valuable output of the review step, so the field
    is a full replacement rather than a list of approvals.
    """

    symptoms = serializers.ListField(
        child=serializers.CharField(max_length=100),
        required=False,
        help_text="The symptoms the clinician accepts. Omit to accept the "
        "proposal unchanged.",
    )
    days = serializers.IntegerField(
        required=False, min_value=1, max_value=MAX_DAYS,
        help_text="How many days. Omit to use the duration read from the "
        "description.",
    )

    def validate_symptoms(self, value):
        unique = list(dict.fromkeys(v.strip() for v in value if v.strip()))
        if len(unique) > MAX_SYMPTOMS:
            raise serializers.ValidationError(
                f"Please confirm no more than {MAX_SYMPTOMS} symptoms."
            )
        return unique


class DiscardRequestSerializer(serializers.Serializer):
    reason = serializers.CharField(
        required=False, allow_blank=True, max_length=200,
        help_text="Why it was rejected. Recorded in the audit trail.",
    )


class ConsultationNoteSerializer(serializers.ModelSerializer):
    sections = serializers.SerializerMethodField()
    state_label = serializers.CharField(source="get_state_display", read_only=True)
    needs_review = serializers.BooleanField(read_only=True)
    can_be_confirmed = serializers.BooleanField(read_only=True)

    class Meta:
        model = ConsultationNote
        fields = (
            "id",
            "patient",
            "assessment",
            "sections",
            "clinician_text",
            "state",
            "state_label",
            "needs_review",
            "can_be_confirmed",
            "simulated",
            "externally_processed",
            "provider",
            "model_version",
            "created_at",
        )
        read_only_fields = fields

    def get_sections(self, obj) -> list[dict]:
        """Ordered and labelled, because a note is read top to bottom.

        A dict would leave the order to whatever JSON the client parses into,
        and a consultation note whose sections arrive shuffled is not a note.
        """
        return [{"label": label, "text": text} for label, text in obj.ordered_sections()]


class SignOffRequestSerializer(serializers.Serializer):
    clinician_text = serializers.CharField(
        required=False, allow_blank=True, max_length=5000,
        help_text="The clinician's own additions. Checked for medication "
        "content like the draft itself.",
    )
