"""Screen Graph: the catalog seen as screens (component 7).

A screen is one place in Settings; its entries are the buttons that reach it (open / on / off /
set). Retrieval matches screens, then the step's verb picks the entry, so the on/off twins stop
competing with each other for the same step.
"""

import json
from pathlib import Path

from app.models import ScreenNode
from app.screengraph.build import build_graph
from app.screengraph.clean import load_clean_catalog

# Which entry a step verb wants, in order of preference.
_VERB_PREFERENCE = {
    "enable": ("onURL", "onClickURL", "updateURL"),
    "disable": ("offURL", "onClickURL", "updateURL"),
    "set": ("updateURL", "onClickURL"),
    "adjust": ("updateURL", "onClickURL"),
    "open": ("onClickURL", "updateURL", "onURL"),
    "check": ("onClickURL", "updateURL", "onURL"),
}
_FALLBACK_ORDER = ("onClickURL", "updateURL", "onURL", "offURL", "unknown")

_nodes: dict[str, ScreenNode] = {}
_extras: dict[str, dict] = {}


def load_graph(catalog_path: str | Path) -> list[ScreenNode]:
    """Build the graph from the catalog and keep it in memory for entry lookups."""
    global _nodes, _extras
    nodes, _extras = build_graph(load_clean_catalog(catalog_path))
    _nodes = {node.node_id: node for node in nodes}
    return nodes


def save_graph(nodes: list[ScreenNode], path: str | Path) -> None:
    """Write the graph to data/build/ so the API loads it instead of rebuilding at boot."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "nodes": [node.model_dump() for node in nodes],
        "extras": _extras,
    }
    path.write_text(json.dumps(payload, indent=1), encoding="utf-8")


def load_prebuilt(path: str | Path) -> list[ScreenNode]:
    """Load a graph written by scripts/build_screengraph.py."""
    global _nodes, _extras
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    nodes = [ScreenNode(**node) for node in payload["nodes"]]
    _extras = payload["extras"]
    _nodes = {node.node_id: node for node in nodes}
    return nodes


def load(catalog_path: str | Path, build_path: str | Path | None = None) -> list[ScreenNode]:
    """Prefer the prebuilt graph; fall back to building it from the catalog."""
    if build_path and Path(build_path).exists():
        return load_prebuilt(build_path)
    return load_graph(catalog_path)


def get_node(node_id: str) -> ScreenNode:
    return _nodes[node_id]


def node_docs(nodes: list[ScreenNode]) -> list[dict]:
    """Search documents for the retrieval modules, one per screen.

    Field names match the catalog documents so BM25 keeps the same weights. Each field carries
    the text of every member entry, so a screen is as findable as its buttons were separately.
    """
    docs = []
    for node in nodes:
        members = _extras.get(node.node_id, {}).get("entries", [])
        docs.append(
            {
                "id": node.node_id,
                "fields": {
                    "message": " ".join([node.path, *(e["message"] for e in members)]),
                    "qna_description": " ".join(
                        [*node.synonyms, *(e.get("qna_description") or "" for e in members)]
                    ),
                    "clean_description": " ".join(
                        dict.fromkeys([node.path, *(e["clean_description"] for e in members)])
                    ),
                },
                "meta": {
                    "polarities": sorted(node.entries_by_polarity),
                    "surface": _extras.get(node.node_id, {}).get("surface", "device Settings"),
                },
            }
        )
    return docs


def node_of(entry_id: str) -> str | None:
    """The screen a catalog entry belongs to."""
    for node in _nodes.values():
        for ids in node.entries_by_polarity.values():
            if entry_id in ids:
                return node.node_id
    return None


def sibling_for(entry_id: str, verb: str | None) -> str:
    """The entry on the same screen that matches the verb, or the entry itself.

    Retrieval ranks entries (best precision); the graph then corrects the on/off choice, so an
    "enable" step never keeps a "Disable ..." entry just because it ranked first.
    """
    node_id = node_of(entry_id)
    return (entry_for(node_id, verb) or entry_id) if node_id else entry_id


def entry_for(node_id: str, verb: str | None) -> str | None:
    """The catalog entry id on this screen that matches the step verb."""
    node = _nodes[node_id]
    order = _VERB_PREFERENCE.get((verb or "").lower(), ()) + _FALLBACK_ORDER
    for polarity in order:
        ids = node.entries_by_polarity.get(polarity)
        if ids:
            return ids[0]
    return None
