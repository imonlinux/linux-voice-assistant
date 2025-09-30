# MODIFIED event_led.py

import logging
import asyncio
from typing import Tuple, Any

# --- MODIFICATION: Import new libraries and remove unused ones ---
import board
import adafruit_dotstar
from .event_bus import EventHandler, subscribe

_LOGGER = logging.getLogger(__name__)

# --- MODIFICATION: Make NUM_LEDS a configurable variable at the top ---
# Set this to the number of APA102 LEDs you have connected to the Grove port.
NUM_LEDS = 2 
GLOBAL_BRIGHTNESS = 1.0 # Brightness as a value from 0.0 to 1.0

# Define some color constants
_OFF = (0, 0, 0)
_WHITE = (255, 255, 255)
_RED = (255, 0, 0)
_YELLOW = (255, 255, 0)
_BLUE = (0, 0, 255)
_GREEN = (0, 255, 0)

# --- MODIFICATION: The old spidev-based APA102 class has been removed entirely ---

class LedEvent(EventHandler):
    def __init__(self, state):
        super().__init__(state)
        self.loop = self.state.loop
        self.current_task = None
        
        # --- MODIFICATION: Initialize LEDs using Adafruit DotStar and GPIO pins ---
        # The ReSpeaker 2-Mic HAT Grove connector uses GPIO13 (Clock) and GPIO12 (Data)
        # If your wiring is different, change board.D13 and board.D12 accordingly.
        try:
            self.leds = adafruit_dotstar.DotStar(
                clock=board.D13,
                data=board.D12,
                n=NUM_LEDS,
                brightness=GLOBAL_BRIGHTNESS,
                auto_write=False # We will call show() manually
            )
            _LOGGER.info(f"Initialized {NUM_LEDS} APA102 LEDs on GPIO12/13.")
        except Exception as e:
            _LOGGER.error(f"Failed to initialize DotStar LEDs: {e}")
            _LOGGER.error("Please ensure you have run 'pip install adafruit-circuitpython-dotstar' and that SPI is enabled if prompted.")
            self.leds = None # Prevent crashes if initialization fails


    def run_action(self, action_method_name: str, *args: Any) -> None:
        if not self.leds:
            _LOGGER.warning("LEDs not initialized, skipping action.")
            return

        if self.current_task and not self.current_task.done():
            self.current_task.cancel()

        self.current_task = asyncio.run_coroutine_threadsafe(getattr(self, action_method_name)(*args), self.loop)

    # --- MODIFICATION: Rewritten 'color' method to work with the new library ---
    async def color(self, rgb: Tuple[int, int, int], brightness_override: float = None) -> None:
        if brightness_override is not None:
            self.leds.brightness = brightness_override
        else:
            self.leds.brightness = GLOBAL_BRIGHTNESS
            
        self.leds.fill(rgb)
        self.leds.show()

    # --- MODIFICATION: Rewritten 'blink' method to be simpler ---
    async def blink(self, rgb: Tuple[int, int, int], count=10000):
        try:
            for _ in range(count):
                self.leds.fill(rgb)
                self.leds.show()
                await asyncio.sleep(0.3)
                self.leds.fill(_OFF)
                self.leds.show()
                await asyncio.sleep(0.3)
        except asyncio.CancelledError:
            pass # Task was cancelled, which is normal

    # --- MODIFICATION: Rewritten 'pulse' method for the new library ---
    async def pulse(self, color: Tuple[int, int, int], speed: float = 0.015):
        try:
            while True:
                # Fade in
                for i in range(101):
                    self.leds.brightness = i / 100.0
                    self.leds.fill(color)
                    self.leds.show()
                    await asyncio.sleep(speed)

                # Fade out
                for i in range(100, -1, -1):
                    self.leds.brightness = i / 100.0
                    self.leds.fill(color)
                    self.leds.show()
                    await asyncio.sleep(speed)
        except asyncio.CancelledError:
            self.leds.brightness = GLOBAL_BRIGHTNESS # Restore brightness on exit


    # --- Event subscriptions remain the same ---
    @subscribe
    def ready(self, data: dict):
        _LOGGER.debug('ready LED green blink')
        # Blink 3 times and then turn off
        self.run_action("blink", _GREEN, 3)

    @subscribe
    def voice_wakeword(self, data: dict):
        self.run_action("pulse", _BLUE)

    @subscribe
    def voice_VOICE_ASSISTANT_STT_VAD_END(self, data: dict):
        self.run_action("pulse", _YELLOW)

    @subscribe
    def voice_play_tts(self, data: dict):
        self.run_action("pulse", _GREEN)

    @subscribe
    def voice__tts_finished(self, data: dict):
        self.run_action("color", _OFF)
