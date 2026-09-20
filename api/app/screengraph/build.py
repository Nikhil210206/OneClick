"""Component 7 (offline): cluster entries into ScreenNodes (shared validation deeplink or near-identical description)."""

import re
from collections import defaultdict

from app.models import ScreenNode

# Catalog verbs that prefix a message ("Enable Power saving"): they name the action, not the screen.
_MESSAGE_VERBS = frozenset(
    ["view", "enable", "disable", "adjust", "check", "switch", "increase", "run", "open"]
)
_WORD_RE = re.compile(r"[a-z0-9]+")


def _screen_name(entry: dict) -> str:
    """The screen a catalog entry belongs to, without its action verb."""
    words = [w for w in _WORD_RE.findall(entry["message"].lower()) if w not in _MESSAGE_VERBS]
    return " ".join(words) or entry["clean_description"].lower()


def _group_key(entry: dict) -> str:
    """Entries sharing a validation deeplink are the same screen; otherwise use the description.

    The two signals agree almost exactly on the kit catalog (419 vs 420 groups), so the
    validation deeplink is trusted first and the cleaned description is the fallback for the
    few entries that carry no validation object.
    """
    validation = entry.get("validation") or {}
    return validation.get("deeplink") or f"desc:{entry['clean_description'].lower()}"


def build_nodes(entries: list[dict]) -> list[ScreenNode]:
    """Cluster cleaned catalog entries into screens, keeping each entry's own validation object."""
    return build_graph(entries)[0]


def build_graph(entries: list[dict]) -> tuple[list[ScreenNode], dict[str, dict]]:
    """Nodes plus per-node extras: the member entries and the surface they belong to.

    Extras live beside the nodes rather than on ScreenNode: models.py is the shared contract
    and changing it needs team agreement.
    """
    grouped: dict[str, list[dict]] = defaultdict(list)
    for entry in entries:
        grouped[_group_key(entry)].append(entry)

    # Second pass: groups whose cleaned description is identical are the same screen reached by
    # different validation keys (a "check" entry and its on/off pair), so fold them together.
    by_description: dict[str, list[dict]] = defaultdict(list)
    for members in grouped.values():
        by_description[members[0]["clean_description"].lower()].extend(members)

    nodes = []
    extras: dict[str, dict] = {}
    for index, (_key, members) in enumerate(sorted(by_description.items()), start=1):
        entries_by_polarity: dict[str, list[str]] = defaultdict(list)
        validation_by_entry: dict[str, dict] = {}
        for entry in members:
            entries_by_polarity[entry.get("polarity") or "unknown"].append(entry["id"])
            if entry.get("validation"):
                validation_by_entry[entry["id"]] = entry["validation"]

        # Name the screen after its description ("Intelligent Wi-Fi"), not its message: many
        # unrelated screens share a generic message like "View WiFi Settings".
        names = sorted({_screen_name(e) for e in members}, key=len)
        descriptions = sorted({e["clean_description"] for e in members}, key=len)
        nodes.append(
            ScreenNode(
                node_id=f"SN-{index:04d}",
                path=descriptions[0] or names[0],
                synonyms=sorted({*names, *descriptions[1:]}),
                entries_by_polarity=dict(entries_by_polarity),
                validation_by_entry=validation_by_entry,
            )
        )
        extras[nodes[-1].node_id] = {
            "surface": members[0].get("surface", "device Settings"),
            "entries": members,
        }
    return nodes, extras
