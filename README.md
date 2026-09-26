# HealthGuard

A pre-consultation symptom checker for Indian clinics. A patient describes what
is wrong — by typing or speaking, in English, Hindi or Marathi — and the system
returns **possible conditions with an urgency assessment** for a clinician to
review.

It is not a diagnosis, it never recommends a medicine, and nothing it produces
enters a patient's record until a clinician has signed it off.

> [!WARNING]
> **Not ready for public deployment.** There is an unresolved data-exposure item
> and several unmet legal obligations. See [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md)
> before deploying anywhere a real patient can reach.

## What is in here

```
backend/     Django project — apps, config, ML artefacts, its own Dockerfile
frontend/    React + TypeScript SPA (Vite)
docs/        the audit, the decision log, and the reference docs below
```

The two halves are separate deployables and talk over HTTP only. In development
the SPA runs on `:5173` and proxies `/api` to Django on `:8000`, which keeps the
session cookie first-party and exercises the same same-origin path production
uses.

## Quick start

**Backend** — every backend command runs from `backend/`:

```bash
cd backend
python -m venv ../venv
../venv/Scripts/activate              # Linux/macOS: source ../venv/bin/activate
pip install -r requirements/dev.txt   # base + pytest; use requirements.txt for runtime only
cp .env.example .env
```

Generate a secret key and put it in `.env`:

```bash
python -c "from django.core.management.utils import get_random_secret_key as k; print(k())"
```

Then migrate, seed the roles, and create a local sign-in account:

```bash
python manage.py migrate
python manage.py seed_roles
python manage.py seed_dev_user
python manage.py runserver
```

**Frontend** — from the repository root:

```bash
cd frontend
npm install
npm run dev
```

The SPA is then on http://localhost:5173. The admin is **not** at `/admin/` — it
is mounted at `DJANGO_ADMIN_URL` from your `.env`, and it has its own 2FA gate.
See [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md#the-admin).

`seed_dev_user` refuses to run unless `DEBUG=True`, because the account it
creates has a password committed to this repository. `seed_roles` is idempotent
and should be run on every deploy — roles defined in a migration drift the moment
someone edits a group in the admin.

## Tests

```bash
cd backend
python -m pytest
```

Runs on in-memory SQLite. CI should also run the same suite against Postgres,
because the two disagree about constraint timing, JSON operators and transaction
behaviour:

```bash
TEST_DATABASE_URL=postgres://healthguard:healthguard@127.0.0.1:5432/healthguard_test python -m pytest
```

Every test in `backend/apps/triage/tests/integration/test_web_flow.py::TestCrashPaths`
pins an exception the audit reproduced against the running application, so the
suite is grounded in real failures rather than in imagined ones.

Architecture rules are enforced by tests rather than by convention: controllers
never touch the ORM, services never touch HTTP, and the language layer never
classifies. The build fails when one is violated — see
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Documentation

| Document | What it covers |
|---|---|
| [`docs/AUDIT.md`](docs/AUDIT.md) | The technical and product audit this project is built from. The plan of record. |
| [`docs/HISTORY.md`](docs/HISTORY.md) | Decision log, newest first — what was done in each phase and why. |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Layering, multi-tenancy, the API surface, the frontend, retraining. |
| [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) | **Read before deploying.** The pre-public checklist, configuration, hardening. |
| [`docs/AI_CARD.md`](docs/AI_CARD.md) | What the AI layer does, what is simulated, what has not been reviewed. |
| [`docs/ABDM.md`](docs/ABDM.md) | ABHA linkage, FHIR R4 bundles, HIE-CM consent — and what is unverified. |
| [`backend/ml/artifacts/MODEL_CARD.md`](backend/ml/artifacts/MODEL_CARD.md) | The classifier: how it was trained and what its numbers mean. |

## What this model is, honestly

The training data is a public synthetic symptom-disease set. After deduplicating
4,920 rows down to 304 unique ones, **no symptom pattern maps to more than one
disease** — the classes are separable by construction, so a forest scores 100%
and that number means nothing about real patients.

No accuracy figure is shown anywhere in the application. The model abstains when
input does not resemble its training data, and a unanimous vote is worded as a
_symptom match_, never as confidence.

Real labelled clinical encounters are the only thing that would make this
measurable. The same caution applies to the AI layer: voice transcription runs
against a stub until a provider is contracted, and the multilingual symptom
lexicon has not been reviewed by a clinician.

## Regulatory position

Built against the India DPDP Act 2023 / DPDP Rules 2025 and the Telemedicine
Practice Guidelines 2020. Three hard lines are enforced in code, not documented as
intentions:

- **`backend/data/MasterData/Medicines.csv` is never loaded.** TPG 2020 prohibits
  an AI platform from counselling or prescribing. A test asserts no loader
  references it, and the consultation-note drafter refuses any output mentioning
  a medication or a dose.
- **The output is ranked possibilities, never a diagnosis.** A definite
  diagnostic output pushes this toward regulated medical device software, which
  is a different CDSCO conversation entirely.
- **Every AI output is a draft until a clinician signs it off.** Drafts start
  unreviewed, a simulated draft can never be confirmed at all, and confirming a
  patient's symptoms is a separate act from agreeing with the model's suggestion.

Neither this nor the audit is legal advice. [`docs/AUDIT.md`](docs/AUDIT.md) §4
has the detail and the sources, and the regulatory reading there should be
re-verified against primary sources before any commercial commitment.
