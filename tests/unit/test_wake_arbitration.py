"""Unit tests for wake word arbitration (fork).

Covers the shared protocol facts the fork and other satellite kinds must
agree on: the energy metric, wire rounding, phrase normalization, the
contend/overtaken decision rule, envelope validation, and fail-open
behavior when arbitration is off.
"""

import json
import threading
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from linux_voice_assistant import wake_arbitration as wa
from linux_voice_assistant.wake_arbitration import (
    ARBITRATION_PORT,
    UdpArbitrationTransport,
    WakeArbiter,
    WakeLevelRing,
    energy_from_levels,
    frame_level_db,
    normalize_phrase,
    round_energy,
)
from tests.unit.conftest import make_satellite

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeTransport(wa.ArbitrationTransport):
    """Records sends; claims injected via inject() bypass the socket."""

    def __init__(self):
        self.packets = []  # (data, target) target None = broadcast
        self.peers = []
        self.opened = False
        self.closed = False
        self.on_packet = None
        self.open_ok = True

    def open(self, on_packet):
        if not self.open_ok:
            return False
        self.opened = True
        self.on_packet = on_packet
        return True

    def send(self, data):
        self.packets.append((data, None))

    def send_to(self, data, address):
        self.packets.append((data, address))

    def close(self):
        self.closed = True

    # -- test conveniences --

    def broadcast_count(self):
        return sum(1 for _, target in self.packets if target is None)

    def inject(self, msg, from_addr="192.168.1.50"):
        self.on_packet(json.dumps(msg).encode(), from_addr)

    @staticmethod
    def claim_packet(device_id, phrase, energy, nonce=1):
        return {
            "ks": "wake",
            "v": 1,
            "id": device_id,
            "n": nonce,
            "p": phrase,
            "e": energy,
        }


def make_arbiter(device_id="aaaa1111", transport=None):
    arbiter = WakeArbiter(device_id=device_id, transport=transport or FakeTransport())
    arbiter.start()
    return arbiter


def set_levels_levels():
    """A 150-frame level history: floor at -60 dBFS, wake word at -20 dBFS."""
    return [-60.0] * 140 + [-20.0] * 10


# ---------------------------------------------------------------------------
# Wire rounding (must match the other device kind; NOT Python's round())
# ---------------------------------------------------------------------------


class TestRoundEnergy:
    def test_halves_away_from_zero_positive(self):
        # Python round(0.25, 1) == 0.2 (banker's); the wire needs 0.3.
        assert round_energy(0.25) == pytest.approx(0.3)

    def test_halves_away_from_zero_negative(self):
        assert round_energy(-0.25) == pytest.approx(-0.3)

    def test_rounds_down_below_half(self):
        assert round_energy(0.24) == pytest.approx(0.2)
        assert round_energy(-0.24) == pytest.approx(-0.2)

    def test_identity_on_tenths(self):
        assert round_energy(-37.9) == pytest.approx(-37.9)
        assert round_energy(42.0) == pytest.approx(42.0)

    def test_typical_energy(self):
        assert round_energy(39.949999) == pytest.approx(39.9)
        assert round_energy(39.95) == pytest.approx(40.0)


class TestNormalizePhrase:
    def test_case_and_whitespace(self):
        assert normalize_phrase("  Okay   Nabu ") == "okay nabu"

    def test_tabs_and_newlines(self):
        assert normalize_phrase("hey\tjarvis\n\nHEY") == "hey jarvis hey"

    def test_already_normal(self):
        assert normalize_phrase("okay nabu") == "okay nabu"


# ---------------------------------------------------------------------------
# Energy metric
# ---------------------------------------------------------------------------


class TestFrameLevelDb:
    def test_known_level(self):
        # Full-scale square-ish sine RMS 0.5 -> about -6 dBFS.
        frame = np.full(320, 0.5, dtype=np.float32)
        assert frame_level_db(frame) == pytest.approx(-6.0206, abs=1e-3)

    def test_silence(self):
        frame = np.zeros(320, dtype=np.float32)
        assert frame_level_db(frame) == wa._SILENCE_DB


