"""Compatibility entry point for the online SlideFlow engine."""

from online_extractor import ExtractionResult, OnlineSlideExtractor, main

SlideExtractor = OnlineSlideExtractor

__all__ = ["ExtractionResult", "OnlineSlideExtractor", "SlideExtractor"]


if __name__ == "__main__":
    main()
