"""Online-first YouTube slide extraction engine."""

from __future__ import annotations

import argparse
import math
import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import cv2
import img2pdf
import numpy as np
import yt_dlp


ProgressCallback = Callable[[str, float], None]


@dataclass
class ExtractionResult:
    title: str
    duration: float
    slide_count: int
    sampled_count: int
    output_dir: str
    pdf_path: str
    transcript_path: Optional[str]
    mode: str = "online"


@dataclass(frozen=True)
class PlaylistVideo:
    position: int
    video_id: str
    title: str
    url: str
    duration: Optional[float] = None


@dataclass(frozen=True)
class PlaylistResult:
    title: str
    videos: list[PlaylistVideo]
    unavailable_count: int = 0


@dataclass
class SavedFrame:
    path: Path
    frame_hash: int
    gray_small: np.ndarray
    sharpness: float
    edge_density: float
    timestamp: float

    @property
    def quality_score(self) -> float:
        return self.sharpness + self.edge_density * 1000.0


def parse_end_time(value: str) -> Optional[float]:
    """Accept hh:mm:ss or mm:ss, with a blank field meaning the full video."""
    value = value.strip()
    if not value:
        return None
    parts = value.split(":")
    if len(parts) not in (2, 3) or any(not re.fullmatch(r"[0-9]+", part) for part in parts):
        raise ValueError("Nhập thời gian dạng hh:mm:ss hoặc mm:ss, ví dụ 1:30:00 hoặc 90:00.")
    numbers = list(map(int, parts))
    if numbers[-1] >= 60 or (len(numbers) == 3 and numbers[-2] >= 60):
        raise ValueError("Giây và phút trong hh:mm:ss phải nhỏ hơn 60.")
    seconds = sum(number * 60 ** index for index, number in enumerate(reversed(numbers)))
    if seconds <= 0 or seconds > 365 * 24 * 3600:
        raise ValueError("Mốc kết thúc phải lớn hơn 0 và không vượt quá 365 ngày.")
    return float(seconds)