class TestEnergyFromLevels:
    def test_too_few_frames(self):
        assert energy_from_levels([-60.0] * 9) is None

    def test_none_input_guard_small_list(self):
        assert energy_from_levels([]) is None

    def test_floor_and_speech(self):
        # Speech is the average of the loudest 10 of the last 75 frames;
        # the floor is the 20th percentile of the whole window.
        levels = set_levels_levels()
        assert energy_from_levels(levels) == pytest.approx(40.0)

    def test_old_loud_frames_out_of_lookback_are_ignored(self):
        levels = [-20.0] * 75 + [-60.0] * 65 + [-20.0] * 10
        # The first 75 loud frames are outside the 75-frame lookback;
        # speech still averages only the final burst at -20.
        assert energy_from_levels(levels) == pytest.approx(40.0)

    def test_floor_clamped(self):
        # A noise suppressor cannot claim an endless margin: a floor below
        # -75 dBFS counts as -75.
        levels = [-90.0] * 140 + [-20.0] * 10
        assert energy_from_levels(levels) == pytest.approx(55.0)

    def test_min_frames_boundary(self):
        # 10 frames: speech = avg(-60 x9, -20) = -56; floor (20th pct) = -60.
        assert energy_from_levels([-60.0] * 9 + [-20.0]) == pytest.approx(4.0)


class TestWakeLevelRing:
    def test_block_size_independence(self):
        rng = np.random.default_rng(7)
        samples = (rng.standard_normal(320 * 5) * 0.1).astype(np.float32)
        ring_a = WakeLevelRing()
        ring_a.feed(samples)
        ring_b = WakeLevelRing()
        # Feed in awkward chunks, including sub-frame sizes.
        for chunk in (samples[:100], samples[100:500], samples[500:1000], samples[1000:]):
            ring_b.feed(chunk)
        assert ring_a.snapshot() == ring_b.snapshot()
        assert len(ring_a.snapshot()) == 5

    def test_window_capped(self):
        samples = np.full(320 * 200, 0.1, dtype=np.float32)
        ring = WakeLevelRing()
        ring.feed(samples)
        assert len(ring.snapshot()) == wa._LEVELS_WINDOW_FRAMES

    def test_feed_carries_remainder(self):
        ring = WakeLevelRing()
        ring.feed(np.zeros(100, dtype=np.float32))
        assert ring.snapshot() == []
        ring.feed(np.zeros(220, dtype=np.float32))
        assert len(ring.snapshot()) == 1

    def test_wake_energy_end_to_end(self):
        # 2.8 s of quiet, then 0.2 s at -20 dBFS: energy ≈ 40 dB.
        quiet = np.full(320 * 140, 1e-3, dtype=np.float32)  # ≈ -60 dBFS
        loud = np.full(320 * 10, 0.1, dtype=np.float32)  # ≈ -20 dBFS
        ring = WakeLevelRing(max_frames=150)
        ring.feed(np.concatenate([quiet, loud]))
        energy = ring.wake_energy()
        assert energy is not None
        assert energy == pytest.approx(40.0, abs=0.5)


# ---------------------------------------------------------------------------
# Arbiter lifecycle
# ---------------------------------------------------------------------------


class TestArbiterLifecycle:
    def test_start_opens_transport(self):
        transport = FakeTransport()
        arbiter = make_arbiter(transport=transport)
        assert arbiter.running
        assert transport.opened
        # A hello went out at start.
        assert transport.broadcast_count() >= 1
        arbiter.stop()

    def test_stop_closes_transport(self):
        arbiter = make_arbiter()
        arbiter.stop()
        assert not arbiter.running
        assert arbiter._transport.closed

    def test_open_failure_fail_open(self):
        transport = FakeTransport()
        transport.open_ok = False
        arbiter = WakeArbiter(device_id="aaaa1111", transport=transport)
        arbiter.start()
        assert not arbiter.running
        # contend() while down answers without settling.
        result = arbiter.contend("okay nabu", 40.0, 0.05)
        assert result.won and result.rivals == 0

    def test_double_start_is_noop(self):
        transport = FakeTransport()
        arbiter = make_arbiter(transport=transport)
        count = transport.broadcast_count()
        arbiter.start()
        assert transport.broadcast_count() == count
        arbiter.stop()

    def test_port_constant_shared(self):
        assert ARBITRATION_PORT == 2330
        assert WakeArbiter.port == 2330


