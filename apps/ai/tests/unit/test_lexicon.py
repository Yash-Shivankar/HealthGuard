"""Tests for the multilingual lexicon (AUDIT 6, Tier 1).

The deterministic half of Tier 1, so it is fully testable with no vendor, and
these are the tests that establish the feature works at all rather than that the
plumbing is connected.

``TestItRefusesToInventAGrade`` is the set that matters most. Everything else is
coverage; that one is about not fabricating a clinical detail the patient never
gave.
"""

from __future__ import annotations

import pytest

from apps.ai.services import lexicon

# A small vocabulary standing in for the artefact, so these tests need no model
# on disk and cannot be broken by a retrain.
VOCAB = [
    "itching", "skin_rash", "headache", "vomiting", "cough", "chest_pain",
    "mild_fever", "high_fever", "stomach_pain", "diarrhoea", "breathlessness",
    "nausea", "fatigue", "joint_pain", "dizziness",
]


def _vocab(vocabulary):
    # `vocabulary or VOCAB` substitutes the default for an explicitly empty list,
    # which is exactly the case one of these tests is about.
    return VOCAB if vocabulary is None else vocabulary


def names(text, vocabulary=None):
    matches, _ = lexicon.match(text, vocabulary=_vocab(vocabulary))
    return [m.symptom for m in matches]


def leftovers(text, vocabulary=None):
    _, unmapped = lexicon.match(text, vocabulary=_vocab(vocabulary))
    return unmapped


class TestEnglish:
    def test_it_reads_a_plain_description(self):
        assert set(names("I have itching and a skin rash")) == {"itching", "skin_rash"}

    def test_it_reads_colloquial_wording(self):
        """Patients do not use the dataset's vocabulary."""
        assert "diarrhoea" in names("I have loose motions since morning")
        assert "breathlessness" in names("I get out of breath on the stairs")

    def test_a_longer_phrase_wins_over_a_shorter_one(self):
        """"chest pain" must not be read as "pain"."""
        assert names("I have chest pain") == ["chest_pain"]

    def test_one_phrase_produces_one_symptom(self):
        assert names("headache headache headache") == ["headache"]

    def test_an_empty_description_finds_nothing(self):
        assert names("") == []
        assert leftovers("") == []


class TestHindiAndMarathi:
    def test_devanagari_hindi(self):
        assert set(names("मुझे सिरदर्द और उल्टी हो रही है")) == {"headache", "vomiting"}

    def test_devanagari_marathi(self):
        assert set(names("मला पोटदुखी आणि जुलाब आहेत")) == {"stomach_pain", "diarrhoea"}

    def test_romanised_hinglish(self):
        """What people actually type on a phone keyboard."""
        assert set(names("mujhe khansi aur chhati me dard hai")) == {
            "cough", "chest_pain"
        }

    def test_mixed_languages_in_one_sentence(self):
        """Code-switching is the normal case, not the exception."""
        assert set(names("I have खांसी and headache")) == {"cough", "headache"}

    def test_composed_and_decomposed_devanagari_match_alike(self):
        """The same word from two keyboards must compare equal.

        Without NFC normalisation this passes or fails depending on how the
        patient's phone composed the combining marks, which is not a property any
        clinical system should have.
        """
        import unicodedata

        # Most Devanagari syllables have no canonical decomposition, so an
        # earlier version of this test compared a word with itself and proved
        # nothing. The nukta forms do decompose, which is what makes NFC worth
        # doing at all.
        assert unicodedata.normalize("NFD", "क़") != "क़"

        composed = "खांसी"
        decomposed = unicodedata.normalize("NFD", composed)
        assert names(decomposed) == names(composed) == ["cough"]


class TestNegation:
    def test_a_denied_symptom_is_not_proposed(self):
        assert "high_fever" not in names("headache but no high fever")

    def test_the_rest_of_the_sentence_still_works(self):
        assert names("no high fever but I do have a headache") == ["headache"]

    def test_hindi_negation(self):
        assert "cough" not in names("खांसी नहीं है")

