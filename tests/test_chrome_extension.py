import base64
import hashlib
import json
import plistlib
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXTENSION_ID = "lgebdjdlladiamggnnnebcjccadfbcfa"


class ChromeExtensionTests(unittest.TestCase):
    def test_manifest_has_stable_expected_extension_id(self):
        manifest_path = ROOT / "chrome_extension" / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        public_key = base64.b64decode(manifest["key"])
        digest = hashlib.sha256(public_key).digest()[:16]
        computed_id = "".join(
            chr(ord("a") + nibble)
            for byte in digest
            for nibble in (byte >> 4, byte & 0xF)
        )

        self.assertEqual(EXTENSION_ID, computed_id)
        self.assertEqual(["activeTab", "nativeMessaging"], manifest["permissions"])
        self.assertNotIn("host_permissions", manifest)
        self.assertEqual("background.js", manifest["background"]["service_worker"])

    def test_installer_allows_only_the_bundled_extension(self):
        installer = ROOT / "chrome_extension" / "install_host.command"
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            app_path = root / "SlideFlow.app"
            host_path = app_path / "Contents" / "MacOS" / "SlideFlowChromeHost"
            host_path.parent.mkdir(parents=True)
            main_path = host_path.with_name("SlideFlow")
            shutil.copyfile("/usr/bin/true", main_path)
            main_path.chmod(0o755)
            shutil.copyfile("/usr/bin/true", host_path)
            host_path.chmod(0o755)
            with (app_path / "Contents" / "Info.plist").open("wb") as plist_file:
                plistlib.dump(
                    {
                        "CFBundleIdentifier": "com.buihuuduc.slideflow",
                        "CFBundleExecutable": "SlideFlow",
                        "CFBundlePackageType": "APPL",
                    },
                    plist_file,
                )
            subprocess.run(
                ["/usr/bin/codesign", "--force", "--sign", "-", str(host_path)],
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                ["/usr/bin/codesign", "--force", "--deep", "--sign", "-", str(app_path)],
                check=True,
                capture_output=True,
                text=True,
            )
            manifest_directory = root / "NativeMessagingHosts"
            bridge_directory = root / "ChromeBridge"
            extension_directory = root / "ChromeExtension"
            environment = {
                "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
                "SLIDEFLOW_NATIVE_HOST_DIR": str(manifest_directory),
                "SLIDEFLOW_BRIDGE_DIR": str(bridge_directory),
                "SLIDEFLOW_EXTENSION_INSTALL_DIR": str(extension_directory),
                "SLIDEFLOW_NONINTERACTIVE": "1",
            }

            install_result = subprocess.run(
                ["/bin/zsh", str(installer), str(app_path)],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertEqual(
                0,
                install_result.returncode,
                install_result.stdout + install_result.stderr,
            )
            manifest_path = manifest_directory / "com.buihuuduc.slideflow.json"
            native_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            installed_host = bridge_directory / "SlideFlowChromeHost"
            installed_app_path = (
                bridge_directory / "slideflow-app-path.txt"
            ).read_text(encoding="utf-8").strip()
            installed_extension_manifest = json.loads(
                (extension_directory / "manifest.json").read_text(encoding="utf-8")
            )
            installed_background_exists = (extension_directory / "background.js").is_file()
            installed_icon_exists = (extension_directory / "icons" / "icon128.png").is_file()

        self.assertEqual(str(installed_host), native_manifest["path"])
        self.assertEqual(str(app_path.resolve()), installed_app_path)
        self.assertEqual(
            [f"chrome-extension://{EXTENSION_ID}/"],
            native_manifest["allowed_origins"],
        )
        self.assertEqual("SlideFlow — Gửi video", installed_extension_manifest["name"])
        self.assertTrue(installed_background_exists)
        self.assertTrue(installed_icon_exists)


if __name__ == "__main__":
    unittest.main()
