"""Tests for Sendspin client identity persistence."""

from pathlib import Path

import pytest

pytest.importorskip("aiosendspin", reason="sendspin extra not installed")

from linux_voice_assistant.sendspin.identity import load_or_create_identity


def test_identity_is_stable_across_loads(tmp_path: Path) -> None:
    """The persisted identity must resolve to the same peer_id on every boot."""
    identity_file = tmp_path / "sendspin_identity.json"

    first = load_or_create_identity(identity_file)
    assert identity_file.exists(), "identity must be persisted on first use"

    second = load_or_create_identity(identity_file)
    assert first.peer_id == second.peer_id


def test_identity_file_contains_key_material(tmp_path: Path) -> None:
    identity_file = tmp_path / "sendspin_identity.json"
    load_or_create_identity(identity_file)

    raw = identity_file.read_text().strip()
    assert raw, "private key material must be written (unpadded base64url)"


def test_identity_rejects_missing_file_by_generating(tmp_path: Path) -> None:
    """A fresh location yields a working identity and creates the file."""
    identity = load_or_create_identity(tmp_path / "nested" / "identity.json")
    assert identity.peer_id
    assert (tmp_path / "nested" / "identity.json").exists()
