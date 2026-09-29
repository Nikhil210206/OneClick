"""Component 1 (normalize): put back the spaces a scraped article lost.

One kit article (rows 3, 11 and 17) arrives with whole paragraphs run together:
"To unlockyourdevicelockedduetosecurityreasons.restartyourdevice...". Left like that, a model cannot
read it and a step made from it is unreadable. Two repairs, both adding only spaces, so every step is
still the article's own words:

  punctuation  a sentence mark with a word glued to each side gets its space back ("biometrics,and"
               -> "biometrics, and", "reasons.restart" -> "reasons. restart": a lowercase word after a
               full stop is a typo for a comma, so no sentence starts there); in a clean article only a
               lost sentence break ("view.Once" -> "view. Once")
  words        in an article that is glued (several long runs of letters no dictionary knows), each
               such run is split into the cheapest sequence of words: unigram costs from wordninja's
               English list, the article's own words and our support corpus's counted as common, and a
               typo kept whole as one unknown piece. A clean article's rare words ("foldable",
               "SmartThings") are never touched.

Linear in the text: a run longer than `settings.deglue_max_run` is left alone and the split looks at
most `_MAX_WORD` characters back.
"""

import gzip
import importlib.util
import json
import math
import re
from functools import lru_cache
from pathlib import Path

from app.config import settings

# Two letters either side keeps "e.g." and "i.e." as they are; digits are not letters, so "8.5" stays.
_GLUED_MARK = re.compile(r"(?<=[a-z]{2})([.!?;,])([A-Za-z])(?=[A-Za-z])")
# In a clean article only a lost sentence break is repaired ("view.Once"): its comma lists
# ("Smartphone,Tablet") are left as written, so its text and hash do not change.
_GLUED_SENTENCE = re.compile(r"(?<=[a-z]{2})([.!?])([A-Z])(?=[a-z])")
_RUN = re.compile(r"[A-Za-z0-9]+")
_PARTS = re.compile(r"[A-Za-z]+|[0-9]+")
_MAX_WORD = 20  # longest piece the split considers
_SINGLE_LETTERS = {"a", "i"}
_WORDS = "wordninja_words.txt.gz"
# Unknown pieces (a typo such as "seecurity") cost this plus this much per letter: dearer than any
# split into common words, cheaper than a chain of rare fragments ("eec uri ty").
_UNKNOWN_BASE, _UNKNOWN_PER_CHAR = 6.0, 4.0
_RARE_SHORT_RANK = 10_000  # a 2-3 letter word rarer than this is a fragment, not a word


@lru_cache(maxsize=1)
def _english() -> dict[str, int]:
    """wordninja's English words by frequency rank (0 = most common); {} if the list is missing."""
    spec = importlib.util.find_spec("wordninja")  # located, not imported: its import loads a model
    if spec is None or not spec.origin:
        return {}
    here = Path(spec.origin).parent
    for folder in (here / "wordninja", here):
        try:
            data = (folder / _WORDS).read_bytes()
        except OSError:
            continue
        ranks: dict[str, int] = {}
        for rank, word in enumerate(gzip.decompress(data).decode("utf-8").split()):
            ranks.setdefault(word, rank)
        return ranks
    return {}


def word_rank(word: str) -> int | None:
    """How common an English word is (0 = the most common), or None when it is not one. The plain word
    or its singular / -es form counts ("flashes"); a typo ("blak", "screeen") is usually not a word."""
    english = _english()
    word = word.lower()
    ranks = [english[w] for w in (word, word[:-1], word[:-2]) if w in english and len(w) >= 3]
    return min(ranks) if ranks else None


def _known_words(text: str) -> frozenset[str]:
    """The dictionary words `text` uses, with their common inflections ("reboot" -> "rebooting").
    Only dictionary words: a short glued run ("andthentap") must never become a known word."""
    english = _english()
    out: set[str] = set()
    for word in set(re.findall(r"[a-z]{2,}", text.lower())):
        if word in english:
            out.update((word, f"{word}s", f"{word}ed", f"{word}ing", f"{word}d"))
    return frozenset(out)


