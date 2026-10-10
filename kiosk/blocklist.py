"""First content-filter layer: a normalised, whole-word blocklist.

Kids will try to sneak rude words past the kiosk ("p00p", "k i l l", "b-o-m-b"),
so text is normalised before matching. Matching is always on whole words, never
on substrings inside longer words, so "skilled" and "scunthorpe" are fine.
"""

import re
from pathlib import Path

_SUBSTITUTIONS = str.maketrans(
    {"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"}
)
_REPEATS = re.compile(r"([a-z])\1{2,}")
_MIN_SUBRUN = 3  # shortest sub-run of spaced-out single letters worth testing


def _clean(text: str, keep: int) -> str:
    """Lower-case, map look-alike characters, drop non-letters, squash letter runs.

    Runs of 3 or more of the same letter are cut down to `keep` letters.
    """
    mapped = text.lower().translate(_SUBSTITUTIONS)
    letters = "".join(c for c in mapped if c.isalpha() or c == " ")
    squashed = _REPEATS.sub(lambda m: m.group(1) * keep, letters)
    return " ".join(squashed.split())


def normalise(text: str) -> str:
    """Normalise text for matching (runs of 3+ of the same letter become one)."""
    return _clean(text, keep=1)


def _single_letter_candidates(words: list[str]) -> set[str]:
    """Words made by joining spaced-out letters, e.g. "p o o p" -> "poop".

    For each maximal run of single-letter tokens, the whole joined run is a
    candidate, as is every contiguous sub-run of 3 or more letters (so
    "a b o m b" still yields "bomb").
    """
    found: set[str] = set()
    i = 0
    while i < len(words):
        if len(words[i]) != 1:
            i += 1
            continue
        j = i
        while j < len(words) and len(words[j]) == 1:
            j += 1
        run = words[i:j]
        if len(run) >= 2:
            found.add("".join(run))
        for start in range(len(run)):
            for end in range(start + _MIN_SUBRUN, len(run) + 1):
                found.add("".join(run[start:end]))
        i = j
    return found


def _contains_sequence(words: list[str], phrase: tuple[str, ...]) -> bool:
    n = len(phrase)
    return any(tuple(words[k : k + n]) == phrase for k in range(len(words) - n + 1))


class Blocklist:
    def __init__(self, entries: list[str]):
        # Entries go through the same normalisation as the text. Duplicates are dropped.
        seen: dict[tuple[str, ...], None] = {}
        for entry in entries:
            words = tuple(normalise(entry).split())
            if words:
                seen[words] = None
        self._entries = list(seen)

    @classmethod
    def from_file(cls, path) -> "Blocklist":
        """One entry per line; `#` starts a comment; blank lines are ignored."""
        entries = []
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                entries.append(line)
        return cls(entries)

    def __len__(self) -> int:
        return len(self._entries)

    def hits(self, text: str) -> list[str]:
        """Blocklist entries found in `text` as whole words, in list order, once each.

        Two views of the text are checked. The first squashes runs of 3+ repeated
        letters to one ("shiiit" -> "shit"). The second squashes them to two
        ("POOOOP" -> "poop"), which catches words that really do have a double letter.
        """
        views = []
        for keep in (1, 2):
            words = _clean(text, keep).split()
            views.append((words, set(words) | _single_letter_candidates(words)))

        hit = []
        for entry in self._entries:
            for words, singles in views:
                if len(entry) == 1:
                    matched = entry[0] in singles
                else:
                    matched = _contains_sequence(words, entry)
                if matched:
                    hit.append(" ".join(entry))
                    break
        return hit
