import tempfile
import unittest
from pathlib import Path

from slideflow_ipc import LocalIPCServer, send_local_message


class SlideFlowIPCTests(unittest.TestCase):
    def test_private_socket_round_trip_returns_dispatch_response(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            socket_path = Path(temp_dir) / "slideflow.sock"
            received = []

            def dispatch(message: dict) -> dict:
                received.append(message)
                return {"ok": True, "message": "queued"}

            server = LocalIPCServer(dispatch, socket_path=socket_path)
            try:
                try:
                    started = server.start()
                except PermissionError:
                    self.skipTest("Sandbox không cho phép tạo Unix-domain socket")
                self.assertTrue(started)
                response = send_local_message(
                    {"action": "enqueue", "url": "https://youtu.be/abcdefghijk"},
                    socket_path=socket_path,
                )
                self.assertEqual(response, {"ok": True, "message": "queued"})
                self.assertEqual(received[0]["action"], "enqueue")
                self.assertEqual(socket_path.stat().st_mode & 0o777, 0o600)
            finally:
                server.stop()
            self.assertFalse(socket_path.exists())

if __name__ == "__main__":
    unittest.main()
