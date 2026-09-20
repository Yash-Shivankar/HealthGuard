# HealthGuard — application image.
#
# Multi-stage so the runtime image does not carry build toolchains. The ML
# dependencies (scikit-learn, scipy, numpy) dominate the image size, which is
# why the wheel install happens once in a builder stage and is copied across.

FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

COPY requirements/ requirements/
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip \
 && /opt/venv/bin/pip install -r requirements/prod.txt


FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=config.settings.prod

# Never run as root. A container running a health application as root is the
# kind of finding that ends a procurement conversation.
RUN useradd --create-home --uid 10001 healthguard

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --chown=healthguard:healthguard . .

# The model artefact is committed, but rebuild it here if it is ever excluded
# from the build context:
#   RUN python -m ml.train

USER healthguard

EXPOSE 8000

# No CMD baked in beyond a sane default: the deploy target (gunicorn, uvicorn,
# a process manager) is a Phase 8 decision and hardcoding one now would be
# guessing. Override in compose or the orchestrator.
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
