"""Media player using mpv in a subprocess."""

import subprocess
import logging
import socket
import os
import uuid
import threading
import json
import time
import tempfile
from collections.abc import Callable
from typing import Optional, Union, List, Any
from pathlib import Path

_LOGGER = logging.getLogger(__name__)


class MpvMediaPlayer:
    def __init__(
        self,
        device: Optional[str] = None,
        socket_path: Optional[Union[str, Path]] = None,
    ) -> None:
        self.device = device
        self.is_playing = False

        if socket_path:
            self._socket_path = str(Path(socket_path).resolve())
        else:
            base_path = (
                Path(os.environ.get("XDG_RUNTIME_DIR") or "/dev/shm") / "mpv-sockets"
            )
            base_path.mkdir(parents=True, exist_ok=True)
            self._socket_path = str(base_path / f"mpv-{uuid.uuid4().hex}.sock")

        self._playlist_file = tempfile.NamedTemporaryFile(
            "w+", suffix=".m3u8", encoding="utf-8"
        )

        cmd = [
            "mpv",
            f"--input-ipc-server={self._socket_path}",
            "--no-video",
            "--quiet",
            "--term-playing-msg=",
            "--msg-level=all=no",
            "--idle=yes",  # keep alive
        ]
        if device:
            cmd.append(f"--audio-device={device}")

        _LOGGER.debug(cmd)

        self._proc = subprocess.Popen(cmd)
        self._socket_connected = False

        self._done_callback: Optional[Callable[[], None]] = None

        threading.Thread(target=self._read_socket, daemon=True).start()

    def play(
        self,
        url: Union[str, List[str]],
        done_callback: Optional[Callable[[], None]] = None,
    ) -> None:
        if isinstance(url, str):
            url = [url]

        self._done_callback = done_callback
        self.is_playing = True

        if len(url) == 1:
            self.send_command(["loadfile", url[0], "replace"])
        else:
            self._playlist_file.seek(0)
            for item in url:
                print(item, file=self._playlist_file, flush=True)

            self.send_command(["loadlist", self._playlist_file.name, "replace"])

    def stop(self) -> None:
        self.send_command(["stop"])
        self.is_playing = False

    def pause(self) -> None:
        if not self.is_playing:
            return

        self.send_command(["set_property", "pause", True])
        self.is_playing = False

    def resume(self) -> None:
        if self.is_playing:
            return

        self.send_command(["set_property", "pause", False])
        self.is_playing = True

    def mute(self) -> None:
        self.send_command(["set_property", "mute", True])

    def unmute(self) -> None:
        self.send_command(["set_property", "mute", False])

    def set_volume(self, volume: int) -> None:
        self.send_command(["set_property", "volume", volume])

    def send_command(self, cmd: List[Any]):
        if not self._socket_connected:
            _LOGGER.warning("Socket not connected: %s", cmd)
            return

        payload = json.dumps({"command": cmd}).encode("utf-8") + b"\n"

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as mpv_socket:
            mpv_socket.connect(self._socket_path)
            mpv_socket.sendall(payload)

        _LOGGER.debug("Sent command: %s", payload)

    def _read_socket(self):
        # Connect to socket
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as mpv_socket:
            timeout = time.monotonic() + 5
            while time.monotonic() < timeout:
                try:
                    mpv_socket.connect(self._socket_path)
                    self._socket_connected = True
                    break
                except (ConnectionRefusedError, FileNotFoundError, OSError):
                    pass

            if not self._socket_connected:
                _LOGGER.warning("Failed to connect to socket: %s", self._socket_path)
                return

            _LOGGER.debug("Connected to socket: %s", self._socket_path)

            # Read from socket
            mpv_buffer = bytes()

            try:
                while True:
                    chunk = mpv_socket.recv(4096)
                    if not chunk:
                        break

                    mpv_buffer += chunk
                    while b"\n" in mpv_buffer:
                        line, mpv_buffer = mpv_buffer.split(b"\n", 1)
                        event = json.loads(line.decode())
                        _LOGGER.debug("Received event: %s", event)

                        event_type = event.get("event")

                        if event_type == "start-file":
                            self.is_playing = True
                            continue

                        if event_type != "end-file":
                            continue

                        self.is_playing = False

                        if not self._done_callback:
                            continue

                        try:
                            self._done_callback()
                        except:
                            _LOGGER.exception("Unexpected error in callback")
            except:
                _LOGGER.exception("Unexpected error reading MPV socket")
