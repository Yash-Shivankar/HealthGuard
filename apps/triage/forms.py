"""Server-side validation for the HTML symptom form.

Mirrors ``TriageRequestSerializer``: both delegate the symptom vocabulary to
the model artefact, so the two surfaces cannot accept different things.
"""

from __future__ import annotations

from django import forms

from apps.triage.serializers.triage_serializer import MAX_DAYS, MAX_SYMPTOMS
from apps.triage.services import predictor
from apps.triage.services.assessment_service import humanise


class SymptomCheckForm(forms.Form):
    symptoms = forms.MultipleChoiceField(
        choices=(),
        label="Select your symptoms",
        error_messages={
            "required": "Select at least one symptom so we have something to check."
        },
    )
    days = forms.IntegerField(
        min_value=1,
        max_value=MAX_DAYS,
        label="How many days have you had these symptoms?",
        error_messages={
            "required": "Tell us how many days you have had these symptoms.",
            "invalid": "Enter the number of days as a whole number.",
        },
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Built from the artefact's feature names, so the form can never offer
        # a symptom the model does not know - which is what turned a crafted
        # POST into a KeyError and a 500 (AUDIT 2.7).
        self.fields["symptoms"].choices = [
            (name, humanise(name)) for name in predictor.available_symptoms()
        ]

    @staticmethod
    def humanise(symptom: str) -> str:
        return humanise(symptom)

    def clean_symptoms(self) -> list[str]:
        symptoms = list(dict.fromkeys(self.cleaned_data["symptoms"]))
        if len(symptoms) > MAX_SYMPTOMS:
            raise forms.ValidationError(
                f"Please select no more than {MAX_SYMPTOMS} symptoms. "
                "Choose the ones troubling you most."
            )
        return symptoms
