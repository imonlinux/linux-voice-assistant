#!/usr/bin/env python3
"""Migrate an LVA config.json to the current format.

Adds missing keys with sensible defaults, removes keys that are no longer
read by the current code, and preserves every user-set value. Safe to run
multiple times (idempotent). Prints what it changed.
"""

import json
import re
import sys

# --- Load config (JSONC-tolerant) ---

path = sys.argv[1] if len(sys.argv) > 1 else "linux_voice_assistant/config.json"

with open(path, "r") as f:
    content = f.read()

# Strip JSONC comments (// and /* */)
content = re.sub(r'//[^\n]*', '', content)
content = re.sub(r'/\*.*?\*/', '', content, flags=re.DOTALL)

config = json.loads(content)

changes = []


def ensure(section: dict, key: str, default, label: str) -> bool:
    """Add a key with a default value if it's missing. Returns True if added."""
    if key not in section:
        section[key] = default
        changes.append(f"  + {label}.{key} = {default!r}")
        return True
    return False


def remove(section: dict, key: str, label: str) -> bool:
    """Remove an obsolete key. Returns True if removed."""
    if key in section:
        del section[key]
        changes.append(f"  - {label}.{key} (obsolete)")
        return True
    return False


# --- Top-level: no migrations needed ---

# --- sendspin section ---

sendspin = config.get("sendspin", {})
if isinstance(sendspin, dict):
    # connection sub-section
    conn = sendspin.get("connection", {})
    if isinstance(conn, dict):
        # server_host is required — can't set a meaningful default,
        # but flag it if missing so the user knows to add it
        if not conn.get("server_host"):
            print("WARNING: sendspin.connection.server_host is not set.")
            print("  The Sendspin client cannot connect without it.")
            print("  Add it to config.json: \"connection\": { \"server_host\": \"<MA-IP>\" }")

        # Remove obsolete connection keys (old protocol client)
        for key in ("mdns", "mode", "time_sync_adaptive", "time_sync_interval_seconds",
                     "time_sync_min_interval_seconds", "time_sync_max_interval_seconds",
                     "time_sync_burst_size", "time_sync_burst_spacing_seconds",
                     "time_sync_burst_grace_seconds", "time_sync_burst_on_connect",
                     "time_sync_burst_on_stream_start", "timeout_seconds",
                     "hello_timeout_seconds", "ping_interval_seconds",
                     "ping_timeout_seconds"):
            remove(conn, key, "sendspin.connection")

    # pairing sub-section — ensure defaults for the spoken-PIN feature
    pairing = sendspin.get("pairing", {})
    if not isinstance(pairing, dict):
        pairing = {}
        sendspin["pairing"] = pairing
    ensure(pairing, "voice_engine", "auto", "sendspin.pairing")
    ensure(pairing, "piper_model", "en_US-lessac-medium", "sendspin.pairing")
    ensure(pairing, "speak_pin", True, "sendspin.pairing")
    ensure(pairing, "pin", None, "sendspin.pairing")
    ensure(pairing, "voice", None, "sendspin.pairing")
    ensure(pairing, "voice_speed", 120, "sendspin.pairing")

    # player sub-section — remove legacy mpv keys (old pipeline)
    player = sendspin.get("player", {})
    if isinstance(player, dict):
        for key in ("mpv_path", "mpv_ao", "mpv_audio_device", "mpv_extra_args",
                     "preferred_codec", "decoder_backend", "ffmpeg_path",
                     "ffmpeg_extra_args", "duck_volume_percent",
                     "clear_drop_window_ms", "supported_commands"):
            remove(player, key, "sendspin.player")

    # roles sub-section — controller is enabled by default, metadata by
    # default; artwork/visualizer not implemented in the current client
    roles = sendspin.get("roles", {})
    if isinstance(roles, dict):
        for key in ("artwork", "visualizer"):
            remove(roles, key, "sendspin.roles")

    # audio_output sub-section — not used by the current client
    if "audio_output" in sendspin:
        del sendspin["audio_output"]
        changes.append("  - sendspin.audio_output (not used by current client)")

    # client sub-section — not used (name comes from app.name)
    if "client" in sendspin:
        del sendspin["client"]
        changes.append("  - sendspin.client (name comes from app.name)")

    # initial sub-section — not used (volume comes from preferences.json)
    if "initial" in sendspin:
        del sendspin["initial"]
        changes.append("  - sendspin.initial (volume comes from preferences.json)")

    # logging sub-section — not used
    if "logging" in sendspin:
        del sendspin["logging"]
        changes.append("  - sendspin.logging (not used by current client)")

# --- led section: remove keys not in the current config ---

led = config.get("led", {})
if isinstance(led, dict):
    for key in ("brightness",):
        remove(led, key, "led")

# --- Write back if anything changed ---

if changes:
    with open(path, "w") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"Migrated {path}:")
    for c in changes:
        print(c)
else:
    print(f"{path}: already up to date.")