class TestNegationBelongsToTheNearestSymptom:
    """Found by running the flow, not by a test - which is why the walkthrough
    is worth doing.

    A fixed window three words wide let a trailing negator reach back past one
    symptom to deny another. Dropping a symptom the patient *did* report is a
    worse failure than the one negation exists to prevent, so both scans now
    stop when they reach another known symptom.
    """

    MARATHI = "मला खाज आणि अंगावर पुरळ आहे, ताप नाही"

    def test_the_reported_symptom_survives(self):
        """Itching and a rash, no fever: the rash must not be denied."""
        assert set(names(self.MARATHI)) == {"itching", "skin_rash"}

    def test_the_denied_one_is_still_denied(self):
        assert not any("which one" in u for u in leftovers(self.MARATHI))

    def test_the_same_shape_in_english(self):
        assert set(names("I have itching and a skin rash, no high fever")) == {
            "itching", "skin_rash"
        }

    def test_an_adjacent_negator_still_works_in_hindi(self):
        assert names("खांसी नहीं है") == []

    def test_an_adjacent_negator_still_works_in_marathi(self):
        assert leftovers("मला ताप नाही") == []

    def test_a_preceding_negator_does_not_reach_past_a_symptom_either(self):
        """The same rule in the other direction: "no headache, itching" denies
        the headache and not the itching.
        """
        assert "itching" in names("no headache, itching")


class TestNegationFallthrough:
    def test_a_negated_phrase_does_not_fall_through_to_a_shorter_one(self):
        """"no chest pain" must not then match "pain" and propose something."""
        assert names("I have no chest pain") == []


class TestItRefusesToInventAGrade:
    """The vocabulary has mild_fever and high_fever; a patient says "fever".

    Choosing one would fabricate a grade nobody stated, and the choice is not
    cosmetic - it changes which diseases match. Dropping it would lose the most
    common complaint in the clinic. So it becomes a question.
    """

    def test_bare_fever_is_not_matched_to_either_grade(self):
        assert names("I have fever and a headache") == ["headache"]

    def test_bare_fever_is_reported_as_a_question(self):
        unmapped = leftovers("I have fever and a headache")
        assert any("fever" in u and "which one" in u for u in unmapped)

    def test_the_question_names_the_options(self):
        unmapped = leftovers("I have fever")
        assert any("mild fever" in u and "high fever" in u for u in unmapped)

    def test_a_graded_fever_matches_and_asks_nothing(self):
        assert names("I have a high fever") == ["high_fever"]
        assert leftovers("I have a high fever") == []

    def test_a_negated_fever_asks_nothing(self):
        assert leftovers("headache but no fever") == []

    def test_hindi_fever_asks_too(self):
        assert any("which one" in u for u in leftovers("मुझे बुखार है"))

    def test_it_asks_nothing_when_the_grade_is_outside_the_vocabulary(self):
        """A question offering options the model cannot accept is worse than
        silence, because the clinician cannot act on it.
        """
        unmapped = leftovers("I have fever", vocabulary=["headache"])
        assert not any("which one" in u for u in unmapped)
        # Still surfaced as an ordinary gap, which is the honest outcome: the
        # clinician sees the word and decides, rather than it vanishing.
        assert any("fever" in u for u in unmapped)


class TestTheVocabularyIsAuthoritative:
    def test_it_never_proposes_a_symptom_the_model_does_not_know(self):
        """The lexicon and the artefact are updated independently, so there will
        be moments when they disagree. The artefact wins.
        """
        matches = names("I have itching and a headache", vocabulary=["headache"])
        assert matches == ["headache"]

    def test_an_empty_vocabulary_proposes_nothing(self):
        assert names("I have itching and a headache", vocabulary=[]) == []


