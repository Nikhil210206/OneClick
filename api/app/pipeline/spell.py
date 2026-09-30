"""Component 2 (cache, matching only): misspelt words put right before a complaint is matched.

"a16 dsplay went balck by itsself" and "my galaxy s22 screne stays blnak" lose the words the cache
matches on: the embedding drifts, and the lost symptom ("black", "blank") turns the slot guard permissive,
so a typo'd paraphrase either misses or is served another row's plan (metrics.md: typo paraphrases hit
78%, and 2 of the 5 wrong-plan hits were typo'd). This module is used only where a complaint is matched
against stored ones (slots and the cache's embeddings); the text an LLM reads, and every step, is left as
the customer wrote it.

A word is rewritten only when it is unknown: not a common English word (rank within
`typo_known_max_rank` in wordninja's list), not in the kit articles or the Settings catalog, not a slot
lexicon phrase. The replacement is the known word one edit away (Damerau: a letter added, dropped,
changed or two swapped), or a lexicon word two edits away for a long word. A rare word that is still in
the English list ("lagging", but also misspellings that happen to be words, "blak") only ever becomes a
lexicon word. Lexicon words win ties, then
support vocabulary, then the more common English word: "blnak" is one edit from "blank" and "bleak",
"screne" from "screen" and "scene".
"""

import re
from functools import lru_cache

from app.config import settings
from app.pipeline.deglue import _corpus_words, _english

_LETTERS = "abcdefghijklmnopqrstuvwxyz"
_WORD = re.compile(r"[A-Za-z]+")
_TIER_LEXICON, _TIER_CORPUS, _TIER_ENGLISH = 0, 1, 2


@lru_cache(maxsize=1)
def _lexicon_words() -> frozenset[str]:
    """Every single word of the slot lexicon's topic phrases (components and symptoms). Only those: the
    direction verbs ("put back") would win the tie that makes "balck" black."""
    from app.pipeline.slots import _raw_lexicon  # lazy: slots imports this module

    words: set[str] = set()
    for field, labels in _raw_lexicon().items():
        if field not in ("component", "symptom") or not isinstance(labels, dict):
            continue
        for phrases in labels.values():
            for phrase in phrases:
                words.update(w for w in _WORD.findall(phrase.lower()) if len(w) >= 3)
    return frozenset(words)


def _known(word: str) -> bool:
    rank = _english().get(word)
    if rank is not None and rank <= settings.typo_known_max_rank:
        return True
    return word in _corpus_words() or word in _lexicon_words()


def _edits1(word: str) -> set[str]:
    splits = [(word[:i], word[i:]) for i in range(len(word) + 1)]
    deletes = {a + b[1:] for a, b in splits if b}
    swaps = {a + b[1] + b[0] + b[2:] for a, b in splits if len(b) > 1}
    replaces = {a + c + b[1:] for a, b in splits if b for c in _LETTERS}
    inserts = {a + c + b for a, b in splits for c in _LETTERS}
    return deletes | swaps | replaces | inserts


def _distance(a: str, b: str) -> int:
    """Optimal string alignment distance (Damerau with adjacent swaps)."""
    rows = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) + 1):
        rows[i][0] = i
    for j in range(len(b) + 1):
        rows[0][j] = j
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            rows[i][j] = min(rows[i - 1][j] + 1, rows[i][j - 1] + 1, rows[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                rows[i][j] = min(rows[i][j], rows[i - 2][j - 2] + 1)
    return rows[-1][-1]


def _rank(word: str) -> tuple[int, int]:
    """Sort key of a candidate: lexicon, then support vocabulary, then English by frequency."""
    english = _english().get(word, 10**9)
    if word in _lexicon_words():
        return _TIER_LEXICON, english
    if word in _corpus_words():
        return _TIER_CORPUS, english
    return _TIER_ENGLISH, english


@lru_cache(maxsize=4096)
def correct_word(word: str) -> str:
    """The word, or the known word it most likely misspells. Lower-case letters only."""
    if len(word) < settings.typo_min_len or _known(word):
        return word
    candidates = [c for c in _edits1(word) if len(c) >= 3 and _known(c)]
    if word in _english():
        # A rare but real word ("lagging", "blak") is changed only into a lexicon word, the vocabulary the
        # cache guard matches on ("blak" -> "black"); "lagging" is never turned into "logging".
        candidates = [c for c in candidates if c in _lexicon_words()]
        return min(candidates, key=lambda c: (_rank(c), c)) if candidates else word
    if not candidates and len(word) >= settings.typo_edits2_min_len:
        candidates = [w for w in _lexicon_words() if abs(len(w) - len(word)) <= 2 and _distance(word, w) <= 2]
    return min(candidates, key=lambda c: (_rank(c), c)) if candidates else word


def correct_typos(text: str) -> str:
    """The text with each misspelt word put right (lower-cased where it changed); `cache_typo_correction`
    off, or no English word list, returns it unchanged."""
    if not settings.cache_typo_correction or not text or not _english():
        return text

    def fix(match: re.Match) -> str:
        word = match.group(0)
        # a word glued to a digit ("a16", "s22") is a model name, not a misspelling
        start, end = match.span()
        if (start and text[start - 1].isdigit()) or (end < len(text) and text[end].isdigit()):
            return word
        fixed = correct_word(word.lower())
        return word if fixed == word.lower() else fixed

    return _WORD.sub(fix, text)