# ---------------------------------------------------------------------------
# Envelope validation / hello / peers
# ---------------------------------------------------------------------------


class TestPackets:
    def test_hello_learns_peer_and_replies(self):
        transport = FakeTransport()
        arbiter = make_arbiter(transport=transport)
        transport.packets.clear()
        transport.inject({"ks": "wake", "v": 1, "hello": True, "id": "bbbb2222"}, "10.0.0.9")
        assert "10.0.0.9" in arbiter._peers
        replies = [data for data, target in transport.packets if target == "10.0.0.9"]
        assert len(replies) == 1
        assert json.loads(replies[0])["hello"] is True
        arbiter.stop()

    def test_second_hello_gets_no_reply(self):
        transport = FakeTransport()
        arbiter = make_arbiter(transport=transport)
        transport.packets.clear()
        for _ in range(2):
            transport.inject({"ks": "wake", "v": 1, "hello": True, "id": "bbbb2222"}, "10.0.0.9")
        replies = [data for data, target in transport.packets if target == "10.0.0.9"]
        assert len(replies) == 1
        arbiter.stop()

    def test_stale_peer_pruned(self):
        arbiter = make_arbiter()
        arbiter._peers["10.0.0.1"] = wa._now_ms() - wa._PEER_TIMEOUT_MS - 1
        arbiter._learn("10.0.0.2")  # any learn prunes stale entries
        assert "10.0.0.1" not in arbiter._peers
        arbiter.stop()

    @pytest.mark.parametrize(
        "msg",
        [
            {"v": 1, "id": "bbbb2222", "n": 1, "p": "okay nabu", "e": 40.0},  # missing ks
            {"ks": "other", "v": 1, "id": "bbbb2222", "n": 1, "p": "x", "e": 40.0},  # wrong ks
            {"ks": "wake", "v": 2, "id": "bbbb2222", "n": 1, "p": "x", "e": 40.0},  # wrong v
            {"ks": "wake", "v": 1, "id": "aaaa1111", "n": 1, "p": "x", "e": 40.0},  # own id
            {"ks": "wake", "v": 1, "id": "b" * 65, "n": 1, "p": "x", "e": 40.0},  # id too long
            {"ks": "wake", "v": 1, "id": 7, "n": 1, "p": "x", "e": 40.0},  # id not str
            {"ks": "wake", "v": 1, "id": "bbbb2222", "n": 1, "p": 5, "e": 40.0},  # phrase not str
            {"ks": "wake", "v": 1, "id": "bbbb2222", "n": 1, "p": "x", "e": True},  # bool energy
            {"ks": "wake", "v": 1, "id": "bbbb2222", "n": 1, "p": "x", "e": "40"},  # str energy
            {"ks": "wake", "v": 1, "id": "bbbb2222", "n": True, "p": "x", "e": 40.0},  # bool nonce
        ],
    )
    def test_invalid_envelopes_ignored(self, msg):
        arbiter = make_arbiter()
        # inject directly through the on_packet callback
        arbiter._transport.on_packet(json.dumps(msg).encode(), "10.0.0.9")
        assert arbiter._claims == {}
        arbiter.stop()

    def test_nan_energy_ignored(self):
        arbiter = make_arbiter()
        raw = json.dumps({"ks": "wake", "v": 1, "id": "bbbb2222", "n": 1, "p": "x", "e": float("nan")})
        arbiter._transport.on_packet(raw.encode(), "10.0.0.9")
        assert arbiter._claims == {}
        arbiter.stop()

    def test_garbage_bytes_ignored(self):
        arbiter = make_arbiter()
        arbiter._transport.on_packet(b"\xff\xfe not json", "10.0.0.9")
        arbiter._transport.on_packet(b"[1, 2, 3]", "10.0.0.9")
        assert arbiter._claims == {}
        arbiter.stop()

    def test_claim_recorded_and_normalized(self):
        arbiter = make_arbiter()
        arbiter._transport.inject(FakeTransport.claim_packet("bbbb2222", "  Okay   Nabu ", 40.04))
        (claim,) = arbiter._claims.values()
        assert claim.phrase == "okay nabu"
        assert claim.energy == pytest.approx(40.0)
        assert claim.from_addr == "192.168.1.50"
        arbiter.stop()

    def test_duplicate_nonce_dedup_keeps_first_arrival(self):
        arbiter = make_arbiter()
        packet = FakeTransport.claim_packet("bbbb2222", "okay nabu", 40.0, nonce=7)
        arbiter._transport.inject(packet, "10.0.0.9")
        # A repeat copy from a different address must not create a rival.
        arbiter._transport.on_packet(json.dumps(packet).encode(), "10.0.0.8")
        assert len(arbiter._claims) == 1
        (claim,) = arbiter._claims.values()
        assert claim.from_addr == "10.0.0.9"
        arbiter.stop()


