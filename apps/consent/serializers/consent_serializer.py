"""DRF representation of a consent artefact."""

from rest_framework import serializers

from apps.consent.models import ConsentArtefact


class ConsentArtefactSerializer(serializers.ModelSerializer):
    purpose_display = serializers.CharField(source="get_purpose_display", read_only=True)
    is_active = serializers.BooleanField(read_only=True)

    class Meta:
        model = ConsentArtefact
        fields = (
            "id",
            "purpose",
            "purpose_display",
            "notice_version",
            "granted_at",
            "withdrawn_at",
            "is_active",
        )
        read_only_fields = fields
