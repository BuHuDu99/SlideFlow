"""Bounded, credential-free proxy rotation for public YouTube captions.

Inspired by yt-bulk-subtitles-downloader's proxy pool, but without Selenium,
browser cookies, unbounded workers, or disabled TLS verification.
"""

from __future__ import annotations

import html
import ipaddress
import random
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Optional
from urllib.parse import quote

import requests
from youtube_transcript_api import (
    NoTranscriptFound,
    TranscriptsDisabled,
    YouTubeTranscriptApi,
)
from youtube_transcript_api.proxies import GenericProxyConfig


class ProxyPoolUnavailable(RuntimeError):
    """No usable public proxy was found within the configured search bound."""


class SiteCaptionUnavailable(RuntimeError):
    """The user-selected caption download site could not return a usable text."""


class SiteCaptionRateLimited(SiteCaptionUnavailable):
    """The download site has temporarily throttled or challenged this client."""


class TimedSession(requests.Session):
    """youtube-transcript-api does not set a request timeout itself."""

    def __init__(self, timeout: float = 7.0) -> None:
        super().__init__()
        self.timeout = timeout
        self.trust_env = False

    def request(self, *args, **kwargs):
        kwargs.setdefault("timeout", self.timeout)
        return super().request(*args, **kwargs)


def parse_public_proxies(page: str) -> list[str]:
    """Read the raw-list modal and reject loopback/private proxy targets."""
    candidates: list[str] = []
    for block in re.findall(r"<textarea[^>]*>(.*?)</textarea>", page, re.I | re.S):
        candidates.extend(
            re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}:\d{1,5}\b", html.unescape(block))
        )
    found: list[str] = []
    for address in dict.fromkeys(candidates):
        ip_text, port_text = address.rsplit(":", 1)
        try:
            ip = ipaddress.IPv4Address(ip_text)
            port = int(port_text)
        except (ipaddress.AddressValueError, ValueError):
            continue
        if ip.is_global and 1 <= port <= 65535:
            found.append(address)
    return found


class FreeProxyPool:
    SOURCE = "https://free-proxy-list.net/en/"
    CHECK_URL = "https://www.youtube.com/"

    def __init__(
        self,
        cancel_event: Optional[threading.Event] = None,
        progress: Optional[Callable[[str], None]] = None,
        max_scanned: int = 128,
        scan_batch: int = 48,
        workers: int = 12,
        check_timeout: float = 3.0,
        source_url: str = SOURCE,
    ) -> None:
        self.cancel_event = cancel_event or threading.Event()
        self.progress = progress
        self.max_scanned = max(0, max_scanned)
        self.scan_batch = max(1, scan_batch)
        self.workers = max(1, min(workers, 16))
        self.check_timeout = check_timeout
        self.source_url = source_url
        self.candidates: Optional[list[str]] = None
        self.next_index = 0
        self.scanned_count = 0
        self.working: list[str] = []
        self.bad: set[str] = set()

    def check_cancelled(self) -> None:
        if self.cancel_event.is_set():
            raise InterruptedError("Người dùng đã dừng")

    def load_candidates(self) -> None:
        if self.candidates is not None:
            return
        self.check_cancelled()
        try:
            with TimedSession(timeout=15) as session:
                response = session.get(self.source_url)
                response.raise_for_status()
            candidates = parse_public_proxies(response.text)
        except requests.RequestException as error:
            raise ProxyPoolUnavailable("Không tải được danh sách proxy công khai.") from error
        if not candidates:
            raise ProxyPoolUnavailable("Danh sách proxy công khai không có địa chỉ hợp lệ.")
        random.SystemRandom().shuffle(candidates)
        self.candidates = candidates

    def _check_proxy(self, address: str) -> bool:
        proxy_url = f"http://{address}"
        try:
            with TimedSession(timeout=self.check_timeout) as session:
                response = session.get(
                    self.CHECK_URL,
                    proxies={"http": proxy_url, "https": proxy_url},
                )
            return 200 <= response.status_code < 400
        except requests.RequestException:
            return False

    def scan(self) -> None:
        self.load_candidates()
        assert self.candidates is not None
        budget = self.max_scanned - self.scanned_count
        batch = self.candidates[self.next_index:self.next_index + min(self.scan_batch, budget)]
        if not batch:
            return
        self.next_index += len(batch)
        self.scanned_count += len(batch)
        if self.progress:
            self.progress(f"Đang kiểm tra proxy công khai • {self.scanned_count}/{self.max_scanned}")
        with ThreadPoolExecutor(max_workers=self.workers) as executor:
            futures = {executor.submit(self._check_proxy, address): address for address in batch}
            for future in as_completed(futures):
                self.check_cancelled()
                address = futures[future]
                if future.result() and address not in self.bad:
                    self.working.append(address)
        random.SystemRandom().shuffle(self.working)

    def next_proxy(self) -> Optional[str]:
        self.check_cancelled()
        while not self.working and self.scanned_count < self.max_scanned:
            self.scan()
        if not self.working:
            return None
        address = self.working.pop(0)
        self.working.append(address)
        return address

    def mark_bad(self, address: str) -> None:
        self.bad.add(address)
        self.working = [item for item in self.working if item != address]

    def exhausted_reason(self) -> str:
        return f"IP hiện tại bị YouTube giới hạn; không tìm được proxy lấy phụ đề sau {self.scanned_count} lượt kiểm tra."