# ---------------------------------------------------------------------------
# Contend: the decision rule
# ---------------------------------------------------------------------------


class TestContend:
    def test_win_with_no_rivals(self):
        arbiter = make_arbiter()
        result = arbiter.contend("okay nabu", 40.0, 0.05)
        assert result.won and result.rivals == 0
        arbiter.stop()

    def test_claims_sent_repeatedly_broadcast_and_unicast(self):
        transport = FakeTransport()
        arbiter = make_arbiter(transport=transport)
        arbiter._peers["10.0.0.9"] = wa._now_ms()
        arbiter._peers["10.0.0.8"] = wa._now_ms()
        transport.packets.clear()
        arbiter.contend("okay nabu", 40.0, 0.05)
        broadcasts = transport.broadcast_count()
        assert broadcasts == 3  # initial + two repeats
        to_9 = sum(1 for _, t in transport.packets if t == "10.0.0.9")
        to_8 = sum(1 for _, t in transport.packets if t == "10.0.0.8")
        assert to_9 == 3 and to_8 == 3
        arbiter.stop()

    def test_loss_to_louder_rival(self):
        arbiter = make_arbiter(device_id="aaaa1111")
        arbiter._transport.inject(FakeTransport.claim_packet("bbbb2222", "okay nabu", 45.0))
        result = arbiter.contend("okay nabu", 40.0, 0.05)
        assert not result.won
        assert result.rivals == 1
        assert result.winner == "192.168.1.50"
        assert result.winner_energy == pytest.approx(45.0)
        assert any("45" in line for line in result.heard)
        arbiter.stop()

    def test_win_against_quieter_rival(self):
        arbiter = make_arbiter(device_id="aaaa1111")
        arbiter._transport.inject(FakeTransport.claim_packet("bbbb2222", "okay nabu", 35.0))
        result = arbiter.contend("okay nabu", 40.0, 0.05)
        assert result.won
        assert result.rivals == 1
        arbiter.stop()

    def test_exact_tie_lower_id_wins(self):
        # Rival id sorts before ours: equal energy, rival wins.
        arbiter = make_arbiter(device_id="ffff0000")
        arbiter._transport.inject(FakeTransport.claim_packet("aaaa0000", "okay nabu", 40.0))
        result = arbiter.contend("okay nabu", 40.0, 0.05)
        assert not result.won
        arbiter.stop()

    def test_exact_tie_higher_id_loses_rival(self):
        arbiter = make_arbiter(device_id="aaaa0000")
        arbiter._transport.inject(FakeTransport.claim_packet("ffff0000", "okay nabu", 40.0))
        result = arbiter.contend("okay nabu", 40.0, 0.05)
        assert result.won
        arbiter.stop()

    def test_phrase_normalization_matches(self):
        arbiter = make_arbiter()
        arbiter._transport.inject(FakeTransport.claim_packet("bbbb2222", "  OKAY   NABU ", 45.0))
        result = arbiter.contend("okay nabu", 40.0, 0.05)
        assert not result.won
        arbiter.stop()

    def test_different_phrase_not_a_rival(self):
        arbiter = make_arbiter()
        arbiter._transport.inject(FakeTransport.claim_packet("bbbb2222", "hey jarvis", 45.0))
        result = arbiter.contend("okay nabu", 40.0, 0.05)
        assert result.won and result.rivals == 0
        arbiter.stop()

    def test_rival_outside_window_ignored(self):
        arbiter = make_arbiter(device_id="aaaa1111")
        original_now = wa._now_ms
        state = {"calls": 0}

        def fake_now():
            state["calls"] += 1
            # _on_packet calls _now_ms three times while recording a claim
            # (learn, prune, arrival); everything after that is "later".
            if state["calls"] <= 3:
                return original_now()  # claim injection
            return original_now() + 10_000  # contend sees it as old news

        with patch.object(wa, "_now_ms", fake_now):
            arbiter._transport.inject(FakeTransport.claim_packet("bbbb2222", "okay nabu", 45.0))
            result = arbiter.contend("okay nabu", 40.0, 0.05)
        assert result.won and result.rivals == 0
        arbiter.stop()


