"""Component 7 (offline): strip description boilerplate, drop appliances (manual list), read polarity from message when originalType is null."""

import json
import re
from pathlib import Path

# "... via device Settings on the device." / "... in Samsung Members on the device."
_SURFACE_RE = re.compile(
    r"\s*(?:via|in)\s+(device Settings|TV Settings|TV Bixby|Samsung Members|Samsung Warranty Care)"
    r"\s+on the device\.?$",
    re.IGNORECASE,
)
# "Opens the X settings page" -> "X"; also "Retrieves the X for monitoring ..." -> "X"
_OPENS_RE = re.compile(r"^opens the\s+(.*?)\s+settings page$", re.IGNORECASE)
_RETRIEVES_RE = re.compile(r"^retrieves the\s+(.*?)\s+for\s+.*$", re.IGNORECASE)
_VERB_PREFIX_RE = re.compile(
    r"^(enables|disables|updates|opens|sets|runs|switches)\s+(?:the\s+)?", re.IGNORECASE
)

_PLACEHOLDER_ID = "DL-DUMMY"

# message prefixes that reveal polarity when originalType is missing (DL-0294 "Offurl", DL-0295 "Onurl")
_MESSAGE_POLARITY = {"onurl": "onURL", "offurl": "offURL"}


def _surface(description: str) -> str:
    """Which product the entry belongs to: device Settings, TV Settings, Samsung Members..."""
    match = _SURFACE_RE.search(description or "")
    return match.group(1) if match else "device Settings"


def clean_description(description: str) -> str:
    """Drop the boilerplate so retrieval and rerank read the screen, not the wrapper text."""
    text = _SURFACE_RE.sub("", description or "").strip().rstrip(".")
    for pattern in (_OPENS_RE, _RETRIEVES_RE):
        match = pattern.match(text)
        if match:
            return match.group(1).strip()
    return _VERB_PREFIX_RE.sub("", text).strip()


def polarity(entry: dict) -> str | None:
    """onURL (turn on) / offURL (turn off) / updateURL (set a value) / onClickURL (open a screen)."""
    original = entry.get("originalType")
    if original in {"onURL", "offURL", "updateURL", "onClickURL"}:
        return original
    return _MESSAGE_POLARITY.get((entry.get("message") or "").strip().lower())


def load_clean_catalog(path: str | Path) -> list[dict]:
    """Catalog entries plus derived fields: clean_description, surface, polarity, leaf.

    The DL-DUMMY placeholder is excluded; the resolver reaches it by its own fallback tier.
    """
    entries = json.loads(Path(path).read_text(encoding="utf-8"))["deeplinks"]
    cleaned = []
    for entry in entries:
        if entry["id"] == _PLACEHOLDER_ID:
            continue
        description = entry.get("description") or ""
        cleaned.append(
            {
                **entry,
                "clean_description": clean_description(description),
                "surface": _surface(description),
                "polarity": polarity(entry),
            }
        )
    return cleaned
