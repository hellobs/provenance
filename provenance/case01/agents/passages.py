"""Lossless, deterministic paragraph/sentence passages with original offsets."""
import re

VERSION = "paragraph-sentence-v1-600"
MAX_CHARS = 600


def passages(text, max_chars=MAX_CHARS):
    if max_chars < 1:
        raise ValueError("max_chars must be positive")
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            window = text[start:end]
            # Prefer a paragraph break, then a complete sentence. A very long
            # sentence needs a hard split; offsets preserve the exact original.
            boundaries = [m.end() for m in re.finditer(r"\n\s*\n", window)]
            if not boundaries:
                boundaries = [m.end() for m in re.finditer(r"[。！？!?；;]|\.\s+|\n", window)]
            if boundaries:
                end = start + boundaries[-1]
        yield start, end, text[start:end]
        start = end
