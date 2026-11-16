"""Turn the sliding Live Caption bubble into one continuous transcript."""

from __future__ import annotations

import difflib
import re

MAX_TRANSCRIPT_CHARS = 200_000

# Chrome announces the bubble to screen readers. It is not caption text.
_CHROME_NOISE = re.compile(
    r"\s*live caption visible, use window switcher to focus\.?\s*",
    re.IGNORECASE,
)


def normalize(text: str) -> str:
    """Collapse caption line wraps into a single readable line."""
    text = _CHROME_NOISE.sub(" ", text)
    text = text.replace("\u200b", "").replace("\ufeff", "").replace("\xa0", " ")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    parts = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    return " ".join(part for part in parts if part)


def common_prefix_len(left: str, right: str) -> int:
    limit = min(len(left), len(right))
    index = 0
    while index < limit and left[index] == right[index]:
        index += 1
    return index


def _word_key(word: str) -> str:
    key = word.casefold().strip(".,!?;:\"'`“”‘’()[]{}")
    return key or word.casefold()


def _match_needed(n_old: int, n_new: int) -> int:
    shortest = min(n_old, n_new)
    if shortest <= 6:
        return max(1, shortest - 1)
    return max(4, int(shortest * 0.4))


def _best_splice(old_words: list[str], new_words: list[str]) -> int | None:
    """Where `new_words` overlaps `old_words`.

    Live Caption keeps a window of recent words and rewrites them as it
    hears more, so the new window is not an exact tail of the old one.
    Matching words, not raw characters, lets those corrections land in
    place instead of pasting the window again.
    """
    if not old_words or not new_words:
        return None
    old_keys = [_word_key(word) for word in old_words]
    new_keys = [_word_key(word) for word in new_words]
    matcher = difflib.SequenceMatcher(a=old_keys, b=new_keys, autojunk=False)
    scores: dict[int, int] = {}
    for block in matcher.get_matching_blocks():
        if block.size == 0:
            continue
        splice = block.a - block.b
        if splice < 0 or splice > len(old_words):
            continue
        replaced = len(old_words) - splice
        if replaced > len(new_words) + 12:
            continue
        scores[splice] = scores.get(splice, 0) + block.size
    if not scores:
        return None
    need = _match_needed(len(old_words), len(new_words))
    splice, score = max(scores.items(), key=lambda item: (item[1], item[0]))
    if score < need:
        return None
    return splice


class Stitcher:
    """Merge successive caption windows without losing words that scrolled off."""

    def __init__(self) -> None:
        self.transcript = ""
        self.window = ""

    def reset(self) -> None:
        self.transcript = ""
        self.window = ""

    def update(self, bubble: str) -> str:
        bubble = normalize(bubble)
        if not bubble or bubble == self.window:
            return self.transcript
        if not self.transcript:
            self.transcript = bubble
            self.window = bubble
            return self._trimmed()

        before, sep, last_para = self.transcript.rpartition("\n")
        old_words = last_para.split()
        new_words = bubble.split()
        # Only the recent words can belong to the current bubble.
        tail_len = min(len(old_words), len(new_words) + 40)
        offset = len(old_words) - tail_len
        splice = _best_splice(old_words[offset:], new_words)
        if splice is None:
            self.transcript = f"{self.transcript}\n{bubble}"
        else:
            merged = " ".join(old_words[: offset + splice] + new_words)
            self.transcript = f"{before}{sep}{merged}" if sep else merged
        self.window = bubble
        return self._trimmed()

    def _trimmed(self) -> str:
        if len(self.transcript) <= MAX_TRANSCRIPT_CHARS:
            return self.transcript
        kept = self.transcript[-(MAX_TRANSCRIPT_CHARS // 2) :]
        line_break = kept.find("\n")
        if line_break != -1:
            kept = kept[line_break + 1 :]
        self.transcript = "Earlier captions were trimmed.\n" + kept
        return self.transcript
