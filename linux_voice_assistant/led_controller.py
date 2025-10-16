import asyncio
import logging
from typing import Any, Tuple

import board

from .event_bus import EventHandler, subscribe

_LOGGER = logging.getLogger(__name__)

# Default Colors
_OFF = (0, 0, 0)
_BLUE = (0, 0, 255)
_YELLOW = (255, 255, 0)
_GREEN = (0, 255, 0)
_DIM_RED = (50, 0, 0)
_ORANGE = (255, 165, 0)
_PURPLE = (128, 0, 255)


class LedController(EventHandler):
    def __init__(
        self,
        state,
        led_type: str = "dotstar",
        interface: str = "spi",
        clock_pin: int = 13,
        data_pin: int = 12,
        num_leds: int = 3,
    ):
        super().__init__(state)
        self.loop = state.loop
        self.num_leds = num_leds
        self.current_task = None
        self._is_ready = False
        self.leds = None
        
        self.configs = {
            "idle":       {"effect": "off", "color": _PURPLE, "brightness": 0.5},
            "listening":  {"effect": "medium_pulse", "color": _BLUE,   "brightness": 0.5},
            "thinking":   {"effect": "spin", "color": _YELLOW, "brightness": 0.8},
            "responding": {"effect": "medium_pulse", "color": _GREEN,  "brightness": 0.5},
            "error":      {"effect": "fast_blink", "color": _ORANGE, "brightness": 1.0},
        }

        try:
            # Universal Hardware Initialization
            if led_type == "neopixel":
                if interface == "spi":
                    # --- THIS IS THE FIX ---
                    import busio
                    import neopixel_spi # The module name is just 'neopixel_spi'
                    _LOGGER.debug(f"Initializing {self.num_leds} NeoPixel LEDs on hardware SPI")
                    spi = busio.SPI(board.SCLK, MOSI=board.MOSI)
                    self.leds = neopixel_spi.NeoPixel_SPI(spi, self.num_leds, auto_write=False)
                else: # GPIO
                    import neopixel
                    _LOGGER.debug(f"Initializing {self.num_leds} NeoPixel LEDs on GPIO data={data_pin}")
                    pin_object = getattr(board, f"D{data_pin}")
                    self.leds = neopixel.NeoPixel(pin_object, self.num_leds, auto_write=False)
            else: # dotstar
                import adafruit_dotstar
                if interface == "gpio":
                    _LOGGER.debug(f"Initializing {self.num_leds} DotStar LEDs on GPIO data={data_pin}, clock={clock_pin}")
                    data_pin_obj = getattr(board, f"D{data_pin}")
                    clock_pin_obj = getattr(board, f"D{clock_pin}")
                    self.leds = adafruit_dotstar.DotStar(clock_pin_obj, data_pin_obj, self.num_leds, auto_write=False)
                else: # SPI
                    _LOGGER.debug(f"Initializing {self.num_leds} DotStar LEDs on hardware SPI")
                    self.leds = adafruit_dotstar.DotStar(board.SCLK, board.MOSI, self.num_leds, auto_write=False)
            
            self._is_ready = True
            _LOGGER.info(f"LED Controller initialized for {self.num_leds} {led_type} LEDs.")
            self.run_action("startup_sequence")

        except Exception:
            _LOGGER.exception("Failed to initialize LED controller. LEDs will be disabled.")

    # ... (The rest of the file is identical and does not need to be changed)
    def run_action(self, action_method_name: str, *args: Any) -> None:
        if not self._is_ready: return
        if self.current_task and not self.current_task.done(): self.current_task.cancel()
        
        coro = getattr(self, action_method_name)(*args)
        self.current_task = asyncio.run_coroutine_threadsafe(coro, self.loop)

    def _apply_state_effect(self, state_name: str, publish_state: bool = True):
        config = self.configs.get(state_name, self.configs["idle"])
        _LOGGER.debug(f"Applying effect for state '{state_name}': {config['effect']}")
        self.run_action(config["effect"], config["color"], config["brightness"])
        
        if publish_state:
            config["state_name"] = state_name
            self.state.event_bus.publish("publish_state_to_mqtt", config)
    async def startup_sequence(self, color=None, brightness=None): await self.blink(_GREEN, 1.0)
    async def off(self, color, brightness): self.leds.fill(_OFF); self.leds.show()
    async def solid(self, color: Tuple[int, int, int], brightness: float):
        r, g, b = color
        self.leds.fill((int(r * brightness), int(g * brightness), int(b * brightness)))
        self.leds.show()
    async def blink(self, color, brightness=1.0): await self.medium_blink(color, brightness)
    
    async def _base_pulse(self, color: Tuple[int, int, int], brightness: float, speed: float):
        try:
            r, g, b = color
            while True:
                for i in range(0, 101, 10):
                    mod = i / 100.0 * brightness
                    self.leds.fill((int(r*mod), int(g*mod), int(b*mod))); self.leds.show(); await asyncio.sleep(speed)
                for i in range(100, -1, -10):
                    mod = i / 100.0 * brightness
                    self.leds.fill((int(r*mod), int(g*mod), int(b*mod))); self.leds.show(); await asyncio.sleep(speed)
        except asyncio.CancelledError: self.leds.fill(_OFF); self.leds.show()
    async def slow_pulse(self, color, brightness): await self._base_pulse(color, brightness, 0.05)
    async def medium_pulse(self, color, brightness): await self._base_pulse(color, 0.02)
    async def fast_pulse(self, color, brightness): await self._base_pulse(color, 0.008)
    
    async def _base_blink(self, color: Tuple[int, int, int], brightness: float, speed: float):
        try:
            r, g, b = color
            bright_color = (int(r * brightness), int(g * brightness), int(b * brightness))
            while True:
                self.leds.fill(bright_color); self.leds.show(); await asyncio.sleep(speed)
                self.leds.fill(_OFF); self.leds.show(); await asyncio.sleep(speed)
        except asyncio.CancelledError: self.leds.fill(_OFF); self.leds.show()
    async def slow_blink(self, color, brightness): await self._base_blink(color, brightness, 1.0)
    async def medium_blink(self, color, brightness): await self._base_blink(color, brightness, 0.5)
    async def fast_blink(self, color, brightness): await self._base_blink(color, brightness, 0.1)

    async def spin(self, color: Tuple[int, int, int], brightness: float, speed: float = 0.1):
        try:
            i = 0
            bright_color = (int(color[0] * brightness), int(color[1] * brightness), int(color[2] * brightness))
            while True:
                self.leds.fill(_OFF); self.leds[i % self.num_leds] = bright_color; self.leds.show(); i += 1; await asyncio.sleep(speed)
        except asyncio.CancelledError: self.leds.fill(_OFF); self.leds.show()
    @subscribe
    def ha_connected(self, data: dict): self._apply_state_effect("idle")
    @subscribe
    def voice_wakeword(self, data: dict): self._apply_state_effect("listening")
    @subscribe
    def voice_stt_start(self, data: dict): self._apply_state_effect("listening")
    @subscribe
    def voice_vad_start(self, data: dict): pass
    @subscribe
    def voice_stt_end(self, data: dict): self._apply_state_effect("thinking")
    @subscribe
    def voice_tts_start(self, data: dict): self._apply_state_effect("responding")
    @subscribe
    def voice_error(self, data: dict): self._apply_state_effect("error")
    @subscribe
    def voice_run_end(self, data: dict): self._apply_state_effect("idle")
    @subscribe
    def mic_muted(self, data: dict): self.run_action("solid", _DIM_RED, 1.0)
    @subscribe
    def mic_unmuted(self, data: dict): self._apply_state_effect("idle")
    
    def _update_config(self, state_name: str, data: dict, apply: bool):
        config = self.configs[state_name]; changed = False; is_retained = data.get("retained", False)
        new_effect = data.get("effect")
        if new_effect is not None and config["effect"] != new_effect: config["effect"] = new_effect; changed = True
        new_color_data = data.get("color")
        if new_color_data is not None:
             new_color_tuple = (new_color_data.get("r"), new_color_data.get("g"), new_color_data.get("b"))
             if config["color"] != new_color_tuple: config["color"] = new_color_tuple; changed = True
        new_brightness = data.get("brightness")
        if new_brightness is not None:
            new_brightness_float = new_brightness / 255.0
            if abs(config["brightness"] - new_brightness_float) > 0.001: config["brightness"] = new_brightness_float; changed = True
        if is_retained:
            if changed and state_name == "idle": self._apply_state_effect("idle", publish_state=False)
            return
        if changed:
            if apply: self._apply_state_effect(state_name)
            else: config["state_name"] = state_name; self.state.event_bus.publish("publish_state_to_mqtt", config)
    
    @subscribe
    def set_idle_effect(self, data: dict): self._update_config("idle", data, True)
    @subscribe
    def set_idle_color(self, data: dict): self._update_config("idle", data, self.configs["idle"]["effect"] != "off")
    @subscribe
    def set_listening_effect(self, data: dict): self._update_config("listening", data, False)
    @subscribe
    def set_listening_color(self, data: dict): self._update_config("listening", data, False)
    @subscribe
    def set_thinking_effect(self, data: dict): self._update_config("thinking", data, False)
    @subscribe
    def set_thinking_color(self, data: dict): self._update_config("thinking", data, False)
    @subscribe
    def set_responding_effect(self, data: dict): self._update_config("responding", data, False)
    @subscribe
    def set_responding_color(self, data: dict): self._update_config("responding", data, False)
    @subscribe
    def set_error_effect(self, data: dict): self._update_config("error", data, False)
    @subscribe
    def set_error_color(self, data: dict): self._update_config("error", data, False)
    @subscribe
    def set_num_leds(self, data: dict):
        num_leds = data.get("num_leds")
        if (num_leds is not None) and (self.num_leds != num_leds):
             _LOGGER.info("Number of LEDs changed to %d. Please restart for change to take effect.", num_leds)
