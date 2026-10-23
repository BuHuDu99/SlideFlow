"""Private local IPC used by SlideFlow's Chrome native-messaging bridge."""

from __future__ import annotations

import json
import os
import socket
import threading
from pathlib import Path
from typing import Callable, Optional


MAX_MESSAGE_BYTES = 64 * 1024
DispatchCallback = Callable[[dict], dict]


def default_socket_path() -> Path:
    return (
        Path.home()
        / "Library"
        / "Application Support"
        / "SlideFlow"
        / "slideflow.sock"
    )


def _receive_line(connection: socket.socket) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while True:
        chunk = connection.recv(min(4096, MAX_MESSAGE_BYTES + 1 - size))
        if not chunk:
            break
        if b"\n" in chunk:
            before, _separator, _after = chunk.partition(b"\n")
            chunks.append(before)
            break
        chunks.append(chunk)
        size += len(chunk)
        if size > MAX_MESSAGE_BYTES:
            raise ValueError("Tin nhắn cục bộ vượt quá giới hạn cho phép.")
    data = b"".join(chunks)
    if len(data) > MAX_MESSAGE_BYTES:
        raise ValueError("Tin nhắn cục bộ vượt quá giới hạn cho phép.")
    return data


def send_local_message(
    message: dict,
    socket_path: Optional[Path] = None,
    timeout: float = 2.0,
) -> dict:
    path = socket_path or default_socket_path()
    payload = json.dumps(message, ensure_ascii=False).encode("utf-8") + b"\n"
    if len(payload) > MAX_MESSAGE_BYTES:
        raise ValueError("Tin nhắn cục bộ vượt quá giới hạn cho phép.")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(timeout)
        connection.connect(str(path))
        connection.sendall(payload)
        response_data = _receive_line(connection)
    if not response_data:
        raise RuntimeError("SlideFlow không phản hồi cầu nối cục bộ.")
    response = json.loads(response_data.decode("utf-8"))
    if not isinstance(response, dict):
        raise RuntimeError("Phản hồi cục bộ của SlideFlow không hợp lệ.")
    return response


class LocalIPCServer:
    """Small Unix-domain socket server; only the current macOS user can access it."""

    def __init__(
        self,
        dispatch: DispatchCallback,
        socket_path: Optional[Path] = None,
    ) -> None:
        self.dispatch = dispatch
        self.socket_path = socket_path or default_socket_path()
        self._server: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._owns_socket = False

    def start(self) -> bool:
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.socket_path.parent, 0o700)
        if self.socket_path.exists():
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                    probe.settimeout(0.2)
                    probe.connect(str(self.socket_path))
            except OSError:
                self.socket_path.unlink(missing_ok=True)
            else:
                return False

        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        bound = False
        try:
            server.bind(str(self.socket_path))
            bound = True
            os.chmod(self.socket_path, 0o600)
            server.listen(4)
            server.settimeout(0.5)
        except Exception:
            server.close()
            if bound:
                self.socket_path.unlink(missing_ok=True)
            raise
        self._server = server
        self._owns_socket = True
        self._thread = threading.Thread(
            target=self._serve,
            name="SlideFlowIPC",
            daemon=True,
        )
        self._thread.start()
        return True

    def _serve(self) -> None:
        assert self._server is not None
        while not self._stop_event.is_set():
            try:
                connection, _address = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with connection:
                connection.settimeout(3.0)
                try:
                    request_data = _receive_line(connection)
                    if not request_data:
                        continue
                    request = json.loads(request_data.decode("utf-8"))
                    if not isinstance(request, dict):
                        raise ValueError("Tin nhắn cục bộ không hợp lệ.")
                    response = self.dispatch(request)
                except Exception as error:
                    response = {"ok": False, "error": str(error)}
                try:
                    connection.sendall(
                        json.dumps(response, ensure_ascii=False).encode("utf-8") + b"\n"
                    )
                except OSError:
                    pass

    def stop(self) -> None:
        self._stop_event.set()
        if self._server is not None:
            self._server.close()
            self._server = None
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=1.0)
        if self._owns_socket:
            self.socket_path.unlink(missing_ok=True)
            self._owns_socket = False