@lru_cache(maxsize=1)
def _corpus_words() -> frozenset[str]:
    """Words Samsung support text uses: the kit articles and the Settings catalog."""
    root = Path(settings.data_dir) / "kit"
    texts: list[str] = []
    try:
        kit = json.loads((root / "siis_responses.json").read_text(encoding="utf-8"))
        texts += [json.dumps(r.get("siis_response")) for r in kit.get("responses", [])]
    except (OSError, ValueError):
        pass
    try:
        catalog = json.loads((root / "deeplinks.json").read_text(encoding="utf-8"))
        texts += [
            f"{e.get('description') or ''} {e.get('message') or ''}" for e in catalog.get("deeplinks", [])
        ]
    except (OSError, ValueError):
        pass
    return _known_words(" ".join(texts))


def is_known_word(word: str) -> bool:
    """An English word, or a word Samsung support text uses ("wifi", "touchscreen"): not a misspelling."""
    return word_rank(word) is not None or word.lower() in _corpus_words()


class _Costs:
    """Word costs for one article: -log unigram probability, with its own and the corpus's words cheap."""

    def __init__(self, article_words: frozenset[str]):
        self.english = _english()
        self.article = article_words
        self.corpus = _corpus_words()
        self.log_n = math.log(len(self.english) + 2)

    def __call__(self, word: str) -> float:
        rank = self.english.get(word)
        if word in self.article:
            rank = min(
                rank if rank is not None else settings.deglue_article_rank, settings.deglue_article_rank
            )
        elif word in self.corpus and rank is not None:
            # a domain word ("restart", "stable") counts as more frequent; a common one keeps its rank
            rank = min(rank, max(rank // settings.deglue_corpus_rank_divisor, settings.deglue_article_rank))
        if len(word) == 1:
            return (
                math.log((rank + 1) * self.log_n)
                if word in _SINGLE_LETTERS and rank is not None
                else math.inf
            )
        if rank is None or (len(word) <= 3 and rank > _RARE_SHORT_RANK):
            return _UNKNOWN_BASE + _UNKNOWN_PER_CHAR * len(word) if len(word) >= 4 else math.inf
        return math.log((rank + 1) * self.log_n)


def split_run(run: str, costs: _Costs) -> list[str] | None:
    """The words `run` is made of, in its own letter case; None when it cannot be split at all."""
    low = run.lower()
    n = len(low)
    best = [0.0] + [math.inf] * n
    back = [0] * (n + 1)
    for end in range(1, n + 1):
        for start in range(max(0, end - _MAX_WORD), end):
            if best[start] != math.inf:
                cost = best[start] + costs(low[start:end])
                if cost < best[end]:
                    best[end], back[end] = cost, start
    if best[n] == math.inf:
        return None
    pieces, end = [], n
    while end > 0:
        pieces.append(run[back[end] : end])
        end = back[end]
    return pieces[::-1]


def _unknown(word: str) -> bool:
    return word.lower() not in _english()


def _repair_run(run: str, costs: _Costs) -> str:
    if len(run) > settings.deglue_max_run:
        return run
    out: list[str] = []
    changed = False
    for part in _PARTS.findall(run):
        if part.isdigit() or len(part) < settings.deglue_min_run or not _unknown(part):
            out.append(part)
            continue
        pieces = split_run(part, costs)
        if pieces and len(pieces) > 1:
            out += pieces
            changed = True
        else:
            out.append(part)
    return " ".join(out) if changed else run


def _mark_space(match: re.Match) -> str:
    """Only the space: a lowercase word after a glued full stop ("reasons.restartyour") is the writer's
    typo for a comma, not a new sentence, so it stays lowercase and the sentence stays whole ("To unlock
    your device locked due to security reasons. restart your device ..."), with its purpose attached."""
    return f"{match.group(1)} {match.group(2)}"


def is_glued(text: str) -> bool:
    """Several long letter runs no dictionary knows: words have lost their spaces, not one rare word."""
    long_runs = re.findall(rf"[A-Za-z]{{{settings.deglue_glued_run},}}", text)
    return sum(1 for run in long_runs if _unknown(run)) >= settings.deglue_glued_runs


def deglue(text: str) -> str:
    """`text` with the spaces a scrape lost put back; text that was not glued comes back unchanged."""
    if not text or not _english():
        return text
    if not is_glued(text):
        return _GLUED_SENTENCE.sub(_mark_space, text)
    text = _GLUED_MARK.sub(_mark_space, text)
    costs = _Costs(_known_words(text))  # a glued run is no dictionary word, so only real words count
    return _RUN.sub(lambda m: _repair_run(m.group(0), costs), text)
