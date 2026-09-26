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

## Frontend

A React + TypeScript SPA under `frontend/`, built with Vite.

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173, proxying /api to Django on :8000
npm run build      # checks locale parity, typechecks, then bundles to dist/
```

Run Django on port 8000 alongside it. Vite proxies `/api` there, which keeps the
session cookie first-party and means development exercises the same same-origin
path production uses. CORS is configured for Vite's origin as well, for anyone
who prefers to point `VITE_API_BASE_URL` straight at the API.

Layout follows AUDIT §7.3:

```
frontend/src/
├── pages/        patient/ · clinic/ · doctor/
├── components/   common/ · clinical/ · layout/
├── services/     api/ · hooks/ · store/
├── utils/        validators/ · formatters/ · i18n/
├── styles/       design tokens
└── types/
```

**Multilingual from day one** (§7.3): English, Hindi and Marathi. `npm run build`
fails if the locale files drift apart, because a half-translated page is worse
than an untranslated one when the subject is someone's symptoms.

**The symptom names are still English** even when the interface is not. They come
from the model's feature names and the source dataset is English; translating 131
clinical terms needs the clinician sign-off §11.6 asks for, and a mistranslated
symptom changes what someone reports. The gap is visible rather than invented.

Session authentication, not tokens: the session cookie is HttpOnly, so a
cross-site script cannot read it, where a JWT in `localStorage` can be read by
anything that gets onto the page. Staff sign-in here grants the clinic app only —
the admin has its own 2FA gate and an API session does not satisfy it.

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
| `GET /api/v1/auth/session/` | Who is signed in, and which clinic they see |
| `POST /api/v1/auth/login/` · `logout/` | Staff session for the clinic app |
| `POST /api/v1/auth/clinic/` | Switch active clinic (locum working at two) |
| `GET /api/v1/patients/` | Patients at the caller's clinic, scoped |
| `GET /api/v1/triage/assessments/` | The clinician review queue, scoped |
| `PATCH /api/v1/triage/assessments/<id>/review/` | Clinician sign-off |

The API and the HTML pages call the same service layer, so they cannot drift.

**API scoping is separate from admin scoping.** `ScopedModelAdmin` overrides
`ModelAdmin` hooks and a DRF view shares none of them, so `common/api/scoped.py`
restates the same discipline for the API — and `apps/accounts/tests/integration/
test_api_isolation.py` asserts it independently. A record at another clinic
returns **404, not 403**: a 403 confirms the record exists.

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
  ├── audit/         read-access log and the ops dashboard
  ├── abdm/          ABHA linkage, FHIR bundles, HIE-CM consent, transfers
  └── ai/            voice intake, symptom extraction, note drafting
common/              middleware, base admin classes, shared building blocks
utils/               validators, domain exceptions, logging, pagination
ml/                  offline training; not imported by the app at runtime
  ├── dataset.py     canonical data loading and cleaning
  ├── train.py       the pipeline
  └── artifacts/     versioned model + metrics + model card
frontend/            React + TypeScript SPA (Vite)
templates/  static/  the server-rendered surface
data/                source CSVs, as delivered
docs/                AUDIT.md, HISTORY.md, AI_CARD.md
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

`abdm` arrived in Phases 5–6 and `ai` in Phase 7. `encounters` is still
deliberately absent rather than scaffolded empty — an empty app is a liability,
not a head start.

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
- [ ] **Obtain ABDM sandbox access** (§11.3) and verify against it: every
      constant in `apps/abdm/services/endpoints.py`, the encryption envelope in
      `apps/abdm/services/crypto.py`, and whether the bundles satisfy the NDHM
      FHIR profile (grep `ABDM-SPECIFIC: verify`). M1 and M2 are built but
      unverified end to end, and certification cannot start without this.
- [ ] Get clinical sign-off on the urgency thresholds in
      `prediction_app/services/triage.py`. They are inherited magic numbers
      with no clinical basis, documented as such in the module.
- [ ] Add red-flag rules as a hard, non-overridable gate (AUDIT §6 Tier 2).
- [ ] Write the Intended Use statement (AUDIT §4.3) and take legal advice on it.
- [ ] Name a grievance contact and publish response times in the privacy notice.
- [ ] Fill the one remaining content gap: `Hyperthyroidism` has no home
      remedies row. It is deliberately **not** aliased to the `Hypothyroidism`
      row, because they are different diseases.

## ABDM (M1 and M2)

M1 — ABHA linkage, HFR and HPR registration, Scan-&-Share.
M2 — FHIR R4 document bundles, encrypted HIP↔HIU transfer, HIE-CM consent.
Both in `apps/abdm/`, with the generic FHIR layer in `utils/fhir/`.

```bash
python manage.py abdm_status     # which mode you are in, and what is registered
```

> **The wire format is unverified.** The endpoint paths and payload shapes in
> `apps/abdm/services/endpoints.py` are written from the published ABDM
> specification and have **not** been round-tripped against a live sandbox,
> because §11.3 — do we have a sandbox account? — is still unanswered.
>
> Everything around them *is* verified: the linkage lifecycle, consent gating,
> idempotency, masking, the audit trail, QR generation. What is unverified is
> one layer, and it is confined to that one file so it can be corrected against
> the current NHA documentation without touching anything else. ABDM has shipped
> more than one major revision of these APIs — treat those constants as a
> starting point to check, not as fact.

`ABDM_ENABLED` is off by default and every call goes to a stub, so the
application runs end to end with no credentials. Each record carries
`simulated=True`, because a stub call and a live call otherwise look identical
in the UI.

Three rules the code enforces:

- **Linking needs its own consent.** `ConsentPurpose.ABDM_LINKAGE` is separate
  from the symptom-check purpose — agreeing to a symptom check is not agreeing
  to link a national health identifier.
- **The ABHA is treated as the most sensitive field in the system.** Masked in
  every list and every API response, not searchable, and it **cascades with the
  patient** — an ABHA left behind after a DPDP erasure is a permanent,
  nationally-resolvable pointer to someone whose data you promised to delete.
- **The QR carries no patient data.** Facility identity and a rotating token
  only. A QR is a broadcast: printed, stuck to a desk, photographed by whoever
  walks past, and it cannot be un-shared.

Every outbound call is recorded — operation, status, timing, correlation id —
with **no request or response bodies**, for the same reason the access log holds
none: a second copy of every record outside the erasure path.

### M2: sharing records

**FHIR R4 is a published standard, so that layer is genuinely verified.**
`utils/fhir/` builds document bundles and validates them structurally: the
Composition is first, every `urn:uuid:` reference resolves inside the bundle, no
duplicate ids, required elements present. What is *not* covered is the NDHM
Implementation Guide's profile constraints on top of base FHIR — those are
marked `# ABDM-SPECIFIC: verify` so the set is greppable.

