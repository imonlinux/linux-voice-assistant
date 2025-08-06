import ctypes
import json
import statistics
from collections import deque
from pathlib import Path
from typing import Deque, List, Union

import numpy as np
from pymicro_features import MicroFrontend

_SAMPLES_PER_SECOND = 16000
_SAMPLES_PER_CHUNK = 160  # 10ms
_BYTES_PER_SAMPLE = 2  # 16-bit
_BYTES_PER_CHUNK = _SAMPLES_PER_CHUNK * _BYTES_PER_SAMPLE
_SECONDS_PER_CHUNK = _SAMPLES_PER_CHUNK / _SAMPLES_PER_SECOND
_STRIDE = 3
_DEFAULT_REFRACTORY = 2  # seconds


class TfLiteQuantizationParams(ctypes.Structure):
    _fields_ = [("scale", ctypes.c_float), ("zero_point", ctypes.c_int32)]


class MicroWakeWord:
    def __init__(
        self,
        id: str,  # pylint: disable=redefined-builtin
        wake_word: str,
        tflite_model: Union[str, Path],
        probability_cutoff: float,
        sliding_window_size: int,
        refractory_seconds: float,
        trained_languages: List[str],
        libtensorflowlite_c_path: Union[str, Path],
    ):
        self.id = id
        self.wake_word = wake_word
        self.tflite_model = tflite_model
        self.probability_cutoff = probability_cutoff
        self.sliding_window_size = sliding_window_size
        self.refractory_seconds = refractory_seconds
        self.trained_languages = trained_languages

        self.is_active = True

        # Load the shared library
        self.lib = ctypes.cdll.LoadLibrary(
            str(Path(libtensorflowlite_c_path).resolve())
        )

        # Define required argument/return types for C API
        self.lib.TfLiteModelCreateFromFile.argtypes = [ctypes.c_char_p]
        self.lib.TfLiteModelCreateFromFile.restype = ctypes.c_void_p

        self.lib.TfLiteInterpreterCreate.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.lib.TfLiteInterpreterCreate.restype = ctypes.c_void_p

        self.lib.TfLiteInterpreterAllocateTensors.argtypes = [ctypes.c_void_p]
        self.lib.TfLiteInterpreterInvoke.argtypes = [ctypes.c_void_p]

        self.lib.TfLiteInterpreterGetInputTensor.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
        ]
        self.lib.TfLiteInterpreterGetInputTensor.restype = ctypes.c_void_p

        self.lib.TfLiteInterpreterGetOutputTensor.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
        ]
        self.lib.TfLiteInterpreterGetOutputTensor.restype = ctypes.c_void_p

        self.lib.TfLiteTensorCopyFromBuffer.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
        ]
        self.lib.TfLiteTensorCopyToBuffer.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
        ]

        self.lib.TfLiteTensorType.restype = ctypes.c_int

        self.lib.TfLiteTensorByteSize.argtypes = [ctypes.c_void_p]
        self.lib.TfLiteTensorByteSize.restype = ctypes.c_size_t

        self.lib.TfLiteTensorQuantizationParams.restype = ctypes.Structure

        self.lib.TfLiteTensorQuantizationParams.argtypes = [ctypes.c_void_p]
        self.lib.TfLiteTensorQuantizationParams.restype = TfLiteQuantizationParams

        # Load the model and create interpreter
        self.model_path = str(Path(tflite_model).resolve()).encode("utf-8")
        self._load_model()

        self._frontend = MicroFrontend()
        self._features: List[np.ndarray] = []
        self._probabilities: Deque[float] = deque(maxlen=self.sliding_window_size)
        self._audio_buffer = bytes()
        self._ignore_seconds: float = 0

    def _load_model(self) -> None:
        self.model = self.lib.TfLiteModelCreateFromFile(self.model_path)
        self.interpreter = self.lib.TfLiteInterpreterCreate(self.model, None)
        self.lib.TfLiteInterpreterAllocateTensors(self.interpreter)

        # Access input and output tensor
        self.input_tensor = self.lib.TfLiteInterpreterGetInputTensor(
            self.interpreter, 0
        )
        self.output_tensor = self.lib.TfLiteInterpreterGetOutputTensor(
            self.interpreter, 0
        )

        # Get quantization parameters
        input_q = self.lib.TfLiteTensorQuantizationParams(self.input_tensor)
        output_q = self.lib.TfLiteTensorQuantizationParams(self.output_tensor)

        self.input_scale, self.input_zero_point = input_q.scale, input_q.zero_point
        self.output_scale, self.output_zero_point = output_q.scale, output_q.zero_point

    def reset(self) -> None:
        """Reload model and clear state.

        This must be done between audio clips when not streaming.
        """
        self._audio_buffer = bytes()
        self._features.clear()
        self._probabilities.clear()
        self._ignore_seconds = 0

        # Clear out residual features
        self._frontend = MicroFrontend()

        # Need to reload model to reset intermediary results
        # reset_all_variables() doesn't work.
        self._load_model()

    @property
    def samples_per_chunk(self) -> int:
        """Number of samples in a streaming audio chunk."""
        return _SAMPLES_PER_CHUNK

    @property
    def bytes_per_chunk(self) -> int:
        """Number of bytes in a streaming audio chunk.

        Assumes 16-bit mono samples at 16Khz.
        """
        return _BYTES_PER_CHUNK

    @staticmethod
    def from_config(
        config_path: Union[str, Path],
        libtensorflowlite_c_path: Union[str, Path],
        refractory_seconds: float = _DEFAULT_REFRACTORY,
    ) -> "MicroWakeWord":
        """Load a microWakeWord model from a JSON config file.

        Parameters
        ----------
        config_path: str or Path
            Path to JSON configuration file
        refractory_seconds: float
            Number of seconds to ignore after detection
        """
        config_path = Path(config_path)
        with open(config_path, "r", encoding="utf-8") as config_file:
            config = json.load(config_file)

        micro_config = config["micro"]

        return MicroWakeWord(
            id=Path(config["model"]).stem,
            wake_word=config["wake_word"],
            tflite_model=config_path.parent / config["model"],
            probability_cutoff=micro_config["probability_cutoff"],
            sliding_window_size=micro_config["sliding_window_size"],
            refractory_seconds=refractory_seconds,
            trained_languages=micro_config.get("trained_languages", []),
            libtensorflowlite_c_path=libtensorflowlite_c_path,
        )

    def process_streaming(self, audio_bytes: bytes) -> bool:
        """Process a chunk of audio in streaming mode.

        Parameters
        ----------
        audio_bytes: bytes
            Raw 16-bit mono audio samples at 16Khz

        Returns True if the wake word was detected.
        """
        self._audio_buffer += audio_bytes

        if len(self._audio_buffer) < _BYTES_PER_CHUNK:
            # Not enough audio to get features
            return False

        detected = False
        audio_buffer_idx = 0
        while (audio_buffer_idx + _BYTES_PER_CHUNK) <= len(self._audio_buffer):
            # Process chunk
            chunk_bytes = self._audio_buffer[
                audio_buffer_idx : audio_buffer_idx + _BYTES_PER_CHUNK
            ]
            frontend_result = self._frontend.ProcessSamples(chunk_bytes)
            audio_buffer_idx += frontend_result.samples_read * _BYTES_PER_SAMPLE
            self._ignore_seconds = max(0, self._ignore_seconds - _SECONDS_PER_CHUNK)

            if not frontend_result.features:
                # Not enough audio for a full window
                continue

            self._features.append(
                np.array(frontend_result.features).reshape(
                    (1, 1, len(frontend_result.features))
                )
            )

            if len(self._features) < _STRIDE:
                # Not enough windows
                continue

            # Allocate and quantize input data
            quant_features = np.round(
                np.concatenate(self._features, axis=1) / self.input_scale
                + self.input_zero_point
            ).astype(np.uint8)

            # Stride instead of rolling
            self._features.clear()

            # Set tensor
            quant_ptr = quant_features.ctypes.data_as(ctypes.c_void_p)
            self.lib.TfLiteTensorCopyFromBuffer(
                self.input_tensor, quant_ptr, quant_features.nbytes
            )

            # Run inference
            self.lib.TfLiteInterpreterInvoke(self.interpreter)

            # Read output
            output_bytes = self.lib.TfLiteTensorByteSize(self.output_tensor)
            output_data = np.empty(output_bytes, dtype=np.uint8)
            self.lib.TfLiteTensorCopyToBuffer(
                self.output_tensor,
                output_data.ctypes.data_as(ctypes.c_void_p),
                output_bytes,
            )

            # Dequantize output
            result = (
                output_data.astype(np.float32) - self.output_zero_point
            ) * self.output_scale

            self._probabilities.append(result.item())

            if len(self._probabilities) < self.sliding_window_size:
                # Not enough probabilities
                continue

            if statistics.mean(self._probabilities) > self.probability_cutoff:
                if self._ignore_seconds <= 0:
                    detected = True
                    self._ignore_seconds = self.refractory_seconds

        # Remove processed audio
        self._audio_buffer = self._audio_buffer[audio_buffer_idx:]

        return detected
