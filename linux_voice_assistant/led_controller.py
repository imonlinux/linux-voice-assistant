import asyncio
import logging
from typing import Any, Tuple

import board
import adafruit_dotstar

from .event_bus import EventHandler, subscribe

_LOGGER = logging.getLogger(__name__)

# Constants for ReSpeaker 2-Mic Hat
NUM_LEDS = 3
_OFF = (0, 0, 0)
_BLUE = (0, 0, 255)
_YELLOW = (255, 255, 0)
_GREEN = (0, 255, 0)
_DIM_RED = (50, 0, 0) # ADDED for Mute


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

    # --- EVENT HANDLERS ---
    
    @subscribe
    def ha_connected(self, data: dict):
        _LOGGER.debug("HA Connected, setting idle LED state.")
        self.voice_run_end(data)

    @subscribe
    def voice_wakeword(self, data: dict):
        _LOGGER.debug("LED Event: voice_wakeword")
        self.run_action("pulse", _BLUE)
    
    @subscribe
    def voice_stt_start(self, data: dict):
        _LOGGER.debug("LED Event: voice_stt_start (listening)")
        self.run_action("pulse", _BLUE)

    @subscribe
    def voice_vad_start(self, data: dict):
        _LOGGER.debug("LED Event: voice_vad_start (user speaking)")
        pass

    @subscribe
    def voice_stt_end(self, data: dict):
        _LOGGER.debug("LED Event: voice_stt_end (processing)")
        self.run_action("pulse", _YELLOW)

    @subscribe
    def voice_tts_start(self, data: dict):
        _LOGGER.debug("LED Event: voice_tts_start (assistant speaking)")
        self.run_action("pulse", _GREEN)

    @subscribe
    def voice_run_end(self, data: dict):
        _LOGGER.debug("LED Event: voice_run_end")
        self.run_action("color", _OFF)
        
    # --- ADDED: Mute State Handlers ---
    @subscribe
    def mic_muted(self, data: dict):
        _LOGGER.debug("LED Event: Mic muted")
        self.run_action("color", _DIM_RED)

    @subscribe
    def mic_unmuted(self, data: dict):
        _LOGGER.debug("LED Event: Mic unmuted")
        # Return to the normal idle state (off)
        self.voice_run_end(data)
