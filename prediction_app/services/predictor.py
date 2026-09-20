"""Inference against the versioned model artefact.

The artefact is loaded **once per process** and reused.  The code this replaces
read a 1.3 MB CSV, split it and fitted a fresh ``DecisionTreeClassifier`` on
every single ``/predict`` request - roughly 900x more work than a cached
predict, and non-deterministic besides, so the same symptoms could produce
different diseases on two consecutive submissions (AUDIT 2.8).

Nothing here trains, and nothing here reads the training data.  Everything the
app needs - feature order, class names, the abstain threshold - travels inside
the artefact, so the served model can never drift out of step with the feature
layout it was fitted on.  Retraining is ``python -m ml.train``.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from django.conf import settings

logger = logging.getLogger(__name__)

MODEL_PATH = Path(settings.ML_ARTIFACT_DIR) / "model.joblib"

SUPPORTED_ARTIFACT_FORMAT = 1

_lock = threading.Lock()
_artefact: "Artefact | None" = None


class ModelUnavailable(RuntimeError):
    """The model artefact is missing or unusable.

    Raised at load time rather than swallowed, so a bad deploy fails loudly
    instead of serving predictions from a model that is not there.
    """


@dataclass(frozen=True)
class Candidate:
    """One entry in the ranked differential.

    A note on how this is worded in the UI.  ``confidence`` is the fraction of
    the forest that voted for this class - that is, **how completely the
    reported symptoms matched a stored pattern**.  It is not the probability
    that the person has the disease, and on this dataset it cannot be: the
    training data is synthetic and perfectly separable, so a textbook
    presentation returns a unanimous vote (see ml/artifacts/MODEL_CARD.md).

    Printing that as "100% confident" would reintroduce exactly the false
    certainty the audit set out to remove, so the templates call it a *symptom
    match* and lead with the qualitative band below.  The number is true as a
    statement about pattern overlap; the wording keeps it from being read as
    diagnostic certainty.
    """

    disease: str
    confidence: float

    @property
    def confidence_percent(self) -> int:
        return round(self.confidence * 100)

    @property
    def match_band(self) -> str:
        """Qualitative strength, which is the primary signal in the UI."""
        if self.confidence >= 0.70:
            return "Strong match"
        if self.confidence >= 0.45:
            return "Possible match"
        return "Weak match"


@dataclass(frozen=True)
class Differential:
    """The model's ranked output for one set of symptoms.

    ``abstained`` is the safety path: when the top candidate's confidence falls
    below the artefact's threshold, the application must not name a condition
    (AUDIT 6, Tier 0.7).  Callers check ``abstained`` before reading
    ``candidates``.
    """

    candidates: list[Candidate]
    abstained: bool
    threshold: float
    recognised_symptoms: list[str]
    unknown_symptoms: list[str]

    @property
    def top(self) -> Candidate | None:
        return self.candidates[0] if self.candidates else None


@dataclass(frozen=True)
class Artefact:
    model: object
    feature_names: list[str]
    classes: list[str]
    abstain_threshold: float
    trained_at: str
    data_fingerprint: str
    sklearn_version: str


def _load() -> Artefact:
    if not MODEL_PATH.exists():
        raise ModelUnavailable(
            f"No model artefact at {MODEL_PATH}. Run: python -m ml.train"
        )

    try:
        payload = joblib.load(MODEL_PATH)
    except Exception as exc:  # noqa: BLE001 - re-raised as a typed error below
        raise ModelUnavailable(f"Could not read {MODEL_PATH}: {exc}") from exc

    fmt = payload.get("artifact_format_version")
    if fmt != SUPPORTED_ARTIFACT_FORMAT:
        raise ModelUnavailable(
            f"Artefact format {fmt!r} is not supported "
            f"(expected {SUPPORTED_ARTIFACT_FORMAT}). Retrain with: python -m ml.train"
        )

    trained_with = payload.get("sklearn_version")
    if trained_with != sklearn.__version__:
        # A model pickled under one scikit-learn minor version and loaded under
        # another warns or misbehaves silently (AUDIT 2.15).  Say so loudly.
        logger.warning(
            "Model was trained with scikit-learn %s but %s is installed. "
            "Retrain with: python -m ml.train",
            trained_with,
            sklearn.__version__,
        )

    model = payload["model"]

    # Serving predicts one row at a time, where joblib's parallel dispatch
    # costs far more than the work it distributes - measured at 106 ms/call
    # with n_jobs=-1 against 24 ms/call serial for the same forest.  Training
    # already saves the artefact this way; this is belt-and-braces for an
    # artefact produced elsewhere.
    if getattr(model, "n_jobs", None) not in (None, 1):
        logger.info("Forcing n_jobs=1 for single-row serving (was %s)", model.n_jobs)
        model.n_jobs = 1

    logger.info(
        "Loaded model artefact: trained %s, %d features, %d classes, fingerprint %s",
        payload.get("trained_at"),
        len(payload["feature_names"]),
        len(payload["classes"]),
        payload.get("data_fingerprint"),
    )

    return Artefact(
        model=model,
        feature_names=list(payload["feature_names"]),
        classes=list(payload["classes"]),
        abstain_threshold=float(payload["abstain_threshold"]),
        trained_at=str(payload.get("trained_at", "unknown")),
        data_fingerprint=str(payload.get("data_fingerprint", "unknown")),
        sklearn_version=str(trained_with),
    )


def artefact() -> Artefact:
    """Return the process-wide artefact, loading it on first use."""
    global _artefact
    if _artefact is None:
        with _lock:
            if _artefact is None:
                _artefact = _load()
    return _artefact


def available_symptoms() -> list[str]:
    """Symptom names the model accepts, in the order it was fitted on.

    The intake form is built from this rather than from a CSV read at import
    time, so the form can never offer a symptom the model does not know.
    """
    return artefact().feature_names


def _threshold() -> float:
    """Abstain threshold, overridable from settings for tuning without a retrain."""
    configured = getattr(settings, "TRIAGE_ABSTAIN_THRESHOLD", None)
    return float(configured) if configured is not None else artefact().abstain_threshold


def predict(symptoms) -> Differential:
    """Rank possible conditions for a set of symptom names.

    Unknown symptom names are reported back rather than raising: the old code
    indexed a dict directly and a name outside the training columns produced an
    unhandled ``KeyError`` and a 500 (AUDIT 2.7).
    """
    art = artefact()
    known = set(art.feature_names)

    recognised = [s for s in dict.fromkeys(symptoms) if s in known]
    unknown = [s for s in dict.fromkeys(symptoms) if s not in known]
    if unknown:
        logger.warning("Ignoring %d unrecognised symptom(s): %s", len(unknown), unknown)

    if not recognised:
        return Differential(
            candidates=[],
            abstained=True,
            threshold=_threshold(),
            recognised_symptoms=[],
            unknown_symptoms=unknown,
        )

    # A named DataFrame, not a bare array: scikit-learn matches on feature
    # names when the estimator was fitted with them, so this cannot silently
    # misalign if the column order ever changes.
    vector = pd.DataFrame(
        [[1 if name in set(recognised) else 0 for name in art.feature_names]],
        columns=art.feature_names,
    )

    probabilities = art.model.predict_proba(vector)[0]
    size = int(getattr(settings, "TRIAGE_DIFFERENTIAL_SIZE", 3))
    ranked_indices = np.argsort(probabilities)[::-1][:size]

    candidates = [
        Candidate(disease=str(art.classes[i]), confidence=float(probabilities[i]))
        for i in ranked_indices
        if probabilities[i] > 0
    ]

    threshold = _threshold()
    abstained = not candidates or candidates[0].confidence < threshold

    return Differential(
        candidates=candidates,
        abstained=abstained,
        threshold=threshold,
        recognised_symptoms=recognised,
        unknown_symptoms=unknown,
    )
