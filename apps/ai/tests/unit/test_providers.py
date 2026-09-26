"""Tests for the providers and the external-processing gate (AUDIT 6).

``TestTheExternalGate`` is the important set. It pins the guardrail the audit
states outright - no patient-identifiable data to third-party LLM APIs without a
consent basis and a processor agreement - as behaviour rather than intent, with
one test per condition so a regression names which condition was lost.
"""

from __future__ import annotations

import json

import pytest

from apps.ai.services import providers


@pytest.fixture
def allow_external(settings):
    """All three deployment conditions satisfied."""
    settings.AI_ALLOW_EXTERNAL_PROCESSING = True
    settings.AI_PROCESSOR_AGREEMENT_REF = "DPA-2026-001"
    settings.AI_LLM_BASE_URL = "https://llm.example"
    settings.AI_LLM_API_KEY = "key"
    settings.AI_LLM_MODEL = "some-model"
    settings.AI_ASR_BASE_URL = "https://asr.example"
    settings.AI_ASR_API_KEY = "key"
    return settings


class TestTheExternalGate:
    def test_it_refuses_by_default(self):
        assert not providers.external_processing(has_credentials=True)

    def test_it_allows_when_every_condition_holds(self, allow_external):
        assert providers.external_processing(has_credentials=True)

    def test_the_flag_alone_is_not_enough(self, settings):
        settings.AI_ALLOW_EXTERNAL_PROCESSING = True
        settings.AI_PROCESSOR_AGREEMENT_REF = "DPA-1"
        decision = providers.external_processing(has_credentials=False)
        assert not decision
        assert "credentials" in decision.reason

    def test_an_empty_processor_agreement_refuses(self, allow_external):
        """The condition that stops "we have a DPA" being a claim in a README.

        DPDP requires a processor agreement before a third party processes
        personal data. A deployment that has switched everything else on and left
        this blank has not done the paperwork, and the code should say so rather
        than assume it happened.
        """
        allow_external.AI_PROCESSOR_AGREEMENT_REF = ""
        decision = providers.external_processing(has_credentials=True)
        assert not decision
        assert "processor agreement" in decision.reason

    def test_whitespace_is_not_a_processor_agreement(self, allow_external):
        allow_external.AI_PROCESSOR_AGREEMENT_REF = "   "
        assert not providers.external_processing(has_credentials=True)

    def test_the_refusal_says_which_condition_refused(self):
        """"AI is off" is a support ticket; naming the condition is a fix."""
        assert providers.external_processing(has_credentials=True).reason


class TestTheFactoriesDefaultLocal:
    def test_the_transcriber_defaults_to_the_stub(self):
        assert isinstance(providers.build_transcriber(), providers.StubTranscriber)

    def test_the_extractor_defaults_to_the_lexicon(self):
        assert isinstance(providers.build_extractor(), providers.LexiconExtractor)

    def test_the_summariser_is_the_template(self):
        assert isinstance(providers.build_summariser(), providers.TemplateSummariser)

    def test_the_vendor_transcriber_is_reachable_once_allowed(self, allow_external):
        assert isinstance(providers.build_transcriber(), providers.VendorTranscriber)

    def test_the_llm_extractor_is_reachable_once_allowed(self, allow_external):
        assert isinstance(providers.build_extractor(), providers.LlmExtractor)

    def test_missing_credentials_fall_back_rather_than_failing(self, allow_external):
        """An unconfigured vendor must degrade, not break intake.

        A clinic mid-consultation is the worst possible place to discover a
        missing environment variable.
        """
        allow_external.AI_LLM_API_KEY = ""
        assert isinstance(providers.build_extractor(), providers.LexiconExtractor)


class TestTheLocalExtractorIsNotAStub:
    def test_it_reads_real_input(self):
        extractor = providers.LexiconExtractor(vocabulary=["itching", "headache"])
        extraction = extractor.extract(text="I have itching", language="en")
        assert extraction.symptom_names == ["itching"]

    def test_it_is_not_marked_simulated(self):
        """Because its output is a real reading of real input - which is what
        makes a draft from it confirmable.
        """
        extractor = providers.LexiconExtractor(vocabulary=["itching"])
        assert extractor.extract(text="itching", language="en").simulated is False

    def test_nothing_leaves_the_building(self):
        extractor = providers.LexiconExtractor(vocabulary=["itching"])
        assert extractor.extract(text="itching", language="en").external is False


class TestTheStubTranscriber:
    def test_it_is_deterministic(self):
        stub = providers.StubTranscriber()
        first = stub.transcribe(audio=b"same bytes", language="en")
        second = stub.transcribe(audio=b"same bytes", language="en")
        assert first.text == second.text

    def test_different_audio_gives_different_text(self):
        """So a demo does not look like it ignored the input."""
        stub = providers.StubTranscriber()
        texts = {
            stub.transcribe(audio=f"clip-{i}".encode(), language="en").text
            for i in range(20)
        }
        assert len(texts) > 1

    def test_everything_it_produces_is_simulated(self):
        """The flag the review gate refuses on. A canned transcript is
        fabricated clinical content, whatever else is true about it.
        """
        stub = providers.StubTranscriber()
        assert stub.transcribe(audio=b"x", language="hi").simulated is True


