import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import patch

from huggingface_hub.errors import LocalEntryNotFoundError
from subtitle_proxy import SiteCaptionUnavailable
from transcript_extractor import AudioTranscriptBatchExtractor, TranscriptBatchExtractor


class TranscriptBatchTests(unittest.TestCase):
    def test_audio_mode_selects_small_audio_and_labels_generated_text(self) -> None:
        info = {
            "id": "abcdefghijk", "title": "Lecture", "duration": 60, "http_headers": {},
            "formats": [
                {"url": "https://audio.example/high", "protocol": "https", "vcodec": "none", "acodec": "mp4a.40.2", "ext": "m4a", "filesize": 4000},
                {"url": "https://audio.example/low", "protocol": "https", "vcodec": "none", "acodec": "mp4a.40.5", "ext": "m4a", "filesize": 2000},
            ],
        }

        class FakeResponse:
            headers = {"Content-Length": "10"}

            def __enter__(self):
                return self
            def __exit__(self, *_args):
                return None
            def read(self, _size):
                if not hasattr(self, "done"):
                    self.done = True
                    return b"audio-data"
                return b""

        class Segment:
            text = " Xin chào "
            end = 60

        class TranscriptionInfo:
            duration = 60

        class FakeModel:
            def transcribe(self, path, **kwargs):
                self.path = path
                self.kwargs = kwargs
                return [Segment()], TranscriptionInfo()

        with tempfile.TemporaryDirectory() as temp_dir:
            progress = []
            extractor = AudioTranscriptBatchExtractor(
                "https://www.youtube.com/watch?v=abcdefghijk", temp_dir,
                model_root=temp_dir,
                progress_callback=lambda message, value: progress.append((message, value)),
            )
            model = FakeModel()
            with patch.object(extractor, "speech_model", return_value=model), \
                 patch("transcript_extractor.urllib.request.urlopen", return_value=FakeResponse()) as fetch:
                text = extractor.read_transcript(info)
            self.assertIn("tự động từ âm thanh", text)
            self.assertIn("Xin chào", text)
            self.assertEqual(model.kwargs["language"], "vi")
            self.assertEqual(model.kwargs["beam_size"], 1)
            self.assertEqual(model.kwargs["best_of"], 1)
            self.assertFalse(model.kwargs["condition_on_previous_text"])
            self.assertFalse(Path(model.path).exists())
            self.assertIn("/low", fetch.call_args.args[0].full_url)
            self.assertTrue(any("Đang tải âm thanh" in message for message, _ in progress))
            self.assertTrue(any("1:00/1:00" in message for message, _ in progress))
            self.assertEqual(progress[-1][0], "Đã nhận dạng xong âm thanh")

    def test_cached_whisper_model_loads_without_network_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            progress = []
            extractor = AudioTranscriptBatchExtractor(
                "https://www.youtube.com/watch?v=abcdefghijk",
                temp_dir,
                model_root=temp_dir,
                progress_callback=lambda message, value: progress.append((message, value)),
            )
            fake_model = object()
            with patch("faster_whisper.utils.download_model", return_value=temp_dir) as cached, \
                 patch("faster_whisper.WhisperModel", return_value=fake_model) as load, \
                 patch("huggingface_hub.snapshot_download") as network:
                model = extractor.speech_model()

            self.assertIs(model, fake_model)
            cached.assert_called_once_with(
                "small",
                local_files_only=True,
                cache_dir=temp_dir,
            )
            network.assert_not_called()
            self.assertEqual(load.call_args.args[0], temp_dir)
            self.assertTrue(load.call_args.kwargs["local_files_only"])
            self.assertGreaterEqual(load.call_args.kwargs["cpu_threads"], 4)
            self.assertTrue(any("Đã có mô hình Whisper" in message for message, _ in progress))

    def test_first_whisper_download_reports_bytes_and_loads_local_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            progress = []
            extractor = AudioTranscriptBatchExtractor(
                "https://www.youtube.com/watch?v=abcdefghijk",
                temp_dir,
                model_root=temp_dir,
                progress_callback=lambda message, value: progress.append((message, value)),
            )
            fake_model = object()

            def download_snapshot(repo_id, **kwargs):
                self.assertEqual(repo_id, "Systran/faster-whisper-small")
                tracker = kwargs["tqdm_class"](total=100, unit="B")
                tracker.update(40)
                tracker.update(60)
                return temp_dir

            with patch(
                "faster_whisper.utils.download_model",
                side_effect=LocalEntryNotFoundError("not cached"),
            ), patch(
                "huggingface_hub.snapshot_download",
                side_effect=download_snapshot,
            ) as network, patch(
                "faster_whisper.WhisperModel",
                return_value=fake_model,
            ) as load:
                model = extractor.speech_model()

            self.assertIs(model, fake_model)
            network.assert_called_once()
            self.assertEqual(load.call_args.args[0], temp_dir)
            self.assertTrue(any("Đang tải mô hình Whisper lần đầu" in message for message, _ in progress))
            self.assertTrue(any("0/0 MB" in message for message, _ in progress))

    def test_video_with_playlist_parameters_uses_playlist_mode(self) -> None:
        url = "https://www.youtube.com/watch?v=P2q8G7OnUXs&list=PLexample&index=2"
        self.assertTrue(TranscriptBatchExtractor.is_playlist_url(url))
        self.assertFalse(
            TranscriptBatchExtractor.is_playlist_url("https://www.youtube.com/watch?v=P2q8G7OnUXs")
        )

    def test_single_video_saves_plain_text_without_needing_video_stream(self) -> None:
        info = {
            "id": "abcdefghijk",
            "title": "Lecture / Part 1",
            "subtitles": {"vi": [{"ext": "json3", "url": "https://caption.example/one"}]},
            "automatic_captions": {},
        }
        payload = b'{"events":[{"segs":[{"utf8":"Xin chao"}]},{"segs":[{"utf8":"Bai giang"}]}]}'

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def read(self):
                return payload

        with tempfile.TemporaryDirectory() as temp_dir:
            extractor = TranscriptBatchExtractor(
                "https://www.youtube.com/watch?v=abcdefghijk", temp_dir
            )
            with patch.object(extractor, "extract_youtube_info", return_value=info), \
                 patch("transcript_extractor.urllib.request.urlopen", return_value=FakeResponse()):
                result = extractor.extract()

            self.assertEqual(result.total_count, 1)
            self.assertEqual(result.saved_count, 1)
            self.assertFalse(result.is_playlist)
            self.assertEqual(Path(result.transcript_paths[0]).read_text(), "Xin chao\nBai giang\n")
            self.assertNotIn(".mp4", str(list(Path(result.output_dir).iterdir())))

    def test_caption_429_retries_native_track_with_ios_before_proxy(self) -> None:
        video_id = "abcdefghijk"
        web_info = {
            "id": video_id,
            "subtitles": {},
            "automatic_captions": {
                "vi": [{"ext": "json3", "url": "https://caption.example/translated"}],
                "vi-orig": [{"ext": "json3", "url": "https://caption.example/web"}],
            },
        }
        ios_info = {
            "id": video_id,
            "subtitles": {},
            "automatic_captions": {
                "vi-orig": [{"ext": "json3", "url": "https://caption.example/ios"}],
            },
        }
        payload = b'{"events":[{"segs":[{"utf8":"Phu de goc"}]}]}'

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def read(self):
                return payload

        def fetch(request, timeout):
            if request.full_url.endswith("/web"):
                raise HTTPError(request.full_url, 429, "Too Many Requests", {}, None)
            self.assertTrue(request.full_url.endswith("/ios"))
            return FakeResponse()

        with tempfile.TemporaryDirectory() as temp_dir:
            extractor = TranscriptBatchExtractor(
                f"https://www.youtube.com/watch?v={video_id}", temp_dir,
            )
            self.assertTrue(extractor.subtitle_track(web_info)["url"].endswith("/web"))
            with patch.object(extractor, "extract_youtube_info", return_value=ios_info) as lookup, \
                 patch("transcript_extractor.urllib.request.urlopen", side_effect=fetch), \
                 patch.object(extractor, "read_via_proxy") as proxy:
                transcript = extractor.read_transcript(web_info)
            self.assertEqual(transcript, "Phu de goc")
            self.assertEqual(extractor.last_origin, "YouTube iOS (phụ đề gốc)")
            self.assertTrue(extractor.prefer_ios)
            self.assertEqual(lookup.call_args.kwargs["overrides"]["extractor_args"]["youtube"]["player_client"], ["ios"])
            proxy.assert_not_called()

    def test_playlist_records_available_missing_and_failed_videos(self) -> None:
        ids = ["abcdefghijk", "lmnopqrstuv", "wxyzABC1234"]
        playlist = {
            "_type": "playlist",
            "title": "Lecture playlist",
            "entries": [{"id": value, "title": f"Video {index}"} for index, value in enumerate(ids)],
        }
        infos = [
            playlist,
            {"id": ids[0], "title": "First", "automatic_captions": {}},
            {"id": ids[1], "title": "Second", "automatic_captions": {}},
            {"id": ids[2], "title": "Third", "automatic_captions": {}},
        ]
        requested = []

        def lookup(url: str, overrides=None):
            requested.append((url, overrides))
            return infos.pop(0)

        def transcript(info: dict):
            if info["id"] == ids[0]:
                return "First transcript"
            if info["id"] == ids[1]:
                return None
            raise OSError("caption endpoint unavailable")

        with tempfile.TemporaryDirectory() as temp_dir:
            extractor = TranscriptBatchExtractor(
                "https://www.youtube.com/playlist?list=PLexample", temp_dir,
                request_delay=0, rate_limit_retry_delay=0,
            )
            with patch.object(extractor, "extract_youtube_info", side_effect=lookup), \
                 patch.object(extractor, "read_transcript", side_effect=transcript):
                result = extractor.extract()

            self.assertEqual(requested[0][1], {"noplaylist": False, "extract_flat": "in_playlist"})
            self.assertEqual(result.total_count, 3)
            self.assertEqual((result.saved_count, result.missing_count, result.failed_count), (1, 1, 1))
            report = Path(result.report_path).read_text()
            self.assertIn("Không có phụ đề", report)
            self.assertIn("Lỗi • OSError: caption endpoint unavailable", report)
            self.assertIn("Script hiện có: 1", report)
            self.assertEqual(len(list(Path(result.output_dir).glob("*.txt.part"))), 0)

    def test_cancel_keeps_completed_scripts_and_stops_before_next_video(self) -> None:
        playlist = {
            "_type": "playlist",
            "title": "Two videos",
            "entries": [
                {"id": "abcdefghijk", "title": "First"},
                {"id": "lmnopqrstuv", "title": "Second"},
            ],
        }
        cancel = threading.Event()
        calls = []

        def lookup(url: str, overrides=None):
            calls.append(url)
            if overrides:
                return playlist
            return {"id": "abcdefghijk", "title": "First"}

        def progress(message: str, _value: float) -> None:
            if "1/2" in message:
                cancel.set()

        with tempfile.TemporaryDirectory() as temp_dir:
            extractor = TranscriptBatchExtractor(
                "https://www.youtube.com/playlist?list=PLexample",
                temp_dir,
                progress_callback=progress,
                cancel_event=cancel,
                request_delay=0,
                rate_limit_retry_delay=0,
            )
            with patch.object(extractor, "extract_youtube_info", side_effect=lookup), \
                 patch.object(extractor, "read_transcript", return_value="Partial result"):
                with self.assertRaises(InterruptedError):
                    extractor.extract()

            self.assertEqual(len(calls), 2)
            self.assertEqual(len(list(Path(temp_dir).rglob("* - abcdefghijk.txt"))), 1)
            reports = list(Path(temp_dir).rglob("Kết quả lấy script.txt"))
            self.assertEqual(len(reports), 1)
            self.assertIn("002. Chưa xử lý", reports[0].read_text())
            self.assertNotIn("001. Chưa xử lý", reports[0].read_text())

    def test_resume_legacy_playlist_reuses_saved_scripts_without_overwriting_old_report(self) -> None:
        url = "https://youtube.com/playlist?list=PLexample&si=share-one"
        playlist = {
            "_type": "playlist",
            "title": "Renamed playlist",
            "entries": [
                {"id": "abcdefghijk", "title": "First"},
                {"id": "lmnopqrstuv", "title": "Second"},
            ],
        }
        calls = []

        def lookup(video_url: str, overrides=None):
            calls.append(video_url)
            if overrides:
                return playlist
            return {"id": "lmnopqrstuv", "title": "Second"}

        with tempfile.TemporaryDirectory() as temp_dir:
            old_folder = Path(temp_dir) / "Old title - Scripts - 2026-09-15"
            old_folder.mkdir()
            old_report = old_folder / "Kết quả lấy script.txt"
            old_report.write_text(f"Nguồn: {url}\nĐã lưu: 1\n", encoding="utf-8")
            old_script = old_folder / "001 - First - abcdefghijk.txt"
            old_script.write_text("Original text\n", encoding="utf-8")
            extractor = TranscriptBatchExtractor(
                "https://www.youtube.com/watch?v=lmnopqrstuv&list=PLexample&index=2",
                temp_dir,
                request_delay=0,
                rate_limit_retry_delay=0,
            )
            with patch.object(extractor, "extract_youtube_info", side_effect=lookup), \
                 patch.object(extractor, "read_transcript", return_value="New text"):
                result = extractor.extract()

            self.assertEqual(calls, [extractor.video_url, "https://www.youtube.com/watch?v=lmnopqrstuv"])
            self.assertEqual(Path(result.output_dir), old_folder)
            self.assertEqual((result.reused_count, result.new_count, result.saved_count), (1, 1, 2))
            self.assertEqual(old_script.read_text(), "Original text\n")
            self.assertEqual(old_report.read_text(), f"Nguồn: {url}\nĐã lưu: 1\n")
            self.assertNotEqual(Path(result.report_path), old_report)
            self.assertIn("001. Đã có", Path(result.report_path).read_text())

    def test_http_429_retries_once_then_pauses_with_pending_checkpoint(self) -> None:
        ids = ["abcdefghijk", "lmnopqrstuv", "wxyzABC1234"]
        playlist = {
            "_type": "playlist",
            "title": "Three videos",
            "entries": [{"id": value, "title": value} for value in ids],
        }
        calls = []

        def lookup(video_url: str, overrides=None):
            calls.append(video_url)
            if overrides:
                return playlist
            return {"id": ids[0], "title": "First"}

        def rate_limited(_info: dict):
            raise HTTPError("https://caption.example/signed?secret=hidden", 429, "Too Many Requests", {}, None)

        with tempfile.TemporaryDirectory() as temp_dir:
            extractor = TranscriptBatchExtractor(
                "https://www.youtube.com/playlist?list=PLexample", temp_dir,
                request_delay=0, rate_limit_retry_delay=0,
            )
            with patch.object(extractor, "extract_youtube_info", side_effect=lookup), \
                 patch.object(extractor, "read_transcript", side_effect=rate_limited), \
                 patch("transcript_extractor.fetch_site_caption_text", side_effect=SiteCaptionUnavailable("Website unavailable")):
                result = extractor.extract()

            self.assertEqual(len(calls), 3)  # playlist plus one video, retried once
            self.assertEqual(result.pending_count, 3)
            self.assertEqual(result.failed_count, 0)
            self.assertIn("HTTP 429", result.paused_reason)
            report = Path(result.report_path).read_text()
            self.assertIn("Chưa xử lý: 3", report)
            self.assertNotIn("secret=hidden", report)

    def test_metadata_429_uses_site_and_continues_the_playlist(self) -> None:
        ids = ["abcdefghijk", "lmnopqrstuv"]
        playlist = {
            "_type": "playlist", "title": "Two videos",
            "entries": [{"id": value, "title": f"Lecture {index}"} for index, value in enumerate(ids, 1)],
        }
        blocked = HTTPError("https://youtube.example/signed?secret=hidden", 429, "Too Many Requests", {}, None)
        calls = []

        def lookup(_url: str, overrides=None):
            calls.append(_url)
            if overrides:
                return playlist
            raise blocked

        with tempfile.TemporaryDirectory() as temp_dir:
            extractor = TranscriptBatchExtractor(
                "https://www.youtube.com/playlist?list=PLexample", temp_dir,
                request_delay=0, rate_limit_retry_delay=0,
            )
            with patch.object(extractor, "extract_youtube_info", side_effect=lookup), \
                 patch("transcript_extractor.fetch_site_caption_text", side_effect=lambda value: f"Script {value}") as site:
                result = extractor.extract()

            self.assertEqual((result.saved_count, result.new_count, result.pending_count), (2, 2, 0))
            self.assertEqual(site.call_count, 2)
            self.assertEqual(len(calls), 3)  # playlist and two 429 attempts for the first video
            saved = [Path(path).read_text(encoding="utf-8") for path in result.transcript_paths]
            self.assertEqual(saved, [f"Script {value}\n" for value in ids])
            report = Path(result.report_path).read_text(encoding="utf-8")
            self.assertIn("DownloadYoutubeSubtitles.com", report)
            self.assertNotIn("secret=hidden", report)

    def test_age_restriction_is_reported_separately_from_network_errors(self) -> None:
        extractor = TranscriptBatchExtractor("https://youtu.be/abcdefghijk", "/tmp")
        kind, reason = extractor.error_reason(RuntimeError("Sign in to confirm your age"))
        self.assertEqual(kind, "age")
        self.assertIn("xác nhận tuổi", reason)
        kind, reason = extractor.error_reason(OSError("Failed at https://caption.example/?token=secret"))
        self.assertEqual(kind, "other")
        self.assertNotIn("token=secret", reason)

    def test_rate_pause_still_counts_existing_script_after_paused_position(self) -> None:
        url = "https://www.youtube.com/playlist?list=PLexample"
        ids = ["abcdefghijk", "lmnopqrstuv", "wxyzABC1234"]
        playlist = {
            "_type": "playlist",
            "title": "Three videos",
            "entries": [{"id": value, "title": value} for value in ids],
        }

        def lookup(_url: str, overrides=None):
            return playlist if overrides else {"id": ids[0], "title": "First"}

        with tempfile.TemporaryDirectory() as temp_dir:
            old_folder = Path(temp_dir) / "Old batch"
            old_folder.mkdir()
            (old_folder / "Kết quả lấy script.txt").write_text(f"Nguồn: {url}\n")
            (old_folder / f"003 - Third - {ids[2]}.txt").write_text("Already saved\n")
            extractor = TranscriptBatchExtractor(
                url, temp_dir, request_delay=0, rate_limit_retry_delay=0
            )
            error = HTTPError("https://caption.example/", 429, "Too Many Requests", {}, None)
            with patch.object(extractor, "extract_youtube_info", side_effect=lookup), \
                 patch.object(extractor, "read_transcript", side_effect=error), \
                 patch("transcript_extractor.fetch_site_caption_text", side_effect=SiteCaptionUnavailable("Website unavailable")):
                result = extractor.extract()

            self.assertEqual((result.saved_count, result.reused_count, result.pending_count), (1, 1, 2))
            report = Path(result.report_path).read_text()
            self.assertIn("003. Đã có", report)
            self.assertNotIn("003. Chưa xử lý", report)


if __name__ == "__main__":
    unittest.main()
