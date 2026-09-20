"""Server-side validation for the intake and symptom forms.

There was none before.  ``register.html`` shipped a JavaScript ``validateForm``
which curl ignores entirely, and ``predict`` read ``request.POST`` straight
into ``int()`` and dictionary lookups - three unhandled exceptions on ordinary
input (AUDIT 2.7).  Every field the user can influence is validated here, once,
on the server.
"""

from __future__ import annotations

from django import forms

from prediction_app.models import UserInfo
from prediction_app.services import predictor

MAX_SYMPTOMS = 20
MAX_DAYS = 365


class IntakeForm(forms.ModelForm):
    """Name, contact and body metrics, plus an explicit consent tick."""

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
    # Inheriting the model's max_length=10 rejected "+91 98765 43210" with a
    # length error before clean_contact ever got the chance to normalise it.
    contact = forms.CharField(
        max_length=20,
        label="Mobile number",
        widget=forms.TextInput(
            attrs={"inputmode": "tel", "autocomplete": "tel", "maxlength": 20}
        ),
    )

    class Meta:
        model = UserInfo
        fields = ("name", "contact", "age", "height_cm", "weight_kg")
        labels = {
            "height_cm": "Height (cm)",
            "weight_kg": "Weight (kg)",
        }
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
        # Accept what people actually type - spaces, dashes, +91 - then store
        # the canonical 10 digits.
        raw = self.cleaned_data["contact"]
        digits = "".join(ch for ch in raw if ch.isdigit())
        if digits.startswith("91") and len(digits) == 12:
            digits = digits[2:]
        if len(digits) != 10:
            raise forms.ValidationError("Enter a 10-digit mobile number.")
        return digits

    def save(self, commit: bool = True) -> UserInfo:
        record = super().save(commit=False)
        record.record_consent()
        if commit:
            record.save()
        return record


class SymptomCheckForm(forms.Form):
    """The symptom multi-select and duration.

    ``symptoms`` is built from the model artefact's feature names, so the form
    can never accept a symptom the model does not know - which is what turned a
    crafted POST into a ``KeyError`` and a 500 before.
    """

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
        self.fields["symptoms"].choices = [
            (name, self.humanise(name)) for name in predictor.available_symptoms()
        ]

    @staticmethod
    def humanise(symptom: str) -> str:
        """``foul_smell_of urine`` -> ``Foul smell of urine``.

        The raw feature names carry the source data's stray underscores and
        spaces; they should not be what a patient reads.
        """
        return symptom.replace("_", " ").strip().capitalize()

    def clean_symptoms(self) -> list[str]:
        symptoms = list(dict.fromkeys(self.cleaned_data["symptoms"]))
        if len(symptoms) > MAX_SYMPTOMS:
            raise forms.ValidationError(
                f"Please select no more than {MAX_SYMPTOMS} symptoms. "
                "Choose the ones troubling you most."
            )
        return symptoms
