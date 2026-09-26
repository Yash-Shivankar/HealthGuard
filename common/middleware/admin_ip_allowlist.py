"""Optional network restriction in front of the admin (AUDIT 8.10).

    IP allowlist in front of the admin if your ops team is on a fixed network
    or VPN. Cheapest high-value control available.

It is **off unless configured**, because AUDIT 11.7 - will ops staff ever be
on a fixed IP or VPN? - is still unanswered. Shipping it enabled with a
guessed range would lock the owner out of their own admin.
"""

from __future__ import annotations

import ipaddress
import logging

from django.conf import settings
from django.http import HttpResponseForbidden

logger = logging.getLogger(__name__)


class AdminIPAllowlistMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.networks = _parse(getattr(settings, "ADMIN_IP_ALLOWLIST", []))
        self.admin_prefix = "/" + settings.ADMIN_URL.lstrip("/")

    def __call__(self, request):
        if self.networks and request.path.startswith(self.admin_prefix):
            client = _client_ip(request)
            if not self._allowed(client):
                logger.warning(
                    "Blocked admin access from a non-allowlisted address",
                    extra={"event": "admin_ip_blocked", "ip": client, "path": request.path},
                )
                return HttpResponseForbidden("Not available from this network.")
        return self.get_response(request)

    def _allowed(self, client: str | None) -> bool:
        if not client:
            # Fail closed: an allowlist that cannot identify the caller must
            # not wave them through.
            return False
        try:
            address = ipaddress.ip_address(client)
        except ValueError:
            return False
        return any(address in network for network in self.networks)


def _parse(entries) -> list:
    networks = []
    for entry in entries:
        try:
            networks.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            logger.error("Ignoring invalid ADMIN_IP_ALLOWLIST entry %r", entry)
    return networks


def _client_ip(request) -> str | None:
    # REMOTE_ADDR first: X-Forwarded-For is client-controlled unless a proxy
    # overwrites it, and trusting it blindly would make this control useless.
    return request.META.get("REMOTE_ADDR") or None
