"""Deeplink validity against data/kit/deeplinks.json (FAQ Q5, Q8, Q15; repo hard rule 8, verbatim copy).

A catalog link is valid only when its URI is in the catalog and its validation object is that entry's
own, copied unchanged. On/off entries share one validation URI but only some carry
condition/value, so the comparison is per entry, never per URI.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from evalkit.checks import FAIL, WARN, Finding, f
from evalkit.paths import CATALOG_PATH

DUMMY = "bixby://dummy_positive"
_VALIDATION_FIELDS = ("key", "resultType", "condition", "value")


@dataclass
class Catalog:
    by_act: dict[str, dict]
    by_id: dict[str, dict]
    val_uris: set[str]

    @classmethod
    def load(cls, path: Path = CATALOG_PATH) -> Catalog:
        entries = json.loads(path.read_text())["deeplinks"]
        return cls(
            by_act={e["deeplink"]: e for e in entries},
            by_id={e["id"]: e for e in entries},
            val_uris={
                e["validation"]["deeplink"] for e in entries if (e.get("validation") or {}).get("deeplink")
            },
        )


@dataclass
class LinkStats:
    act_total: int = 0
    act_valid: int = 0
    act_catalog: int = 0
    act_dummy: int = 0
    val_total: int = 0
    val_valid: int = 0
    auto_total: int = 0
    auto_linked: int = 0
    entry_ids: list[str] = field(default_factory=list)

    def add(self, other: LinkStats) -> None:
        for name in (
            "act_total",
            "act_valid",
            "act_catalog",
            "act_dummy",
            "val_total",
            "val_valid",
            "auto_total",
            "auto_linked",
        ):
            setattr(self, name, getattr(self, name) + getattr(other, name))
        self.entry_ids += other.entry_ids

    @property
    def validity(self) -> float | None:
        total = self.act_total + self.val_total
        return (self.act_valid + self.val_valid) / total if total else None

    @property
    def auto_link_rate(self) -> float | None:
        return self.auto_linked / self.auto_total if self.auto_total else None

    @property
    def dummy_rate(self) -> float | None:
        return self.act_dummy / self.act_total if self.act_total else None


def _word_count(text: object) -> int:
    return len(text.split()) if isinstance(text, str) else 0


def check_deeplinks(body: object, catalog: Catalog) -> tuple[list[Finding], LinkStats]:
    out: list[Finding] = []
    stats = LinkStats()
    if not isinstance(body, dict) or not isinstance(body.get("contexts"), list):
        return out, stats
    for gi, goal in enumerate(body["contexts"]):
        actions = goal.get("actions") if isinstance(goal, dict) else None
        for ai, action in enumerate(actions if isinstance(actions, list) else []):
            if not isinstance(action, dict):
                continue
            groups = action.get("stepGroups") if isinstance(action.get("stepGroups"), list) else []
            if action.get("category") == "auto":
                stats.auto_total += 1
                stats.auto_linked += any(isinstance(g, dict) and g.get("actionableDeeplink") for g in groups)
            for si, group in enumerate(groups):
                if isinstance(group, dict):
                    _check_group(group, f"contexts[{gi}].actions[{ai}].stepGroups[{si}]", catalog, out, stats)
    return out, stats


def _check_group(group: dict, path: str, catalog: Catalog, out: list[Finding], stats: LinkStats) -> None:
    act = group.get("actionableDeeplink")
    val = group.get("validationDeeplink")
    entry = None

    if isinstance(act, dict):
        stats.act_total += 1
        uri = act.get("deeplink")
        apath = f"{path}.actionableDeeplink"
        if uri == DUMMY:
            stats.act_valid += 1
            stats.act_dummy += 1
            for name in ("description", "message"):
                n = _word_count(act.get(name))
                if not 5 <= n <= 7:
                    out.append(
                        f(
                            "DUMMY_TEXT",
                            WARN,
                            f"{apath}.{name}",
                            f"dummy_positive {name} has {n} words; FAQ Q15 wants 5-7 naming the screen",
                        )
                    )
            if isinstance(val, dict):
                out.append(
                    f(
                        "DUMMY_WITH_VALIDATION",
                        WARN,
                        f"{path}.validationDeeplink",
                        "dummy_positive link carries a validation object",
                    )
                )
        elif uri in catalog.by_act:
            entry = catalog.by_act[uri]
            stats.act_valid += 1
            stats.act_catalog += 1
            stats.entry_ids.append(entry["id"])
            for name in ("description", "message", "originalType"):
                expected = entry.get(name)
                got = act.get(name, "" if name == "message" else None)
                if got != expected:
                    out.append(
                        f(
                            "DL_ALTERED",
                            WARN,
                            f"{apath}.{name}",
                            f"{entry['id']} {name} not copied verbatim: {got!r} != {expected!r}",
                        )
                    )
        else:
            hint = " (that is a validation URI)" if uri in catalog.val_uris else ""
            out.append(f("DL_UNKNOWN", FAIL, f"{apath}.deeplink", f"not in catalog{hint}: {uri!r}"))

    if isinstance(val, dict):
        stats.val_total += 1
        vuri = val.get("deeplink")
        vpath = f"{path}.validationDeeplink"
        if vuri not in catalog.val_uris:
            out.append(
                f("VAL_UNKNOWN", FAIL, f"{vpath}.deeplink", f"validation URI not in catalog: {vuri!r}")
            )
        elif entry is None:
            stats.val_valid += 1
            if not (isinstance(act, dict) and act.get("deeplink") == DUMMY):
                out.append(
                    f(
                        "VAL_WITHOUT_CATALOG_ACT",
                        WARN,
                        vpath,
                        "validation without a catalog actionable link to own it",
                    )
                )
        else:
            own = entry.get("validation") or {}
            if own.get("deeplink") != vuri:
                out.append(
                    f(
                        "VAL_NOT_OWN",
                        FAIL,
                        f"{vpath}.deeplink",
                        f"belongs to another entry, {entry['id']} owns {own.get('deeplink')!r}",
                    )
                )
                return
            diffs = [n for n in _VALIDATION_FIELDS if val.get(n) != own.get(n)]
            if diffs:
                detail = ", ".join(f"{n}: {val.get(n)!r} != {own.get(n)!r}" for n in diffs)
                out.append(f("VAL_ALTERED", FAIL, vpath, f"{entry['id']} validation changed: {detail}"))
            else:
                stats.val_valid += 1


__all__ = ["DUMMY", "FAIL", "Catalog", "LinkStats", "check_deeplinks"]