# ---------------------------------------------------------------------------
# Overtaken: a louder late claim cancels the winner's turn
# ---------------------------------------------------------------------------


class TestOvertaken:
    def _settled_result(self, arbiter, window_s=0.1):
        """A won contend whose claimed_at is far enough in the past that
        overtaken() does not sleep."""
        result = arbiter.contend("okay nabu", 40.0, 0.05)
        span_ms = int(window_s * 1000)
        result.claimed_at = wa._now_ms() - 3 * span_ms
        return result

    def test_no_late_claim_returns_none(self):
        arbiter = make_arbiter(device_id="aaaa1111")
        result = self._settled_result(arbiter)
        assert arbiter.overtaken(result, 0.1) is None
        arbiter.stop()

    def test_lost_result_returns_none(self):
        arbiter = make_arbiter(device_id="aaaa1111")
        result = wa.ArbitrationResult(won=False, rivals=1)
        assert arbiter.overtaken(result, 0.1) is None
        arbiter.stop()

    def test_louder_late_claim_detected(self):
        arbiter = make_arbiter(device_id="aaaa1111")
        result = self._settled_result(arbiter)
        span_ms = 100
        # A louder claim inside (window, 2*window] after the win.
        late = FakeTransport.claim_packet("bbbb2222", "okay nabu", 55.0, nonce=9)
        with patch.object(wa, "_now_ms", lambda: result.claimed_at + 150):
            arbiter._transport.on_packet(json.dumps(late).encode(), "10.0.0.7")
        late_claim = arbiter.overtaken(result, window_s=span_ms / 1000.0)
        assert late_claim is not None
        assert late_claim.from_addr == "10.0.0.7"
        assert late_claim.energy == pytest.approx(55.0)
        arbiter.stop()

    def test_quieter_late_claim_ignored(self):
        arbiter = make_arbiter(device_id="aaaa1111")
        result = self._settled_result(arbiter)
        late = FakeTransport.claim_packet("bbbb2222", "okay nabu", 30.0, nonce=9)
        with patch.object(wa, "_now_ms", lambda: result.claimed_at + 150):
            arbiter._transport.on_packet(json.dumps(late).encode(), "10.0.0.7")
        assert arbiter.overtaken(result, 0.1) is None
        arbiter.stop()

    def test_claim_at_window_boundary_not_late(self):
        arbiter = make_arbiter(device_id="aaaa1111")
        result = self._settled_result(arbiter)
        # exactly one window after the win: still inside the first settle,
        # so it is not a "late" claim and must not trigger stand-down here.
        late = FakeTransport.claim_packet("bbbb2222", "okay nabu", 55.0, nonce=9)
        with patch.object(wa, "_now_ms", lambda: result.claimed_at + 100):
            arbiter._transport.on_packet(json.dumps(late).encode(), "10.0.0.7")
        assert arbiter.overtaken(result, 0.1) is None
        arbiter.stop()

    def test_claim_too_late_ignored(self):
        arbiter = make_arbiter(device_id="aaaa1111")
        result = self._settled_result(arbiter)
        late = FakeTransport.claim_packet("bbbb2222", "okay nabu", 55.0, nonce=9)
        with patch.object(wa, "_now_ms", lambda: result.claimed_at + 250):
            arbiter._transport.on_packet(json.dumps(late).encode(), "10.0.0.7")
        assert arbiter.overtaken(result, 0.1) is None
        arbiter.stop()


# ---------------------------------------------------------------------------
# Real UDP transport (loopback)
# ---------------------------------------------------------------------------