The mapping is where a clinical claim could quietly get upgraded, so it does
not: a Condition is `unconfirmed` unless a clinician signed off, the symptoms
travel as their own Observation so a receiving clinician can work from the
evidence without trusting the model, no SNOMED code is invented for a free-text
label, and where no clinician was involved the Composition's author is the
**Organization** — never a fabricated Practitioner.

**HIE-CM consent is not DPDP consent.** `apps.consent.ConsentArtefact` is our
lawful basis to process. `abdm.HieConsentArtefact` is the patient's instruction,
issued through the Consent Manager, authorising a named HIU to fetch specified
categories for a specified period. Holding one does not grant the other, and
treating either as the other would be a disclosure with no basis.

Four checks gate every disclosure — usable consent, right category, right date
range, and **right patient**. The last looks redundant and is the only one that
catches assembling someone else's record under a valid consent.

**Encryption** is X25519 → HKDF-SHA256 → AES-256-GCM, every primitive from
`cryptography`. GCM authenticates, so a payload altered in transit fails to
decrypt rather than decrypting to something plausible. Keys are ephemeral per
transfer, and both sides contribute to the nonce — a nonce one party chooses
alone is one that party can repeat, and a repeated GCM nonce is catastrophic.
The crypto is fully tested; the **wire envelope is not**.

Transfers store a SHA-256 of the bundle, never the bundle. That answers "was
this exact document sent, under which consent?" without being a second copy of
the clinical data.

## AI assistance (Tier 1)

A patient describes their problem in Hindi, Marathi or English — typed or spoken —
and the system proposes canonical symptoms for a clinician to check. A
consultation note is then drafted from the recorded encounter.

**Read `docs/AI_CARD.md` before trusting any of this.** It is the honest account
of what works, what is simulated, and what has not been reviewed.

### The separation that makes it auditable

> The language layer normalises. The deterministic model classifies.

Nothing in `apps/ai/` names a condition. Extraction produces symptom names that
already exist in the classifier's 131-term vocabulary and stops there. An
architecture test fails the build if an extraction module imports the predictor,
and a second test asserts the extraction result type has no field capable of
holding a diagnosis — because what a structure permits outlives what the code
currently does with it.

### What is real and what needs a vendor

