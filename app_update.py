"""Bounded, on-demand updates from the project's public GitHub releases."""
from __future__ import annotations

import hashlib
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from app_version import APP_VERSION

REPOSITORY = "BuHuDu99/SlideFlow"
API_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
MAX_DOWNLOAD = 1024 * 1024 * 1024


def version_tuple(value: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", value)
    if not match:
        raise ValueError("Phiên bản phát hành không hợp lệ.")
    return tuple(map(int, match.groups()))


@dataclass(frozen=True)
class Release:
    version: str
    url: str
    digest: str
    size: int


def parse_release(payload: dict, current: str = APP_VERSION) -> Release | None:
    if payload.get("draft") or payload.get("prerelease"):
        return None
    version = str(payload.get("tag_name", ""))
    if version_tuple(version) <= version_tuple(current):
        return None
    for asset in payload.get("assets", []):
        if asset.get("name") != "SlideFlow.dmg" or asset.get("state") != "uploaded":
            continue
        url = str(asset.get("browser_download_url", ""))
        parsed = urlparse(url)
        expected = f"/{REPOSITORY}/releases/download/{version}/SlideFlow.dmg"
        if parsed.scheme != "https" or parsed.netloc != "github.com" or parsed.path != expected or parsed.query or parsed.fragment:
            raise ValueError("Địa chỉ tải không thuộc kho SlideFlow.")
        digest = str(asset.get("digest", ""))
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise ValueError("Bản phát hành chưa có mã SHA-256 để xác minh.")
        size = int(asset.get("size", 0))
        if not 0 < size <= MAX_DOWNLOAD:
            raise ValueError("Dung lượng bản cập nhật không hợp lệ.")
        return Release(version.removeprefix("v"), url, digest[7:], size)
    raise ValueError("Bản phát hành chưa có SlideFlow.dmg dành cho macOS Apple Silicon.")


def check_release() -> Release | None:
    request = Request(API_URL, headers={"Accept": "application/vnd.github+json", "User-Agent": f"SlideFlow/{APP_VERSION}"})
    try:
        with urlopen(request, timeout=15) as response:
            payload = response.read(2 * 1024 * 1024 + 1)
    except HTTPError as error:
        if error.code == 404:
            return None
        raise RuntimeError("Không kết nối được GitHub. Hãy thử lại sau.") from error
    if len(payload) > 2 * 1024 * 1024:
        raise ValueError("Phản hồi GitHub quá lớn.")
    return parse_release(json.loads(payload))


def download_release(release: Release, target: Path) -> None:
    checksum = hashlib.sha256()
    total = 0
    try:
        request = Request(release.url, headers={"User-Agent": f"SlideFlow/{APP_VERSION}"})
        with urlopen(request, timeout=30) as response, target.open("wb") as output:
            while block := response.read(1024 * 1024):
                total += len(block)
                if total > release.size or total > MAX_DOWNLOAD:
                    raise ValueError("Tệp cập nhật vượt quá dung lượng công bố.")
                checksum.update(block)
                output.write(block)
        if total != release.size or checksum.hexdigest() != release.digest:
            raise ValueError("Tệp cập nhật bị thiếu hoặc sai mã SHA-256.")
    except BaseException:
        target.unlink(missing_ok=True)
        raise


def installed_app() -> Path | None:
    if not getattr(sys, "frozen", False):
        return None
    path = Path(sys.executable).resolve().parent.parent.parent
    return path if path.suffix == ".app" else None


def verify_bundle(path: Path, version: str) -> None:
    with (path / "Contents/Info.plist").open("rb") as source:
        info = plistlib.load(source)
    if info.get("CFBundleIdentifier") != "com.buihuuduc.slideflow" or info.get("CFBundleShortVersionString") != version:
        raise ValueError("Ứng dụng tải về không đúng SlideFlow hoặc sai phiên bản.")
    subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(path)], check=True, capture_output=True, timeout=120)
    subprocess.run(["/usr/bin/lipo", "-verify_arch", "arm64", str(path / "Contents/MacOS/SlideFlow")], check=True, capture_output=True, timeout=15)


def prepare_update(release: Release, destination: Path) -> Path:
    """Download and stage on the destination volume; clean every failure path."""
    if not os.access(destination.parent, os.W_OK):
        raise PermissionError("Không có quyền ghi. Chuyển SlideFlow vào thư mục Applications của bạn rồi thử lại.")
    stage = Path(tempfile.mkdtemp(prefix=".slideflow-update-", dir=destination.parent))
    try:
        with tempfile.TemporaryDirectory(prefix="slideflow-download-") as temporary:
            root = Path(temporary)
            dmg, mount = root / "SlideFlow.dmg", root / "volume"
            mount.mkdir()
            download_release(release, dmg)
            try:
                subprocess.run(["/usr/bin/hdiutil", "attach", "-readonly", "-nobrowse", "-mountpoint", str(mount), str(dmg)], check=True, capture_output=True, timeout=120)
                candidate = mount / "SlideFlow.app"
                verify_bundle(candidate, release.version)
                subprocess.run(["/usr/bin/ditto", str(candidate), str(stage / "SlideFlow.app")], check=True, capture_output=True, timeout=180)
                verify_bundle(stage / "SlideFlow.app", release.version)
            finally:
                detached = subprocess.run(["/usr/bin/hdiutil", "detach", str(mount)], capture_output=True, timeout=60)
                if detached.returncode and os.path.ismount(mount):
                    subprocess.run(["/usr/bin/hdiutil", "detach", "-force", str(mount)], check=True, capture_output=True, timeout=60)
        return stage
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def launch_installer(stage: Path, destination: Path) -> None:
    helper = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "scripts/install_update.sh"
    # Copy the trusted bundled helper out of the running app before replacement.
    shutil.copyfile(helper, stage / "install.sh")
    subprocess.Popen(["/bin/sh", str(stage / "install.sh"), str(os.getpid()), str(destination), str(stage)], start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