def fetch_caption_text(video_id: str, proxy: Optional[str] = None, timeout: float = 7.0) -> Optional[str]:
    """Fetch an existing/manual-or-ASR YouTube transcript, without auth cookies."""
    session = TimedSession(timeout=timeout)
    session.headers.update({"Accept-Language": "vi,en-US;q=0.8"})
    proxy_config = None
    if proxy:
        proxy_url = f"http://{proxy}"
        proxy_config = GenericProxyConfig(http_url=proxy_url, https_url=proxy_url)
    try:
        api = YouTubeTranscriptApi(proxy_config=proxy_config, http_client=session)
        try:
            available = api.list(video_id)
        except TranscriptsDisabled:
            return None
        tracks = list(available)
        if not tracks:
            return None
        try:
            chosen = available.find_transcript(["vi", "vi-VN", "en", "en-US"])
        except NoTranscriptFound:
            chosen = tracks[0]
        fetched = chosen.fetch()
        lines: list[str] = []
        for snippet in fetched:
            line = re.sub(r"\s+", " ", html.unescape(snippet.text)).strip()
            if line and (not lines or line != lines[-1]):
                lines.append(line)
        return "\n".join(lines) or None
    finally:
        session.close()


def fetch_site_caption_text(video_id: str, timeout: float = 30.0) -> str:
    """Last-resort TXT from DownloadYoutubeSubtitles.com for a public video ID.

    The site gets only the public video URL. Browser cookies are never sent.
    """
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        raise ValueError("Mã video YouTube không hợp lệ.")
    watch_url = f"https://www.youtube.com/watch?v={video_id}"
    referer = f"https://www.downloadyoutubesubtitles.com/?u={quote(watch_url, safe='')}"
    with TimedSession(timeout=timeout) as session:
        session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/152.0.0.0 Safari/537.36"
            ),
            "Referer": referer,
        })
        for language in ("a.vi", "vi"):
            url = (
                "https://www.downloadyoutubesubtitles.com/get2.php?"
                f"i={video_id}&format=txt&hl={language}&a="
            )
            try:
                response = session.get(url)
                if response.status_code == 404:
                    continue
                if response.status_code == 429:
                    raise SiteCaptionRateLimited("HTTP 429: website dự phòng giới hạn lượt lấy phụ đề.")
                response.raise_for_status()
            except requests.RequestException as error:
                status = getattr(getattr(error, "response", None), "status_code", None)
                detail = f"HTTP {status}" if status else "lỗi kết nối"
                raise SiteCaptionUnavailable(f"{detail}: website dự phòng từ chối yêu cầu lấy phụ đề.") from error
            content_type = response.headers.get("Content-Type", "").lower()
            if not content_type.startswith(("application/force-download", "application/octet-stream", "text/plain")):
                raise SiteCaptionUnavailable("Website dự phòng trả HTML thay vì tệp TXT; có thể cần xác minh trên trình duyệt.")
            try:
                text = response.content.decode("utf-8-sig")
            except UnicodeError as error:
                raise SiteCaptionUnavailable("Tệp TXT từ website dự phòng không đọc được.") from error
            lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
            clean = "\n".join(line for line in lines if line)
            if clean:
                return clean
    raise SiteCaptionUnavailable("HTTP 404: website dự phòng không có tệp TXT tiếng Việt cho video này.")