| Capability | Default provider | Real? |
|---|---|---|
| Transcription | `StubTranscriber` | **Simulated** — needs a vendor (§11.5) |
| Extraction | `LexiconExtractor` | Real, offline, deterministic |
| Summarisation | `TemplateSummariser` | Real, deterministic |

Only transcription genuinely requires a third party. The lexicon covers **123 of
131** canonical symptoms across three languages with 849 phrases, runs offline,
and is the baseline any paid model has to beat. The summariser cannot hallucinate
because it is not generating: every sentence is built from a field that was
already recorded.

```bash
python manage.py ai_status
```

Prints which providers are live, whether data may leave, and — when it may not —
**which condition refused**.

### Nothing enters a record without a clinician

Every draft starts in `draft` state, confirmation requires a named actor, and **a
simulated draft can never be confirmed.** That last rule is stricter than the
ABDM stub precedent on purpose: a fake ABHA number in a dev database is obviously
fake, but a fake transcript is fabricated clinical content.

There are **two** sign-offs, for two different claims:

- confirming an **intake draft** asserts *these are the symptoms this patient has*
- reviewing the resulting **assessment** asserts *the differential is clinically
  sensible*

The assessment created by confirming a draft is still `unreviewed`. A clinician
may add symptoms the extractor missed and remove ones it invented; proposed and
confirmed symptoms are separate fields, so the review is visible as a diff.

### It will not invent a clinical detail

The vocabulary has `mild_fever` and `high_fever`. A patient saying "fever" has not
said which — so bare "fever" becomes a question (`fever - which one?`) surfaced to
the clinician, rather than guessed or silently dropped. Picking one would fabricate
a grade *and* change which diseases match.

### No medication, ever

`assert_no_medication()` scans every draft and every clinician edit for drug
names, dose shapes (`500mg`, `5 ml`), and prescription shorthand (`bd`, `tds`,
`sos`), and **refuses** rather than flagging. A flag relies on someone noticing,
and the premise of ambient documentation is that nobody is reading carefully. It
runs on the deterministic path too, where it cannot fail — a guard that only runs
where you expect failure is not a guard.

### Four conditions before anything leaves the building

AUDIT §6: *no patient-identifiable data to third-party LLM APIs without a consent
basis and a processor agreement.* Implemented as four conditions that must **all**
hold:

1. `AI_ALLOW_EXTERNAL_PROCESSING=True` — off by default
2. the provider has both a URL and a key
3. `AI_PROCESSOR_AGREEMENT_REF` is non-empty — **an empty string refuses**
4. the patient granted `ConsentPurpose.AI_ASSISTANCE`, checked per call

`AI_ASSISTANCE` is a new itemised DPDP purpose: agreeing to a symptom check is not
agreeing that a recording of your voice goes to a third party.

Before any external call, identifiers are stripped — the patient's own name,
phone and ABHA **by value**, plus pattern-matched numbers, ABHA addresses and
emails. It does **not** remove a third party's name, and that limit is pinned by
tests so it cannot be quietly relied upon.

### Provenance survives erasure; content does not

The draft holds the patient's words and **cascades** when they are erased.
`AiCall` is append-only and holds the provider, model version, timings and
SHA-256 hashes — **no text**. So "which version processed this?" survives an
erasure while the content does not.

**Audio is never stored.** The transcript serves every purpose the recording
would, and a recording of someone's voice is biometric data about them.

### Fairness

§6 requires fairness measured across age, sex and language, and the current
dataset cannot support it. That has not changed. What has: **input language is now
recorded on every draft**, at capture. The dashboard shows which languages
patients use — it does **not** show whether each is served equally well.

### Not built

The **red-flag gate** ("chest pain radiating to the arm plus sweating must bypass
every model") is §6 Tier 2, not Tier 1. Free-text intake makes it more urgent than
it was with a checkbox list. It is the first thing to build next.

```bash
grep -rn "AI-VENDOR-SPECIFIC: verify" apps/   # needs a chosen vendor (§11.5)
grep -rn "CLINICAL-REVIEW-NEEDED" apps/       # needs a clinician (§11.6)
```

## Regulatory position

Built against the India DPDP Act 2023 / DPDP Rules 2025 and the Telemedicine
Practice Guidelines 2020. Two hard lines are enforced in code:

- **`data/MasterData/Medicines.csv` is never loaded.** TPG 2020 prohibits an AI
  platform from counselling or prescribing. A test asserts no loader references
  it.
- **The output is ranked possibilities, never a diagnosis.** A definite
  diagnostic output pushes this toward regulated medical device software.

Neither is legal advice. AUDIT §4 has the detail and the sources.
