import unittest

from slide_queue import SequentialSlideQueue, SlideQueueJob


def job(number: int) -> SlideQueueJob:
    return SlideQueueJob(
        video_url=f"https://www.youtube.com/watch?v=video{number:06d}",
        display_title=f"Video {number}",
        output_dir="/tmp",
        interval=7,
        similarity_threshold=0.92,
        quality=720,
        save_transcript=True,
    )


class SequentialSlideQueueTests(unittest.TestCase):
    def test_jobs_are_taken_in_fifo_order_and_never_overlap(self) -> None:
        queue = SequentialSlideQueue()
        jobs = [job(1), job(2), job(3)]
        queue.add(jobs)

        self.assertEqual(queue.take_next(), jobs[0])
        self.assertIsNone(queue.take_next())
        self.assertEqual(queue.pending, (jobs[1], jobs[2]))

        self.assertEqual(queue.finish_active(), jobs[0])
        self.assertEqual(queue.take_next(), jobs[1])
        self.assertEqual(queue.finish_active(), jobs[1])
        self.assertEqual(queue.take_next(), jobs[2])
        self.assertEqual(queue.finish_active(), jobs[2])
        self.assertIsNone(queue.take_next())

    def test_clear_pending_keeps_the_active_job(self) -> None:
        queue = SequentialSlideQueue()
        jobs = [job(1), job(2), job(3)]
        queue.add(jobs)
        self.assertEqual(queue.take_next(), jobs[0])

        self.assertEqual(queue.clear_pending(), 2)
        self.assertEqual(queue.active, jobs[0])
        self.assertEqual(queue.pending, ())


if __name__ == "__main__":
    unittest.main()
