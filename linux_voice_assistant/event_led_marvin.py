
import logging
from typing import Any, Tuple, Optional

from .event_bus import EventHandler, subscribe

_LOGGER = logging.getLogger(__name__)

from math import ceil
import asyncio
import spidev

NUM_LEDS = 2
RGB_MAP = {
    "rgb": [3, 2, 1],
    "rbg": [3, 1, 2],
    "grb": [2, 3, 1],
    "gbr": [2, 1, 3],
    "brg": [1, 3, 2],
    "bgr": [1, 2, 3],
}

_OFF = (0, 0, 0)
_GREEN = (0, 255, 0)

# How long to keep "listening" after wakeword if user never starts speaking
LISTEN_IDLE_TIMEOUT_SEC = 15.0


class APA102:
    MAX_BRIGHTNESS = 0b11111
    LED_START = 0b11100000

    def __init__(
        self,
        num_led: int,
        global_brightness: int,
        loop: asyncio.AbstractEventLoop,
        order: str = "rgb",
        bus: int = 0,
        device: int = 1,
        max_speed_hz: int = 8000000,
    ):
        self.num_led = num_led
        order = order.lower()
        self.rgb = RGB_MAP.get(order, RGB_MAP["rgb"])
        self.global_brightness = min(global_brightness, self.MAX_BRIGHTNESS)
        _LOGGER.debug("LED brightness: %s", self.global_brightness)

        self.leds = [self.LED_START, 0, 0, 0] * self.num_led
        self.spi = spidev.SpiDev()
        self.spi.open(bus, device)
        if max_speed_hz:
            self.spi.max_speed_hz = max_speed_hz

        self.current_task: Optional[asyncio.Future] = None
        self.loop = loop

    def clock_start_frame(self):
        self.spi.xfer2([0] * 4)

    def clock_end_frame(self):
        self.spi.xfer2([0xFF] * 4)

    def set_pixel(self, led_num, red, green, blue, bright_percent=100):
        if led_num < 0 or led_num >= self.num_led:
            return
        brightness = int(ceil(bright_percent * self.global_brightness / 100.0))
        ledstart = (brightness & 0b00011111) | self.LED_START
        start_index = 4 * led_num
        self.leds[start_index] = ledstart
        self.leds[start_index + self.rgb[0]] = red
        self.leds[start_index + self.rgb[1]] = green
        self.leds[start_index + self.rgb[2]] = blue

    def show(self):
        self.clock_start_frame()
        data = list(self.leds)
        while data:
            self.spi.xfer2(data[:32])
            data = data[32:]
        self.clock_end_frame()

    def cleanup(self):
        self.spi.close()

    def run_action(self, action_method_name: str, *args: Any):
        # Cancel any currently running animation task (pulse/blink/etc.)
        if self.current_task and not self.current_task.done():
            self.current_task.cancel()
        self.current_task = asyncio.run_coroutine_threadsafe(
            getattr(self, action_method_name)(*args), self.loop
        )

    async def color(self, rgb, brightness=None):
        for i in range(NUM_LEDS):
            self.set_pixel(i, rgb[0], rgb[1], rgb[2], brightness or self.global_brightness)
        self.show()

    async def pulse(self, color, speed=0.02):
        """Pulse continuously until cancelled."""
        while True:
            for brightness in range(1, self.MAX_BRIGHTNESS + 1):
                await self.color(color, brightness)
                await asyncio.sleep(speed)
            for brightness in range(self.MAX_BRIGHTNESS, 0, -1):
                await self.color(color, brightness)
                await asyncio.sleep(speed)

    async def flash(self, flash_color, times, on_time=0.1, off_time=0.1):
        """Flash color on/off N times, then leave LEDs OFF (caller decides next state)."""
        for _ in range(times):
            await self.color(flash_color)
            await asyncio.sleep(on_time)
            await self.color(_OFF)
            await asyncio.sleep(off_time)

    async def flash_then_pulse(self, flash_color, times, pulse_color, pulse_speed=0.02):
        """Flash N times, then enter continuous pulse."""
        await self.flash(flash_color, times)
        await self.pulse(pulse_color, pulse_speed)


class LedEvent(EventHandler):
    def __init__(self, state):
        super().__init__(state)
        self.leds = APA102(num_led=3, global_brightness=31, loop=self.state.loop)
        self._base_color = _GREEN
        # Keep LEDs off until connected
        self.leds.run_action("color", _OFF)
        # Watchdog for the "no speech after wakeword" case
        self._listen_idle_watchdog: Optional[asyncio.Task] = None

    def _cancel_watchdog(self):
        if self._listen_idle_watchdog and not self._listen_idle_watchdog.done():
            self._listen_idle_watchdog.cancel()
        self._listen_idle_watchdog = None

    async def _listen_watchdog(self, timeout: float):
        try:
            await asyncio.sleep(timeout)
            _LOGGER.debug("Listen idle watchdog timeout -> return to base color")
            self.leds.run_action("color", self._base_color)
        except asyncio.CancelledError:
            pass

    # Connectivity ------------------------------------------------------------------
    @subscribe
    def ready(self, data: dict):
        _LOGGER.debug("ready -> solid GREEN")
        self._cancel_watchdog()
        self.leds.run_action("color", self._base_color)

    @subscribe
    def disconnected(self, data: dict):
        _LOGGER.debug("disconnected -> LEDs OFF")
        self._cancel_watchdog()
        self.leds.run_action("color", _OFF)

    # Wakeword ----------------------------------------------------------------------
    @subscribe
    def voice_wakeword(self, data: dict):
        _LOGGER.debug("wakeword -> fast GREEN flash x2, then enter listening (pulse)")
        # Start a watchdog: if user never speaks, stop pulsing after timeout
        self._cancel_watchdog()
        self._listen_idle_watchdog = self.state.loop.create_task(
            self._listen_watchdog(LISTEN_IDLE_TIMEOUT_SEC)
        )
        # Flash twice quickly, then pulse
        self.leds.run_action("flash_then_pulse", _GREEN, 2, _GREEN, 0.02)

    # Listening (VAD) ---------------------------------------------------------------
    @subscribe
    def voice_VOICE_ASSISTANT_STT_START(self, data: dict):
        _LOGGER.debug("VAD_START -> ensure GREEN pulsing (listening)")
        # User started speaking: cancel the idle watchdog and ensure pulse
        self._cancel_watchdog()
        self.leds.run_action("pulse", _GREEN, 0.02)

    @subscribe
    def voice_VOICE_ASSISTANT_STT_VAD_END(self, data: dict):
        _LOGGER.debug("VAD_END -> stop pulsing -> solid GREEN")
        self._cancel_watchdog()
        self.leds.run_action("color", self._base_color)

    # TTS (speaking) ----------------------------------------------------------------
    @subscribe
    def voice_play_tts(self, data: dict):
        _LOGGER.debug("TTS -> solid GREEN")
        self._cancel_watchdog()
        self.leds.run_action("color", self._base_color)

    @subscribe
    def voice__tts_finished(self, data: dict):
        _LOGGER.debug("TTS finished -> solid GREEN")
        self._cancel_watchdog()
        self.leds.run_action("color", self._base_color)