class TestTheLlmOutputIsFiltered:
    """Where the normalise/classify separation is actually enforced.

    An LLM asked for symptoms will sometimes volunteer a diagnosis. The only
    reliable defence is to make a disease unrepresentable in the output, rather
    than to ask the model not to produce one.
    """

    VOCAB = ["itching", "skin_rash", "headache"]

    def filtered(self, raw):
        return providers.validate_against_vocabulary(raw, self.VOCAB)

    def test_a_valid_answer_passes_through(self):
        matches, discarded = self.filtered(
            json.dumps([{"symptom": "itching", "evidence": "khujli", "confidence": 0.9}])
        )
        assert [m.symptom for m in matches] == ["itching"]
        assert discarded == []

    def test_a_disease_name_is_discarded(self):
        matches, discarded = self.filtered(
            json.dumps(
                [
                    {"symptom": "itching", "evidence": "khujli", "confidence": 0.9},
                    {"symptom": "Fungal infection", "evidence": "rash", "confidence": 0.8},
                ]
            )
        )
        assert [m.symptom for m in matches] == ["itching"]
        assert "Fungal infection" in discarded

    def test_an_invented_symptom_is_discarded(self):
        matches, _ = self.filtered(
            json.dumps([{"symptom": "feeling_of_doom", "confidence": 1.0}])
        )
        assert matches == []

    def test_unparseable_output_yields_nothing_rather_than_raising(self):
        """Failing to extract is recoverable; a 500 during intake is not."""
        matches, discarded = self.filtered("I think the patient has a cold")
        assert matches == []
        assert discarded

    def test_a_non_list_answer_is_rejected(self):
        matches, _ = self.filtered(json.dumps({"symptom": "itching"}))
        assert matches == []

    def test_duplicates_collapse(self):
        matches, _ = self.filtered(
            json.dumps(
                [{"symptom": "itching", "confidence": 0.9}, {"symptom": "itching", "confidence": 0.5}]
            )
        )
        assert len(matches) == 1

    def test_a_confidence_outside_the_range_is_clamped(self):
        matches, _ = self.filtered(json.dumps([{"symptom": "itching", "confidence": 5}]))
        assert matches[0].confidence == 1.0

    def test_a_missing_confidence_does_not_crash(self):
        matches, _ = self.filtered(json.dumps([{"symptom": "itching"}]))
        assert 0.0 <= matches[0].confidence <= 1.0

    def test_the_prompt_forbids_naming_a_disease(self):
        """Not the guarantee - the filter above is - but it should still say so."""
        extractor = providers.LlmExtractor(
            base_url="https://x", api_key="k", model="m", vocabulary=self.VOCAB
        )
        prompt = extractor.prompt("my head hurts")
        assert "Do not name a disease" in prompt
        assert "itching" in prompt, "the vocabulary must be in the prompt"


class TestTheTemplateSummariser:
    def base_facts(self, **overrides):
        facts = {
            "symptoms": ["Itching", "Skin rash"],
            "days": 4,
            "abstained": False,
            "differential": [{"disease": "Fungal infection", "confidence": 0.79}],
            "urgency_label": "Book a consultation",
        }
        facts.update(overrides)
        return facts

    def test_it_produces_the_four_fixed_sections(self):
        note = providers.TemplateSummariser().summarise(facts=self.base_facts())
        assert set(note.sections) == {
            "presenting_complaint", "history", "assessment", "plan"
        }

    def test_there_is_no_medication_section_to_fill(self):
        note = providers.TemplateSummariser().summarise(facts=self.base_facts())
        assert "medication" not in note.sections
        assert "prescription" not in note.sections

    def test_the_assessment_does_not_claim_a_diagnosis(self):
        """A note is the artefact most likely to be signed unread, so this is
        the same discipline as the Phase 6 FHIR mapping.
        """
        note = providers.TemplateSummariser().summarise(facts=self.base_facts())
        assessment = note.sections["assessment"]
        assert "Not a diagnosis" in assessment
        assert "symptom match" in assessment
        assert "not a probability" in assessment

    def test_an_abstention_says_so_rather_than_reassuring(self):
        note = providers.TemplateSummariser().summarise(
            facts=self.base_facts(abstained=True, differential=[])
        )
        assert "abstained" in note.sections["assessment"]
        assert "clinical assessment required" in note.sections["assessment"].lower()

    def test_the_plan_is_a_disposition_not_a_treatment(self):
        note = providers.TemplateSummariser().summarise(facts=self.base_facts())
        plan = note.sections["plan"]
        assert "Triage disposition" in plan
        assert "no treatment or medication advice" in plan

    def test_it_cannot_hallucinate_because_it_does_not_generate(self):
        """Same facts, same note, every time."""
        summariser = providers.TemplateSummariser()
        first = summariser.summarise(facts=self.base_facts())
        second = summariser.summarise(facts=self.base_facts())
        assert first.sections == second.sections

    def test_the_patients_own_words_are_quoted_when_available(self):
        note = providers.TemplateSummariser().summarise(
            facts=self.base_facts(source_text="mujhe khujli hai")
        )
        assert "mujhe khujli hai" in note.sections["history"]

    def test_a_translated_intake_is_declared(self):
        note = providers.TemplateSummariser().summarise(
            facts=self.base_facts(language="hi", language_label="Hindi")
        )
        assert "Hindi" in note.sections["history"]


class TestTheExtractionResultCannotExpressAConclusion:
    def test_it_has_no_disease_field(self):
        """Structural, not behavioural: what a type permits outlives what the
        code currently does with it.
        """
        fields = set(providers.Extraction.__dataclass_fields__)
        assert not fields & {"disease", "diagnosis", "condition", "differential"}

    def test_a_match_carries_its_evidence(self):
        fields = set(providers.SymptomMatch.__dataclass_fields__)
        assert "evidence" in fields
