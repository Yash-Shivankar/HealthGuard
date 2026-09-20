"""Canonical loading and cleaning of the symptom/disease dataset.

This is the single place the raw CSVs are interpreted.  Training uses it;
the web app does not — the app reads feature names and classes from the
artefact instead, so the two can never disagree about column order.

Data defects this module corrects (AUDIT §2.10, verified against the files):

``fluid_overload`` appears **twice** in ``Training.csv``.  pandas renames the
second occurrence to ``fluid_overload.1``.  The first copy is all zeros (a
feature that can never fire); the second carries the real signal (114
positives).  The audit reported these as two separate defects — a dead column
and a severity-dictionary key miss.  They are one defect: the live column is
simply under the wrong name.  We drop the empty copy and restore the real one
to its proper name, which also reunites it with its severity entry.

``Training.csv`` holds 4,920 rows of which only 304 are unique.  Deduplicating
*before* splitting is what makes any reported score meaningful; without it,
281 of 282 test rows appear verbatim in training and the model scores 1.00 by
memorisation.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"

TRAINING_CSV = DATA_DIR / "Data" / "Training.csv"
TESTING_CSV = DATA_DIR / "Data" / "Testing.csv"

TARGET_COLUMN = "prognosis"

# The empty duplicate of fluid_overload, and the real one pandas renamed.
_EMPTY_DUPLICATE = "fluid_overload"
_REAL_DUPLICATE = "fluid_overload.1"


@dataclass(frozen=True)
class Dataset:
    """Cleaned features and labels, plus the provenance needed for a model card."""

    X: pd.DataFrame
    y: pd.Series
    feature_names: list[str]
    classes: list[str]
    raw_row_count: int
    unique_row_count: int
    fingerprint: str

    @property
    def n_samples(self) -> int:
        return len(self.X)


def _clean_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Apply the column-level corrections described in the module docstring."""
    df = df.copy()

    # Drop any fully-empty trailing column pandas manufactures from a stray
    # comma at the end of a header line.
    df = df.drop(columns=[c for c in df.columns if str(c).startswith("Unnamed")], errors="ignore")

    if _REAL_DUPLICATE in df.columns:
        if _EMPTY_DUPLICATE in df.columns and df[_EMPTY_DUPLICATE].sum() == 0:
            df = df.drop(columns=[_EMPTY_DUPLICATE])
        df = df.rename(columns={_REAL_DUPLICATE: _EMPTY_DUPLICATE})

    # Two labels carry a trailing space ("Diabetes ", "Hypertension ").  Strip
    # them here so the model's class names are the clean ones; the content
    # lookup in the app normalises on its own side as well.
    df[TARGET_COLUMN] = df[TARGET_COLUMN].str.strip()

    return df


def _fingerprint(df: pd.DataFrame) -> str:
    """Stable hash of the cleaned data, recorded in the artefact.

    Lets the model card state exactly which data produced the model, and lets
    a future run detect that the training data changed.
    """
    digest = hashlib.sha256(pd.util.hash_pandas_object(df, index=False).values.tobytes())
    return digest.hexdigest()[:16]


def load_training_data(*, deduplicate: bool = True) -> Dataset:
    """Load ``Training.csv``, cleaned and (by default) deduplicated.

    ``deduplicate=False`` exists only so the evaluation report can demonstrate
    the inflated score the duplicates produce.
    """
    raw = pd.read_csv(TRAINING_CSV)
    df = _clean_frame(raw)

    raw_rows = len(df)
    unique_rows = len(df.drop_duplicates())

    if deduplicate:
        df = df.drop_duplicates().reset_index(drop=True)

    feature_names = [c for c in df.columns if c != TARGET_COLUMN]

    return Dataset(
        X=df[feature_names],
        y=df[TARGET_COLUMN],
        feature_names=feature_names,
        classes=sorted(df[TARGET_COLUMN].unique()),
        raw_row_count=raw_rows,
        unique_row_count=unique_rows,
        fingerprint=_fingerprint(df),
    )


def load_holdout_data(feature_names: list[str]) -> tuple[pd.DataFrame, pd.Series]:
    """Load ``Testing.csv`` — 41 rows, one per disease — as a held-out set.

    The audit noted this file ships with the project and is never evaluated
    (§2.10).  Its columns align exactly with Training.csv after cleaning, so it
    is usable as-is.  It is small and one-row-per-class, so treat its score as
    a smoke test rather than a precise estimate.
    """
    df = _clean_frame(pd.read_csv(TESTING_CSV))
    return df[feature_names], df[TARGET_COLUMN]
