import json
import os
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

from slideflow_ipc import LocalIPCServer


ROOT = Path(__file__).resolve().parents[1]


def frame(message: dict) -> bytes:
    payload = json.dumps(message, ensure_ascii=False).encode("utf-8")
    return struct.pack("<I", len(payload)) + payload


def unframe(data: bytes) -> dict:
    if len(data) < 4:
        raise AssertionError("Native host response is missing its header")
    length = struct.unpack("<I", data[:4])[0]
    payload = data[4:]
    if len(payload) != length:
        raise AssertionError("Native host response length is incorrect")
    return json.loads(payload.decode("utf-8"))


class ChromeNativeHostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        installed_host = os.environ.get("SLIDEFLOW_NATIVE_HOST_BINARY")
        if installed_host:
            cls._temporary_directory = None
            cls.host_path = Path(installed_host)
            return
        cls._temporary_directory = tempfile.TemporaryDirectory()
        cls.host_path = Path(cls._temporary_directory.name) / "SlideFlowChromeHost"
        subprocess.run(
            [
                "/usr/bin/xcrun",
                "clang",
                "-std=c11",
                "-O2",
                "-Wall",
                "-Wextra",
                "-Werror",
                str(ROOT / "chrome_native_host.c"),
                "-o",
                str(cls.host_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        if cls._temporary_directory is not None:
            cls._temporary_directory.cleanup()

    def test_empty_input_exits_cleanly(self) -> None:
        result = subprocess.run(
            [str(self.host_path)],
            input=b"",
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, result.returncode)
        self.assertEqual(b"", result.stdout)

    def test_rejects_oversized_native_message(self) -> None:
        result = subprocess.run(
            [str(self.host_path)],
            input=struct.pack("<I", 100_000),
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, result.returncode)
        self.assertFalse(unframe(result.stdout)["ok"])

    def test_forwards_unicode_message_over_private_socket(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            socket_path = Path(temp_dir) / "slideflow.sock"
            received = []

            def dispatch(message: dict) -> dict:
                received.append(message)
                return {"ok": True, "message": "Đã xếp hàng"}

            server = LocalIPCServer(dispatch, socket_path=socket_path)
            try:
                try:
                    server.start()
                except PermissionError:
                    self.skipTest("Sandbox không cho phép tạo Unix-domain socket")
                environment = os.environ.copy()
                environment["SLIDEFLOW_SOCKET_PATH"] = str(socket_path)
                message = {"action": "enqueue", "title": "Bài giảng siêu âm"}
                result = subprocess.run(
                    [str(self.host_path)],
                    input=frame(message),
                    capture_output=True,
                    check=False,
                    env=environment,
                )
            finally:
                server.stop()

        self.assertEqual(0, result.returncode)
        self.assertEqual({"ok": True, "message": "Đã xếp hàng"}, unframe(result.stdout))
        self.assertEqual(message, received[0])


if __name__ == "__main__":
    unittest.main()
