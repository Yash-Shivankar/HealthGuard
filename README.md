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
python manage.py seed_roles        # the four role groups and their permissions
python manage.py runserver
```

`seed_roles` is idempotent, so run it on every deploy. Roles defined in a
migration drift the moment someone edits a group in the admin; re-asserting them
from `apps/accounts/management/commands/seed_roles.py` keeps that file the source
of truth.

### The admin

It is **not** at `/admin/` — it is mounted at `DJANGO_ADMIN_URL` from your
`.env` (default `internal-ops/`). Django Admin themed with
[django-unfold](https://unfoldadmin.com/), which is a **staff tool, not a
product surface**: the doctor's daily screen is a Phase 4 React build, and
keeping that line is what stops this becoming a bad EMR (AUDIT §8.1).

Staff access needs a verified second factor. The first device is enrolled out
of band, because a 2FA requirement nobody can satisfy is worse than none:

```bash
python manage.py addstatictoken your-username
```

That prints a single-use backup token. Sign in with it — username and password
first, then pick the device and enter the token — and add a TOTP device from
the admin. For local development before anyone has enrolled, set
`ADMIN_REQUIRE_2FA=False`.

Other controls, all in `.env.example`: login lockout after 5 failures on
username+IP together (`django-axes`), a 15-minute idle timeout on staff
sessions, and an optional `ADMIN_IP_ALLOWLIST`. The allowlist ships empty —
AUDIT §11.7 asks whether ops will be on a fixed IP and that is unanswered, so
enabling it by default would risk locking you out of your own admin.

### Postgres

SQLite works for a quick start, but Postgres is the target (AUDIT §11.1):

```bash
docker compose up -d db
```

Then set `DATABASE_URL` in `.env` to
`postgres://healthguard:healthguard@127.0.0.1:5432/healthguard` and migrate.

### Settings

`manage.py` defaults to `config.settings.dev`. Production must set
`DJANGO_SETTINGS_MODULE=config.settings.prod`, which applies HTTPS and cookie
hardening unconditionally rather than behind a `DEBUG` check.

## Tests

```bash
python -m pytest
```

Runs on in-memory SQLite. To run the same suite against Postgres — which CI
should do, because SQLite and Postgres disagree about constraint timing, JSON
operators and transaction behaviour:

```bash
TEST_DATABASE_URL=postgres://healthguard:healthguard@127.0.0.1:5432/healthguard_test python -m pytest
```

Every test in `apps/triage/tests/integration/test_web_flow.py::TestCrashPaths`
pins an exception the audit reproduced against the running application.

## API

`/api/v1/`, with an OpenAPI schema at `/api/v1/schema/` and Swagger UI at
`/api/v1/docs/`.

| Endpoint | What it does |
|---|---|
| `POST /api/v1/patients/intake/` | Register details, record consent |
| `GET /api/v1/patients/me/` | The intake record held in this session |
| `DELETE /api/v1/patients/me/` | Withdraw consent and erase the record |
| `GET /api/v1/triage/symptoms/` | The symptom vocabulary the model accepts |
| `POST /api/v1/triage/check/` | Run a check, get a ranked differential |

The API and the HTML pages call the same service layer, so they cannot drift.
There is deliberately **no** endpoint that lists patients or fetches one by id:
until the `accounts` app brings authentication and per-clinic scoping in
Phase 4, that would be a cross-tenant leak with no tenants (AUDIT §8.7).

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
config/              Django project package only
  ├── settings/      base · dev · prod · test · unfold
  ├── urls.py        root router
  ├── api.py         /api/v1/ router
  └── admin.py       custom AdminSite: branding and the 2FA gate
apps/                one app per bounded context
  ├── accounts/      clinics, staff memberships, roles - the tenant boundary
  ├── patients/      patient master
  ├── consent/       DPDP consent artefacts
  ├── triage/        symptom intake and prediction
  ├── compliance/    retention notices, data requests, breach incidents
  └── audit/         read-access log and the ops dashboard
