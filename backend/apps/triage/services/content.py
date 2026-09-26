"""Curated clinical content: descriptions, precautions, remedies, severities.

Loaded once and cached for the process lifetime.  The previous implementation
re-read four CSVs from disk on every ``/predict`` request (AUDIT 2.8).

Three classes of defect in the source files are corrected here rather than by
editing the CSVs, so the raw data stays as delivered and the corrections are
reviewable in one place:

**Header rows were being read as data.**  ``Symptom_severity.csv`` starts with
``symptoms,weight``.  The old loader wrapped its entire read loop in
``try/except: pass``, so ``int("weight")`` raised on the very first row and
silently aborted the loop - leaving the severity dictionary **completely
empty** and every severity score at 0.  The audit reported 4 missing keys
(2.10); the real figure was all 131.

**Keys differ only by whitespace, case or a non-breaking space.**  Training
labels include ``"Diabetes "`` with a trailing space; the remedies file spells
Hepatitis entries with U+00A0; ``GERD`` is written ``Gerd``.  All lookups go
through :func:`_normalise`, which collapses these.

**A handful of names genuinely differ** - misspellings baked into the training
labels (``Osteoarthristis``), or a parenthetical the content table omits
(``Paralysis (brain hemorrhage)`` vs ``Paralysis``).  Those need the explicit
alias table below, which is deliberately small and deliberately conservative:
``Hyperthyroidism`` is **not** aliased to the ``Hypothyroidism`` row, because
they are different diseases and a near-miss string match would be a clinical
content error, not a convenience.
"""

from __future__ import annotations

import csv
import logging
import re
from functools import lru_cache
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

MASTER_DATA_DIR = Path(settings.DATA_DIR) / "MasterData"

DESCRIPTION_CSV = MASTER_DATA_DIR / "symptom_Description.csv"
PRECAUTION_CSV = MASTER_DATA_DIR / "symptom_precaution.csv"
REMEDIES_CSV = MASTER_DATA_DIR / "HomeRemedies.csv"
# Capital S.  Windows does not care; every Linux host does, and the old
# lowercase spelling guaranteed a 500 on the first prediction (AUDIT 2.4).
SEVERITY_CSV = MASTER_DATA_DIR / "Symptom_severity.csv"

# data/MasterData/Medicines.csv is deliberately NOT loaded and must never be
# surfaced to a patient.  The Telemedicine Practice Guidelines 2020 prohibit an
# AI platform from counselling or prescribing medicine; showing this table to a
# patient is the specific thing they forbid (AUDIT 4.3).  Behind a registered
# practitioner's login, as a reference they sign off on, it would be fine.

REMEDY_FALLBACK = "No home remedies are recorded for this condition yet."

# Normalised-name aliases: training label -> the key the content table uses.
# Keep this list short and justify every entry.
_DISEASE_ALIASES = {
    # Training labels carry a parenthetical the content tables drop.
    "vertigoparoymsalpositionalvertigo": "paroymsalpositionalvertigo",
    "paralysisbrainhemorrhage": "paralysis",
    "dimorphichemmorhoidspiles": "dimorphichemmorhoids",
    # Misspellings baked into the training labels.
    "osteoarthristis": "osteoarthritis",
    "pepticulcerdiseae": "pepticulcerdisease",
}

# Same idea for the description table, which spells the piles entry with one
# "m" where the remedies table uses two.
_DESCRIPTION_ALIASES = {
    "dimorphichemmorhoidspiles": "dimorphichemorrhoidspiles",
}

_NON_ALNUM = re.compile(r"[^a-z0-9]")
_WHITESPACE = re.compile(r"\s+")


def _clean_text(value: str) -> str:
    r"""Collapse runs of whitespace and trim.

    ``\s`` matches U+00A0 in Python 3 str mode, so the non-breaking spaces
    inside HomeRemedies.csv are handled here without a special case.
    """
    return _WHITESPACE.sub(" ", value).strip()


def _normalise(value: str) -> str:
    """Collapse a disease or symptom name to a comparable key.

    Lowercases and strips everything that is not a letter or digit, which
    absorbs spacing differences, punctuation, case and non-breaking spaces.
    """
    return _NON_ALNUM.sub("", value.lower())


# The master-data files are not uniformly encoded.  HomeRemedies.csv is cp1252
# (it contains 0xA0 non-breaking spaces); symptom_Description.csv is genuine
# UTF-8 containing an em-dash.  UTF-8 is tried first, because decoding real
# UTF-8 as cp1252 succeeds silently and mojibakes the result.
#
# This is a second instance of the AUDIT 2.4 class of bug and is not in the
# audit: the old loader called bare open(), which uses the platform's locale
# encoding.  On Windows that is cp1252, so it worked.  On a Linux host with a
# UTF-8 locale, reading HomeRemedies.csv raises UnicodeDecodeError - another
# guaranteed production-only failure.
_ENCODINGS = ("utf-8-sig", "cp1252")


