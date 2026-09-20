# HealthGuard

A symptom checker that suggests **possible conditions with an urgency
assessment** — not a diagnosis, and never a medicine recommendation.

> **Not for public deployment yet.** See "Before this goes public" below.
> Read [`docs/AUDIT.md`](docs/AUDIT.md) for the full technical and product
> audit, and [`docs/HISTORY.md`](docs/HISTORY.md) for the decision log.

## Setup

```bash
python -m venv venv
venv/Scripts/activate          # Linux/macOS: source venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
```

Generate a secret key and put it in `.env`:

```bash
python -c "from django.core.management.utils import get_random_secret_key as k; print(k())"
```

Then:

```bash
python manage.py migrate
python manage.py runserver
```

The admin is **not** at `/admin/` — it is mounted at `DJANGO_ADMIN_URL` from
your `.env` (default `internal-ops/`).

## Tests

```bash
python -m pytest
```

Every test in `prediction_app/tests/test_views.py::TestCrashPaths` pins an
exception the audit reproduced against the running application.

## Retraining the model

```bash
python -m ml.train
```

Writes `ml/artifacts/model.joblib`, `metrics.json` and `MODEL_CARD.md`. The web
app never trains — it loads the artefact once per process. Read
[`ml/artifacts/MODEL_CARD.md`](ml/artifacts/MODEL_CARD.md) before quoting any
number about this model.

## Layout

```
diseasePrediction/   Django project config only (settings, root urls, wsgi/asgi)
prediction_app/      the application
  ├── services/      business logic: content, predictor, triage
  ├── forms.py       all server-side validation
  ├── views.py       HTTP only — no CSV reads, no training, no business rules
  └── tests/
ml/                  offline training; not imported by the app at runtime
  ├── dataset.py     canonical data loading and cleaning
  ├── train.py       the pipeline
  └── artifacts/     versioned model + metrics + model card
data/                source CSVs, as delivered
docs/                AUDIT.md, HISTORY.md
```

The boundary that keeps this honest: **views never contain business rules,
services never touch HTTP.** Violate it once and you are back to the 193-line
`views.py` the audit found.

## What this model is, honestly

The training data is a public synthetic symptom-disease set. After
deduplicating 4,920 rows down to 304 unique ones, **no symptom pattern maps to
more than one disease** — the classes are separable by construction, so a
forest scores 100% and that number means nothing about real patients.

No accuracy figure is shown anywhere in the application, the model abstains
when input does not resemble its training data, and a unanimous vote is worded
as a *symptom match*, never as confidence. `MODEL_CARD.md` has the full
reasoning.

Real labelled clinical encounters are the only thing that would make this
measurable.

## Before this goes public

- [ ] **Purge `data.csv` from git history.** 40 real names and mobile numbers
      are in the pushed history. The repo is currently **private with 0 forks**,
      so there is no live exposure — but this must happen before it is ever
      made public. See `docs/HISTORY.md`.
- [ ] Rotate `SECRET_KEY` (the old one is in git history).
- [ ] Get clinical sign-off on the urgency thresholds in
      `prediction_app/services/triage.py`. They are inherited magic numbers
      with no clinical basis, documented as such in the module.
- [ ] Add red-flag rules as a hard, non-overridable gate (AUDIT §6 Tier 2).
- [ ] Write the Intended Use statement (AUDIT §4.3) and take legal advice on it.
- [ ] Name a grievance contact and publish response times in the privacy notice.
- [ ] Fill the one remaining content gap: `Hyperthyroidism` has no home
      remedies row. It is deliberately **not** aliased to the `Hypothyroidism`
      row, because they are different diseases.

## Regulatory position

Built against the India DPDP Act 2023 / DPDP Rules 2025 and the Telemedicine
Practice Guidelines 2020. Two hard lines are enforced in code:

- **`data/MasterData/Medicines.csv` is never loaded.** TPG 2020 prohibits an AI
  platform from counselling or prescribing. A test asserts no loader references
  it.
- **The output is ranked possibilities, never a diagnosis.** A definite
  diagnostic output pushes this toward regulated medical device software.

Neither is legal advice. AUDIT §4 has the detail and the sources.
