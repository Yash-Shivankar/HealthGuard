"""Server-side validation for the HTML intake form.

Shares its phone handling with the serializer through utils.validators, so the
API and the page apply identical rules.
"""

from __future__ import annotations

from django import forms

from apps.patients.models import Patient
from utils.exceptions.domain import ValidationError as DomainValidationError
from utils.validators.phone import normalise_mobile


class IntakeForm(forms.ModelForm):
    consent = forms.BooleanField(
        required=True,
        label="I have read the notice above and agree to my details being used "
        "for this symptom check.",
        error_messages={
            "required": "We cannot continue without your consent. "
            "Please tick the box to agree."
        },
    )

    # Declared explicitly so the form accepts what people actually type.
    # Inheriting the model's max_length=10 rejects "+91 98765 43210" with a
    # length error before clean_contact can normalise it.
    contact = forms.CharField(
        max_length=20,
        label="Mobile number",
        widget=forms.TextInput(
            attrs={"inputmode": "tel", "autocomplete": "tel", "maxlength": 20}
        ),
    )

    class Meta:
        model = Patient
        fields = ("name", "contact", "age", "height_cm", "weight_kg")
        labels = {"height_cm": "Height (cm)", "weight_kg": "Weight (kg)"}
        widgets = {
            "name": forms.TextInput(attrs={"autocomplete": "name", "maxlength": 100}),
            "age": forms.NumberInput(attrs={"min": 0, "max": 120}),
            "height_cm": forms.NumberInput(attrs={"min": 30, "max": 275, "step": "any"}),
            "weight_kg": forms.NumberInput(attrs={"min": 1, "max": 650, "step": "any"}),
        }

    def clean_name(self) -> str:
        name = self.cleaned_data["name"].strip()
        if not name:
            raise forms.ValidationError("Please enter your name.")
        return name

    def clean_contact(self) -> str:
        try:
            return normalise_mobile(self.cleaned_data["contact"])
        except DomainValidationError as exc:
            raise forms.ValidationError(exc.message) from exc