class TestUdpTransport:
    def _free_udp_port(self):
        import socket as pysocket

        sock = pysocket.socket(pysocket.AF_INET, pysocket.SOCK_DGRAM)
        sock.bind(("", 0))
        port = sock.getsockname()[1]
        sock.close()
        return port

    def test_loopback_delivery(self):
        port = self._free_udp_port()
        transport = UdpArbitrationTransport(port=port)
        received = []
        done = threading.Event()

        def on_packet(data, addr):
            received.append((data, addr))
            done.set()

        try:
            assert transport.open(on_packet) is True
            transport.send_to(b'{"ks": "wake"}', "127.0.0.1")
            assert done.wait(2.0), "packet not delivered on loopback"
            data, addr = received[0]
            assert json.loads(data)["ks"] == "wake"
            assert addr == "127.0.0.1"
        finally:
            transport.close()
        assert transport._socket is None

    def test_bind_failure_returns_false(self):
        import socket as pysocket

        blocker = pysocket.socket(pysocket.AF_INET, pysocket.SOCK_DGRAM)
        blocker.bind(("", 0))
        blocker.setsockopt(pysocket.SOL_SOCKET, pysocket.SO_REUSEADDR, 0)
        port = blocker.getsockname()[1]
        transport = UdpArbitrationTransport(port=port)
        try:
            assert transport.open(lambda data, addr: None) is False
            assert transport._socket is None
        finally:
            blocker.close()
            transport.close()

    def test_send_without_open_is_silent(self):
        transport = UdpArbitrationTransport(port=self._free_udp_port())
        transport.send(b"{}")  # must not raise
        transport.send_to(b"{}", "127.0.0.1")
        transport.close()


# ---------------------------------------------------------------------------
# Satellite integration
# ---------------------------------------------------------------------------


