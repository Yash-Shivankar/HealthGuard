"""Tests for the clinical content tables.

Each test here pins a specific defect the audit found in the data or its
loader, so a future change to the CSVs or the normalisation cannot quietly
reintroduce one.
"""

from __future__ import annotations

import pytest

from prediction_app.services import content, predictor


@pytest.fixture(scope="module")
def diseases() -> list[str]:
    return predictor.artefact().classes


@pytest.fixture(scope="module")
def symptoms() -> list[str]:
    return predictor.artefact().feature_names


class TestSeverity:
    def test_dictionary_is_not_empty(self):
        """The original loader left this completely empty.

        ``Symptom_severity.csv`` opens with a ``symptoms,weight`` header. The
        old code wrapped its whole read loop in ``try/except: pass``, so
        ``int("weight")`` raised on row one and aborted the loop, scoring every
        symptom 0 (AUDIT 2.10, which reported this as 4 missing keys).
        """
        assert len(content.severity_scores()) > 100

    def test_header_row_is_not_loaded_as_data(self):
        assert content._normalise("symptoms") not in content.severity_scores()

    def test_every_model_feature_has_a_severity(self, symptoms):
        """Including the four whose names differ only by stray whitespace.

        ``spotting_ urination``, ``foul_smell_of urine`` and
        ``dischromic _patches`` are spelled without the space in the severity
        file; ``fluid_overload.1`` was a duplicate column, corrected in
        ``ml.dataset``.
        """
        missing = [s for s in symptoms if content._normalise(s) not in content.severity_scores()]
        assert missing == []

    def test_whitespace_mismatched_names_resolve(self):
        for symptom in ("spotting_ urination", "foul_smell_of urine", "dischromic _patches"):
            assert content.severity_of(symptom) > 0

    def test_unknown_symptom_scores_zero_without_raising(self):
        assert content.severity_of("definitely_not_a_symptom") == 0


class TestDescriptions:
    def test_every_disease_has_a_description(self, diseases):
        """Two of the three gaps were trailing-space names, one needed an alias."""
        missing = [d for d in diseases if not content.description_for(d)]
        assert missing == []

    def test_trailing_space_names_resolve(self):
        assert content.description_for("Diabetes ") == content.description_for("Diabetes")


class TestPrecautions:
    def test_every_disease_has_precautions(self, diseases):
        missing = [d for d in diseases if not content.precautions_for(d)]
        assert missing == []

    def test_precautions_are_a_list_of_non_empty_strings(self):
        items = content.precautions_for("Malaria")
        assert isinstance(items, list)
        assert items and all(isinstance(i, str) and i.strip() for i in items)


class TestRemedies:
    def test_remedies_are_always_a_list_never_a_string(self, diseases):
        """The per-letter bullet bug.

        The old code assigned a bare string on a miss and the template iterated
        it, rendering one bullet per character on roughly a third of results
        (AUDIT 2.9). A list of one keeps the template loop correct.
        """
        for disease in diseases:
            result = content.remedies_for(disease)
            assert isinstance(result, list), disease
            assert not isinstance(result, str), disease
            assert result, disease

    def test_missing_remedies_return_the_fallback_as_a_single_item(self):
        result = content.remedies_for("Hyperthyroidism")
        assert result == [content.REMEDY_FALLBACK]

    def test_alias_recovers_names_that_differ_from_the_content_table(self):
        """Misspellings and parentheticals baked into the training labels."""
        for disease in (
            "Osteoarthristis",
            "Peptic ulcer diseae",
            "Paralysis (brain hemorrhage)",
            "Dimorphic hemmorhoids(piles)",
            "(vertigo) Paroymsal  Positional Vertigo",
        ):
            assert content.remedies_for(disease) != [content.REMEDY_FALLBACK], disease

    def test_non_breaking_space_and_case_differences_resolve(self):
        """The remedies file spells Hepatitis entries with U+00A0 and GERD as Gerd."""
        for disease in ("Hepatitis B", "hepatitis A", "GERD", "Alcoholic hepatitis"):
            assert content.remedies_for(disease) != [content.REMEDY_FALLBACK], disease

    def test_hyperthyroidism_is_not_aliased_to_hypothyroidism(self):
        """A near-miss string match here would be a clinical content error.

        The remedies table has a Hypothyroidism row and no Hyperthyroidism row.
        They are different diseases, so the miss must stay a miss.
        """
        assert content.remedies_for("Hyperthyroidism") == [content.REMEDY_FALLBACK]


class TestMedicinesAreNeverLoaded:
    def test_no_loader_references_the_medicines_table(self):
        """Telemedicine Practice Guidelines 2020: an AI platform may not
        counsel or prescribe medicine. ``Medicines.csv`` must never reach a
        patient-facing code path (AUDIT 4.3).
        """
        import inspect

        source = inspect.getsource(content)
        assert "Medicines.csv" not in source.replace(
            "data/MasterData/Medicines.csv is deliberately NOT loaded", ""
        )
