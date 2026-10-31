import hashlib
import io
import json
import plistlib
import os
import subprocess
import threading
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app_update as updater


def payload(version="v2.9.0", content=b"dmg"):
    return {"tag_name": version, "assets": [{
        "name": "SlideFlow.dmg", "state": "uploaded", "size": len(content),
        "digest": "sha256:" + hashlib.sha256(content).hexdigest(),
        "browser_download_url": f"https://github.com/BuHuDu99/SlideFlow/releases/download/{version}/SlideFlow.dmg",
    }]}


class UpdateTests(unittest.TestCase):
    def test_only_new_stable_versions(self):
        self.assertIsNone(updater.parse_release(payload("v2.8.0")))
        self.assertIsNone(updater.parse_release(payload("v2.7.3")))
        self.assertEqual(updater.parse_release(payload("v2.10.0")).version, "2.10.0")
        for field in ("draft", "prerelease"):
            self.assertIsNone(updater.parse_release({**payload(), field: True}))
        with self.assertRaises(ValueError):
            updater.parse_release(payload("v2.9.0-rc1"))

    def test_reject_foreign_or_insecure_downloads(self):
        for url in ("http://github.com/BuHuDu99/SlideFlow/releases/download/v2.9.0/SlideFlow.dmg",
                    "https://evil.test/SlideFlow.dmg", "https://github.com/other/repo/releases/download/v2.9.0/SlideFlow.dmg",
                    "https://github.com/BuHuDu99/SlideFlow/releases/download/v2.9.0/SlideFlow.dmg?x=1"):
            data = payload(); data["assets"][0]["browser_download_url"] = url
            with self.assertRaises(ValueError): updater.parse_release(data)

    def test_missing_checksum_or_asset_fails_closed(self):
        for change in ({"digest": None}, {"size": 0}, {"size": updater.MAX_DOWNLOAD + 1}, {"name": "other.dmg"}):
            data = payload(); data["assets"][0].update(change)
            with self.assertRaises(ValueError): updater.parse_release(data)

    def test_download_validates_size_and_hash_and_deletes_bad_file(self):
        release = updater.parse_release(payload(content=b"expected"))
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / "download.dmg"
            for content in (b"short", b"wrongval", b"expected but longer"):
                with patch.object(updater, "urlopen", return_value=io.BytesIO(content)):
                    with self.assertRaises(ValueError): updater.download_release(release, target)
                    self.assertFalse(target.exists())
            with patch.object(updater, "urlopen", return_value=io.BytesIO(b"expected")):
                updater.download_release(release, target)
                self.assertEqual(target.read_bytes(), b"expected")

    def test_download_reports_actual_byte_progress(self):
        content = b"x" * (600 * 1024)
        release = updater.parse_release(payload(content=content))
        events = []
        with tempfile.TemporaryDirectory() as root:
            with patch.object(updater, "urlopen", return_value=io.BytesIO(content)):
                updater.download_release(release, Path(root)/"update.dmg", progress=lambda *event: events.append(event))
        downloads = [event for event in events if event[0]=="download"]
        self.assertEqual(downloads[0], ("download", 0, len(content)))
        self.assertEqual(downloads[-1], ("download", len(content), len(content)))
        self.assertEqual([e[1] for e in downloads], sorted(e[1] for e in downloads))
        self.assertEqual(events[-1][0], "verify")

    def test_cancel_download_deletes_partial_file(self):
        content = b"x" * (600 * 1024)
        release = updater.parse_release(payload(content=content))
        cancel = threading.Event()
        def progress(phase, count, total):
            if phase=="download" and count:
                cancel.set()
        with tempfile.TemporaryDirectory() as root:
            target = Path(root)/"update.dmg"
            with patch.object(updater, "urlopen", return_value=io.BytesIO(content)):
                with self.assertRaises(updater.UpdateCancelled):
                    updater.download_release(release, target, progress=progress, cancel=cancel)
            self.assertFalse(target.exists())

    def test_cancel_before_download_never_connects(self):
        cancel = threading.Event(); cancel.set()
        with tempfile.TemporaryDirectory() as root, patch.object(updater, "urlopen") as fetch:
            with self.assertRaises(updater.UpdateCancelled):
                updater.download_release(updater.parse_release(payload()), Path(root)/"update.dmg", cancel=cancel)
            fetch.assert_not_called()

    def test_cancel_after_download_cleans_stage_before_mounting(self):
        cancel = threading.Event()
        with tempfile.TemporaryDirectory() as root:
            def download(release, target, **kwargs):
                target.write_bytes(b"dmg")
                cancel.set()
            with patch.object(updater, "download_release", side_effect=download), patch.object(updater.subprocess, "run") as run:
                with self.assertRaises(updater.UpdateCancelled):
                    updater.prepare_update(updater.parse_release(payload()), Path(root)/"SlideFlow.app", cancel=cancel)
                run.assert_not_called()
            self.assertEqual(list(Path(root).iterdir()), [])

    def test_bundle_identity_and_version_checked_before_execution(self):
        with tempfile.TemporaryDirectory() as root:
            app = Path(root); (app / "Contents").mkdir()
            for info in ({"CFBundleIdentifier": "other", "CFBundleShortVersionString": "2.9.0"},
                         {"CFBundleIdentifier": "com.buihuuduc.slideflow", "CFBundleShortVersionString": "2.8.0"}):
                (app / "Contents/Info.plist").write_bytes(plistlib.dumps(info))
                with patch.object(updater.subprocess, "run") as run:
                    with self.assertRaises(ValueError): updater.verify_bundle(app, "2.9.0")
                    run.assert_not_called()

    def test_prepare_failure_removes_download_and_stage(self):
        release = updater.parse_release(payload())
        with tempfile.TemporaryDirectory() as root:
            destination = Path(root) / "SlideFlow.app"
            with patch.object(updater, "download_release", side_effect=OSError("offline")):
                with self.assertRaises(OSError): updater.prepare_update(release, destination)
            self.assertEqual(list(Path(root).iterdir()), [])

    def test_detached_helper_success_and_rollback(self):
        helper = (Path(__file__).resolve().parents[1] / "scripts/install_update.sh").read_text()
        for succeed in (True, False):
            with self.subTest(succeed=succeed), tempfile.TemporaryDirectory() as root:
                root = Path(root)
                app = root / "SlideFlow.app"; app.mkdir(); (app / "marker").write_text("old")
                stage = root / "stage"; (stage / "SlideFlow.app").mkdir(parents=True)
                (stage / "SlideFlow.app/marker").write_text("new")
                runner = root / "installer.sh"
                runner.write_text(helper.replace("/usr/bin/open", "/usr/bin/true" if succeed else "/usr/bin/false"))
                result = subprocess.run(["/bin/sh", str(runner), "99999999", str(app), str(stage)], env={**os.environ, "HOME": str(root)}, capture_output=True, timeout=5)
                self.assertEqual(result.returncode, 0 if succeed else 1, result.stderr)
                self.assertEqual((app / "marker").read_text(), "new" if succeed else "old")
                self.assertFalse(stage.exists())
                self.assertEqual((root / "Library/Application Support/SlideFlow/update-error.txt").exists(), not succeed)

    def test_latest_api_uses_timeout_and_public_endpoint(self):
        with patch.object(updater, "urlopen", return_value=io.BytesIO(json.dumps(payload()).encode())) as fetch:
            self.assertEqual(updater.check_release().version, "2.9.0")
            self.assertEqual(fetch.call_args.args[0].full_url, updater.API_URL)
            self.assertEqual(fetch.call_args.kwargs["timeout"], 15)


if __name__ == "__main__":
    unittest.main()