class TestSatelliteArbitration:
    def _sat(self, tmp_path, **prefs):
        sat = make_satellite(tmp_path)
        for key, value in prefs.items():
            setattr(sat.state.preferences, key, value)
        return sat

    def test_window_clamped(self, tmp_path):
        sat = self._sat(tmp_path, wake_arbitration_window_ms=50)
        assert sat._arbitration_window() == pytest.approx(0.1)
        sat.state.preferences.wake_arbitration_window_ms = 900
        assert sat._arbitration_window() == pytest.approx(0.5)
        sat.state.preferences.wake_arbitration_window_ms = 400
        assert sat._arbitration_window() == pytest.approx(0.4)

    def test_window_ms_helper_clamped(self, tmp_path):
        sat = self._sat(tmp_path, wake_arbitration_window_ms=700)
        assert sat._arbitration_window_ms() == 500

    def test_set_window_persists_and_clamps(self, tmp_path):
        sat = self._sat(tmp_path)
        sat._set_wake_arbitration_window(700)
        assert sat.state.preferences.wake_arbitration_window_ms == 500
        sat._set_wake_arbitration_window(250)
        assert sat.state.preferences.wake_arbitration_window_ms == 250
        import json as _json

        saved = _json.loads(sat.state.preferences_path.read_text())
        assert saved["wake_arbitration_window_ms"] == 250

    def test_set_offset_persists_and_clamps(self, tmp_path):
        sat = self._sat(tmp_path)
        sat._set_wake_arbitration_offset(-45)
        assert sat.state.preferences.wake_arbitration_offset_db == -30.0
        sat._set_wake_arbitration_offset(6.4)
        assert sat.state.preferences.wake_arbitration_offset_db == pytest.approx(6.4)

    def test_enable_switch_starts_and_stops_arbiter(self, tmp_path):
        sat = self._sat(tmp_path)
        fake_arbiter = MagicMock()
        fake_arbiter.running = False

        def _start():
            fake_arbiter.running = True

        fake_arbiter.start.side_effect = _start
        with patch("linux_voice_assistant.satellite.WakeArbiter", return_value=fake_arbiter):
            sat._set_wake_arbitration_enabled(True)
        assert sat.state.wake_arbiter is fake_arbiter
        fake_arbiter.start.assert_called_once()
        assert sat.state.preferences.wake_arbitration_enabled is True

        sat._set_wake_arbitration_enabled(False)
        fake_arbiter.stop.assert_called_once()
        assert sat.state.preferences.wake_arbitration_enabled is False

    def test_enable_switch_left_off_when_start_fails(self, tmp_path):
        sat = self._sat(tmp_path)
        fake_arbiter = MagicMock()
        fake_arbiter.running = False  # start() did not bring it up
        with patch("linux_voice_assistant.satellite.WakeArbiter", return_value=fake_arbiter):
            sat._set_wake_arbitration_enabled(True)
        assert sat.state.preferences.wake_arbitration_enabled is False

    def test_arbitrate_skips_without_arbiter(self, tmp_path):
        sat = self._sat(tmp_path)
        wake_word = MagicMock()
        wake_word.wake_word = "okay nabu"
        assert sat._arbitrate(wake_word) is None

    def test_arbitrate_skips_without_levels(self, tmp_path):
        sat = self._sat(tmp_path)
        sat.state.wake_arbiter = MagicMock(running=True)
        sat.state.wake_levels = None
        wake_word = MagicMock()
        wake_word.wake_word = "okay nabu"
        assert sat._arbitrate(wake_word) is None

    def test_arbitrate_applies_offset_and_rounding(self, tmp_path):
        sat = self._sat(tmp_path, wake_arbitration_offset_db=1.2)
        sat.state.wake_levels = MagicMock()
        sat.state.wake_levels.wake_energy.return_value = 38.0
        arbiter = MagicMock()
        arbiter.running = True
        arbiter.contend.return_value = wa.ArbitrationResult(won=True, rivals=0, claimed_at=wa._now_ms())
        sat.state.wake_arbiter = arbiter
        wake_word = MagicMock()
        wake_word.wake_word = "okay nabu"

        with patch("linux_voice_assistant.satellite.threading.Thread") as thread_mock:
            result = sat._arbitrate(wake_word)
        assert result.won
        arbiter.contend.assert_called_once()
        kwargs = arbiter.contend.call_args.kwargs
        # 38.0 + 1.2 = 39.2, rounded on the wire to a tenth
        assert kwargs["energy"] == pytest.approx(39.2)
        assert kwargs["phrase"] == "okay nabu"
        assert kwargs["window_s"] == pytest.approx(0.4)
        thread_mock.assert_called_once()  # overtaken watcher armed on a win

    def test_wakeup_loser_returns_before_pipeline(self, tmp_path):
        sat = self._sat(tmp_path)
        sat.duck = MagicMock()
        sat._emit = MagicMock()
        lost = wa.ArbitrationResult(won=False, rivals=1, winner="10.0.0.9")
        wake_word = MagicMock()
        wake_word.wake_word = "okay nabu"
        with patch.object(sat, "_arbitrate", return_value=lost):
            sat.wakeup(wake_word)
        assert sat._pipeline_active is False
        sat.duck.assert_not_called()
        sat._emit.assert_not_called()

    def test_wakeup_winner_clears_stand_down(self, tmp_path):
        sat = self._sat(tmp_path)
        sat._arbitration_stand_down = True
        sat.duck = MagicMock()
        sat._emit = MagicMock()
        sat.state.tts_player = MagicMock()
        won = wa.ArbitrationResult(won=True, rivals=0, claimed_at=wa._now_ms())
        wake_word = MagicMock()
        wake_word.wake_word = "okay nabu"
        with patch.object(sat, "_arbitrate", return_value=won):
            sat.wakeup(wake_word)
        assert sat._arbitration_stand_down is False
        assert sat._pipeline_active is True
        sat.duck.assert_called_once()

    def test_stand_down_blocks_streaming_restart(self, tmp_path):
        sat = self._sat(tmp_path)
        sat._arbitration_stand_down = True
        sat._is_streaming_audio = True
        sat._pipeline_active = True
        sat.state.tts_player = MagicMock()
        sat.unduck = MagicMock()
        sat._emit = MagicMock()

        sat._stand_down()
        assert sat._is_streaming_audio is False
        assert sat._pipeline_active is False
        sat.state.tts_player.stop.assert_called_once()
        sat.unduck.assert_called_once()

        # The chime's done_callback fires from stop(): the flag keeps it
        # from restarting the turn.
        sat._on_wakeup_sound_finished("okay nabu")
        sat._writelines.assert_not_called()

        sat._start_audio_streaming("okay nabu")
        assert sat._is_streaming_audio is False
