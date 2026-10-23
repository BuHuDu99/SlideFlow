import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np
import yt_dlp

from main import SlideFlowApp
from online_extractor import OnlineSlideExtractor


class FakeOnlineExtractor(OnlineSlideExtractor):
    def resolve_video(self) -> dict:
        return {
            "title": "Bài giảng thử nghiệm",
            "duration": 12,
            "url": "memory://video",
            "http_headers": {},
            "subtitles": {},
            "automatic_captions": {},
        }

    def fetch_frame(self, stream_url: str, headers: dict, timestamp: float) -> np.ndarray:
        slide_number = min(3, int(timestamp // 4) + 1)
        colors = [(245, 245, 245), (225, 245, 255), (245, 225, 255)]
        frame = np.full((360, 640, 3), colors[slide_number - 1], dtype=np.uint8)
        left = 45 + (slide_number - 1) * 120
        cv2.rectangle(frame, (left, 65), (left + 230, 285), (40 * slide_number, 90, 180), -1)
        cv2.putText(
            frame,
            f"TOPIC {slide_number}",
            (left + 10, 195),
            cv2.FONT_HERSHEY_SIMPLEX,
            2,
            (15, 15, 15),
            4,
            cv2.LINE_AA,
        )
        return frame


class MostlyFailingExtractor(FakeOnlineExtractor):
    def resolve_video(self) -> dict:
        info = super().resolve_video()
        info["duration"] = 20
        return info

    def fetch_frame(self, stream_url: str, headers: dict, timestamp: float) -> np.ndarray:
        if timestamp > 1:
            raise RuntimeError("simulated stream failure")
        return super().fetch_frame(stream_url, headers, timestamp)


class OnlineExtractorTests(unittest.TestCase):
    @staticmethod
    def lecture_slide(second_line: str) -> np.ndarray:
        frame = np.full((720, 1280, 3), 248, dtype=np.uint8)
        cv2.rectangle(frame, (60, 45), (1220, 120), (36, 76, 140), -1)
        cv2.putText(
            frame,
            "OVARY DEVELOPMENT",
            (90, 210),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.5,
            (20, 20, 20),
            3,
            cv2.LINE_AA,
        )
        cv2.putText(
            frame,
            second_line,
            (90, 300),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.5,
            (20, 20, 20),
            3,
            cv2.LINE_AA,
        )
        return frame

    def test_online_pipeline_keeps_pdf_and_removes_temporary_images(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            extractor = FakeOnlineExtractor(
                "https://youtu.be/example",
                temp_dir,
                interval=4,
                save_transcript=False,
            )
            result = extractor.extract()
            output = Path(result.output_dir)
            self.assertEqual(result.slide_count, 3)
            self.assertTrue(Path(result.pdf_path).is_file())
            self.assertGreater(Path(result.pdf_path).stat().st_size, 0)
            self.assertEqual(list(output.rglob("*.mp4")), [])
            self.assertFalse((output / "slides").exists())
            self.assertEqual(list(output.rglob("*.jpg")), [])

    def test_pdf_failure_preserves_temporary_images_for_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            extractor = FakeOnlineExtractor(
                "https://youtu.be/example",
                temp_dir,
                interval=4,
                save_transcript=False,
            )
            with patch("online_extractor.img2pdf.convert", side_effect=RuntimeError("merge failed")):
                with self.assertRaisesRegex(RuntimeError, "merge failed"):
                    extractor.extract()
            self.assertGreater(len(list(Path(temp_dir).rglob("slides/*.jpg"))), 0)
            self.assertEqual(list(Path(temp_dir).rglob("*.pdf.part")), [])

    def test_transcript_json_is_cleaned_and_deduplicated(self) -> None:
        payload = b'{"events":[{"segs":[{"utf8":"Hello world"}]},{"segs":[{"utf8":"Hello world"}]},{"segs":[{"utf8":"Next slide"}]}]}'
        text = OnlineSlideExtractor.parse_transcript(payload, "json3")
        self.assertEqual(text, "Hello world\nNext slide")

    def test_white_slides_with_changed_text_are_not_duplicates(self) -> None:
        extractor = OnlineSlideExtractor("https://youtu.be/example", "/tmp", save_transcript=False)
        first = self.lecture_slide("Follicles and hormones")
        second = self.lecture_slide("Placenta and hormones")
        first_hash, first_gray, sharpness, edge_density = extractor.analyse_frame(first)
        second_hash, second_gray, _, _ = extractor.analyse_frame(second)
        previous = type("Previous", (), {
            "frame_hash": first_hash,
            "gray_small": first_gray,
            "sharpness": sharpness,
            "edge_density": edge_density,
        })()
        self.assertFalse(extractor.is_duplicate(previous, second_hash, second_gray))

    def test_jpeg_noise_on_same_slide_is_a_duplicate(self) -> None:
        extractor = OnlineSlideExtractor("https://youtu.be/example", "/tmp", save_transcript=False)
        first = self.lecture_slide("Follicles and hormones")
        encoded = cv2.imencode(".jpg", first, [cv2.IMWRITE_JPEG_QUALITY, 70])[1]
        second = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
        first_hash, first_gray, sharpness, edge_density = extractor.analyse_frame(first)
        second_hash, second_gray, _, _ = extractor.analyse_frame(second)
        previous = type("Previous", (), {
            "frame_hash": first_hash,
            "gray_small": first_gray,
            "sharpness": sharpness,
            "edge_density": edge_density,
        })()
        self.assertTrue(extractor.is_duplicate(previous, second_hash, second_gray))

    def test_direct_http_stream_is_preferred_over_hls(self) -> None:
        extractor = OnlineSlideExtractor(
            "https://youtu.be/example", "/tmp", quality=480, save_transcript=False
        )
        formats = [
            {"url": "https://manifest/480.m3u8", "protocol": "m3u8_native", "height": 480, "vcodec": "vp9", "ext": "mp4"},
            {"url": "https://direct/360.mp4", "protocol": "https", "height": 360, "vcodec": "avc1", "ext": "mp4"},
            {"url": "https://direct/480.webm", "protocol": "https", "height": 480, "vcodec": "vp9", "ext": "webm"},
            {"url": "https://direct/480.mp4", "protocol": "https", "height": 480, "vcodec": "avc1", "ext": "mp4"},
            {"url": "https://direct/720.mp4", "protocol": "https", "height": 720, "vcodec": "avc1", "ext": "mp4"},
        ]
        selected = extractor.select_direct_stream(formats)
        self.assertIsNotNone(selected)
        self.assertEqual(selected["url"], "https://direct/480.mp4")

    def test_default_quality_prefers_1080_then_highest_available(self) -> None:
        extractor = OnlineSlideExtractor(
            "https://youtu.be/example", "/tmp", save_transcript=False
        )
        formats = [
            {"url": "https://direct/720.mp4", "protocol": "https", "height": 720, "vcodec": "avc1", "ext": "mp4"},
            {"url": "https://direct/1080.mp4", "protocol": "https", "height": 1080, "vcodec": "avc1", "ext": "mp4"},
            {"url": "https://direct/2160.mp4", "protocol": "https", "height": 2160, "vcodec": "vp9", "ext": "webm"},
        ]

        self.assertEqual(extractor.quality, 1080)
        self.assertEqual(
            extractor.select_direct_stream(formats)["url"],
            "https://direct/1080.mp4",
        )
        self.assertEqual(
            extractor.select_direct_stream([formats[0], formats[2]])["url"],
            "https://direct/2160.mp4",
        )

    def test_1080_dash_stream_beats_lower_direct_stream_without_prompt(self) -> None:
        extractor = OnlineSlideExtractor(
            "https://youtu.be/example", "/tmp", save_transcript=False
        )
        fragments = [
            {"url": "https://segments.example/init"},
            {"url": "https://segments.example/chunk"},
        ]
        formats = [
            {"url": "https://direct/720.mp4", "protocol": "https", "height": 720, "vcodec": "avc1", "ext": "mp4"},
            {
                "url": "https://segments.example/1080",
                "protocol": "http_dash_segments",
                "height": 1080,
                "vcodec": "avc1",
                "ext": "mp4",
                "target_duration": 5,
                "fragments": fragments,
            },
        ]

        with patch.object(
            extractor,
            "extract_youtube_info",
            return_value={"title": "Lecture", "duration": 60, "formats": formats},
        ):
            info = extractor.resolve_video()

        self.assertEqual(info["url"], "https://segments.example/1080")
        self.assertEqual(info["_dash_stream"]["height"], 1080)

    def test_playlist_lookup_returns_selectable_videos_in_playlist_order(self) -> None:
        extractor = OnlineSlideExtractor(
            "https://www.youtube.com/playlist?list=PL123", "/tmp", save_transcript=False
        )
        info = {
            "_type": "playlist",
            "title": "Khóa siêu âm",
            "entries": [
                {"id": "abcdefghijk", "title": "Bài một", "duration": 125},
                None,
                {"id": "invalid", "title": "Video riêng tư"},
                {"id": "lmnopqrstuv", "title": "Bài hai", "duration": "3600"},
            ],
        }
        with patch.object(extractor, "extract_youtube_info", return_value=info) as lookup:
            result = extractor.resolve_playlist()

        self.assertEqual(
            lookup.call_args.args[1],
            {"noplaylist": False, "extract_flat": "in_playlist"},
        )
        self.assertEqual(result.title, "Khóa siêu âm")
        self.assertEqual([video.position for video in result.videos], [1, 4])
        self.assertEqual([video.video_id for video in result.videos], ["abcdefghijk", "lmnopqrstuv"])
        self.assertEqual(result.videos[1].duration, 3600.0)
        self.assertEqual(result.unavailable_count, 2)

    def test_playlist_url_resolves_single_dash_only_video(self) -> None:
        playlist_url = (
            "https://www.youtube.com/watch?v=P2q8G7OnUXs"
            "&list=PLNMj_gnL0WIIfh7R8GeoyI1pErcUpPEo-&index=2"
        )
        calls = []
        fragments = [{"url": f"https://segments.example/sq={index}"} for index in range(5)]

        class FakeYoutubeDL:
            def __init__(self, options: dict) -> None:
                calls.append(options)

            def __enter__(self):
                return self

            def __exit__(self, *_args) -> None:
                return None

            def extract_info(self, url: str, download: bool) -> dict:
                calls.append((url, download))
                return {
                    "title": "Dinh dưỡng miễn dịch nhi",
                    "duration": 11221,
                    "formats": [{
                        "url": "https://segments.example/base",
                        "protocol": "http_dash_segments",
                        "height": 720,
                        "vcodec": "avc1",
                        "ext": "mp4",
                        "target_duration": 5,
                        "fragments": fragments,
                    }],
                }

        extractor = OnlineSlideExtractor(playlist_url, "/tmp", save_transcript=False)
        with patch("online_extractor.yt_dlp.YoutubeDL", FakeYoutubeDL):
            info = extractor.resolve_video()
        self.assertTrue(calls[0]["noplaylist"])
        self.assertEqual(calls[1], (playlist_url, False))
        self.assertEqual(info["_dash_stream"]["fragments"], fragments)

    def test_dash_frame_uses_only_initial_and_target_fragment_and_cleans_temp_file(self) -> None:
        extractor = OnlineSlideExtractor("https://youtu.be/example", "/tmp")
        fragments = [{"url": f"https://segments.example/sq={index}"} for index in range(5)]
        stream = {"ext": "mp4", "target_duration": 5, "fragments": fragments}
        frame = np.full((20, 30, 3), 210, dtype=np.uint8)
        encoded = cv2.imencode(".png", frame)[1].tobytes()
        requested = []
        temporary_paths = []

        def fetch_fragment(fragment: dict, _headers: dict) -> bytes:
            requested.append(fragment["url"])
            return b"segment"

        def run_ffmpeg(command: list[str], **_kwargs) -> SimpleNamespace:
            temporary_paths.append(Path(command[command.index("-i") + 1]))
            self.assertEqual(temporary_paths[-1].read_bytes(), b"initsegment")
            return SimpleNamespace(returncode=0, stdout=encoded, stderr=b"")

        with patch.object(extractor, "fetch_dash_fragment", side_effect=fetch_fragment), \
             patch("online_extractor.subprocess.run", side_effect=run_ffmpeg):
            actual = extractor.fetch_dash_frame(stream, {}, 10.5, b"init")

        self.assertEqual(requested, [fragments[3]["url"]])
        self.assertTrue(np.array_equal(actual, frame))
        self.assertFalse(temporary_paths[0].exists())

    def test_age_restricted_video_retries_with_browser_login(self) -> None:
        calls = []
        progress = []

        class FakeYoutubeDL:
            def __init__(self, options: dict) -> None:
                self.options = options
                calls.append(options)

            def __enter__(self):
                return self

            def __exit__(self, *_args) -> None:
                return None

            def extract_info(self, _url: str, download: bool) -> dict:
                self.assert_download_is_false = download
                if "cookiesfrombrowser" not in self.options:
                    raise yt_dlp.utils.DownloadError(
                        "Sign in to confirm your age. Use --cookies-from-browser"
                    )
                return {
                    "title": "Restricted lecture",
                    "duration": 60,
                    "formats": [{
                        "url": "https://direct/restricted.mp4",
                        "protocol": "https",
                        "height": 720,
                        "vcodec": "avc1",
                        "ext": "mp4",
                    }],
                }

        extractor = OnlineSlideExtractor(
            "https://youtu.be/restricted",
            "/tmp",
            cookie_browser="chrome",
            progress_callback=lambda message, value: progress.append((message, value)),
        )
        with patch("online_extractor.yt_dlp.YoutubeDL", FakeYoutubeDL):
            info = extractor.resolve_video()

        self.assertNotIn("cookiesfrombrowser", calls[0])
        self.assertEqual(calls[1]["cookiesfrombrowser"], ("chrome",))
        self.assertEqual(
            calls[1]["extractor_args"]["youtube"]["player_client"],
            ["default", "-tv_downgraded", "web_embedded"],
        )
        self.assertEqual(info["url"], "https://direct/restricted.mp4")
        self.assertTrue(any("giới hạn tuổi" in message for message, _value in progress))
        self.assertEqual(SlideFlowApp.COOKIE_BROWSER_OPTIONS["Chrome"], "chrome")

    def test_youtube_options_enable_bundled_deno_runtime(self) -> None:
        with patch.object(OnlineSlideExtractor, "deno_executable", return_value="/app/deno"):
            options = OnlineSlideExtractor.youtube_options("chrome")
        self.assertEqual(options["js_runtimes"], {"deno": {"path": "/app/deno"}})

    def test_mostly_failed_stream_does_not_create_misleading_one_slide_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            extractor = MostlyFailingExtractor(
                "https://youtu.be/example",
                temp_dir,
                interval=4,
                save_transcript=False,
            )
            with self.assertRaisesRegex(RuntimeError, "Kết quả chưa đáng tin cậy"):
                extractor.extract()

    def test_balanced_mode_ignores_camera_jitter_and_lighting_drift(self) -> None:
        self.assertEqual(SlideFlowApp.PROCESSING_PRESETS["Cân bằng"], (7, 0.92))
        with tempfile.TemporaryDirectory() as temp_dir:
            slides_dir = Path(temp_dir) / "slides"
            slides_dir.mkdir()
            extractor = OnlineSlideExtractor(
                "https://youtu.be/example",
                temp_dir,
                similarity_threshold=0.92,
                save_transcript=False,
            )
            saved = []
            for topic in ("FIRST TOPIC", "SECOND TOPIC", "THIRD TOPIC"):
                base = self.lecture_slide(topic)
                for offset in range(8):
                    shifted = np.roll(base, offset % 2, axis=1)
                    changed_light = np.clip(
                        shifted.astype(np.int16) + (offset % 3) - 1,
                        0,
                        255,
                    ).astype(np.uint8)
                    extractor.select_frame(changed_light, offset, slides_dir, saved)
            self.assertEqual(len(saved), 3)

    def test_custom_processing_settings_are_clamped_to_safe_ranges(self) -> None:
        self.assertEqual(SlideFlowApp.processing_settings("Tùy chỉnh", 9, 0.94), (9, 0.94))
        self.assertEqual(SlideFlowApp.processing_settings("Tùy chỉnh", 1, 1.2), (2, 0.99))
        self.assertEqual(SlideFlowApp.processing_settings("Tùy chỉnh", 90, 0.5), (60, 0.75))
        self.assertEqual(SlideFlowApp.processing_settings("Cân bằng", 30, 0.8), (7, 0.92))

    def test_latest_pdf_uses_selected_download_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            older = root / "Bài cũ" / "Bài cũ - Slides.pdf"
            newer = root / "Bài mới" / "Bài mới - Slides.pdf"
            older.parent.mkdir()
            newer.parent.mkdir()
            older.write_bytes(b"old")
            newer.write_bytes(b"new")
            older.touch()
            os.utime(older, (1, 1))
            self.assertEqual(SlideFlowApp.find_latest_pdf(temp_dir), newer)

    def test_latest_pdf_returns_none_for_empty_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            self.assertIsNone(SlideFlowApp.find_latest_pdf(temp_dir))

    def test_tk9_touchpad_delta_decodes_both_directions(self) -> None:
        packed_down = (7 << 16) | 5
        packed_up = ((-3 & 0xFFFF) << 16) | (-9 & 0xFFFF)
        self.assertEqual(SlideFlowApp.decode_touchpad_delta(packed_down), (7, 5))
        self.assertEqual(SlideFlowApp.decode_touchpad_delta(packed_up), (-3, -9))

    def test_tk9_touchpad_scroll_uses_canvas_direction(self) -> None:
        calls = []
        app = SimpleNamespace(
            decode_touchpad_delta=SlideFlowApp.decode_touchpad_delta,
            MAX_SCROLL_PIXELS=SlideFlowApp.MAX_SCROLL_PIXELS,
            SCROLL_PIXELS_PER_DELTA=SlideFlowApp.SCROLL_PIXELS_PER_DELTA,
            scroll_body_pixels=lambda amount: calls.append(amount) or "break",
        )

        self.assertEqual(
            SlideFlowApp.scroll_with_touchpad(app, SimpleNamespace(delta=5)),
            "break",
        )
        self.assertEqual(
            SlideFlowApp.scroll_with_touchpad(app, SimpleNamespace(delta=(-9 & 0xFFFF))),
            "break",
        )
        self.assertEqual(calls, [-5, 9])

    def test_trackpad_scroll_keeps_native_pixel_scale(self) -> None:
        calls = []
        app = SimpleNamespace(
            MAX_SCROLL_PIXELS=SlideFlowApp.MAX_SCROLL_PIXELS,
            SCROLL_PIXELS_PER_DELTA=SlideFlowApp.SCROLL_PIXELS_PER_DELTA,
            scroll_body_pixels=lambda amount: calls.append(amount) or "break",
        )

        self.assertEqual(
            SlideFlowApp.scroll_with_trackpad(app, SimpleNamespace(delta=7)),
            "break",
        )
        self.assertEqual(
            SlideFlowApp.scroll_with_trackpad(app, SimpleNamespace(delta=-120)),
            "break",
        )
        self.assertEqual(calls, [-7, 96])

    def test_touchpad_binding_is_global_and_installed_once(self) -> None:
        class Widget:
            def __init__(self, children=()):
                self._children = list(children)
                self._tags = ("widget",)

            def bindtags(self, tags=None):
                if tags is not None:
                    self._tags = tags
                return self._tags

            def winfo_children(self):
                return self._children

        child = Widget()
        root = Widget([child])
        class_bindings = []
        global_bindings = []
        root.bind_class = lambda tag, event, callback: class_bindings.append((tag, event, callback))
        root.bind_all = lambda event, callback, add=None: global_bindings.append((event, callback, add))
        root.scroll_with_trackpad = lambda _event: "break"
        root.scroll_with_touchpad = lambda _event: "break"
        root.scroll_with_buttons = lambda _event, _direction: "break"

        SlideFlowApp.install_scroll_bindings(root)
        SlideFlowApp.install_scroll_bindings(root)

        touchpad_bindings = [binding for binding in global_bindings if binding[0] == "<TouchpadScroll>"]
        self.assertEqual(len(touchpad_bindings), 1)
        self.assertEqual(touchpad_bindings[0][2], "+")
        self.assertIn("SlideFlowTrackpad", root.bindtags())
        self.assertIn("SlideFlowTrackpad", child.bindtags())
        self.assertNotIn(
            "<TouchpadScroll>",
            [event for _tag, event, _callback in class_bindings],
        )

    def test_pixel_scroll_is_responsive_and_clamped_at_edges(self) -> None:
        positions = []
        canvas = SimpleNamespace(
            yview=lambda: (0.2, 0.6),
            winfo_height=lambda: 320,
            yview_moveto=lambda position: positions.append(position),
        )
        app = SimpleNamespace(body=SimpleNamespace(_parent_canvas=canvas))

        self.assertEqual(SlideFlowApp.scroll_body_pixels(app, 64), "break")
        self.assertAlmostEqual(positions[-1], 0.28)

        canvas.yview = lambda: (0.0, 0.4)
        self.assertEqual(SlideFlowApp.scroll_body_pixels(app, -64), "break")
        self.assertEqual(len(positions), 1)

        canvas.yview = lambda: (0.6, 1.0)
        self.assertEqual(SlideFlowApp.scroll_body_pixels(app, 64), "break")
        self.assertEqual(len(positions), 1)

        canvas.yview = lambda: (0.0, 1.0)
        self.assertEqual(SlideFlowApp.scroll_body_pixels(app, 64), "break")
        self.assertEqual(len(positions), 1)

    def test_playlist_dialog_becomes_the_active_scroll_target(self) -> None:
        main_canvas = SimpleNamespace()
        playlist_canvas = SimpleNamespace()
        app = SimpleNamespace(
            body=SimpleNamespace(_parent_canvas=main_canvas),
            playlist_dialog=SimpleNamespace(winfo_exists=lambda: True),
            playlist_scroll_canvas=playlist_canvas,
        )

        self.assertIs(SlideFlowApp.active_scroll_canvas(app), playlist_canvas)
        app.playlist_dialog = None
        self.assertIs(SlideFlowApp.active_scroll_canvas(app), main_canvas)

    def test_scroll_body_stops_at_both_edges(self) -> None:
        calls = []
        canvas = SimpleNamespace(
            yview=lambda: (0.0, 0.4),
            yview_scroll=lambda amount, mode: calls.append((amount, mode)),
        )
        app = SimpleNamespace(body=SimpleNamespace(_parent_canvas=canvas))
        self.assertEqual(SlideFlowApp.scroll_body(app, -3), "break")
        self.assertEqual(calls, [])

        canvas.yview = lambda: (0.2, 0.7)
        self.assertEqual(SlideFlowApp.scroll_body(app, 3), "break")
        self.assertEqual(calls, [(3, "units")])

        canvas.yview = lambda: (0.6, 1.0)
        self.assertEqual(SlideFlowApp.scroll_body(app, 3), "break")
        self.assertEqual(calls, [(3, "units")])

        canvas.yview = lambda: (0.0, 1.0)
        self.assertEqual(SlideFlowApp.scroll_body(app, -1, "pages"), "break")
        self.assertEqual(SlideFlowApp.scroll_body(app, 1, "pages"), "break")
        self.assertEqual(calls, [(3, "units")])

    def test_quit_application_cancels_work_before_destroying(self) -> None:
        calls = []
        cancel_event = SimpleNamespace(set=lambda: calls.append("cancel"))
        app = SimpleNamespace(
            cancel_event=cancel_event,
            destroy=lambda: calls.append("destroy"),
        )
        self.assertEqual(SlideFlowApp.quit_application(app), "break")
        self.assertEqual(calls, ["cancel", "destroy"])


if __name__ == "__main__":
    unittest.main()
