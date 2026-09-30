"""Component 1: normalize query, scrub URLs/emails from SIIS, compute siis_hash. No LLM."""

import hashlib
import re

from app.compiler.scrub import scrub
from app.pipeline.deglue import deglue

_LIST_NUMBER = re.compile(r"^\s*\d+[.)]\s*")
_FIRST_ITEM = re.compile(r"\s*1[.)]\s")
_QUOTES = "\"'“”‘’ "
_WHITESPACE = re.compile(r"\s+")
_HASH_LENGTH = 16  # matches the siis_hash values in data/fixtures/*/cache_events.json


def _split_inline(line: str) -> list[str]:
    """'1. "a" 2. "b" 3. "c"' on one line (input.txt's form of a numbered kit query) as its items.

    Only a line that opens with item 1 is split, and only at the next number in sequence after a
    space, so a stray "5." inside a complaint ("Android 14. The screen...") is left alone.
    """
    if not _FIRST_ITEM.match(line):
        return [line]
    items, number = [], 2
    while match := re.search(rf"\s{number}[.)]\s", line):
        items.append(line[: match.start()])
        line, number = line[match.start() :], number + 1
    return [*items, line]


def _unlist(query: str) -> str:
    """Kit numbering and wrapping quotes removed from every item, items joined into one text.

    A kit query can be a numbered list of quoted complaints, one per line ('1. "..."\\n2. "..."', as
    in siis_responses.json) or all on one line ('1. "..." 2. "..."', as in input.txt).
    """
    items = (item for line in (query or "").splitlines() for item in _split_inline(line))
    lines = (_LIST_NUMBER.sub("", item).strip(_QUOTES) for item in items)
    return _WHITESPACE.sub(" ", " ".join(line for line in lines if line)).strip()


def normalize_query(query: str) -> str:
    """Cache-key form of the complaint: kit numbering and quotes gone, whitespace collapsed, lowercase.

    Punctuation is kept: the exact tier should only match what really is the same question.
    """
    return _unlist(query).lower()


def display_query(query: str) -> str:
    """The complaint as the LLM and the cache index see it: as written, minus kit numbering, quotes,
    links and email addresses (scrubbed before any LLM call, like the article)."""
    return _WHITESPACE.sub(" ", scrub(_unlist(query))).strip()


def siis_text(siis: dict | str | None) -> str:
    """The article body from either accepted shape: a plain string (the guide's example) or
    `{title, content}` (the kit and the FAQ).

    Missing, `null`, `""`, `{}`, whitespace-only content and a title with no content all come back
    as "" - one "no article" case for the caller. A title alone holds no steps to ground.
    """
    if isinstance(siis, str):
        return siis
    if not isinstance(siis, dict):
        return ""
    if "content" in siis:
        content = siis["content"]
        return content if isinstance(content, str) else ""
    # Unknown shape: every string value but the title, in order, so nothing the article says is lost.
    return "\n".join(v for k, v in siis.items() if k != "title" and isinstance(v, str))


def coerce_query(value: object, max_chars: int) -> str:
    """Any JSON value as the complaint (hard rule 4: a malformed request still gets an answer).

    null or an object is "", a number or boolean its text, a list its string items joined; the result
    is cut to `max_chars`.
    """
    if isinstance(value, str):
        text = value
    elif isinstance(value, bool | int | float):
        text = str(value)
    elif isinstance(value, list):
        text = " ".join(v for v in value if isinstance(v, str))
    else:
        text = ""
    return text[:max_chars]


def coerce_siis(value: object) -> dict | str | None:
    """Any JSON value as the article: a list of articles (strings or {title, content}) becomes their
    texts joined; anything else that is not a string or an object is no article."""
    if value is None or isinstance(value, dict | str):
        return value
    if isinstance(value, list):
        return "\n\n".join(t for t in (siis_text(v) for v in value) if t.strip())
    return None


def siis_title(siis: dict | str | None) -> str | None:
    title = siis.get("title") if isinstance(siis, dict) else None
    return scrub(title) if isinstance(title, str) and title.strip() else None


def clean_siis(siis: dict | str | None) -> tuple[str, str | None]:
    """Return (siis_clean, siis_hash).

    The scrub runs here, before any LLM sees the article (the kit carries a real-looking address), and
    lost spaces are put back (pipeline/deglue.py), so a glued address is scrubbed first and the
    repaired text is scrubbed again. The hash is over the cleaned text, so the same article always
    keys the same cache entries.
    """
    text = scrub(siis_text(siis).replace("\r\n", "\n"))
    try:
        repaired = scrub(deglue(text))
    except Exception:  # noqa: BLE001 - a repair that fails leaves the article as it came
        repaired = text
    text = repaired
    if not text.strip():
        return "", None
    return text, hashlib.sha256(text.encode("utf-8")).hexdigest()[:_HASH_LENGTH]