def _decode(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in _ENCODINGS:
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        if encoding != _ENCODINGS[0]:
            logger.info("%s is not UTF-8; decoded as %s", path.name, encoding)
        return text
    raise UnicodeDecodeError(
        "utf-8", raw, 0, len(raw), f"{path.name} is not readable as {_ENCODINGS}"
    )


def _read_rows(path: Path, *, expected_columns: int) -> list[list[str]]:
    """Read a master-data CSV, skipping the header and any short or blank row.

    Unlike the loader this replaces, a malformed row is logged rather than
    silently ending the read (AUDIT 2.13).
    """
    try:
        text = _decode(path)
    except FileNotFoundError:
        logger.exception("Master data file missing: %s", path)
        raise

    rows: list[list[str]] = []
    for line_number, row in enumerate(csv.reader(text.splitlines()), start=1):
        if not row or not row[0].strip():
            continue
        if len(row) < expected_columns:
            logger.warning(
                "%s line %d: expected %d columns, got %d - skipped",
                path.name,
                line_number,
                expected_columns,
                len(row),
            )
            continue
        rows.append(row)

    return rows


def _is_header(row: list[str], *, numeric_column: int | None = None) -> bool:
    """Detect the header row without hardcoding its text.

    For the severity file the second column must parse as an integer; for the
    others we compare against the known header labels.
    """
    if numeric_column is not None:
        try:
            int(row[numeric_column])
        except (ValueError, IndexError):
            return True
        return False
    return _normalise(row[0]) in {"disease", "nameofthediseases"}


@lru_cache(maxsize=1)
def severity_scores() -> dict[str, int]:
    """Symptom name (normalised) -> severity weight."""
    scores: dict[str, int] = {}
    for row in _read_rows(SEVERITY_CSV, expected_columns=2):
        if _is_header(row, numeric_column=1):
            continue
        try:
            scores[_normalise(row[0])] = int(row[1])
        except ValueError:
            logger.warning(
                "%s: non-numeric severity %r for %r - skipped",
                SEVERITY_CSV.name,
                row[1],
                row[0],
            )
    logger.info("Loaded %d symptom severity weights", len(scores))
    return scores


@lru_cache(maxsize=1)
def descriptions() -> dict[str, str]:
    """Disease name (normalised) -> description text."""
    table = {
        _normalise(row[0]): _clean_text(row[1])
        for row in _read_rows(DESCRIPTION_CSV, expected_columns=2)
        if not _is_header(row)
    }
    logger.info("Loaded %d disease descriptions", len(table))
    return table


@lru_cache(maxsize=1)
def precautions() -> dict[str, list[str]]:
    """Disease name (normalised) -> list of precautions."""
    table = {
        _normalise(row[0]): [_clean_text(i) for i in row[1:5] if _clean_text(i)]
        for row in _read_rows(PRECAUTION_CSV, expected_columns=5)
        if not _is_header(row)
    }
    logger.info("Loaded precautions for %d diseases", len(table))
    return table


@lru_cache(maxsize=1)
def remedies() -> dict[str, list[str]]:
    """Disease name (normalised) -> list of home remedies."""
    table = {
        _normalise(row[0]): [_clean_text(i) for i in row[1:4] if _clean_text(i)]
        for row in _read_rows(REMEDIES_CSV, expected_columns=4)
        if not _is_header(row)
    }
    logger.info("Loaded home remedies for %d diseases", len(table))
    return table


def _lookup(table: dict, disease: str, aliases: dict[str, str]):
    key = _normalise(disease)
    if key in table:
        return table[key]
    aliased = aliases.get(key)
    if aliased is not None and aliased in table:
        return table[aliased]
    return None


def severity_of(symptom: str) -> int:
    """Severity weight for a symptom, 0 if the name is unknown.

    An unknown symptom now produces a warning instead of silently scoring 0,
    which is how the four whitespace-mismatched names went unnoticed.
    """
    score = severity_scores().get(_normalise(symptom))
    if score is None:
        logger.warning("No severity weight recorded for symptom %r", symptom)
        return 0
    return score


def description_for(disease: str) -> str:
    text = _lookup(descriptions(), disease, _DESCRIPTION_ALIASES)
    if text is None:
        logger.warning("No description recorded for disease %r", disease)
        return ""
    return text


def precautions_for(disease: str) -> list[str]:
    items = _lookup(precautions(), disease, _DISEASE_ALIASES)
    if items is None:
        logger.warning("No precautions recorded for disease %r", disease)
        return []
    return items


def remedies_for(disease: str) -> list[str]:
    """Home remedies for a disease - **always a list**.

    The old code assigned the bare string "Remedies for this disease were not
    found" on a miss, and the template iterated it, so Django rendered one
    bullet per character (AUDIT 2.9).  Returning a list of one keeps the
    template's loop correct in both cases.
    """
    items = _lookup(remedies(), disease, _DISEASE_ALIASES)
    if not items:
        logger.info("No home remedies recorded for disease %r", disease)
        return [REMEDY_FALLBACK]
    return items


def warm_cache() -> None:
    """Load every table up front so the first request does not pay for it."""
    severity_scores()
    descriptions()
    precautions()
    remedies()
