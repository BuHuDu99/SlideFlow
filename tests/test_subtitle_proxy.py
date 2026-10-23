import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError

from subtitle_proxy import (
    FreeProxyPool, ProxyPoolUnavailable, SiteCaptionRateLimited, SiteCaptionUnavailable,
    fetch_caption_text, fetch_site_caption_text, parse_public_proxies,
)
from transcript_extractor import TranscriptBatchExtractor


class SubtitleProxyTests(unittest.TestCase):
    def test_proxy_parser_rejects_private_loopback_and_invalid_targets(self) -> None:
        page = '''<textarea class="form-control">
            8.8.8.8:8080\n127.0.0.1:8080\n192.168.1.5:3128
            1.1.1.1:65536\n9.9.9.9:8080\n8.8.8.8:8080
        </textarea>'''
        self.assertEqual(parse_public_proxies(page), ["8.8.8.8:8080", "9.9.9.9:8080"])

    def test_pool_scans_in_bounded_batches_and_skips_bad_proxies(self) -> None:
        pool = FreeProxyPool(max_scanned=3, scan_batch=2, workers=2)
        pool.candidates = ["8.8.8.8:80", "9.9.9.9:80", "1.1.1.1:80"]
        with patch.object(pool, "_check_proxy", side_effect=lambda item: item != "8.8.8.8:80"):
            first = pool.next_proxy()
            self.assertEqual(first, "9.9.9.9:80")
            pool.mark_bad(first)
            second = pool.next_proxy()
            self.assertEqual(second, "1.1.1.1:80")
            pool.mark_bad(second)
            self.assertIsNone(pool.next_proxy())
        self.assertEqual(pool.scanned_count, 3)

    def test_caption_fetch_prefers_vietnamese_and_uses_no_browser_cookies(self) -> None:
        transcript = SimpleNamespace(fetch=lambda: [SimpleNamespace(text=" Xin&nbsp;chào "), SimpleNamespace(text=" Bài học ")])
        class FakeList:
            def __iter__(self):
                return iter([transcript])
            def find_transcript(self, languages):
                self.languages = languages
                return transcript
        fake_list = FakeList()
        api = SimpleNamespace(list=lambda _video_id: fake_list)
        with patch("subtitle_proxy.YouTubeTranscriptApi", return_value=api) as constructor:
            text = fetch_caption_text("abcdefghijk", proxy="8.8.8.8:8080")
        self.assertEqual(text, "Xin chào\nBài học")
        self.assertEqual(fake_list.languages[0], "vi")
        self.assertEqual(constructor.call_args.kwargs["http_client"].cookies.get_dict(), {})
        self.assertIn("8.8.8.8:8080", constructor.call_args.kwargs["proxy_config"].http_url)

    def test_direct_429_uses_proxy_if_ios_captions_are_unavailable(self) -> None:
        info = {
            "id": "abcdefghijk",
            "subtitles": {"vi": [{"ext": "json3", "url": "https://www.youtube.com/api/timedtext"}]},
        }
        extractor = TranscriptBatchExtractor("https://www.youtube.com/watch?v=abcdefghijk", "/tmp")
        rate_limit = HTTPError("https://www.youtube.com/api/timedtext", 429, "Too Many Requests", {}, None)
        with patch("transcript_extractor.urllib.request.urlopen", side_effect=rate_limit), \
             patch.object(extractor, "read_via_ios", return_value=None) as ios, \
             patch.object(extractor, "read_via_proxy", return_value="Proxy transcript") as proxy:
            self.assertEqual(extractor.read_transcript(info), "Proxy transcript")
            self.assertTrue(extractor.prefer_proxy)
            self.assertEqual(extractor.read_transcript(info), "Proxy transcript")
        self.assertEqual(proxy.call_count, 2)
        self.assertEqual(ios.call_count, 2)

    def test_proxy_unavailable_is_not_reported_as_no_captions(self) -> None:
        extractor = TranscriptBatchExtractor("https://www.youtube.com/watch?v=abcdefghijk", "/tmp")
        kind, reason = extractor.error_reason(ProxyPoolUnavailable("Không có proxy lấy phụ đề"))
        self.assertEqual(kind, "proxy_unavailable")
        self.assertIn("proxy", reason)

    def test_bad_proxy_is_rotated_before_a_caption_is_saved(self) -> None:
        extractor = TranscriptBatchExtractor("https://www.youtube.com/watch?v=abcdefghijk", "/tmp")
        with patch.object(extractor.proxy_pool, "next_proxy", side_effect=["8.8.8.8:80", "9.9.9.9:80"]), \
             patch.object(extractor.proxy_pool, "mark_bad") as mark_bad, \
             patch("transcript_extractor.fetch_caption_text", side_effect=[OSError("proxy failed"), "Xin chào"]):
            text = extractor.read_via_proxy("abcdefghijk")
        self.assertEqual(text, "Xin chào")
        mark_bad.assert_called_once_with("8.8.8.8:80")
        self.assertIn("proxy công khai", extractor.last_origin)

    def test_site_txt_is_used_after_proxy_pool_exhaustion(self) -> None:
        extractor = TranscriptBatchExtractor("https://www.youtube.com/watch?v=abcdefghijk", "/tmp")
        with patch.object(extractor.proxy_pool, "next_proxy", return_value=None), \
             patch("transcript_extractor.fetch_site_caption_text", return_value="Lời bài giảng") as site:
            text = extractor.read_via_proxy("abcdefghijk")
        self.assertEqual(text, "Lời bài giảng")
        site.assert_called_once_with("abcdefghijk")
        self.assertIn("DownloadYoutubeSubtitles.com", extractor.last_origin)

    def test_site_failure_keeps_the_video_pending_instead_of_marking_no_captions(self) -> None:
        extractor = TranscriptBatchExtractor("https://www.youtube.com/watch?v=abcdefghijk", "/tmp")
        with patch.object(extractor.proxy_pool, "next_proxy", return_value=None), \
             patch("transcript_extractor.fetch_site_caption_text", side_effect=SiteCaptionUnavailable("HTTP 403")):
            with self.assertRaises(ProxyPoolUnavailable) as captured:
                extractor.read_via_proxy("abcdefghijk")
        kind, reason = extractor.error_reason(captured.exception)
        self.assertEqual(kind, "proxy_unavailable")
        self.assertIn("Website dự phòng", reason)

    def test_site_txt_normalizes_blank_lines_and_sends_only_public_video_url(self) -> None:
        response = SimpleNamespace(
            status_code=200,
            headers={"Content-Type": "application/force-download"},
            content="Xin chào\r\n\r\nBài giảng siêu âm\r\n".encode("utf-8"),
            raise_for_status=lambda: None,
        )
        with patch("subtitle_proxy.TimedSession") as session_type:
            session = session_type.return_value.__enter__.return_value
            session.get.return_value = response
            text = fetch_site_caption_text("abcdefghijk")
        self.assertEqual(text, "Xin chào\nBài giảng siêu âm")
        self.assertIn("i=abcdefghijk", session.get.call_args.args[0])
        headers = session.headers.update.call_args.args[0]
        self.assertIn("watch%3Fv%3Dabcdefghijk", headers["Referer"])
        self.assertNotIn("Cookie", headers)

    def test_site_rate_limit_and_missing_txt_are_reported_differently(self) -> None:
        for status, exception, expected_calls in (
            (429, SiteCaptionRateLimited, 1),
            (404, SiteCaptionUnavailable, 2),
        ):
            with self.subTest(status=status), patch("subtitle_proxy.TimedSession") as session_type:
                session = session_type.return_value.__enter__.return_value
                session.get.return_value = SimpleNamespace(status_code=status)
                with self.assertRaises(exception) as captured:
                    fetch_site_caption_text("abcdefghijk")
                self.assertIn(f"HTTP {status}", str(captured.exception))
                self.assertEqual(session.get.call_count, expected_calls)


if __name__ == "__main__":
    unittest.main()