common/              middleware, base admin classes, shared building blocks
utils/               validators, domain exceptions, logging, pagination
ml/                  offline training; not imported by the app at runtime
  ├── dataset.py     canonical data loading and cleaning
  ├── train.py       the pipeline
  └── artifacts/     versioned model + metrics + model card
templates/  static/  the server-rendered surface
data/                source CSVs, as delivered
docs/                AUDIT.md, HISTORY.md
```

Each app is layered the same way (AUDIT §7.1):

```
routes/         URL -> controller
controllers/    HTTP only: parse, authorise, respond
services/       business rules; no ORM, no HTTP
repositories/   the ONLY place ORM queries live
serializers/    DRF validation in and out
admin/          one ModelAdmin per file
```

The rule that keeps it honest: **controllers never touch the ORM, services
never touch HTTP, repositories never contain business logic.** Violate it once
and you are back to the 193-line `views.py` the audit found.

The apps the audit provisions but that do not exist yet — `encounters` and
`abdm` — are deliberately absent rather than scaffolded empty. They belong to
Phases 5–6, and an empty app is a liability, not a head start.

## Multi-tenancy

A `Clinic` is the tenant. Staff reach it through a `Membership`, not a field on
the user, because a locum doctor works at more than one clinic and duplicate
accounts would destroy the audit trail. Platform operations is a **role**, not
`is_superuser` — the audit is explicit that handing out `is_superuser` bypasses
every scoping check.

The rule that matters: **a signed-in user with no clinic sees nothing, not
everything.** Cross-clinic access requires the explicit `platform_ops` role.
`apps/accounts/tests/integration/test_tenant_isolation.py` asserts all four
scoping paths against two real clinics, plus that fail-closed case.

## Retention

DPDP retention is enforced by code, in two passes, on a schedule:

```bash
python manage.py run_retention --notices   # open notices for dormant records
python manage.py run_retention --erase     # erase those whose 48h has elapsed
```

They are separate on purpose — a single pass that noticed and erased together
would satisfy the letter of "give notice" and none of its purpose. Both are
idempotent. Run them from cron, a systemd timer or Task Scheduler; there is no
Celery broker in this project yet, and adding one to run two daily jobs would be
infrastructure to maintain for no benefit.

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

- [x] ~~Purge `data.csv` from git history.~~ Done — `git filter-repo` stripped
      it from all commits, and the force-pushed history verifies clean from a
      fresh clone.
- [ ] **Ask GitHub Support to garbage-collect unreachable objects** for this
      repo. A force-push does not delete them: the pre-purge commit `76bfb48`
      is still fetchable by SHA, so the 40 records remain retrievable by anyone
      with repo access who knows it. **Do not make this repo public until that
      is confirmed done.**
- [x] ~~Rotate `SECRET_KEY`.~~ Done — a fresh key is in `.env` (gitignored) and
      the old one is redacted throughout history.
- [ ] Enrol a TOTP device for every staff account and confirm
      `ADMIN_REQUIRE_2FA=True` in the deployed environment.
- [ ] Schedule `run_retention --notices` and `--erase` as daily jobs. Retention
      is enforced in code, but nothing enforces the schedule.
- [ ] Name the grievance contact on the `DataSubjectRequest` intake path. The
      90-day clock is tracked; the public way to start one is not built.
- [ ] Decide whether ops staff will be on a fixed IP or VPN (AUDIT §11.7). If
      so, set `ADMIN_IP_ALLOWLIST` — it is the cheapest high-value control
      available and it ships disabled.
- [ ] Serve the admin from a separate hostname to the patient app, so different
      WAF and network rules can apply (AUDIT §8.10).
- [ ] Add re-authentication for sensitive admin actions. The idle timeout is
      in; per-action re-auth is not.
- [ ] Route `event=admin_superuser_login` to a channel a human reads. The log
      line exists; the alert rule is a deployment step.
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
