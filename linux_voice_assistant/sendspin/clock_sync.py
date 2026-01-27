"""Kalman filter-based clock synchronization for Sendspin Protocol.

This module implements a 2-state Kalman filter that tracks:

1) Clock offset (local_time_us - server_time_us) in microseconds
2) Clock drift rate in microseconds per second (us/s)

It is designed for Sendspin's NTP-like time sync messages and for converting
server timestamps on audio frames into local time for scheduling.

Design goals
------------
- **Monotonic clocks only**: Does not require OS NTP or wall-clock agreement.
- **Robust startup**: Initializes offset from the first measurement to avoid
  injecting an absurd drift estimate while the initial offset is large.
- **Jitter-aware**: Measurement noise adapts to RTT (higher RTT => noisier).
- **Outlier resistance**: Optional innovation gating to ignore pathological
  samples (e.g., packet reordering or timestamp glitches).
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from typing import Optional

_LOGGER = logging.getLogger(__name__)


@dataclass
class ClockSyncStats:
    """Statistics about clock synchronization quality."""

    offset_us: float
    drift_ppm: float
    # Uncertainty of the offset estimate (us^2)
    variance: float
    # Standard deviation of the offset estimate (us)
    offset_stddev_us: float
    samples: int
    last_rtt_us: int
    # EWMA RTT estimate (us)
    ewma_rtt_us: float
    # EWMA jitter estimate derived from RTT deltas (us)
    ewma_jitter_us: float
    # EWMA magnitude of recent residuals (us)
    last_std_us: float
    # Convenience: whether we're "good enough" for multi-room scheduling
    synced: bool


class KalmanClockSync:
    """Kalman filter for clock offset and drift estimation.

    Notes
    -----
    - The filter assumes *microseconds* for all timestamps.
    - Drift is modeled in us/s, which is numerically equivalent to ppm when
      expressed as microseconds per second (1 ppm == 1 us/s).
    """

    def __init__(
        self,
        *,
        # Initial state
        initial_offset_us: float = 0.0,
        initial_offset_variance: float = 1e12,
        initial_drift_us_per_s: float = 0.0,
        initial_drift_variance: float = 1e2,
        # Process noise (how quickly we believe offset/drift can change)
        process_noise_offset: float = 5e4,
        process_noise_drift: float = 5e-1,
        # Base measurement noise (network jitter), in us^2
        measurement_noise: float = 1e8,
        # EWMA for measurement std estimate
        ewma_alpha: float = 0.05,
        # Sanity clamps
        max_abs_drift_ppm: float = 2000.0,
        # Innovation gating (outlier rejection)
        innovation_gate_sigma: float = 8.0,
        gate_after_samples: int = 6,
    ) -> None:
        # State estimate x = [offset_us, drift_us_per_s]
        self._x0: float = float(initial_offset_us)
        self._x1: float = float(initial_drift_us_per_s)

        # Covariance P (2x2) stored as scalars
        self._p00: float = float(initial_offset_variance)
        self._p01: float = 0.0
        self._p10: float = 0.0
        self._p11: float = float(initial_drift_variance)

        # Noises
        self._q_offset: float = float(process_noise_offset)
        self._q_drift: float = float(process_noise_drift)
        self._r_base: float = float(measurement_noise)

        # EWMA on measurement residual magnitude (used for reporting)
        self._ewma_alpha: float = max(0.001, min(0.5, float(ewma_alpha)))
        self._ewma_std_us: float = 0.0

        # EWMA RTT / jitter (for diagnostics and scheduling)
        self._ewma_rtt_us: float = 0.0
        self._ewma_jitter_us: float = 0.0

        # Constraints
        self._max_abs_drift_ppm: float = max(0.0, float(max_abs_drift_ppm))
        self._gate_sigma: float = max(0.0, float(innovation_gate_sigma))
        self._gate_after: int = max(0, int(gate_after_samples))

        # State tracking
        self._last_update_us: Optional[int] = None
        self._samples: int = 0
        self._last_rtt_us: int = 0

    # ------------------------------------------------------------------
    # Time helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _monotonic_us() -> int:
        return int(time.monotonic_ns() // 1000)

    @staticmethod
    def _calc_rtt_offset(
        *,
        t1: int,
        t2: int,
        t3: int,
        t4: int,
    ) -> tuple[int, float]:
        """Return (rtt_us, measured_offset_us).

        Sendspin uses an NTP-like exchange:

        - RTT = (t4 - t1) - (t3 - t2)
        - offset = ((t1 - t2) + (t4 - t3)) / 2

        Offset is defined as local - server.
        """
        rtt = (t4 - t1) - (t3 - t2)
        rtt = max(0, int(rtt))
        offset = ((t1 - t2) + (t4 - t3)) / 2.0
        return rtt, float(offset)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update(
        self,
        *,
        client_transmitted_us: int,
        server_received_us: int,
        server_transmitted_us: int,
        client_received_us: int,
    ) -> None:
        """Update the filter with a new time sync measurement."""

        t1 = int(client_transmitted_us)
        t2 = int(server_received_us)
        t3 = int(server_transmitted_us)
        t4 = int(client_received_us)

        if t1 <= 0 or t2 <= 0 or t3 <= 0 or t4 <= 0:
            return

        now_us = self._monotonic_us()
        rtt_us, z = self._calc_rtt_offset(t1=t1, t2=t2, t3=t3, t4=t4)
        self._last_rtt_us = rtt_us

        # Update RTT/jitter EWMAs (for visibility + gating heuristics)
        if self._samples == 0:
            self._ewma_rtt_us = float(rtt_us)
            self._ewma_jitter_us = 0.0
        else:
            prev_rtt = float(self._ewma_rtt_us)
            self._ewma_rtt_us = (1.0 - self._ewma_alpha) * prev_rtt + self._ewma_alpha * float(rtt_us)
            jitter = abs(float(rtt_us) - prev_rtt)
            self._ewma_jitter_us = (1.0 - self._ewma_alpha) * float(self._ewma_jitter_us) + self._ewma_alpha * jitter

        # Adaptive measurement noise: base + (rtt/2)^2
        r_meas = self._r_base + (float(rtt_us) ** 2) / 4.0
        r_meas = max(1.0, r_meas)

        # Robust startup: initialize offset from the first measurement.
        if self._samples == 0 or self._last_update_us is None:
            self._x0 = float(z)
            self._x1 = 0.0
            self._p00 = r_meas
            self._p01 = 0.0
            self._p10 = 0.0
            # Keep drift variance modest; we will learn it over time.
            self._p11 = max(self._p11, 1.0)
            self._last_update_us = now_us
            self._samples = 1
            self._ewma_std_us = math.sqrt(r_meas)
            _LOGGER.debug(
                "ClockSync: init offset=%.1fus drift=%.3fppm rtt=%dus std=%.1fus",
                self._x0,
                self._x1,
                rtt_us,
                self._ewma_std_us,
            )
            return

        # Time since last update
        dt_s = (now_us - self._last_update_us) / 1_000_000.0
        # Clamp dt to keep the filter numerically stable if scheduling hiccups
        dt_s = max(0.001, min(10.0, float(dt_s)))

        # ---------------- Prediction ----------------
        # x0' = x0 + x1 * dt
        x0p = self._x0 + self._x1 * dt_s
        x1p = self._x1

        # P' = F P F^T + Q
        # F = [[1, dt],[0,1]]
        p00p = self._p00 + dt_s * (self._p10 + self._p01) + (dt_s * dt_s) * self._p11 + self._q_offset * dt_s
        p01p = self._p01 + dt_s * self._p11
        p10p = self._p10 + dt_s * self._p11
        p11p = self._p11 + self._q_drift * dt_s

        # ---------------- Update ----------------
        # Measurement model: z = x0 + v
        y = float(z) - x0p  # innovation
        s = p00p + r_meas  # innovation covariance
        s = max(1.0, float(s))

        # Optional innovation gate: ignore pathological samples.
        if self._gate_sigma > 0.0 and self._samples >= self._gate_after:
            gate = self._gate_sigma * math.sqrt(s)
            if abs(y) > gate:
                # Still advance time/variance so the filter doesn't freeze.
                self._x0, self._x1 = x0p, x1p
                self._p00, self._p01, self._p10, self._p11 = p00p, p01p, p10p, p11p
                self._last_update_us = now_us
                self._samples += 1
                # Track residual magnitude for visibility
                self._ewma_std_us = (1 - self._ewma_alpha) * self._ewma_std_us + self._ewma_alpha * abs(y)
                _LOGGER.debug(
                    "ClockSync: gated sample (|y|=%.0fus > %.0fus) rtt=%dus",
                    abs(y),
                    gate,
                    rtt_us,
                )
                return

        k0 = p00p / s
        k1 = p10p / s

        self._x0 = x0p + k0 * y
        self._x1 = x1p + k1 * y

        # Clamp drift to a sane range (ppm == us/s)
        if self._max_abs_drift_ppm > 0:
            self._x1 = max(-self._max_abs_drift_ppm, min(self._max_abs_drift_ppm, self._x1))

        # Joseph form isn't necessary here; do standard covariance update.
        p00 = (1 - k0) * p00p
        p01 = (1 - k0) * p01p
        p10 = p10p - k1 * p00p
        p11 = p11p - k1 * p01p

        # Keep covariance positive-ish
        self._p00 = max(1.0, float(p00))
        self._p01 = float(p01)
        self._p10 = float(p10)
        self._p11 = max(0.01, float(p11))

        self._last_update_us = now_us
        self._samples += 1

        # Update EWMA residual magnitude (for reporting)
        # Approximate residual std: sqrt(max(r_meas, y^2))
        resid = max(math.sqrt(r_meas), abs(y))
        self._ewma_std_us = (1 - self._ewma_alpha) * self._ewma_std_us + self._ewma_alpha * resid

        _LOGGER.debug(
            "ClockSync: offset=%.1fus drift=%.3fppm rtt=%dus std=%.1fus",
            self.offset_us,
            self.drift_ppm,
            rtt_us,
            self._ewma_std_us,
        )

    def server_to_local(self, server_time_us: int) -> int:
        """Convert server timestamp (us) to estimated local timestamp (us)."""
        st = int(server_time_us)
        return st + int(self.offset_us)

    def local_to_server(self, local_time_us: int) -> int:
        """Convert local timestamp (us) to estimated server timestamp (us)."""
        lt = int(local_time_us)
        return lt - int(self.offset_us)

    def get_local_time_us(self) -> int:
        return self._monotonic_us()

    def get_current_server_time_us(self) -> int:
        return self.local_to_server(self.get_local_time_us())

    @property
    def offset_us(self) -> float:
        """Current estimated offset (local - server) in microseconds."""
        if self._last_update_us is None:
            return self._x0
        dt_s = (self._monotonic_us() - self._last_update_us) / 1_000_000.0
        dt_s = max(0.0, min(10.0, float(dt_s)))
        return self._x0 + self._x1 * dt_s

    @property
    def drift_ppm(self) -> float:
        """Estimated drift in parts-per-million (ppm).

        Internally drift is in us/s, which is numerically equal to ppm.
        """
        return float(self._x1)

    @property
    def variance(self) -> float:
        """Uncertainty (variance) of the offset estimate in us^2."""
        return float(self._p00)

    @property
    def samples(self) -> int:
        return int(self._samples)

    @property
    def last_rtt_us(self) -> int:
        return int(self._last_rtt_us)

    @property
    def last_std_us(self) -> float:
        return float(self._ewma_std_us)

    @property
    def ewma_rtt_us(self) -> float:
        return float(self._ewma_rtt_us)

    @property
    def ewma_jitter_us(self) -> float:
        return float(self._ewma_jitter_us)

    @property
    def is_synced(self) -> bool:
        """True when we have enough samples and variance is below ~1ms^2."""
        return self._samples >= 6 and self._p00 < 1e6

    @property
    def sync_quality(self) -> str:
        if self._samples < 2:
            return "not_synced"
        if self._p00 > 1e9:
            return "poor"
        if self._p00 > 1e6:
            return "fair"
        return "good"

    def get_stats(self) -> ClockSyncStats:
        var = self.variance
        std = math.sqrt(var) if var > 0 else 0.0
        return ClockSyncStats(
            offset_us=self.offset_us,
            drift_ppm=self.drift_ppm,
            variance=var,
            offset_stddev_us=float(std),
            samples=self.samples,
            last_rtt_us=self.last_rtt_us,
            ewma_rtt_us=self.ewma_rtt_us,
            ewma_jitter_us=self.ewma_jitter_us,
            last_std_us=self.last_std_us,
            synced=self.is_synced,
        )

    def reset(self) -> None:
        self._x0 = 0.0
        self._x1 = 0.0
        self._p00 = 1e12
        self._p01 = 0.0
        self._p10 = 0.0
        self._p11 = 1e2
        self._last_update_us = None
        self._samples = 0
        self._last_rtt_us = 0
        self._ewma_std_us = 0.0
        self._ewma_rtt_us = 0.0
        self._ewma_jitter_us = 0.0

