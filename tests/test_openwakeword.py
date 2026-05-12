"""Tests for openWakeWord."""

import wave
from pathlib import Path

import pytest

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


# Skip the entire module when the bundled TensorFlow Lite shared library
# isn't present. This happens on dev/test hosts that haven't run the install
# script, on architectures we don't ship a build for, or in CI without the
# native dependencies. Users who actually run LVA always have the library;
# this guard just keeps `pytest tests/` green on bare environments.
pytestmark = pytest.mark.skipif(
    not libtensorflowlite_c_path.is_file(),
    reason=f"libtensorflowlite_c.so not found at {libtensorflowlite_c_path}",
)


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
