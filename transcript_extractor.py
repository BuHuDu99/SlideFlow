"""Transcript-only extraction with resumable YouTube playlist batches."""

from __future__ import annotations

import os
import re
import tempfile
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

from online_extractor import OnlineSlideExtractor, ProgressCallback
from subtitle_proxy import (
    FreeProxyPool,
    ProxyPoolUnavailable,
    SiteCaptionUnavailable,
    fetch_caption_text,
    fetch_site_caption_text,
)
from youtube_transcript_api import AgeRestricted, IpBlocked, RequestBlocked, VideoUnavailable


@dataclass
class TranscriptBatchResult:
    title: str
    total_count: int
    saved_count: int
    reused_count: int
    new_count: int
    missing_count: int
    age_count: int
    failed_count: int
    pending_count: int
    output_dir: str
    report_path: str
    transcript_paths: list[str]
    is_playlist: bool
    paused_reason: Optional[str] = None
    method_label: str = "Phụ đề do YouTube cung cấp"


class TranscriptBatchExtractor(OnlineSlideExtractor):
    """Save existing YouTube captions, rotating public proxies only on blocking."""

    REQUEST_DELAY = 5.0
    RATE_LIMIT_RETRY_DELAY = 20.0
    method_label = "Phụ đề YouTube gốc; dự phòng client iOS, proxy công khai và DownloadYoutubeSubtitles.com"
    missing_label = "Không có phụ đề"
    missing_report_label = "Không có phụ đề/bỏ qua"
    site_fallback_enabled = True

    def __init__(
        self,
        video_url: str,
        output_dir: str,
        cookie_browser: Optional[str] = None,
        progress_callback: Optional[ProgressCallback] = None,
        cancel_event: Optional[threading.Event] = None,
        request_delay: float = REQUEST_DELAY,
        rate_limit_retry_delay: float = RATE_LIMIT_RETRY_DELAY,
    ) -> None:
        super().__init__(
            video_url,
            output_dir,
            save_transcript=True,
            cookie_browser=cookie_browser,
            progress_callback=progress_callback,
            cancel_event=cancel_event,
        )
        self.request_delay = max(0.0, float(request_delay))
        self.rate_limit_retry_delay = max(0.0, float(rate_limit_retry_delay))
        self.proxy_pool = FreeProxyPool(
            cancel_event=self.cancel_event,
            progress=lambda message: self.progress(message, 0.10),
        )
        self.prefer_proxy = False
        self.prefer_site = False
        self.prefer_ios = False
        self.last_origin = "YouTube trực tiếp"
        self.current_item_index = 1
        self.current_item_total = 1

    @staticmethod
    def is_playlist_url(url: str) -> bool:
        return bool(parse_qs(urlparse(url).query).get("list"))

    @staticmethod
    def source_key(url: str) -> Optional[tuple[str, str]]:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if list_ids := query.get("list"):
            return "playlist", list_ids[0]
        video_id = (query.get("v") or [None])[0]
        if not video_id and (parsed.hostname or "").lower() == "youtu.be":
            video_id = parsed.path.strip("/")
        if video_id and re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            return "video", video_id
        return None

    @staticmethod
    def video_link(entry: dict) -> Optional[str]:
        video_id = str(entry.get("id") or "")
        if re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            return f"https://www.youtube.com/watch?v={video_id}"
        return None

    @staticmethod
    def existing_scripts(folder: Path) -> dict[str, Path]:
        """Index nonempty saved texts by video ID; reports are never scripts."""
        found: dict[str, Path] = {}
        for path in folder.glob("*.txt"):
            match = re.search(r" - ([A-Za-z0-9_-]{11})\.txt$", path.name)
            if match:
                try:
                    if path.is_file() and path.stat().st_size > 0:
                        found.setdefault(match.group(1), path)
                except OSError:
                    continue
        return found

    def find_resume_folder(self) -> Optional[Path]:
        """Match previous batch reports by ID, ignoring share/index parameters."""
        identity = self.source_key(self.video_url)
        if not identity or not self.output_root.is_dir():
            return None
        candidates: list[Path] = []
        for folder in self.output_root.iterdir():
            if not folder.is_dir():
                continue
            for report in folder.glob("Kết quả lấy script*.txt"):
                try:
                    with report.open(encoding="utf-8") as handle:
                        first_line = handle.readline().strip()
                except (OSError, UnicodeError):
                    continue
                if first_line.startswith("Nguồn: ") and self.source_key(first_line[7:]) == identity:
                    candidates.append(folder)
                    break
        return max(candidates, key=lambda path: path.stat().st_mtime, default=None)

    @staticmethod
    def subtitle_track(info: dict) -> Optional[dict]:
        """Prefer uploaded captions, then native automatic captions over translations."""
        manual = info.get("subtitles") or {}
        track = OnlineSlideExtractor.subtitle_track({"subtitles": manual, "automatic_captions": {}})
        if track:
            return track
        automatic = info.get("automatic_captions") or {}
        original_languages = [
            language for language in ("vi-orig", "en-orig") if language in automatic
        ] + [
            language for language in automatic
            if language.endswith("-orig") and language not in ("vi-orig", "en-orig")
        ]
        for language in original_languages:
            formats = automatic.get(language) or []
            for extension in ("json3", "vtt"):
                track = next(
                    (item for item in formats if item.get("ext") == extension and item.get("url")),
                    None,
                )
                if track:
                    return track
        return OnlineSlideExtractor.subtitle_track({"subtitles": {}, "automatic_captions": automatic})

    def read_via_ios(self, video_id: str) -> Optional[str]:
        """Get a fresh original-caption URL from YouTube's iOS player client."""
        self.check_cancelled()
        self.progress("YouTube iOS • đang thử phụ đề gốc…", 0.11)
        info = self.extract_youtube_info(
            self.video_link({"id": video_id}),
            overrides={
                "ignore_no_formats_error": True,
                "extractor_args": {"youtube": {"player_client": ["ios"]}},
            },
        )
        track = self.subtitle_track(info)
        if not track:
            return None
        request = urllib.request.Request(track["url"], headers=info.get("http_headers") or {})
        with urllib.request.urlopen(request, timeout=25) as response:
            text = self.parse_transcript(response.read(), track.get("ext", "vtt"))
        text = text.strip()
        if text:
            self.last_origin = "YouTube iOS (phụ đề gốc)"
            self.prefer_ios = True
        return text or None

    def read_after_block(self, video_id: str) -> Optional[str]:
        try:
            transcript = self.read_via_ios(video_id)
            if transcript:
                return transcript
        except InterruptedError:
            raise
        except Exception:
            pass
        self.prefer_proxy = True
        return self.read_via_proxy(video_id)

    def read_transcript(self, info: dict) -> Optional[str]:
        video_id = str(info.get("id") or "")
        if (self.prefer_ios or self.prefer_proxy) and (info.get("age_limit") or 0) < 18 and re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            try:
                transcript = self.read_via_ios(video_id)
                if transcript:
                    return transcript
            except InterruptedError:
                raise
            except Exception:
                pass
        if self.prefer_proxy and (info.get("age_limit") or 0) < 18 and re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            return self.read_via_proxy(video_id)
        track = self.subtitle_track(info)
        if track:
            request = urllib.request.Request(
                track["url"], headers=info.get("http_headers") or {}
            )
            try:
                with urllib.request.urlopen(request, timeout=25) as response:
                    text = self.parse_transcript(response.read(), track.get("ext", "vtt"))
                self.last_origin = "YouTube trực tiếp"
                return text.strip() or None
            except urllib.error.HTTPError as error:
                if error.code != 429 or not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
                    raise
                return self.read_after_block(video_id)
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            return None
        try:
            transcript = fetch_caption_text(video_id)
            self.last_origin = "YouTube Transcript API trực tiếp"
            return transcript
        except (IpBlocked, RequestBlocked, VideoUnavailable):
            return self.read_after_block(video_id)

    def read_via_proxy(self, video_id: str) -> Optional[str]:
        """Use only public video IDs; no browser credentials enter this path."""
        for attempt in range(1, 17):
            self.check_cancelled()
            address = self.proxy_pool.next_proxy()
            if not address:
                return self.read_via_site(video_id, self.proxy_pool.exhausted_reason())
            self.progress(f"Proxy công khai • đang thử phụ đề {attempt}/16", 0.11)
            try:
                transcript = fetch_caption_text(video_id, proxy=address)
            except InterruptedError:
                raise
            except AgeRestricted:
                raise
            except Exception:
                self.proxy_pool.mark_bad(address)
                continue
            self.last_origin = "YouTube Transcript API qua proxy công khai"
            return transcript
        reason = (
            f"IP hiện tại bị YouTube giới hạn; 16 proxy đã thử không lấy được phụ đề "
            f"(đã kiểm tra {self.proxy_pool.scanned_count} địa chỉ)."
        )
        return self.read_via_site(video_id, reason)

    def read_via_site(self, video_id: str, proxy_reason: str) -> str:
        """Try the user-requested third-party site after direct and proxy routes fail."""
        self.check_cancelled()
        self.progress("Website dự phòng • đang lấy phụ đề tiếng Việt…", 0.11)
        try:
            transcript = fetch_site_caption_text(video_id)
        except SiteCaptionUnavailable as error:
            raise ProxyPoolUnavailable(f"{proxy_reason} Website dự phòng: {error}") from error
        self.check_cancelled()
        self.last_origin = "DownloadYoutubeSubtitles.com (phụ đề tiếng Việt)"
        self.prefer_site = True
        return transcript

    @staticmethod
    def save_text(path: Path, text: str) -> None:
        temporary_path = path.with_suffix(path.suffix + ".part")
        try:
            temporary_path.write_text(text, encoding="utf-8")
            temporary_path.replace(path)
        finally:
            temporary_path.unlink(missing_ok=True)

    @classmethod
    def error_reason(cls, error: Exception) -> tuple[str, str]:
        """Return a useful category and a report-safe reason without signed URLs."""
        message = str(error)
        lowered = message.lower()
        if (isinstance(error, urllib.error.HTTPError) and error.code == 429) or (
            "429" in lowered and "too many requests" in lowered
        ):
            return "rate_limit", "HTTP 429: YouTube giới hạn lượt lấy phụ đề"
        if isinstance(error, ProxyPoolUnavailable):
            return "proxy_unavailable", str(error)
        if isinstance(error, AgeRestricted):
            return "age", "Cần xác nhận tuổi/đăng nhập bằng trình duyệt đã chọn"
        if cls.requires_browser_cookies(error) or "không thể xác thực video" in lowered:
            return "age", "Cần xác nhận tuổi/đăng nhập bằng trình duyệt đã chọn"
        if isinstance(error, urllib.error.HTTPError):
            return "other", f"HTTP {error.code}: máy chủ từ chối yêu cầu"
        if isinstance(error, TimeoutError) or "timed out" in lowered:
            return "other", "Hết thời gian chờ kết nối"
        message = re.sub(r"https?://\S+", "[liên kết]", message)
        message = re.sub(r"\s+", " ", message).strip()
        return "other", f"{type(error).__name__}: {message[:120]}"

    def wait_for_request(self, seconds: float, message: str, progress: float) -> None:
        if seconds <= 0:
            return
        self.progress(message, progress)
        if self.cancel_event.wait(seconds):
            self.check_cancelled()

    def extract(self) -> TranscriptBatchResult:
        if not self.video_url:
            raise ValueError("Vui lòng nhập liên kết video hoặc playlist.")
        self.check_cancelled()
        is_playlist = self.is_playlist_url(self.video_url)
        self.progress("Đang đọc danh sách YouTube…" if is_playlist else "Đang đọc video YouTube…", 0.03)

        if is_playlist:
            source_info = self.extract_youtube_info(
                self.video_url, {"noplaylist": False, "extract_flat": "in_playlist"}
            )
            if not source_info or source_info.get("_type") != "playlist":
                raise RuntimeError("Không đọc được playlist từ liên kết này.")
            entries = list(source_info.get("entries") or [])
            if not entries:
                raise RuntimeError("Playlist không có video nào có thể đọc được.")
            title = self.safe_name(source_info.get("title") or "YouTube Playlist")
        else:
            source_info = self.extract_youtube_info(self.video_url)
            if not source_info:
                raise RuntimeError("Không đọc được thông tin video này.")
            entries = [source_info]
            title = self.safe_name(source_info.get("title") or "YouTube Video")

        self.check_cancelled()
        stamp = datetime.now().strftime("%Y-%m-%d %H.%M.%S.%f")
        previous_folder = self.find_resume_folder()
        job_dir = previous_folder or self.output_root / f"{title} - Scripts - {stamp}"
        if previous_folder is None:
            job_dir.mkdir(parents=True, exist_ok=False)
        existing = self.existing_scripts(job_dir)
        if previous_folder:
            self.progress(f"Đã tìm thấy {len(existing)} script • chỉ lấy video còn thiếu", 0.06)
        report_path = job_dir / (
            f"Kết quả lấy script - {stamp}.txt" if previous_folder else "Kết quả lấy script.txt"
        )

        statuses: list[str] = []
        transcript_paths: list[str] = []
        reused_count = new_count = missing_count = age_count = failed_count = pending_count = 0
        paused_reason: Optional[str] = None
        fresh_attempts = 0
        total = len(entries)

        def finish_report() -> None:
            lines = [
                f"Nguồn: {self.video_url}",
                f"Loại: {'Playlist' if is_playlist else 'Video'}",
                f"Phương pháp: {self.method_label}",
                f"Tổng video: {total}",
                f"Script hiện có: {len(transcript_paths)}",
                f"Dùng lại: {reused_count}",
                f"Mới lưu: {new_count}",
                f"{self.missing_report_label}: {missing_count}",
                f"Cần xác nhận tuổi/đăng nhập: {age_count}",
                f"Lỗi khác: {failed_count}",
                f"Chưa xử lý: {pending_count}",
                f"Trạng thái: {paused_reason or 'Đã hoàn tất'}",
                "",
                *statuses,
            ]
            self.save_text(report_path, "\n".join(lines) + "\n")

        def pending_entries(start: int, reason: str) -> None:
            nonlocal pending_count, reused_count
            for number in range(start, total + 1):
                entry = entries[number - 1] or {}
                entry_title = self.safe_name(entry.get("title") or f"Video {number}", "Video")
                video_id = str(entry.get("id") or "")
                if video_id in existing:
                    saved_path = existing[video_id]
                    reused_count += 1
                    transcript_paths.append(str(saved_path))
                    statuses.append(f"{number:03d}. Đã có • {entry_title} • {saved_path.name}")
                else:
                    pending_count += 1
                    detail = reason if number == start else "Chưa thử do tác vụ đã tạm dừng"
                    statuses.append(f"{number:03d}. Chưa xử lý • {detail} • {entry_title}")

        def save_new_script(number: int, entry_title: str, file_id: str, transcript: str) -> None:
            nonlocal new_count
            safe_id = self.safe_name(file_id or "video", "video")
            path = job_dir / f"{number:03d} - {entry_title} - {safe_id}.txt"
            self.save_text(path, transcript + "\n")
            new_count += 1
            transcript_paths.append(str(path))
            existing[safe_id] = path
            statuses.append(
                f"{number:03d}. Đã lưu • {entry_title} • {path.name} • {self.last_origin}"
            )

        current_index = 1
        last_completed_index = 0
        try:
            for current_index, raw_entry in enumerate(entries, start=1):
                self.check_cancelled()
                entry = raw_entry or {}
                video_url = self.video_link(entry)
                video_id = str(entry.get("id") or "")
                entry_title = self.safe_name(entry.get("title") or f"Video {current_index}", "Video")
                current_progress = 0.08 + 0.84 * current_index / total

                if not video_url and is_playlist:
                    missing_count += 1
                    statuses.append(f"{current_index:03d}. Bỏ qua • không có mã video • {entry_title}")
                elif video_id in existing:
                    saved_path = existing[video_id]
                    reused_count += 1
                    transcript_paths.append(str(saved_path))
                    statuses.append(f"{current_index:03d}. Đã có • {entry_title} • {saved_path.name}")
                else:
                    video_url = video_url or self.video_url
                    self.current_item_index = current_index
                    self.current_item_total = total
                    if fresh_attempts and is_playlist:
                        self.wait_for_request(
                            self.request_delay,
                            f"Giãn lượt YouTube • {current_index}/{total}",
                            current_progress,
                        )
                    fresh_attempts += 1
                    for attempt in range(2):
                        try:
                            if (
                                attempt == 0 and self.prefer_site and self.site_fallback_enabled
                                and re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id)
                            ):
                                try:
                                    transcript = self.read_via_site(video_id, "Nguồn YouTube trực tiếp đang bị giới hạn.")
                                except ProxyPoolUnavailable:
                                    self.prefer_site = False
                                else:
                                    self.check_cancelled()
                                    save_new_script(current_index, entry_title, video_id, transcript)
                                    break
                            info = (
                                source_info if not is_playlist and attempt == 0
                                else self.extract_youtube_info(video_url)
                            )
                            if not info:
                                raise RuntimeError("Không đọc được thông tin video.")
                            entry_title = self.safe_name(info.get("title") or entry_title, "Video")
                            transcript = self.read_transcript(info)
                            self.check_cancelled()
                            if transcript is None:
                                missing_count += 1
                                statuses.append(
                                    f"{current_index:03d}. {self.missing_label} • {entry_title} • {video_url}"
                                )
                            else:
                                save_new_script(
                                    current_index, entry_title,
                                    str(info.get("id") or video_id), transcript,
                                )
                            break
                        except InterruptedError:
                            raise
                        except Exception as error:
                            kind, reason = self.error_reason(error)
                            if kind == "rate_limit" and attempt == 0:
                                self.wait_for_request(
                                    self.rate_limit_retry_delay,
                                    f"HTTP 429 • chờ rồi thử lại video {current_index}/{total}",
                                    current_progress,
                                )
                                continue
                            if (
                                kind == "rate_limit" and attempt == 1
                                and self.site_fallback_enabled
                                and re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id)
                            ):
                                try:
                                    transcript = self.read_via_site(video_id, reason)
                                    self.check_cancelled()
                                    save_new_script(current_index, entry_title, video_id, transcript)
                                    break
                                except InterruptedError:
                                    raise
                                except ProxyPoolUnavailable as site_error:
                                    kind, reason = self.error_reason(site_error)
                            if kind in ("rate_limit", "proxy_unavailable"):
                                paused_reason = reason
                                pending_entries(current_index, reason)
                            elif kind == "age":
                                age_count += 1
                                statuses.append(f"{current_index:03d}. Cần xác thực • {reason} • {entry_title} • {video_url}")
                            else:
                                failed_count += 1
                                statuses.append(f"{current_index:03d}. Lỗi • {reason} • {entry_title} • {video_url}")
                            break

                if not paused_reason:
                    last_completed_index = current_index
                self.progress(
                    f"Đang lấy script • {current_index}/{total} • {entry_title}",
                    current_progress,
                )
                if paused_reason:
                    break
            if not paused_reason:
                self.check_cancelled()
        except InterruptedError:
            paused_reason = "Người dùng đã dừng"
            pending_entries(last_completed_index + 1, paused_reason)
            finish_report()
            raise

        finish_report()
        self.progress(
            "Tạm dừng lấy script; xem báo cáo và bấm Lấy script lại sau để tiếp tục"
            if paused_reason else "Hoàn tất lấy script",
            1.0 if not paused_reason else min(0.92, 0.08 + 0.84 * (total - pending_count) / total),
        )
        return TranscriptBatchResult(
            title=title,
            total_count=total,
            saved_count=len(transcript_paths),
            reused_count=reused_count,
            new_count=new_count,
            missing_count=missing_count,
            age_count=age_count,
            failed_count=failed_count,
            pending_count=pending_count,
            output_dir=str(job_dir),
            report_path=str(report_path),
            transcript_paths=transcript_paths,
            is_playlist=is_playlist,
            paused_reason=paused_reason,
            method_label=self.method_label,
        )