class OnlineSlideExtractor:
    """Extract frames from a temporary stream URL without saving the video."""

    COOKIE_BROWSER_NAMES = {
        "chrome": "Chrome",
        "safari": "Safari",
        "firefox": "Firefox",
    }
    AUTH_ERROR_MARKERS = (
        "sign in to confirm your age",
        "age-restricted",
        "login required",
        "use --cookies-from-browser",
        "confirm you’re not a bot",
        "confirm you're not a bot",
    )

    def __init__(
        self,
        video_url: str,
        output_dir: str,
        interval: int = 8,
        similarity_threshold: float = 0.90,
        quality: int = 1080,
        save_transcript: bool = True,
        cookie_browser: Optional[str] = None,
        progress_callback: Optional[ProgressCallback] = None,
        cancel_event: Optional[threading.Event] = None,
        end_time: Optional[float] = None,
    ) -> None:
        if end_time is not None and (not math.isfinite(end_time) or end_time <= 0):
            raise ValueError("Mốc kết thúc phải là số giây lớn hơn 0.")
        self.end_time = end_time
        self.video_url = video_url.strip()
        self.output_root = Path(output_dir).expanduser()
        self.interval = max(2, int(interval))
        self.similarity_threshold = min(0.99, max(0.75, float(similarity_threshold)))
        self.quality = min(1080, max(360, int(quality)))
        self.save_transcript = save_transcript
        normalized_browser = (cookie_browser or "").strip().lower()
        self.cookie_browser = normalized_browser if normalized_browser in self.COOKIE_BROWSER_NAMES else None
        self.progress_callback = progress_callback
        self.cancel_event = cancel_event or threading.Event()
        self.last_observed: Optional[SavedFrame] = None

    @staticmethod
    def safe_name(value: str, fallback: str = "YouTube Slides") -> str:
        value = re.sub(r"[\\/:*?\"<>|]+", " ", value)
        value = re.sub(r"\s+", " ", value).strip(" .")
        return value[:80] or fallback

    @staticmethod
    def ffmpeg_executable() -> str:
        try:
            import imageio_ffmpeg

            return imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            executable = shutil.which("ffmpeg")
            if executable:
                return executable
        raise RuntimeError("Không tìm thấy FFmpeg đi kèm ứng dụng.")

    def progress(self, message: str, value: float) -> None:
        if self.progress_callback:
            self.progress_callback(message, min(1.0, max(0.0, value)))

    def check_cancelled(self) -> None:
        if self.cancel_event.is_set():
            raise InterruptedError("Đã dừng theo yêu cầu của người dùng.")

    @staticmethod
    def deno_executable() -> Optional[str]:
        executable_dir = Path(sys.executable).parent
        contents_dir = executable_dir.parent
        bundle_root = Path(getattr(sys, "_MEIPASS", executable_dir))
        candidates = (
            executable_dir / "deno",
            contents_dir / "Frameworks" / "deno",
            contents_dir / "Resources" / "deno",
            bundle_root / "deno",
        )
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
        return shutil.which("deno")

    @classmethod
    def youtube_options(cls, cookie_browser: Optional[str] = None) -> dict:
        options = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": True,
            "socket_timeout": 20,
            "retries": 2,
            "extractor_retries": 2,
        }
        if deno_path := cls.deno_executable():
            options["js_runtimes"] = {"deno": {"path": deno_path}}
        if cookie_browser:
            options["cookiesfrombrowser"] = (cookie_browser,)
            # YouTube's tv_downgraded client currently returns UNPLAYABLE/
            # "The page needs to be reloaded" for some logged-in sessions.
            options["extractor_args"] = {
                "youtube": {
                    "player_client": ["default", "-tv_downgraded", "web_embedded"],
                },
            }
        return options

    @classmethod
    def requires_browser_cookies(cls, error: Exception) -> bool:
        message = str(error).lower()
        return any(marker in message for marker in cls.AUTH_ERROR_MARKERS)

    def extract_youtube_info(self, url: str, overrides: Optional[dict] = None) -> dict:
        """Fetch metadata publicly, retrying with browser login only if required."""
        options = self.youtube_options()
        options.update(overrides or {})
        try:
            with yt_dlp.YoutubeDL(options) as downloader:
                info = downloader.extract_info(url, download=False)
        except yt_dlp.utils.DownloadError as error:
            if not self.cookie_browser or not self.requires_browser_cookies(error):
                raise

            browser_name = self.COOKIE_BROWSER_NAMES[self.cookie_browser]
            self.progress(
                f"Video giới hạn tuổi • đang dùng đăng nhập {browser_name}…",
                0.04,
            )
            try:
                cookie_options = self.youtube_options(self.cookie_browser)
                cookie_options.update(overrides or {})
                with yt_dlp.YoutubeDL(cookie_options) as downloader:
                    info = downloader.extract_info(url, download=False)
            except Exception as cookie_error:
                cookie_detail = str(cookie_error).lower()
                if "could not find chrome cookies database" in cookie_detail or "operation not permitted" in cookie_detail:
                    raise RuntimeError(
                        f"Không thể đọc phiên đăng nhập {browser_name} trên máy này. "
                        "Hãy kiểm tra quyền truy cập hồ sơ trình duyệt của SlideFlow trong Cài đặt macOS, "
                        "rồi thử lại. "
                        f"Chi tiết: {cookie_error}"
                    ) from cookie_error
                raise RuntimeError(
                    f"Không thể xác thực video bằng {browser_name}. "
                    f"Hãy đăng nhập YouTube và xác nhận tuổi trong {browser_name}, rồi thử lại. "
                    "Nếu macOS hỏi quyền truy cập mật khẩu/cookie, hãy cho phép SlideFlow. "
                    f"Chi tiết: {cookie_error}"
                ) from cookie_error
        return info

    def resolve_video(self) -> dict:
        self.progress("Đang kết nối với YouTube…", 0.03)
        info = self.extract_youtube_info(self.video_url)
        if not info:
            raise RuntimeError("YouTube không cung cấp được luồng video phù hợp.")
        if not info.get("duration"):
            raise RuntimeError("Không xác định được thời lượng video.")
        stream, is_dash_stream = self.select_video_stream(info.get("formats") or [])
        if stream:
            info = dict(info)
            info["url"] = stream["url"]
            info["http_headers"] = stream.get("http_headers") or info.get("http_headers") or {}
            if is_dash_stream:
                info["_dash_stream"] = stream
        elif not info.get("url"):
            raise RuntimeError("YouTube không cung cấp được luồng video phù hợp.")
        return info

    def resolve_playlist(self) -> PlaylistResult:
        """Return lightweight playlist entries without downloading any video."""
        self.progress("Đang đọc danh sách video trong playlist…", 0.02)
        info = self.extract_youtube_info(
            self.video_url,
            {"noplaylist": False, "extract_flat": "in_playlist"},
        )
        if not info or info.get("_type") != "playlist":
            raise RuntimeError("Không đọc được playlist từ liên kết này.")

        videos: list[PlaylistVideo] = []
        unavailable_count = 0
        for position, raw_entry in enumerate(info.get("entries") or [], start=1):
            entry = raw_entry or {}
            video_id = str(entry.get("id") or "")
            if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
                unavailable_count += 1
                continue
            raw_duration = entry.get("duration")
            try:
                duration = float(raw_duration) if raw_duration is not None else None
            except (TypeError, ValueError):
                duration = None
            videos.append(
                PlaylistVideo(
                    position=position,
                    video_id=video_id,
                    title=self.safe_name(entry.get("title") or f"Video {position}", "Video"),
                    url=f"https://www.youtube.com/watch?v={video_id}",
                    duration=duration,
                )
            )
        if not videos:
            raise RuntimeError("Playlist không có video nào có thể xử lý.")
        return PlaylistResult(
            title=self.safe_name(info.get("title") or "YouTube Playlist", "YouTube Playlist"),
            videos=videos,
            unavailable_count=unavailable_count,
        )

    def select_direct_stream(self, formats: list[dict]) -> Optional[dict]:
        """Choose a range-seekable HTTP stream instead of a slow HLS playlist."""
        candidates = [
            item
            for item in formats
            if item.get("url")
            and item.get("protocol") in {"http", "https"}
            and item.get("vcodec") not in {None, "none"}
            and 0 < float(item.get("height") or 0)
        ]
        candidates = self.preferred_quality_candidates(candidates)
        if not candidates:
            return None
        return max(
            candidates,
            key=lambda item: (
                float(item.get("height") or 0),
                item.get("ext") == "mp4",
                str(item.get("vcodec") or "").startswith(("avc", "h264")),
                float(item.get("fps") or 0),
                float(item.get("tbr") or 0),
            ),
        )

    def select_dash_stream(self, formats: list[dict]) -> Optional[dict]:
        """Choose a fragmented video stream when YouTube has no direct HTTP URL."""
        candidates = [
            item
            for item in formats
            if item.get("url")
            and item.get("protocol") == "http_dash_segments"
            and item.get("vcodec") not in {None, "none"}
            and 0 < float(item.get("height") or 0)
            and float(item.get("target_duration") or 0) > 0
            and len(item.get("fragments") or []) >= 2
        ]
        candidates = self.preferred_quality_candidates(candidates)
        if not candidates:
            return None
        return max(
            candidates,
            key=lambda item: (
                float(item.get("height") or 0),
                item.get("ext") == "mp4",
                str(item.get("vcodec") or "").startswith(("avc", "h264")),
                float(item.get("fps") or 0),
            ),
        )

    def preferred_quality_candidates(self, candidates: list[dict]) -> list[dict]:
        """Prefer 1080p exactly, falling back silently to the highest quality."""
        if self.quality == 1080:
            exact = [
                item
                for item in candidates
                if float(item.get("height") or 0) == 1080
            ]
            return exact or candidates
        return [
            item
            for item in candidates
            if float(item.get("height") or 0) <= self.quality
        ]

    def select_video_stream(self, formats: list[dict]) -> tuple[Optional[dict], bool]:
        """Pick the requested resolution across direct and fragmented streams."""
        direct_stream = self.select_direct_stream(formats)
        dash_stream = self.select_dash_stream(formats)
        candidates = [
            (stream, is_dash)
            for stream, is_dash in ((direct_stream, False), (dash_stream, True))
            if stream is not None
        ]
        if not candidates:
            return None, False

        exact = [
            candidate
            for candidate in candidates
            if float(candidate[0].get("height") or 0) == self.quality
        ]
        pool = exact or candidates
        return max(
            pool,
            key=lambda candidate: (
                float(candidate[0].get("height") or 0),
                not candidate[1],
            ),
        )

    @staticmethod
    def header_blob(headers: dict) -> str:
        allowed = {"User-Agent", "Referer", "Origin", "Accept-Language"}
        return "".join(
            f"{key}: {value}\r\n"
            for key, value in headers.items()
            if key in allowed and value
        )

    def fetch_frame(self, stream_url: str, headers: dict, timestamp: float) -> np.ndarray:
        command = [
            self.ffmpeg_executable(),
            "-hide_banner",
            "-loglevel", "error",
            "-nostdin",
            "-rw_timeout", "20000000",
        ]
        command.extend(["-ss", f"{max(0.0, timestamp):.3f}"])
        blob = self.header_blob(headers)
        if blob:
            command.extend(["-headers", blob])
        command.extend([
            "-i", stream_url,
            "-frames:v", "1",
            "-an",
            "-f", "image2pipe",
            "-vcodec", "mjpeg",
            "-q:v", "2",
            "pipe:1",
        ])
        completed = subprocess.run(command, capture_output=True, timeout=45, check=False)
        if completed.returncode != 0 or not completed.stdout:
            error = completed.stderr.decode("utf-8", errors="ignore").strip()
            raise RuntimeError(error or f"Không lấy được khung hình tại giây {timestamp:.0f}.")
        frame = cv2.imdecode(np.frombuffer(completed.stdout, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise RuntimeError(f"Khung hình tại giây {timestamp:.0f} không hợp lệ.")
        return frame

    @staticmethod
    def fetch_dash_fragment(fragment: dict, headers: dict) -> bytes:
        url = fragment.get("url")
        if not url:
            raise RuntimeError("Mảnh video DASH không có liên kết.")
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=25) as response:
            data = response.read()
        if not data:
            raise RuntimeError("Mảnh video DASH bị rỗng.")
        return data

    def fetch_dash_frame(
        self, stream: dict, headers: dict, timestamp: float, initial_fragment: bytes
    ) -> np.ndarray:
        """Decode one frame from a tiny temporary MP4 made of DASH fragments."""
        fragments = stream["fragments"]
        segment_duration = float(stream["target_duration"])
        segment_index = min(len(fragments) - 1, max(1, int(max(0.0, timestamp) / segment_duration) + 1))
        segment = self.fetch_dash_fragment(fragments[segment_index], headers)
        suffix = ".mp4" if stream.get("ext") == "mp4" else f".{stream.get('ext') or 'mp4'}"
        temporary_path: Optional[Path] = None
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temporary_file:
                temporary_path = Path(temporary_file.name)
                temporary_file.write(initial_fragment)
                temporary_file.write(segment)
            command = [
                self.ffmpeg_executable(),
                "-hide_banner", "-loglevel", "error", "-nostdin",
                "-i", str(temporary_path),
                "-ss", f"{max(0.0, timestamp):.3f}",
                "-frames:v", "1", "-an",
                "-f", "image2pipe", "-vcodec", "png", "pipe:1",
            ]
            completed = subprocess.run(command, capture_output=True, timeout=30, check=False)
            if completed.returncode != 0 or not completed.stdout:
                error = completed.stderr.decode("utf-8", errors="ignore").strip()
                raise RuntimeError(error or f"Không lấy được khung hình tại giây {timestamp:.0f}.")
            frame = cv2.imdecode(np.frombuffer(completed.stdout, dtype=np.uint8), cv2.IMREAD_COLOR)
            if frame is None:
                raise RuntimeError(f"Khung hình tại giây {timestamp:.0f} không hợp lệ.")
            return frame
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    @staticmethod
    def frame_hash(gray: np.ndarray, size: int = 8) -> int:
        reduced = cv2.resize(gray, (size + 1, size), interpolation=cv2.INTER_AREA)
        differences = reduced[:, 1:] > reduced[:, :-1]
        result = 0
        for bit in differences.flatten():
            result = (result << 1) | int(bit)
        return result

    @staticmethod
    def hamming_distance(first: int, second: int) -> int:
        return (first ^ second).bit_count()

    @staticmethod
    def analyse_frame(frame: np.ndarray) -> tuple[int, np.ndarray, float, float]:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray_small = cv2.resize(gray, (320, 180), interpolation=cv2.INTER_AREA)
        sharpness = float(cv2.Laplacian(gray_small, cv2.CV_64F).var())
        edges = cv2.Canny(gray_small, 80, 180)
        edge_density = float(np.count_nonzero(edges)) / float(edges.size)
        return OnlineSlideExtractor.frame_hash(gray_small), gray_small, sharpness, edge_density

    def is_duplicate(self, previous: SavedFrame, frame_hash: int, gray_small: np.ndarray) -> bool:
        """Return True only when two frames are effectively the same slide.

        Handheld recordings introduce tiny camera shifts and lighting changes even
        while the slide stays unchanged.  Align the frames, remove slow lighting
        gradients, then compare high-frequency structure and tolerant edge overlap.
        """
        previous_gray = previous.gray_small
        height, width = previous_gray.shape
        y0, y1 = int(height * 0.08), int(height * 0.97)
        x0, x1 = int(width * 0.03), int(width * 0.97)
        first = previous_gray[y0:y1, x0:x1]
        second = gray_small[y0:y1, x0:x1]

        warp = np.eye(2, 3, dtype=np.float32)
        try:
            cv2.findTransformECC(
                first,
                second,
                warp,
                cv2.MOTION_TRANSLATION,
                (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 18, 0.002),
                None,
                3,
            )
        except cv2.error:
            pass
        if abs(float(warp[0, 2])) > 12 or abs(float(warp[1, 2])) > 12:
            warp = np.eye(2, 3, dtype=np.float32)
        second = cv2.warpAffine(
            second,
            warp,
            (first.shape[1], first.shape[0]),
            flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
            borderMode=cv2.BORDER_REPLICATE,
        )

        def structural_image(image: np.ndarray) -> np.ndarray:
            local_contrast = image.astype(np.float32) - cv2.GaussianBlur(image, (0, 0), 15)
            return cv2.normalize(local_contrast, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)

        first_structure = structural_image(first)
        second_structure = structural_image(second)
        correlation = float(
            cv2.matchTemplate(first_structure, second_structure, cv2.TM_CCOEFF_NORMED)[0, 0]
        )

        first_edges = cv2.Canny(first_structure, 60, 140)
        second_edges = cv2.Canny(second_structure, 60, 140)
        kernel = np.ones((3, 3), dtype=np.uint8)
        first_dilated = cv2.dilate(first_edges, kernel)
        second_dilated = cv2.dilate(second_edges, kernel)
        edge_total = np.count_nonzero(first_edges) + np.count_nonzero(second_edges)
        edge_overlap = (
            np.count_nonzero(first_edges & second_dilated)
            + np.count_nonzero(second_edges & first_dilated)
        ) / max(1, edge_total)

        edge_threshold = max(0.70, self.similarity_threshold - 0.10)
        return correlation >= self.similarity_threshold and edge_overlap >= edge_threshold

    @staticmethod
    def timestamp_label(seconds: float) -> str:
        total = max(0, int(round(seconds)))
        hours, remainder = divmod(total, 3600)
        minutes, secs = divmod(remainder, 60)
        return f"{hours:02d}-{minutes:02d}-{secs:02d}"

    def save_frame(self, frame: np.ndarray, folder: Path, index: int, timestamp: float) -> Path:
        path = folder / f"slide_{index:03d}_{self.timestamp_label(timestamp)}.jpg"
        if not cv2.imwrite(str(path), frame, [cv2.IMWRITE_JPEG_QUALITY, 94]):
            raise RuntimeError(f"Không thể lưu slide {index}.")
        return path

    def select_frame(
        self,
        frame: np.ndarray,
        timestamp: float,
        folder: Path,
        saved: list[SavedFrame],
    ) -> None:
        frame_hash, gray_small, sharpness, edge_density = self.analyse_frame(frame)
        candidate_score = sharpness + edge_density * 1000.0
        observed = SavedFrame(
            Path(), frame_hash, gray_small, sharpness, edge_density, timestamp
        )

        if not saved:
            path = self.save_frame(frame, folder, 1, timestamp)
            saved.append(SavedFrame(path, frame_hash, gray_small, sharpness, edge_density, timestamp))
            self.last_observed = observed
            return

        previous = saved[-1]
        comparison_frame = self.last_observed or previous
        if self.is_duplicate(comparison_frame, frame_hash, gray_small):
            if candidate_score > previous.quality_score * 1.12:
                previous.path.unlink(missing_ok=True)
                path = self.save_frame(frame, folder, len(saved), timestamp)
                saved[-1] = SavedFrame(path, frame_hash, gray_small, sharpness, edge_density, timestamp)
            self.last_observed = observed
            return

        if sharpness < 12.0 and edge_density < 0.01:
            return
        path = self.save_frame(frame, folder, len(saved) + 1, timestamp)
        saved.append(SavedFrame(path, frame_hash, gray_small, sharpness, edge_density, timestamp))
        self.last_observed = observed

    @staticmethod
    def subtitle_track(info: dict) -> Optional[dict]:
        sources = [info.get("subtitles") or {}, info.get("automatic_captions") or {}]
        preferred = ("vi", "vi-VN", "en", "en-US")
        for source in sources:
            languages = list(source)
            for language in preferred + tuple(languages):
                formats = source.get(language) or []
                for extension in ("json3", "vtt"):
                    match = next((item for item in formats if item.get("ext") == extension), None)
                    if match and match.get("url"):
                        return match
        return None

    @staticmethod
    def parse_transcript(data: bytes, extension: str) -> str:
        if extension == "json3":
            payload = json.loads(data.decode("utf-8", errors="replace"))
            lines: list[str] = []
            for event in payload.get("events", []):
                text = "".join(segment.get("utf8", "") for segment in event.get("segs", []))
                text = re.sub(r"\s+", " ", text).strip()
                if text and (not lines or text != lines[-1]):
                    lines.append(text)
            return "\n".join(lines)

        text = data.decode("utf-8", errors="replace")
        lines = []
        for line in text.splitlines():
            line = re.sub(r"<[^>]+>", "", line).strip()
            if not line or "-->" in line or line.startswith(("WEBVTT", "Kind:", "Language:")):
                continue
            if not lines or line != lines[-1]:
                lines.append(line)
        return "\n".join(lines)

    def save_available_transcript(self, info: dict, folder: Path, title: str) -> Optional[str]:
        if not self.save_transcript:
            return None
        track = self.subtitle_track(info)
        if not track:
            return None
        request = urllib.request.Request(track["url"], headers=info.get("http_headers") or {})
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                text = self.parse_transcript(response.read(), track.get("ext", "vtt"))
        except Exception:
            return None
        if not text.strip():
            return None
        path = folder / f"{self.safe_name(title)} - transcript.txt"
        path.write_text(text, encoding="utf-8")
        return str(path)

    @staticmethod
    def create_pdf(saved: list[SavedFrame], pdf_path: Path) -> None:
        """Write atomically so source images remain recoverable on PDF failure."""
        temporary_path = pdf_path.with_suffix(f"{pdf_path.suffix}.part")
        try:
            pdf_data = img2pdf.convert([str(item.path) for item in saved])
            if not pdf_data or not pdf_data.startswith(b"%PDF"):
                raise RuntimeError("Tệp PDF được tạo không hợp lệ.")
            temporary_path.write_bytes(pdf_data)
            if temporary_path.stat().st_size == 0:
                raise RuntimeError("Tệp PDF được tạo bị rỗng.")
            temporary_path.replace(pdf_path)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise

    def extract(self) -> ExtractionResult:
        if not self.video_url:
            raise ValueError("Vui lòng nhập liên kết video.")
        self.check_cancelled()
        info = self.resolve_video()
        title = self.safe_name(info.get("title") or "YouTube Slides")
        duration = float(info["duration"])
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("Thời lượng video không hợp lệ.")
        if self.end_time is not None:
            duration = min(duration, self.end_time)
        stamp = datetime.now().strftime("%Y-%m-%d %H.%M.%S.%f")
        job_dir = self.output_root / f"{title} - {stamp}"
        slides_dir = job_dir / "slides"
        slides_dir.mkdir(parents=True, exist_ok=False)

        stream_url = str(info["url"])
        headers = info.get("http_headers") or {}
        dash_stream = info.get("_dash_stream")
        initial_fragment = (
            self.fetch_dash_fragment(dash_stream["fragments"][0], headers)
            if dash_stream else None
        )
        end = max(0.5, duration - 0.5)
        timestamps = [value for value in np.arange(0.5, end + 0.01, self.interval).tolist() if value < duration]
        if not timestamps:
            timestamps = [0.0]

        saved: list[SavedFrame] = []
        self.last_observed = None
        failures = 0
        completed_count = 0
        with ThreadPoolExecutor(max_workers=4) as pool:
            for start in range(0, len(timestamps), 4):
                self.check_cancelled()
                chunk = timestamps[start:start + 4]
                futures = [
                    pool.submit(
                        self.fetch_dash_frame, dash_stream, headers, value, initial_fragment
                    ) if dash_stream else pool.submit(self.fetch_frame, stream_url, headers, value)
                    for value in chunk
                ]
                for timestamp, future in zip(chunk, futures):
                    self.check_cancelled()
                    completed_count += 1
                    value = 0.08 + 0.78 * (completed_count / len(timestamps))
                    self.progress(
                        f"Đang xem trực tuyến • {int(timestamp // 60):02d}:{int(timestamp % 60):02d}",
                        value,
                    )
                    try:
                        self.select_frame(future.result(), timestamp, slides_dir, saved)
                    except (subprocess.TimeoutExpired, RuntimeError, OSError):
                        failures += 1
                        if failures >= 4 and not saved:
                            raise RuntimeError("Không thể đọc luồng video trực tuyến. Hãy thử lại sau.")

        if not saved:
            raise RuntimeError("Không tìm thấy khung hình phù hợp trong video.")
        successful_count = len(timestamps) - failures
        minimum_successes = max(2, int(len(timestamps) * 0.65))
        if len(timestamps) >= 4 and successful_count < minimum_successes:
            raise RuntimeError(
                f"Chỉ đọc được {successful_count}/{len(timestamps)} mốc video. "
                "Kết quả chưa đáng tin cậy; hãy kiểm tra Internet rồi thử lại."
            )

        self.progress("Đang tạo PDF…", 0.90)
        pdf_path = job_dir / f"{title} - Slides.pdf"
        self.create_pdf(saved, pdf_path)

        self.progress("Đang dọn ảnh chụp tạm…", 0.94)
        try:
            shutil.rmtree(slides_dir)
        except OSError as error:
            raise RuntimeError(
                f"PDF đã được tạo nhưng không thể xóa ảnh chụp tạm: {error}"
            ) from error

        self.progress("Đang lấy phụ đề…", 0.96)
        transcript_path = self.save_available_transcript(info, job_dir, title)
        result = ExtractionResult(
            title=title,
            duration=duration,
            slide_count=len(saved),
            sampled_count=successful_count,
            output_dir=str(job_dir),
            pdf_path=str(pdf_path),
            transcript_path=transcript_path,
        )
        self.progress("Hoàn tất", 1.0)
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Online-first YouTube slide extractor")
    parser.add_argument("url", help="YouTube video URL")
    parser.add_argument("--output", default=str(Path.home() / "Documents" / "SlideFlow"))
    parser.add_argument("--interval", type=int, default=8)
    parser.add_argument("--end-time", type=parse_end_time, default=None, help="Stop slide scanning at hh:mm:ss or mm:ss")
    parser.add_argument("--threshold", type=float, default=0.90)
    parser.add_argument("--quality", type=int, default=1080)
    parser.add_argument("--no-transcript", action="store_true")
    parser.add_argument(
        "--cookies-from-browser",
        choices=tuple(OnlineSlideExtractor.COOKIE_BROWSER_NAMES),
        help="Use browser login only when YouTube requires authentication",
    )
    args = parser.parse_args()
    extractor = OnlineSlideExtractor(
        args.url,
        args.output,
        interval=args.interval,
        end_time=args.end_time,
        similarity_threshold=args.threshold,
        quality=args.quality,
        save_transcript=not args.no_transcript,
        cookie_browser=args.cookies_from_browser,
        progress_callback=lambda message, value: print(f"[{value:>5.0%}] {message}"),
    )
    result = extractor.extract()
    print(f"PDF: {result.pdf_path}")


if __name__ == "__main__":
    main()
