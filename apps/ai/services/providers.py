"""AI providers, and the gate that decides whether data may leave.

## Read this before configuring a vendor

AUDIT 6 ends with a guardrail that is not negotiable:

    No patient-identifiable data to third-party LLM APIs without a consent
    basis and a processor agreement. Plan for on-prem or India-region
    inference.

That sentence is implemented here, as four conditions that must *all* hold
before a single character leaves this machine (:func:`external_processing`):

1. ``AI_ALLOW_EXTERNAL_PROCESSING`` is on - a deliberate act, off by default
2. the provider has credentials configured
3. ``AI_PROCESSOR_AGREEMENT_REF`` names a signed agreement. An empty string
   refuses. This is what stops "we have a DPA" from being a claim in a README
4. the patient has granted ``ConsentPurpose.AI_ASSISTANCE``, checked per call
   by the services, not here - because consent is about a person and this
   function does not know which person

Conditions 1 to 3 are deployment facts and are checked here. Condition 4 is
checked in the service that has the patient. Both are tested.

## Three capabilities, three local defaults

``Transcriber``  speech to text. **This is the one that genuinely needs a
                 vendor** - AUDIT 11.5 recommends buying Indian-language
                 medical ASR for the pilot, and the decision is unanswered. The
                 default is a stub, and everything it produces is
                 ``simulated``, which the review gate refuses to confirm.

``Extractor``    free text to canonical symptoms. The default is
                 :class:`LexiconExtractor`, which is **not a stub** - it is a
                 deterministic multilingual matcher that works today, offline,
                 auditably, and is the baseline any LLM has to beat.

``Summariser``   consultation note. The default is
                 :class:`TemplateSummariser`, which assembles a note from data
                 that is *already structured*. It cannot hallucinate, because
                 it is not generating - which for a clinical note is a feature
                 and not a limitation.

The pattern is deliberately the same as ``apps/abdm/services/gateway.py``: a
Protocol the services depend on, a local implementation that is the default, and
a vendor implementation that takes explicit configuration to reach. A project
that cannot run without third-party credentials is a project nobody can
contribute to.

## What is unverified here

Nothing about the vendor wire formats is verified, and unlike ABDM there is not
even a published spec to write against until a vendor is chosen. So the vendor
classes are deliberately thin and deliberately unreachable by default; every
guess is marked ``AI-VENDOR-SPECIFIC: verify`` so the set is greppable, exactly
as ``ABDM-SPECIFIC: verify`` is.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Protocol

from django.conf import settings

from apps.ai.services import lexicon
from utils.exceptions.domain import DomainError, ServiceUnavailableError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Transcript:
    text: str
    language: str
    provider: str
    model_version: str = ""
    simulated: bool = True
    external: bool = False


@dataclass(frozen=True)
class SymptomMatch:
    """One canonical symptom the text pointed at.

    ``symptom`` is always a name the triage model already knows. ``evidence`` is
    the phrase that produced it, kept so a clinician reviewing the draft can see
    *why* rather than being asked to trust it.
    """

    symptom: str
    evidence: str
    confidence: float


@dataclass(frozen=True)
class Extraction:
    """Canonical symptoms, and the phrases that did not map.

    Note what this class does **not** have: any field that could carry a
    disease, a diagnosis or a severity. The extractor normalises language and
    stops. Adding such a field here is how the separation the app exists to
    protect would be lost, so its absence is the point.
    """

    matches: list[SymptomMatch] = field(default_factory=list)
    unmapped: list[str] = field(default_factory=list)
    language: str = "unknown"
    provider: str = ""
    model_version: str = ""
    simulated: bool = True
    external: bool = False

    @property
    def symptom_names(self) -> list[str]:
        return [m.symptom for m in self.matches]


@dataclass(frozen=True)
class NoteDraft:
    sections: dict[str, str]
    provider: str
    model_version: str = ""
    simulated: bool = True
    external: bool = False


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------


class Transcriber(Protocol):
    name: str
    simulated: bool

    def transcribe(self, *, audio: bytes, language: str) -> Transcript: ...


class Extractor(Protocol):
    name: str
    simulated: bool

    def extract(self, *, text: str, language: str) -> Extraction: ...


class Summariser(Protocol):
    name: str
    simulated: bool

    def summarise(self, *, facts: dict) -> NoteDraft: ...


# ---------------------------------------------------------------------------
# The external-processing gate
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExternalDecision:
    """Whether data may leave, and if not, which condition refused.

    A structured answer rather than a bare bool so the reason reaches a log and
    a status command. "AI is off" is a support ticket; "the processor agreement
    reference is empty" is a five-minute fix.
    """

    allowed: bool
    reason: str = ""

    def __bool__(self) -> bool:
        return self.allowed


def external_processing(*, has_credentials: bool) -> ExternalDecision:
    """Conditions 1 to 3 of the guardrail. See the module docstring."""
    if not getattr(settings, "AI_ALLOW_EXTERNAL_PROCESSING", False):
        return ExternalDecision(False, "AI_ALLOW_EXTERNAL_PROCESSING is off")
    if not has_credentials:
        return ExternalDecision(False, "no provider credentials configured")
    if not str(getattr(settings, "AI_PROCESSOR_AGREEMENT_REF", "")).strip():
        return ExternalDecision(
            False,
            "AI_PROCESSOR_AGREEMENT_REF is empty: DPDP requires a processor "
            "agreement before a third party processes personal data",
        )
    return ExternalDecision(True)


# ---------------------------------------------------------------------------
# Local implementations - the defaults
# ---------------------------------------------------------------------------


class StubTranscriber:
    """Deterministic canned transcripts.

    Speech recognition cannot be faked usefully and this does not try. It
    returns a fixed sentence keyed on the audio's hash so the pipeline is
    testable end to end and a developer can see the whole flow work without a
    vendor account.

    **Everything it returns is ``simulated``**, and the review gate refuses to
    confirm a simulated draft. That is the difference between this and
    ``StubTransport``: a fake ABHA number in a dev database is obviously fake,
    whereas a fake transcript is fabricated clinical content and must not be
    one click away from a patient's record.
    """

    name = "stub"
    simulated = True

    #: Keyed by hash so the same audio always yields the same transcript, and
    #: two different clips are visibly different in a demo.
    SAMPLES = (
        "I have had itching and a skin rash for four days.",
        "My head has been hurting since yesterday and I feel sick.",
        "There is a burning feeling when I pass urine and I have a fever.",
        "I have been coughing for a week and get breathless on the stairs.",
    )

    def transcribe(self, *, audio: bytes, language: str) -> Transcript:
        if not audio:
            raise DomainError(
                "No audio was received.", code="ai_empty_audio"
            )
        index = int(hashlib.sha256(audio).hexdigest(), 16) % len(self.SAMPLES)
        logger.info(
            "Simulated transcription",
            extra={"event": "ai_transcribe_stub", "language": language},
        )
        return Transcript(
            text=self.SAMPLES[index],
            language=language,
            provider=self.name,
            model_version="stub-1",
            simulated=True,
            external=False,
        )


class LexiconExtractor:
    """Deterministic multilingual matching against the canonical vocabulary.

    Not a placeholder. It reads the patient's words, finds phrases the lexicon
    knows in English, Hindi or Marathi - in Devanagari or romanised - and
    returns the canonical symptom names those phrases mean. It runs offline, it
    is reproducible, and every match carries the phrase that produced it, so a
    clinician can check the work rather than trust it.

    It is also the baseline an LLM has to beat. Reaching for a model before
    measuring against something this cheap is how projects end up paying per
    token for what a dictionary already did.

    ``simulated`` is False: the output is a real reading of real input.
    """

    name = "lexicon"
    simulated = False

    def __init__(self, vocabulary: list[str] | None = None):
        # Injected so a test does not need a trained artefact on disk, and so
        # the extractor can never propose a symptom outside what it was given.
        self._vocabulary = vocabulary

    def vocabulary(self) -> list[str]:
        if self._vocabulary is not None:
            return self._vocabulary
        # Imported lazily: the artefact is loaded on first use, and importing
        # the predictor at module scope would make a missing artefact break
        # this module's import rather than one call.
        from apps.triage.services import predictor

        return predictor.available_symptoms()

    def extract(self, *, text: str, language: str) -> Extraction:
        matches, unmapped = lexicon.match(text, vocabulary=self.vocabulary())
        return Extraction(
            matches=[
                SymptomMatch(
                    symptom=m.symptom, evidence=m.evidence, confidence=m.confidence
                )
                for m in matches
            ],
            unmapped=unmapped,
            language=language,
            provider=self.name,
            model_version=lexicon.VERSION,
            simulated=False,
            external=False,
        )


class TemplateSummariser:
    """Assembles a consultation note from data that is already structured.

    It cannot hallucinate, because it is not generating: every sentence is built
    from a field that was already recorded - the symptoms, the duration, the
    urgency band, the ranked differential. For a clinical note that is a feature.
    The audit's caveat on ambient scribes is hallucination in under 5% of notes;
    this path's rate is zero by construction, and the cost is that it reads like
    a form rather than prose.

    It also cannot mention a medication, because it has no field that holds one.
    The no-medication check in ``summary_service`` still runs over its output -
    a guard that only runs on the paths you expect is not a guard.
    """

    name = "template"
    simulated = False

    def summarise(self, *, facts: dict) -> NoteDraft:
        complaint = _sentence_for_complaint(facts)
        history = _sentence_for_history(facts)
        assessment = _sentence_for_assessment(facts)
        plan = _sentence_for_plan(facts)

        return NoteDraft(
            sections={
                "presenting_complaint": complaint,
                "history": history,
                "assessment": assessment,
                "plan": plan,
            },
            provider=self.name,
            model_version="template-1",
            simulated=False,
            external=False,
        )


def _sentence_for_complaint(facts: dict) -> str:
    symptoms = facts.get("symptoms") or []
    if not symptoms:
        return "No symptoms were recorded at intake."
    listed = _readable_list(symptoms)
    days = facts.get("days")
    if days:
        unit = "day" if days == 1 else "days"
        return f"Reports {listed}, for {days} {unit}."
    return f"Reports {listed}."


def _sentence_for_history(facts: dict) -> str:
    parts = []
    if facts.get("language") and facts["language"] != "en":
        parts.append(
            "Intake was given in "
            f"{facts.get('language_label', facts['language'])} and translated."
        )
    if facts.get("from_audio"):
        parts.append("Captured by voice and transcribed.")
    if facts.get("source_text"):
        parts.append(f'In their own words: "{facts["source_text"].strip()}"')
    return " ".join(parts) or "No additional history was captured."


def _sentence_for_assessment(facts: dict) -> str:
    """The careful one.

    This is a note a clinician may sign, so it must not upgrade a symptom match
    into a diagnosis - the same discipline as the FHIR mapping in Phase 6. The
    wording says *symptom match*, names the tool, and says it is not a
    diagnosis, in the section most likely to be read.
    """
    if facts.get("abstained"):
        return (
            "The symptom checker did not have enough information to suggest a "
            "pattern and abstained. Not a diagnosis; clinical assessment required."
        )

    differential = facts.get("differential") or []
    if not differential:
        return "No automated suggestion was produced. Clinical assessment required."

    top = differential[0]
    percent = round(float(top.get("confidence", 0)) * 100)
    others = ", ".join(entry.get("disease", "") for entry in differential[1:3])
    text = (
        f"Automated symptom checker: closest pattern match "
        f"{top.get('disease')} ({percent}% symptom match, not a probability of "
        "disease). Not a diagnosis."
    )
    if others:
        text += f" Also considered: {others}."
    return text


def _sentence_for_plan(facts: dict) -> str:
    """Next step, never a prescription.

    Phrased as a triage disposition - who should be seen, how soon - which is
    what the urgency rules actually computed. TPG 2020 forbids this system
    prescribing or dosing, so the plan section carries no treatment at all.
    """
    urgency = facts.get("urgency_label") or facts.get("urgency_level") or ""
    base = f"Triage disposition: {urgency}." if urgency else ""
    return (
        f"{base} Clinician to confirm findings and decide management. "
        "This draft carries no treatment or medication advice."
    ).strip()


def _readable_list(items: list[str]) -> str:
    """Join for use *inside* a sentence, so the items are lowercased.

    The symptom labels arrive title-cased for display in a list, and "Reports
    Itching and Skin rash" reads like a ransom note. Every canonical symptom is a
    common noun, so lowercasing is safe here.
    """
    cleaned = [
        str(i).replace("_", " ").strip().lower() for i in items if str(i).strip()
    ]
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return cleaned[0]
    return ", ".join(cleaned[:-1]) + f" and {cleaned[-1]}"


# ---------------------------------------------------------------------------
# Vendor implementations - unreachable by default
# ---------------------------------------------------------------------------


class VendorTranscriber:
    """Third-party speech to text.

    Deliberately thin and deliberately unused. AUDIT 11.5 is unanswered, so the
    vendor is unchosen and there is no published spec to write against - which
    makes this less verified than even the ABDM endpoints. What is *not* a guess
    is the governance: this class is only ever constructed when
    :func:`external_processing` allows it, and the service redacts before
    calling it.

    AI-VENDOR-SPECIFIC: verify. The request shape below is a placeholder for
    whichever Indian-language medical ASR is contracted.
    """

    name = "vendor-asr"
    simulated = False

    def __init__(self, *, base_url: str, api_key: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def transcribe(self, *, audio: bytes, language: str) -> Transcript:
        try:
            import requests
        except ImportError as exc:  # pragma: no cover - depends on deployment
            raise ServiceUnavailableError(
                "Voice transcription needs `requests` installed. It is not in "
                "requirements/base.txt because nothing uses this path until a "
                "speech provider is configured."
            ) from exc

        # AI-VENDOR-SPECIFIC: verify the path, the field names and whether the
        # audio goes as multipart or base64.
        try:
            response = requests.post(
                f"{self.base_url}/v1/transcribe",
                headers={"Authorization": f"Bearer {self.api_key}"},
                files={"audio": audio},
                data={"language": language},
                timeout=self.timeout,
            )
        except Exception as exc:  # noqa: BLE001 - network failures are all the same here
            raise ServiceUnavailableError(
                "The transcription service did not respond."
            ) from exc

        if response.status_code >= 400:
            raise ServiceUnavailableError(
                f"The transcription service rejected the request ({response.status_code})."
            )

        payload = response.json()
        return Transcript(
            text=str(payload.get("text", "")),
            language=str(payload.get("language", language)),
            provider=self.name,
            model_version=str(payload.get("model", "")),
            simulated=False,
            external=True,
        )


class LlmExtractor:
    """Third-party LLM normalising free text into the canonical vocabulary.

    The prompt asks for symptom names only, and the answer is then **validated
    against the vocabulary and anything else is discarded**. That validation is
    not belt-and-braces; it is the mechanism. An LLM asked for symptoms will
    sometimes volunteer a diagnosis, and the only reliable way to keep the
    normalise/classify separation is to make a disease name unrepresentable in
    the output rather than to ask the model not to produce one.

    AI-VENDOR-SPECIFIC: verify the endpoint and response shape once a provider
    is chosen. Prefer on-prem or India-region inference (AUDIT 6 guardrails).
    """

    name = "llm"
    simulated = False

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        vocabulary: list[str] | None = None,
        timeout: float = 20.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self._vocabulary = vocabulary

    def vocabulary(self) -> list[str]:
        if self._vocabulary is not None:
            return self._vocabulary
        from apps.triage.services import predictor

        return predictor.available_symptoms()

    def prompt(self, text: str) -> str:
        """The instruction. Kept as a method so it is reviewable and testable.

        It says what not to do, but the guarantee comes from the filter applied
        to the answer, not from this wording.
        """
        vocabulary = ", ".join(self.vocabulary())
        return (
            "You normalise a patient's description of their symptoms into a "
            "fixed vocabulary. Reply with a JSON array of objects, each with "
            '"symptom" (exactly one term from the vocabulary), "evidence" (the '
            'words in the input that support it) and "confidence" (0 to 1).\n'
            "Do not name a disease, a diagnosis or a treatment. Do not invent "
            "symptoms that are not described. If nothing matches, reply [].\n"
            f"Vocabulary: {vocabulary}\n"
            f"Patient description: {text}"
        )

    def extract(self, *, text: str, language: str) -> Extraction:
        try:
            import requests
        except ImportError as exc:  # pragma: no cover - depends on deployment
            raise ServiceUnavailableError(
                "LLM extraction needs `requests` installed."
            ) from exc

        # AI-VENDOR-SPECIFIC: verify the path and the response envelope.
        try:
            response = requests.post(
                f"{self.base_url}/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": self.prompt(text)}],
                    # Deterministic where the provider allows it: the same
                    # description should normalise the same way twice.
                    "temperature": 0,
                },
                timeout=self.timeout,
            )
        except Exception as exc:  # noqa: BLE001
            raise ServiceUnavailableError("The language service did not respond.") from exc

        if response.status_code >= 400:
            raise ServiceUnavailableError(
                f"The language service rejected the request ({response.status_code})."
            )

        raw = _llm_content(response.json())
        matches, discarded = validate_against_vocabulary(raw, self.vocabulary())
        if discarded:
            # Worth a log line rather than silence: a model that keeps
            # volunteering diagnoses is telling you something about the prompt.
            logger.warning(
                "Discarded %d LLM output(s) outside the vocabulary",
                len(discarded),
                extra={"event": "ai_llm_output_discarded", "count": len(discarded)},
            )

        return Extraction(
            matches=matches,
            unmapped=discarded,
            language=language,
            provider=self.name,
            model_version=self.model,
            simulated=False,
            external=True,
        )


def _llm_content(payload: dict) -> str:
    """Pull the assistant text out of a chat-completions response.

    AI-VENDOR-SPECIFIC: verify. Tolerant by design - a shape change should
    yield no symptoms rather than an exception, because failing to extract is
    recoverable and a 500 during intake is not.
    """
    try:
        return str(payload["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError):
        logger.warning("Unrecognised LLM response shape", extra={"event": "ai_llm_shape"})
        return ""


def validate_against_vocabulary(
    raw: str, vocabulary: list[str]
) -> tuple[list[SymptomMatch], list[str]]:
    """Keep only symptoms the model already knows; report the rest.

    This is where the normalise/classify separation is actually enforced. A
    disease name, a severity, an invented symptom - none of them are in the
    vocabulary, so none of them survive. Free text the LLM returned that did not
    validate is handed back as ``unmapped`` rather than dropped silently,
    because it is evidence about the prompt.
    """
    import json

    known = set(vocabulary)
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return [], [raw.strip()] if raw.strip() else []

    if not isinstance(parsed, list):
        return [], [raw.strip()]

    matches: list[SymptomMatch] = []
    discarded: list[str] = []
    seen: set[str] = set()

    for entry in parsed:
        if not isinstance(entry, dict):
            discarded.append(str(entry))
            continue
        symptom = str(entry.get("symptom", "")).strip()
        if symptom not in known:
            discarded.append(symptom or str(entry))
            continue
        if symptom in seen:
            continue
        seen.add(symptom)
        matches.append(
            SymptomMatch(
                symptom=symptom,
                evidence=str(entry.get("evidence", ""))[:200],
                confidence=_clamp(entry.get("confidence")),
            )
        )
    return matches, discarded


def _clamp(value) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.5
    return max(0.0, min(1.0, number))


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


def build_transcriber() -> Transcriber:
    """The transcriber this deployment should use.

    Defaults to the stub. Reaching the vendor takes the full external gate, and
    a refusal is logged with the condition that refused so it is diagnosable
    rather than mysterious.
    """
    decision = external_processing(
        has_credentials=bool(
            getattr(settings, "AI_ASR_BASE_URL", "")
            and getattr(settings, "AI_ASR_API_KEY", "")
        )
    )
    if not decision:
        logger.info(
            "Using the stub transcriber: %s",
            decision.reason,
            extra={"event": "ai_transcriber_stub", "reason": decision.reason},
        )
        return StubTranscriber()

    return VendorTranscriber(
        base_url=settings.AI_ASR_BASE_URL,
        api_key=settings.AI_ASR_API_KEY,
        timeout=float(getattr(settings, "AI_TIMEOUT_SECONDS", 30.0)),
    )


def build_extractor() -> Extractor:
    """The extractor this deployment should use.

    Defaults to the lexicon, which is a working implementation rather than a
    fallback - so the absence of an LLM degrades quality, not function.
    """
    decision = external_processing(
        has_credentials=bool(
            getattr(settings, "AI_LLM_BASE_URL", "")
            and getattr(settings, "AI_LLM_API_KEY", "")
            and getattr(settings, "AI_LLM_MODEL", "")
        )
    )
    if not decision:
        logger.debug(
            "Using the lexicon extractor: %s",
            decision.reason,
            extra={"event": "ai_extractor_lexicon", "reason": decision.reason},
        )
        return LexiconExtractor()

    return LlmExtractor(
        base_url=settings.AI_LLM_BASE_URL,
        api_key=settings.AI_LLM_API_KEY,
        model=settings.AI_LLM_MODEL,
        timeout=float(getattr(settings, "AI_TIMEOUT_SECONDS", 20.0)),
    )


def build_summariser() -> Summariser:
    """The summariser this deployment should use.

    Always the template for now. An LLM summariser is a Tier 1 option, but the
    template produces a note with a zero hallucination rate by construction,
    and swapping that for prose is a decision that belongs with the clinician
    who signs the notes (AUDIT 11.6, unanswered).
    """
    return TemplateSummariser()
