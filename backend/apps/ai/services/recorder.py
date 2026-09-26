"""Records every AI invocation.

One wrapper, so no service can make an unrecorded call by forgetting to - the
same reason ``apps/abdm/services/recorder.py`` exists, and the same shape.

**No text is stored, only hashes.** MDSW guidance expects every output traceable
to the model version that produced it; the naive reading of that - keep the
input and the output - would build a second copy of every patient's words
outside the erasure path. So the content lives on the draft, which cascades when
the patient is erased, and what survives here is proof of which version
processed what.

Recording is best-effort, like the access log and for the same reason: a patient
must not be refused an intake because a log table was full. The honest statement
of the trade-off is that this record is authoritative for what it contains and
not guaranteed to be complete.
"""

from __future__ import annotations

import hashlib
import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass, field

from apps.ai.repositories.ai_repository import AiCallRepository
from utils.logging.context import get_request_id

logger = logging.getLogger(__name__)


def sha256_text(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload or b"").hexdigest()


@dataclass
class CallOutcome:
    """Filled in by the caller inside the recording block.

    A mutable holder rather than a return value because the interesting fields -
    which provider actually ran, what came back - are only known after the work,
    and the recorder must log them whether the work succeeded or raised.
    """

    provider: str = ""
    model_version: str = ""
    simulated: bool = True
    external: bool = False
    redacted: bool = False
    input_sha256: str = ""
    output_sha256: str = ""
    input_chars: int = 0
    extra: dict = field(default_factory=dict)


class AiRecorder:
    def __init__(self, repository: AiCallRepository | None = None):
        self.repository = repository or AiCallRepository()

    @contextmanager
    def record(self, capability: str, *, clinic=None):
        """Wrap one invocation. Yields a :class:`CallOutcome` to fill in.

        Records on the way out either way, so a provider that times out still
        leaves a trace - which is exactly the case someone will be investigating.
        """
        outcome = CallOutcome()
        started = time.perf_counter()
        try:
            yield outcome
        except Exception as exc:  # noqa: BLE001 - re-raised after recording
            self._write(
                capability,
                outcome,
                clinic=clinic,
                succeeded=False,
                duration_ms=int((time.perf_counter() - started) * 1000),
                error_message=str(exc)[:300],
            )
            logger.warning(
                "AI call failed",
                extra={
                    "event": "ai_call_failed",
                    "capability": capability,
                    "provider": outcome.provider,
                    "external": outcome.external,
                },
            )
            raise
        else:
            duration = int((time.perf_counter() - started) * 1000)
            self._write(
                capability,
                outcome,
                clinic=clinic,
                succeeded=True,
                duration_ms=duration,
                error_message="",
            )
            logger.info(
                "AI call",
                extra={
                    "event": "ai_call",
                    "capability": capability,
                    "provider": outcome.provider,
                    "duration_ms": duration,
                    "simulated": outcome.simulated,
                    "external": outcome.external,
                    "redacted": outcome.redacted,
                    **outcome.extra,
                },
            )

    def _write(self, capability: str, outcome: CallOutcome, **fields) -> None:
        try:
            self.repository.create(
                capability=capability,
                provider=outcome.provider or "unknown",
                model_version=outcome.model_version,
                simulated=outcome.simulated,
                external=outcome.external,
                redacted=outcome.redacted,
                input_sha256=outcome.input_sha256,
                output_sha256=outcome.output_sha256,
                input_chars=outcome.input_chars,
                correlation_id=get_request_id()[:64],
                **fields,
            )
        except Exception:  # noqa: BLE001 - see the module docstring
            logger.exception(
                "Could not record an AI call", extra={"capability": capability}
            )