class TestTheBacklog:
    """Unmapped phrases are the honest list of what this file does not know."""

    def test_an_unrecognised_complaint_is_reported(self):
        unmapped = leftovers("my tongue feels furry and metallic")
        assert any("furry" in u for u in unmapped)

    def test_a_fully_understood_description_leaves_no_backlog(self):
        assert leftovers("I have itching and a skin rash for four days") == []

    def test_duration_words_are_not_treated_as_gaps(self):
        """They are read by extract_duration. Leaving them here made every
        backlog entry start with "for four days and a", which buries the real
        ones.
        """
        assert leftovers("headache for three days") == []

    def test_filler_alone_is_not_a_gap(self):
        assert leftovers("I have a headache and it is there") == []


class TestDuration:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("itching for four days", 4),
            ("itching for 4 days", 4),
            ("cough for one week", 7),
            ("cough for 2 weeks", 14),
            ("pain for a month", 30),
            ("started yesterday", 1),
            ("since this morning", 1),
            ("मुझे तीन दिन से सिरदर्द है", 3),
            ("मला चार दिवसांपासून पोटदुखी आहे", 4),
            ("do din se khansi", 2),
        ],
    )
    def test_it_reads_what_was_said(self, text, expected):
        assert lexicon.extract_duration(text) == expected

    def test_nothing_said_means_nothing_returned(self):
        """None rather than a default: the service decides what to do with
        silence, and a lexicon that guessed 1 would hide that it guessed.
        """
        assert lexicon.extract_duration("I have a headache") is None

    def test_the_longest_duration_wins(self):
        """"headache two days, itching a week" is a week-long episode, and the
        urgency rules should see the larger number.
        """
        assert lexicon.extract_duration("headache for two days, itching for a week") == 7

    def test_an_absurd_duration_is_capped_not_rejected(self):
        """The field is a small integer and the draft still has to save."""
        assert lexicon.extract_duration("pain for 900 days") == lexicon.MAX_DAYS


class TestConfidenceIsSpecificity:
    def test_a_multi_word_phrase_scores_higher_than_a_single_word(self):
        specific, _ = lexicon.match("chest pain", vocabulary=VOCAB)
        single, _ = lexicon.match("headache", vocabulary=VOCAB)
        assert specific[0].confidence > single[0].confidence

    def test_the_matched_phrase_travels_with_the_match(self):
        """Without the evidence a clinician has nothing to check the reading
        against, and a review that cannot fail is not a review.
        """
        matches, _ = lexicon.match("mujhe khansi hai", vocabulary=VOCAB)
        assert matches[0].evidence == "khansi"

    def test_matches_are_ordered_most_specific_first(self):
        matches, _ = lexicon.match("itching and chest pain", vocabulary=VOCAB)
        assert [m.symptom for m in matches] == ["chest_pain", "itching"]


class TestCoverage:
    def test_it_reports_its_own_size(self):
        stats = lexicon.coverage()
        assert stats["symptoms_covered"] > 100
        assert stats["phrases"] > stats["symptoms_covered"]

    def test_every_mapped_symptom_has_at_least_one_phrase(self):
        empty = [s for s, phrases in lexicon.PHRASES.items() if not phrases]
        assert not empty, f"symptoms with no phrases: {empty}"

    def test_no_phrase_is_mapped_to_two_symptoms(self):
        """An ambiguous phrase belongs in AMBIGUOUS, where it becomes a question,
        not in two places where whichever sorts first silently wins.
        """
        seen: dict[str, str] = {}
        clashes = []
        for symptom, phrases in lexicon.PHRASES.items():
            for phrase in phrases:
                key = lexicon._normalise(phrase)
                if key in seen and seen[key] != symptom:
                    clashes.append(f"{phrase!r}: {seen[key]} vs {symptom}")
                seen[key] = symptom
        assert not clashes, "\n".join(clashes)

    def test_the_version_is_stamped(self):
        """A draft records which version of this file read it, so a mapping
        change is traceable to the drafts it affected.
        """
        assert lexicon.VERSION
