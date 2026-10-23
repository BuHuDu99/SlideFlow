"""FIFO state for slide jobs that must never run concurrently."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class SlideQueueJob:
    video_url: str
    display_title: str
    output_dir: str
    interval: int
    similarity_threshold: float
    quality: int
    save_transcript: bool
    cookie_browser: Optional[str] = None


class SequentialSlideQueue:
    """Keep one active slide job and an ordered list of waiting jobs."""

    def __init__(self) -> None:
        self._pending: deque[SlideQueueJob] = deque()
        self.active: Optional[SlideQueueJob] = None

    @property
    def pending(self) -> tuple[SlideQueueJob, ...]:
        return tuple(self._pending)

    def __len__(self) -> int:
        return len(self._pending)

    def add(self, jobs: list[SlideQueueJob]) -> None:
        self._pending.extend(jobs)

    def take_next(self) -> Optional[SlideQueueJob]:
        if self.active is not None or not self._pending:
            return None
        self.active = self._pending.popleft()
        return self.active

    def finish_active(self) -> Optional[SlideQueueJob]:
        finished = self.active
        self.active = None
        return finished

    def clear_pending(self) -> int:
        count = len(self._pending)
        self._pending.clear()
        return count
