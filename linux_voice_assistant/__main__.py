#!/usr/bin/env python3
import argparse
import asyncio
import json
import logging
import threading
from pathlib import Path
from queue import Queue
from typing import Dict

import sounddevice as sd

from .microwakeword import MicroWakeWord
from .models import AvailableWakeWord, Preferences, ServerState
from .mpv_player import MpvMediaPlayer
from .satellite import VoiceSatelliteProtocol
from .util import get_mac, is_arm

_LOGGER = logging.getLogger(__name__)
_MODULE_DIR = Path(__file__).parent
_REPO_DIR = _MODULE_DIR.parent
_WAKEWORDS_DIR = _REPO_DIR / "wakewords"
_SOUNDS_DIR = _REPO_DIR / "sounds"

if is_arm():
    _LIB_DIR = _REPO_DIR / "lib" / "linux_arm64"
else:
    _LIB_DIR = _REPO_DIR / "lib" / "linux_amd64"


# -----------------------------------------------------------------------------


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument(
        "--audio-input-device",
        default="default",
        help="sounddevice name for input device",
    )
    parser.add_argument("--audio-input-block-size", type=int, default=1024)
    parser.add_argument("--audio-output-device", help="mpv name for output device")
    parser.add_argument(
        "--wake-word-dir",
        default=_WAKEWORDS_DIR,
        help="Directory with wake word models (.tflite) and configs (.json)",
    )
    parser.add_argument(
        "--wake-model", default="okay_nabu", help="Id of active wake model"
    )
    parser.add_argument("--stop-model", default="stop", help="Id of stop model")
    parser.add_argument(
        "--wakeup-sound", default=str(_SOUNDS_DIR / "wake_word_triggered.flac")
    )
    parser.add_argument(
        "--timer-finished-sound", default=str(_SOUNDS_DIR / "timer_finished.flac")
    )
    #
    parser.add_argument("--preferences-file", default=_REPO_DIR / "preferences.json")
    #
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Address for ESPHome server (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port", type=int, default=6053, help="Port for ESPHome server (default: 6053)"
    )
    parser.add_argument(
        "--debug", action="store_true", help="Print DEBUG messages to console"
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)
    _LOGGER.debug(args)

    # Load available wake words
    wake_word_dir = Path(args.wake_word_dir)
    available_wake_words: Dict[str, AvailableWakeWord] = {}
    for model_config_path in wake_word_dir.glob("*.json"):
        model_id = model_config_path.stem
        if model_id == args.stop_model:
            # Don't show stop model as an available wake word
            continue

        with open(model_config_path, "r", encoding="utf-8") as model_config_file:
            model_config = json.load(model_config_file)
            available_wake_words[model_id] = AvailableWakeWord(
                id=model_id,
                wake_word=model_config["wake_word"],
                trained_languages=model_config.get("trained_languages", []),
                config_path=model_config_path,
            )

    _LOGGER.debug("Available wake words: %s", list(sorted(available_wake_words.keys())))

    # Load preferences
    preferences_path = Path(args.preferences_file)
    if preferences_path.exists():
        _LOGGER.debug("Loading preferences: %s", preferences_path)
        with open(preferences_path, "r", encoding="utf-8") as preferences_file:
            preferences_dict = json.load(preferences_file)
            preferences = Preferences(**preferences_dict)
    else:
        preferences = Preferences()

    libtensorflowlite_c_path = _LIB_DIR / "libtensorflowlite_c.so"
    _LOGGER.debug("libtensorflowlite_c path: %s", libtensorflowlite_c_path)

    # Load wake/stop models
    wake_config_path = wake_word_dir / f"{args.wake_model}.json"
    if preferences.active_wake_words:
        wake_word_id = preferences.active_wake_words[0]
        maybe_wake_config_path = wake_word_dir / f"{wake_word_id}.json"
        if maybe_wake_config_path.exists():
            # Override with last set
            wake_config_path = maybe_wake_config_path

    _LOGGER.debug("Loading wake model: %s", wake_config_path)
    wake_model = MicroWakeWord.from_config(wake_config_path, libtensorflowlite_c_path)

    stop_config_path = wake_word_dir / f"{args.stop_model}.json"
    _LOGGER.debug("Loading stop model: %s", stop_config_path)
    stop_model = MicroWakeWord.from_config(stop_config_path, libtensorflowlite_c_path)

    state = ServerState(
        name=args.name,
        mac_address=get_mac(),
        audio_queue=Queue(),
        entities=[],
        available_wake_words=available_wake_words,
        wake_word=wake_model,
        stop_word=stop_model,
        music_player=MpvMediaPlayer(device=args.audio_output_device),
        tts_player=MpvMediaPlayer(device=args.audio_output_device),
        wakeup_sound=args.wakeup_sound,
        timer_finished_sound=args.timer_finished_sound,
        preferences=preferences,
        preferences_path=preferences_path,
    )

    process_audio_thread = threading.Thread(
        target=process_audio, args=(state,), daemon=True
    )
    process_audio_thread.start()

    def sd_callback(indata, _frames, _time, _status):
        state.audio_queue.put_nowait(bytes(indata))

    loop = asyncio.get_running_loop()
    server = await loop.create_server(
        lambda: VoiceSatelliteProtocol(state), host=args.host, port=args.port
    )

    try:
        _LOGGER.debug("Opening audio input device: %s", args.audio_input_device)
        with sd.RawInputStream(
            samplerate=16000,
            blocksize=args.audio_input_block_size,
            device=args.audio_input_device,
            dtype="int16",
            channels=1,
            callback=sd_callback,
        ):
            async with server:
                _LOGGER.info("Server started (host=%s, port=%s)", args.host, args.port)
                await server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        state.audio_queue.put_nowait(None)
        process_audio_thread.join()

    _LOGGER.debug("Server stopped")


# -----------------------------------------------------------------------------


def process_audio(state: ServerState):

    try:
        while True:
            audio_chunk = state.audio_queue.get()
            if audio_chunk is None:
                break

            if state.satellite is None:
                continue

            try:
                state.satellite.handle_audio(audio_chunk)

                if state.wake_word.is_active and state.wake_word.process_streaming(
                    audio_chunk
                ):
                    state.satellite.wakeup()

                if state.stop_word.is_active and state.stop_word.process_streaming(
                    audio_chunk
                ):
                    state.satellite.stop()
            except Exception:
                _LOGGER.exception("Unexpected error handling audio")

    except Exception:
        _LOGGER.exception("Unexpected error processing audio")


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    asyncio.run(main())
