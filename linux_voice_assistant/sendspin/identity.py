"""Persistent client identity for the Sendspin client.

The client identity is a Curve25519 keypair whose base64url public key IS the
Sendspin client_id. Persisting it (alongside the FileClientPairingStore used
for server pairing records) makes the player identity stable across reboots,
so Music Assistant remembers the device and no re-pairing is needed.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path

from aiosendspin.noise.keys import Identity

_LOGGER = logging.getLogger(__name__)


def load_or_create_identity(identity_file: Path) -> Identity:
    """Load the persisted identity, or generate and persist a new one.

    The file stores the unpadded base64url private key (``Identity.private_b64u``).
    """
    if identity_file.exists():
        raw = identity_file.read_text().strip()
        raw += "=" * (-len(raw) % 4)  # stored unpadded
        identity = Identity.from_private_bytes(base64.urlsafe_b64decode(raw))
        _LOGGER.info("Using persisted Sendspin identity: %s…", identity.peer_id[:12])
        return identity

    identity = Identity.generate()
    identity_file.parent.mkdir(parents=True, exist_ok=True)
    identity_file.write_text(identity.private_b64u)
    _LOGGER.info("Generated new Sendspin identity: %s… (saved to %s)", identity.peer_id[:12], identity_file)
    return identity
