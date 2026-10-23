"""Persistent history for completed SlideFlow PDFs."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class SlideHistoryEntry:
    title: str
    pdf_path: str
    output_dir: str
    created_at: float


class SlideHistoryStore:
    """Keep a small, local list of completed slide PDFs."""

    VERSION = 1

    def __init__(self, path: Path | None = None, limit: int = 50) -> None:
        self.path = path or (
            Path.home()
            / "Library"
            / "Application Support"
            / "SlideFlow"
            / "history.json"
        )
        self.limit = max(1, int(limit))
        self.initialized = self.path.is_file()
        self.entries = self._load()

    @staticmethod
    def title_from_pdf(path: Path) -> str:
        return path.stem.removesuffix(" - Slides").strip() or path.stem

    @staticmethod
    def _canonical(path: str | Path) -> str:
        return str(Path(path).expanduser().resolve())

    def _load(self) -> list[SlideHistoryEntry]:
        if not self.initialized:
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(payload, dict) or payload.get("version") != self.VERSION:
            return []

        entries: list[SlideHistoryEntry] = []
        seen: set[str] = set()
        for item in payload.get("entries", []):
            if not isinstance(item, dict):
                continue
            try:
                pdf_path = self._canonical(str(item["pdf_path"]))
                title = str(item["title"]).strip()
                output_dir = self._canonical(str(item["output_dir"]))
                created_at = float(item["created_at"])
            except (KeyError, TypeError, ValueError, OSError):
                continue
            if not title or pdf_path in seen or not Path(pdf_path).is_file():
                continue
            seen.add(pdf_path)
            entries.append(SlideHistoryEntry(title, pdf_path, output_dir, created_at))
        entries.sort(key=lambda entry: entry.created_at, reverse=True)
        return entries[: self.limit]

    def _save(self) -> bool:
        temporary_path = self.path.with_name(f".{self.path.name}.tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": self.VERSION,
                "entries": [asdict(entry) for entry in self.entries],
            }
            temporary_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary_path.replace(self.path)
        except OSError:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
            return False
        self.initialized = True
        return True

    def bootstrap(self, output_dir: str | Path) -> bool:
        """Import existing PDFs once, before the first persisted history exists."""
        if self.initialized:
            return True
        root = Path(output_dir).expanduser()
        candidates: list[Path] = []
        if root.is_dir():
            try:
                candidates = [path for path in root.rglob("* - Slides.pdf") if path.is_file()]
            except OSError:
                candidates = []
        candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        self.entries = [
            SlideHistoryEntry(
                title=self.title_from_pdf(path),
                pdf_path=self._canonical(path),
                output_dir=self._canonical(path.parent),
                created_at=path.stat().st_mtime,
            )
            for path in candidates[: self.limit]
        ]
        return self._save()

    def add(
        self,
        title: str,
        pdf_path: str | Path,
        output_dir: str | Path,
        created_at: float | None = None,
    ) -> bool:
        canonical_pdf = self._canonical(pdf_path)
        entry = SlideHistoryEntry(
            title=title.strip() or self.title_from_pdf(Path(canonical_pdf)),
            pdf_path=canonical_pdf,
            output_dir=self._canonical(output_dir),
            created_at=float(created_at if created_at is not None else time.time()),
        )
        self.entries = [
            entry,
            *(existing for existing in self.entries if existing.pdf_path != canonical_pdf),
        ][: self.limit]
        return self._save()

    def clear(self) -> bool:
        previous = self.entries
        self.entries = []
        if self._save():
            return True
        self.entries = previous
        return False