class AudioTranscriptBatchExtractor(TranscriptBatchExtractor):
    """Produce a clearly labelled local speech-to-text draft from audio only.

    The model is downloaded on first use to the app's model directory, not
    packaged into the .app. Each audio file is temporary and removed after
    transcription, including when an error or cancellation occurs.
    """

    method_label = "Nhận dạng tiếng nói từ âm thanh bằng Whisper; không phải phụ đề gốc"
    missing_label = "Không nhận dạng được tiếng nói"
    missing_report_label = "Không nhận dạng được tiếng nói/bỏ qua"
    site_fallback_enabled = False
    MAX_AUDIO_BYTES = 500 * 1024 * 1024
    MODEL_ALLOW_PATTERNS = (
        "config.json",
        "preprocessor_config.json",
        "model.bin",
        "tokenizer.json",
        "vocabulary.*",
    )

    def __init__(self, *args, model_size: str = "small", model_root: Optional[str] = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.model_size = model_size
        self.model_root = Path(model_root).expanduser() if model_root else (
            Path.home() / "Library" / "Application Support" / "SlideFlow" / "Models"
        )
        self._model = None

    def item_progress(self, fraction: float) -> float:
        """Map one audio stage onto the current video's share of batch progress."""
        total = max(1, int(self.current_item_total))
        index = min(total, max(1, int(self.current_item_index)))
        fraction = min(1.0, max(0.0, float(fraction)))
        return 0.08 + 0.84 * ((index - 1 + fraction) / total)

    @staticmethod
    def clock_label(seconds: float) -> str:
        seconds = max(0, int(seconds))
        return f"{seconds // 60}:{seconds % 60:02d}"

    @staticmethod
    def audio_format(info: dict) -> Optional[dict]:
        candidates = [
            fmt for fmt in info.get("formats") or []
            if str(fmt.get("url") or "").startswith("https://") and fmt.get("protocol") == "https"
            and fmt.get("vcodec") == "none" and fmt.get("acodec") != "none"
            and fmt.get("ext") in ("m4a", "mp4", "webm")
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda fmt: (
            fmt.get("filesize") or fmt.get("filesize_approx") or float("inf"),
            fmt.get("abr") or float("inf"),
        ))

    def speech_model(self):
        if self._model is None:
            try:
                from faster_whisper import WhisperModel
                from faster_whisper.utils import _MODELS, download_model
                from huggingface_hub import snapshot_download
                from huggingface_hub.errors import LocalEntryNotFoundError
                from tqdm.auto import tqdm
            except ImportError as error:
                raise RuntimeError("Thiếu faster-whisper để chép lời từ âm thanh.") from error
            self.check_cancelled()
            self.model_root.mkdir(parents=True, exist_ok=True)

            try:
                model_path = download_model(
                    self.model_size,
                    local_files_only=True,
                    cache_dir=str(self.model_root),
                )
                self.progress("Đã có mô hình Whisper • đang nạp vào bộ nhớ…", self.item_progress(0.06))
            except LocalEntryNotFoundError:
                repo_id = self.model_size if "/" in self.model_size else _MODELS.get(self.model_size)
                if not repo_id:
                    raise RuntimeError(f"Không nhận diện được mô hình Whisper: {self.model_size}")

                extractor = self
                update_lock = threading.Lock()

                class ModelDownloadProgress(tqdm):
                    """Forward Hugging Face byte progress to the desktop status card."""

                    def __init__(self, *args, **kwargs):
                        self._unit_name = kwargs.get("unit")
                        self._last_reported_percent = -1
                        kwargs["disable"] = True
                        super().__init__(*args, **kwargs)

                    def refresh(self, *args, **kwargs):
                        extractor.check_cancelled()
                        return None

                    def update(self, amount=1):
                        extractor.check_cancelled()
                        amount = 1 if amount is None else amount
                        with update_lock:
                            self.n += amount
                            if self._unit_name != "B" or amount <= 0:
                                return None
                            total = float(self.total or 0)
                            ratio = min(1.0, self.n / total) if total > 0 else 0.0
                            percent = int(ratio * 100)
                            if percent <= self._last_reported_percent:
                                return None
                            self._last_reported_percent = percent
                            downloaded_mb = max(0.0, self.n) / (1024 * 1024)
                            if total > 0:
                                total_mb = total / (1024 * 1024)
                                detail = f"{downloaded_mb:.0f}/{total_mb:.0f} MB"
                            else:
                                detail = f"{downloaded_mb:.0f} MB"
                            extractor.progress(
                                f"Đang tải mô hình Whisper lần đầu • {detail}",
                                extractor.item_progress(0.02 + 0.10 * ratio),
                            )
                        return None

                self.progress(
                    "Đang kết nối để tải mô hình Whisper lần đầu (khoảng 460 MB)…",
                    self.item_progress(0.02),
                )
                try:
                    model_path = snapshot_download(
                        repo_id,
                        cache_dir=str(self.model_root),
                        allow_patterns=self.MODEL_ALLOW_PATTERNS,
                        etag_timeout=15,
                        max_workers=2,
                        tqdm_class=ModelDownloadProgress,
                    )
                except InterruptedError:
                    raise
                except Exception as error:
                    raise RuntimeError(
                        "Không thể tải mô hình Whisper. Hãy kiểm tra kết nối mạng rồi thử lại; "
                        "phần đã tải sẽ được dùng tiếp ở lần sau."
                    ) from error
                self.progress("Đã tải mô hình Whisper • đang nạp vào bộ nhớ…", self.item_progress(0.12))

            cpu_threads = min(8, max(4, (os.cpu_count() or 4) // 2))
            self._model = WhisperModel(
                model_path,
                device="cpu",
                compute_type="int8",
                cpu_threads=cpu_threads,
                local_files_only=True,
            )
            self.check_cancelled()
            self.progress("Mô hình Whisper đã sẵn sàng", self.item_progress(0.15))
        return self._model

    def read_transcript(self, info: dict) -> Optional[str]:
        self.last_origin = "Whisper cục bộ từ âm thanh"
        fmt = self.audio_format(info)
        if not fmt:
            raise RuntimeError("YouTube không cung cấp luồng âm thanh công khai phù hợp.")
        try:
            expected_size = int(fmt.get("filesize") or fmt.get("filesize_approx") or 0)
        except (TypeError, ValueError):
            expected_size = 0
        if expected_size > self.MAX_AUDIO_BYTES:
            raise RuntimeError("Âm thanh vượt quá 500 MB; video này được bỏ qua để tránh đầy ổ đĩa.")
        self.check_cancelled()
        model = self.speech_model()
        extension = "m4a" if fmt["ext"] == "mp4" else fmt["ext"]
        with tempfile.TemporaryDirectory(prefix="slideflow-audio-") as temporary:
            audio_path = Path(temporary) / f"audio.{extension}"
            headers = info.get("http_headers") or {}
            request = urllib.request.Request(fmt["url"], headers=headers)
            self.progress("Đang tải tạm âm thanh để chép lời…", self.item_progress(0.16))
            with urllib.request.urlopen(request, timeout=40) as response, audio_path.open("wb") as destination:
                size = 0
                response_headers = getattr(response, "headers", {})
                content_length = response_headers.get("Content-Length") if response_headers else None
                total_size = int(content_length or expected_size or 0)
                last_audio_percent = -1
                last_audio_reported_bytes = -1
                while chunk := response.read(1024 * 1024):
                    self.check_cancelled()
                    size += len(chunk)
                    if size > self.MAX_AUDIO_BYTES:
                        raise RuntimeError("Âm thanh vượt quá 500 MB; video này được bỏ qua để tránh đầy ổ đĩa.")
                    destination.write(chunk)
                    ratio = min(1.0, size / total_size) if total_size > 0 else 0.0
                    percent = int(ratio * 100)
                    should_report = (
                        percent > last_audio_percent
                        if total_size > 0
                        else last_audio_reported_bytes < 0
                        or size - last_audio_reported_bytes >= 5 * 1024 * 1024
                    )
                    if should_report:
                        last_audio_percent = percent
                        last_audio_reported_bytes = size
                        downloaded_mb = size / (1024 * 1024)
                        detail = (
                            f"{downloaded_mb:.0f}/{total_size / (1024 * 1024):.0f} MB"
                            if total_size > 0 else f"{downloaded_mb:.0f} MB"
                        )
                        self.progress(
                            f"Đang tải âm thanh • {detail}",
                            self.item_progress(0.16 + 0.12 * ratio),
                        )
            self.check_cancelled()
            self.progress("Đang phân tích âm thanh trước khi nhận dạng…", self.item_progress(0.30))
            segments, transcription_info = model.transcribe(
                str(audio_path),
                language="vi",
                beam_size=1,
                best_of=1,
                vad_filter=True,
                condition_on_previous_text=False,
            )
            duration = float(
                getattr(transcription_info, "duration", 0)
                or info.get("duration")
                or 0
            )
            lines = []
            last_transcript_percent = -1
            for segment in segments:
                self.check_cancelled()
                line = re.sub(r"\s+", " ", segment.text).strip()
                if line and (not lines or line != lines[-1]):
                    lines.append(line)
                segment_end = float(getattr(segment, "end", 0) or 0)
                ratio = min(1.0, segment_end / duration) if duration > 0 else 0.0
                percent = int(ratio * 100)
                if percent > last_transcript_percent:
                    last_transcript_percent = percent
                    detail = (
                        f"{self.clock_label(segment_end)}/{self.clock_label(duration)}"
                        if duration > 0 else self.clock_label(segment_end)
                    )
                    self.progress(
                        f"Đang nhận dạng tiếng nói • {detail}",
                        self.item_progress(0.30 + 0.68 * ratio),
                    )
            self.progress("Đã nhận dạng xong âm thanh", self.item_progress(0.98))
        if not lines:
            return None
        return "[Bản chép lời tự động từ âm thanh; cần kiểm tra lại, nhất là thuật ngữ chuyên môn]\n" + "\n".join(lines)
