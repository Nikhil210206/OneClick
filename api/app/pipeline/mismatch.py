"""Component 4/6 (relevance): does the article share any real word with the complaint?

Two things were measured (2026-09-29) before this module was written:

* Embedding relevance cannot say whether an article is about the complaint: "phone is hot" against the
  touchscreen article scores 0.62, a real kit row 0.63.
* The model's own verdict (call C's `match`) is wording-sensitive on loosely paired articles, which
  a third of the kit's pairs are: 16% of the paraphrases of well-matched rows came back `unrelated`
  (kit row 8: 10 of 10), and the judge scores those plans 3 of 3.

So the model's word is never enough on its own. An article is turned away only when the model says it is
`unrelated` AND this deterministic check agrees: none of the complaint's words appears anywhere in the
article. Measured on the kit, the unseen set, the near misses and the 200 paraphrases, the check alone
flags 0 of 17 kit complaints, 0 of 15 unseen, 0 of 60 near misses (5% of the paraphrases, all misspelt),
and 8 of 10 hand-made mismatches; together with the model's verdict it acts on 1 of 170 matched
paraphrases, a misspelt one that call B's typo-free problem statement then protects.

What counts:

* Support: any complaint word (a typo, a rare word, anything) that the article also uses, compared by its
  first `settings.mismatch_stem_chars` letters so "flickers" meets "flickering". One shared word saves the
  article, so a longer list of complaint texts (the complaint plus the typo-free problem statements the
  model wrote) can only add support.
* Trigger: at least one of the complaint's words is a common English word ("hot"; a typo such as "blak"
  proves nothing), so a complaint made only of typos and model numbers gets no verdict.
* Ignored on both sides: stopwords, UI verbs, words every complaint uses ("phone", "galaxy", "problem"),
  and the general-fix vocabulary ("charge", "restart", "update", "reset", "support"): every article has those
  sections, so sharing one says nothing about the topic ("hot while charging" is not covered by the
  touchscreen article's Charger Issues section).
"""

from collections.abc import Iterable

from app.config import settings
from app.pipeline.deglue import is_known_word, word_rank
from app.pipeline.text import STOPWORDS, UI_VERBS, tokens, word_block

# Words that say nothing about what is wrong: they are in nearly every complaint and every article.
GENERIC = word_block(
    """
    phone galaxy samsung device tablet mobile smartphone flip fold ultra plus new old only even still already
    something anything everything nothing stops start starts started stay stays stayed keeps keep kept goes going
    gone gets get getting got becomes become became shows show showing showed appears appear appearing comes come
    coming work works working worked problem problems issue issues trouble happens happen happened happening
    seems seem able unable cant cannot wont doesnt dont isnt time times way thing things lot really much many
    like also completely totally suddenly always never sometimes often whenever every each
    """
)
# The fixes every article offers whatever its topic: sharing one of these words proves nothing.
GENERAL_FIX = word_block(
    """
    charge charges charged charging charger chargers restart restarts restarted restarting reboot reboots
    rebooting update updates updated updating reset resets resetting support contact service services safe mode
    factory backup
    """
)


def _stem(word: str) -> str:
    return word[: settings.mismatch_stem_chars]


def _common(word: str) -> bool:
    rank = word_rank(word)
    return rank is not None and rank <= settings.mismatch_max_word_rank


def _alphabetic_words(text: str) -> list[str]:
    """Words a reader would know (4+ letters, letters only), leaving out the ones every complaint uses."""
    out = []
    for word in tokens(text):
        word = word.strip("'-")
        if len(word) < 4 or not word.isalpha():
            continue
        if word not in STOPWORDS and word not in UI_VERBS and word not in GENERIC:
            out.append(word)
    return out


def distinctive_words(text: str) -> list[str]:
    """The complaint's words that could say what is wrong (unstemmed, in order, without repeats)."""
    out: dict[str, None] = {}
    for word in tokens(text):
        word = word.strip("'-")
        if len(word) < 3 or any(c.isdigit() for c in word):
            continue
        if word in STOPWORDS or word in UI_VERBS or word in GENERIC or word in GENERAL_FIX:
            continue
        out[word] = None
    return list(out)


def check(complaints: Iterable[str], article: Iterable[str]) -> dict:
    """{unrelated, terms, shared, typos}. `unrelated` is True when the complaint has at least one common
    English word, no complaint word at all (common or not) appears in the article, and no word of it looks
    misspelt. `terms` are the common words, `shared` the complaint words the article uses, `typos` the words
    that are not English, not Samsung support vocabulary and not in the article.

    A misspelt complaint gets no verdict: its real words are few, and the words it lost may be exactly the
    ones the article shares (measured: the misspelt paraphrases are what a bare word check flags)."""
    words: dict[str, None] = {}
    alphabetic: dict[str, None] = {}
    for text in complaints:
        words.update(dict.fromkeys(distinctive_words(text)))
        alphabetic.update(dict.fromkeys(_alphabetic_words(text)))
    vocabulary = {_stem(w.strip("'-")) for text in article for w in tokens(text) if len(w) >= 3}
    real = sorted(w for w in words if _common(w))
    shared = sorted(w for w in words if _stem(w) in vocabulary)
    typos = sorted(w for w in alphabetic if not is_known_word(w) and _stem(w) not in vocabulary)
    return {
        "unrelated": bool(real) and not shared and not typos,
        "terms": real,
        "shared": shared,
        "typos": typos,
    }
