import os
import tempfile
import unittest
from pathlib import Path

from slide_history import SlideHistoryStore


class SlideHistoryStoreTests(unittest.TestCase):
    def test_bootstrap_imports_existing_pdfs_newest_first(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            older = root / "Bài cũ" / "Bài cũ - Slides.pdf"
            newer = root / "Bài mới" / "Bài mới - Slides.pdf"
            older.parent.mkdir()
            newer.parent.mkdir()
            older.write_bytes(b"old")
            newer.write_bytes(b"new")
            os.utime(older, (1, 1))
            os.utime(newer, (2, 2))

            store = SlideHistoryStore(root / "state" / "history.json")
            self.assertTrue(store.bootstrap(root))

            self.assertEqual([entry.title for entry in store.entries], ["Bài mới", "Bài cũ"])
            self.assertEqual(Path(store.entries[0].pdf_path), newer.resolve())

    def test_clear_keeps_pdfs_and_does_not_reimport_them(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pdf = root / "Bài học" / "Bài học - Slides.pdf"
            pdf.parent.mkdir()
            pdf.write_bytes(b"pdf")
            history_path = root / "state" / "history.json"
            store = SlideHistoryStore(history_path)
            store.bootstrap(root)

            self.assertTrue(store.clear())
            self.assertTrue(pdf.is_file())
            reloaded = SlideHistoryStore(history_path)
            self.assertEqual(reloaded.entries, [])
            reloaded.bootstrap(root)
            self.assertEqual(reloaded.entries, [])

    def test_add_deduplicates_and_persists_latest_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pdf = root / "Bài học" / "Bài học - Slides.pdf"
            pdf.parent.mkdir()
            pdf.write_bytes(b"pdf")
            history_path = root / "history.json"
            store = SlideHistoryStore(history_path)

            self.assertTrue(store.add("Tên cũ", pdf, pdf.parent, created_at=1))
            self.assertTrue(store.add("Tên mới", pdf, pdf.parent, created_at=2))

            reloaded = SlideHistoryStore(history_path)
            self.assertEqual(len(reloaded.entries), 1)
            self.assertEqual(reloaded.entries[0].title, "Tên mới")
            self.assertEqual(reloaded.entries[0].created_at, 2)


if __name__ == "__main__":
    unittest.main()
