"""Tests for openWakeWord."""

import wave
from pathlib import Path

from linux_voice_assistant.openwakeword import OpenWakeWordFeatures, OpenWakeWord
from linux_voice_assistant.util import is_arm

_TESTS_DIR = Path(__file__).parent
_REPO_DIR = _TESTS_DIR.parent
_OWW_DIR = _REPO_DIR / "wakewords" / "openWakeWord"

if is_arm():
    _LIB_DIR = _REPO_DIR / "lib" / "linux_arm64"
else:
    _LIB_DIR = _REPO_DIR / "lib" / "linux_amd64"


libtensorflowlite_c_path = _LIB_DIR / "libtensorflowlite_c.so"


def test_features() -> None:
    features = OpenWakeWordFeatures(
        melspectrogram_model=_OWW_DIR / "melspectrogram.tflite",
        embedding_model=_OWW_DIR / "embedding_model.tflite",
        libtensorflowlite_c_path=libtensorflowlite_c_path,
    )
    ww = OpenWakeWord(
        id="ok_nabu",
        wake_word="okay nabu",
        tflite_model=_OWW_DIR / "ok_nabu_v0.1.tflite",
        libtensorflowlite_c_path=libtensorflowlite_c_path,
    )

    max_prob = 0.0
    with wave.open(str(_TESTS_DIR / "ok_nabu.wav"), "rb") as wav_file:
        assert wav_file.getframerate() == 16000
        assert wav_file.getsampwidth() == 2
        assert wav_file.getnchannels() == 1

        for embeddings in features.process_streaming(
            wav_file.readframes(wav_file.getnframes())
        ):
            for prob in ww.process_streaming(embeddings):
                max_prob = max(max_prob, prob)

    assert max_prob > 0.5
