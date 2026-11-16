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
    hears more. Inserted words ("gonna" becoming "going to") shift every
    match after them, so later matches do not share one offset. Anchor on
    the first overlap and count later matches that still sit on that same
    rewrite, instead of pasting the window again.
    """
    if not old_words or not new_words:
        return None
    old_keys = [_word_key(word) for word in old_words]
    new_keys = [_word_key(word) for word in new_words]
    matcher = difflib.SequenceMatcher(a=old_keys, b=new_keys, autojunk=False)
    blocks = [block for block in matcher.get_matching_blocks() if block.size]
    if not blocks:
        return None
    # How far a later match may drift when words were inserted or dropped.
    tolerance = max(8, len(new_words) // 3)
    scores: dict[int, int] = {}
    for anchor in blocks:
        splice = anchor.a - anchor.b
        if splice < 0 or splice > len(old_words):
            continue
        replaced = len(old_words) - splice
        if replaced > len(new_words) + 12:
            continue
        score = 0
        prev_a_end = None
        prev_b_end = None
        for block in blocks:
            if block.a < splice or block.b < anchor.b:
                continue
            if prev_a_end is not None:
                # A stray repeated word far from the overlap is not a rewrite.
                if block.a - prev_a_end > 4 or block.b - prev_b_end > 5:
                    continue
            drift = splice + block.b - block.a
            if not -6 <= drift <= tolerance:
                continue
            score += block.size
            prev_a_end = block.a + block.size
            prev_b_end = block.b + block.size
        scores[splice] = max(scores.get(splice, 0), score)
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
        self._collapse_rewritten_prefix()
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
        self._collapse_rewritten_prefix()
        return self._trimmed()

    def _collapse_rewritten_prefix(self) -> None:
        """Drop a paragraph when the next one is that same caption, rewritten."""
        while "\n" in self.transcript:
            before, _, last = self.transcript.rpartition("\n")
            prev_before, sep, prev = before.rpartition("\n")
            prev_words = prev.split()
            last_words = last.split()
            splice = _best_splice(prev_words, last_words)
            if splice is None or splice > 3:
                return
            merged = " ".join(prev_words[:splice] + last_words)
            collapsed = f"{prev_before}{sep}{merged}" if sep else merged
            if collapsed == self.transcript:
                return
            self.transcript = collapsed

    def _trimmed(self) -> str:
        if len(self.transcript) <= MAX_TRANSCRIPT_CHARS:
            return self.transcript
        kept = self.transcript[-(MAX_TRANSCRIPT_CHARS // 2) :]
        line_break = kept.find("\n")
        if line_break != -1:
            kept = kept[line_break + 1 :]
        self.transcript = "Earlier captions were trimmed.\n" + kept
        return self.transcript
