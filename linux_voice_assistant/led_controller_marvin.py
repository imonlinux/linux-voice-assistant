import asyncio
import logging
from typing import Any, Tuple

import board
import adafruit_dotstar

from .event_bus import EventHandler, subscribe

_LOGGER = logging.getLogger(__name__)

# --- Marvin Theme Constants ---
NUM_LEDS = 2
_OFF = (0, 0, 0)
_GREEN = (0, 255, 0)


class LedController(EventHandler):
    def __init__(
        self,
        state,
        interface: str = "spi",
        clock_pin: int = 13,
        data_pin: int = 12,
    ):
        super().__init__(state)
        self.loop = state.loop
        self.current_task = None
        self._is_ready = False

        try:
            if interface == "gpio":
                _LOGGER.debug(f"Initializing LEDs on GPIO data={data_pin}, clock={clock_pin}")
                data_pin_obj = getattr(board, f"D{data_pin}")
                clock_pin_obj = getattr(board, f"D{clock_pin}")
                self.leds = adafruit_dotstar.DotStar(
                    clock_pin_obj, data_pin_obj, NUM_LEDS, brightness=0.5, auto_write=False
                )
            else:  # Default to SPI
                _LOGGER.debug("Initializing LEDs on hardware SPI")
                self.leds = adafruit_dotstar.DotStar(
                    board.SCLK, board.MOSI, NUM_LEDS, brightness=0.5, auto_write=False
                )
            
            self._is_ready = True
            _LOGGER.info(f"LED Controller initialized using {interface} interface.")
            # Signal that the system is ready
            self.run_action("blink", _GREEN, 2)

        except Exception:
            _LOGGER.exception("Failed to initialize LED controller. LEDs will be disabled.")

    def run_action(self, action_method_name: str, *args: Any) -> None:
        if not self._is_ready:
            return
        if self.current_task and not self.current_task.done():
            self.current_task.cancel()
        
        coro = getattr(self, action_method_name)(*args)
        self.current_task = asyncio.run_coroutine_threadsafe(coro, self.loop)

    async def color(self, rgb: Tuple[int, int, int]):
        if not self._is_ready:
            return
        self.leds.fill(rgb)
        self.leds.show()

    async def blink(self, color, count=3):
        for _ in range(count):
            await self.color(color)
            await asyncio.sleep(0.2)
            await self.color(_OFF)
            await asyncio.sleep(0.2)

    async def pulse(self, color: Tuple[int, int, int], speed: float = 0.015):
        try:
            r, g, b = color
            while True:
                for i in range(0, 101, 5):
                    brightness = i / 100.0
                    await self.color((int(r * brightness), int(g * brightness), int(b * brightness)))
                    await asyncio.sleep(speed)
                for i in range(100, -1, -5):
                    brightness = i / 100.0
                    await self.color((int(r * brightness), int(g * brightness), int(b * brightness)))
                    await asyncio.sleep(speed)
        except asyncio.CancelledError:
            await self.color(_OFF)

    async def fast_pulse(self, color: Tuple[int, int, int], speed: float = 0.008):
        """Faster version of the pulse animation."""
        try:
            r, g, b = color
            while True:
                for i in range(0, 101, 10):
                    brightness = i / 100.0
                    await self.color((int(r * brightness), int(g * brightness), int(b * brightness)))
                    await asyncio.sleep(speed)
                for i in range(100, -1, -10):
                    brightness = i / 100.0
                    await self.color((int(r * brightness), int(g * brightness), int(b * brightness)))
                    await asyncio.sleep(speed)
        except asyncio.CancelledError:
            await self.color(_OFF)


    # --- EVENT HANDLERS FOR MARVIN ---
    @subscribe
    def ha_connected(self, data: dict):
        _LOGGER.debug("HA Connected, setting idle LED state.")
        self.voice_run_end(data)

    @subscribe
    def voice_wakeword(self, data: dict):
        self.run_action("pulse", _GREEN)
    
    @subscribe
    def voice_stt_start(self, data: dict):
        self.run_action("pulse", _GREEN)

    @subscribe
    def voice_vad_start(self, data: dict):
        pass # Continues pulsing green

    @subscribe
    def voice_stt_end(self, data: dict):
        self.run_action("fast_pulse", _GREEN)

    @subscribe
    def voice_tts_start(self, data: dict):
        self.run_action("pulse", _GREEN)

    @subscribe
    def voice_run_end(self, data: dict):
        self.run_action("color", _GREEN) # Idle state is solid green
