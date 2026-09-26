"""Tests for de-identification before third-party processing (AUDIT 6).

The last class is the point of this file. A de-identifier that is described as
better than it is causes more harm than one whose limits are pinned by a test,
because the second kind cannot be quietly relied upon for something it does not
do.
"""

from __future__ import annotations

from apps.ai.services import redaction


class TestKnownValues:
    """Removing a value we hold cannot miss it; a pattern can."""

    def test_the_patients_full_name_goes(self):
        result = redaction.redact(
            "Sunita Deshmukh has itching", name="Sunita Deshmukh"
        )
        assert "Sunita" not in result.text
        assert "Deshmukh" not in result.text

    def test_a_first_name_alone_goes(self):
        """People refer to themselves by first name."""
        result = redaction.redact("Sunita has itching", name="Sunita Deshmukh")
        assert "Sunita" not in result.text

    def test_the_symptom_survives(self):
        result = redaction.redact("Sunita has itching", name="Sunita")
        assert "itching" in result.text

    def test_matching_is_case_insensitive(self):
        result = redaction.redact("sunita has itching", name="Sunita")
        assert "sunita" not in result.text

    def test_a_very_short_name_part_is_left_alone(self):
        """Removing every occurrence of a two-letter initial would mangle the
        text without protecting anyone.
        """
        result = redaction.redact("Jo has a bad headache", name="Jo")
        assert "headache" in result.text

    def test_the_patients_number_goes_even_when_stored_with_a_prefix(self):
        result = redaction.redact("call me on 9876500001", contact="+91 9876500001")
        assert "9876500001" not in result.text

    def test_the_abha_number_goes(self):
        result = redaction.redact(
            "my abha is 12345678901234", abha="12345678901234"
        )
        assert "12345678901234" not in result.text

    def test_a_name_inside_another_word_is_not_broken(self):
        """Word-boundary matching: a patient called "Ana" must not turn
        "banana" into "b[patient]na".
        """
        result = redaction.redact("I ate a banana", name="Ana")
        assert "banana" in result.text


class TestPatterns:
    """Identifiers belonging to nobody we have on file."""

    def test_an_indian_mobile_number_goes(self):
        assert "9876543210" not in redaction.redact("ring 9876543210").text

    def test_a_spaced_number_goes(self):
        assert "98765" not in redaction.redact("ring 98765 43210").text

    def test_a_prefixed_number_goes(self):
        assert "9876543210" not in redaction.redact("ring +91-9876543210").text

    def test_an_email_goes(self):
        assert "someone@example.com" not in redaction.redact(
            "write to someone@example.com"
        ).text

    def test_an_abha_address_goes(self):
        assert "sunita@sbx" not in redaction.redact("my address is sunita@sbx").text

    def test_a_grouped_abha_number_goes(self):
        assert "1234" not in redaction.redact("12 3456 7890 1234").text

    def test_a_long_digit_run_goes(self):
        """A long number in a symptom description is not clinical content."""
        assert "123456789" not in redaction.redact("ref 123456789").text

    def test_a_short_number_survives(self):
        """Because clinical text is full of them: 4 days, 102 degrees, 2 tablets
        a day in a quoted complaint. Stripping those would gut the description.
        """
        result = redaction.redact("fever of 102 for 4 days")
        assert "102" in result.text
        assert "4 days" in result.text

    def test_what_was_removed_is_counted(self):
        result = redaction.redact("ring 9876543210 or mail a@b.com")
        assert result.counts["phone"] >= 1
        assert result.counts["email"] >= 1
        assert result.changed

    def test_clean_text_is_unchanged(self):
        result = redaction.redact("I have had itching for four days")
        assert result.text == "I have had itching for four days"
        assert not result.changed


class TestTheReplacementIsVisible:
    def test_a_token_is_left_behind_rather_than_a_blank(self):
        """A clinician reading a redacted transcript should see that something
        was removed, not wonder why the sentence stops making sense.
        """
        result = redaction.redact("Sunita has itching", name="Sunita")
        assert redaction.NAME_TOKEN in result.text

    def test_empty_input_is_handled(self):
        assert redaction.redact("").text == ""
        assert redaction.redact(None).text == ""


class TestWhatItDoesNotDo:
    """Pinned as tests, because the dangerous failure here is over-claiming.

    If one of these starts failing because real named-entity recognition was
    added, that is good news - and the docstrings in redaction.py and the AI
    card must be updated in the same change.
    """

    def test_a_third_partys_name_is_not_removed(self):
        result = redaction.redact(
            "my brother Rahul had the same rash", name="Sunita"
        )
        assert "Rahul" in result.text

    def test_a_place_name_is_not_removed(self):
        result = redaction.redact("I travelled to Pune last week", name="Sunita")
        assert "Pune" in result.text

    def test_a_doctors_name_is_not_removed(self):
        result = redaction.redact("Dr Mehta saw me on Tuesday", name="Sunita")
        assert "Mehta" in result.text

    def test_this_is_why_the_external_path_is_off_by_default(self):
        """Not an assertion about code - a note about why the residual risk is
        acceptable. Four conditions gate the external path, and this is the
        reason there are four rather than one.
        """
        from apps.ai.services import providers

        assert not providers.external_processing(has_credentials=True)
