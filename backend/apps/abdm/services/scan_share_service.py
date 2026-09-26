"""Scan-&-Share QR codes (AUDIT 4.2 M1).

The one part of M1 that is entirely local and therefore **actually verifiable
without a sandbox**: the QR is generated here, and what it encodes is a
decision, not a gateway response.

## What the QR carries, and what it deliberately does not

It carries the **facility's** identity - the HFR id, the clinic name, and a
counter token that identifies this particular display of the code. That is all
the scanning app needs to know where it is.

It carries **no patient data at all**. A QR is a broadcast: it is printed,
stuck to a desk, photographed by whoever is standing there, and it cannot be
un-shared. Putting an ABHA number or a patient id on one would mean handing a
national health identifier to anyone who walks past the reception desk. Anything
patient-specific belongs in the authenticated exchange that follows the scan,
not in the thing on the wall.

## Rotation

The token changes when asked to, and the QR is regenerated. A static code
printed once and left up for a year is a code that has been photographed and
reposted, and there is no way to tell. Rotation is cheap; not being able to
rotate is not.
"""

from __future__ import annotations

import base64
import io
import logging
import secrets
from dataclasses import dataclass

from django.utils import timezone

from apps.abdm.repositories.abdm_repository import FacilityRepository
from utils.exceptions.domain import DomainError

logger = logging.getLogger(__name__)

#: Version prefix, so a scanner meeting a future payload knows it is future.
PAYLOAD_VERSION = "hg1"


@dataclass(frozen=True)
class ScanShareCode:
    """A generated code, ready to display."""

    payload: str
    token: str
    clinic_name: str
    hfr_id: str
    generated_at: str
    #: SVG markup. A template can inline it and a JSON response can carry it
    #: without a media file that would then need serving and expiring.
    #:
    #: SVG rather than PNG for two reasons: it scales, and a code on a
    #: reception desk gets printed at whatever size the printer feels like;
    #: and the PNG renderer needs Pillow, which is a binary dependency this
    #: project does not otherwise want.
    svg: str

    @property
    def data_uri(self) -> str:
        encoded = base64.b64encode(self.svg.encode("utf-8")).decode("ascii")
        return f"data:image/svg+xml;base64,{encoded}"


class ScanShareService:
    def __init__(self, facilities: FacilityRepository | None = None):
        self.facilities = facilities or FacilityRepository()

    def generate(self, *, clinic, token: str | None = None) -> ScanShareCode:
        """Build a Scan-&-Share code for a clinic.

        Refuses when the facility is not registered: a QR that names an
        unregistered facility is a QR that fails on the patient's phone, at the
        reception desk, in front of them.
        """
        registration = self.facilities.for_clinic(clinic)
        if registration is None or not registration.is_verified:
            raise DomainError(
                "Register the facility with the HFR before generating a "
                "Scan-&-Share code.",
                code="facility_not_registered",
            )

        code_token = token or secrets.token_urlsafe(12)
        generated_at = timezone.now().isoformat(timespec="seconds")

        # A compact, self-describing payload. Deliberately not JSON: a QR's
        # capacity is the constraint that matters, and every byte spent on
        # punctuation is a byte of error correction given up.
        payload = "|".join(
            [PAYLOAD_VERSION, registration.hfr_id, str(clinic.pk), code_token]
        )

        logger.info(
            "Scan-and-Share code generated",
            extra={"event": "scan_share_generated", "clinic_id": clinic.pk},
        )

        return ScanShareCode(
            payload=payload,
            token=code_token,
            clinic_name=clinic.name,
            hfr_id=registration.hfr_id,
            generated_at=generated_at,
            svg=self._render(payload),
        )

    @staticmethod
    def _render(payload: str) -> str:
        import qrcode
        import qrcode.image.svg

        # Error correction Q: a code on a reception desk gets scuffed, taped
        # over and photographed at an angle. M would be the usual default; the
        # extra redundancy is worth more than the density here.
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_Q,
            box_size=10,
            border=4,
            image_factory=qrcode.image.svg.SvgPathImage,
        )
        qr.add_data(payload)
        qr.make(fit=True)

        buffer = io.BytesIO()
        qr.make_image().save(buffer)
        return buffer.getvalue().decode("utf-8")

    @staticmethod
    def parse(payload: str) -> dict[str, str]:
        """Read a payload back.

        Used by the tests and by anything that receives a scan. Validating the
        version first means a future payload fails with a clear message rather
        than being silently misread as this one.
        """
        parts = payload.split("|")
        if len(parts) != 4 or parts[0] != PAYLOAD_VERSION:
            raise DomainError("Unrecognised Scan-and-Share payload.", code="bad_payload")

        return {
            "version": parts[0],
            "hfr_id": parts[1],
            "clinic_id": parts[2],
            "token": parts[3],
        }
