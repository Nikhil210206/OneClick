"""Component 9: disruption rank + dependency topological sort (data/dependencies.json), SIIS order tiebreak.

Least disruptive first; critical actions last in the order restart < safe mode < software update <
factory reset, unless a dependency needs something after them ("uninstall in safe mode" follows the
safe mode restart).
"""

import re

from app.models import DraftAction
from app.pipeline.categorize import (
    _headline,
    _words,
    action_text,
    critical_kind,
    critical_rank,
    dependency_edges,
)

_NO_SOURCE = 10**6
_EXITS_SAFE_MODE = re.compile(
    r"\bexit(?:s|ing)? (?:the )?safe mode\b|\bleave safe mode\b|"
    r"\brestart (?:your |the )?(?:phone|device|tablet) normally\b",
    re.IGNORECASE,
)
_ENTERS_SAFE_MODE = re.compile(
    r"\btap safe mode\b|\b(?:enter|boot|restart|reboot|start)\w* (?:(?:in|into|to) )?(?:the )?safe mode\b",
    re.IGNORECASE,
)
_IN_SAFE_MODE = re.compile(r"\b(?:while |when )?in safe mode\b", re.IGNORECASE)


def _siis_position(action: DraftAction) -> int:
    ids = [int(i[1:]) for s in action.steps for i in s.src_ids if i[1:].isdigit()]
    return min(ids, default=_NO_SOURCE)


def _provides(action: DraftAction, kind: str) -> bool:
    for before, before_words, _ in dependency_edges():
        if before == kind and before_words and before_words <= _words(_headline(action)):
            return True
    return False


def order(actions: list[DraftAction]) -> list[DraftAction]:
    ranked = sorted(
        enumerate(actions),
        key=lambda pair: (
            pair[1].disruption_rank,
            critical_rank(pair[1]) if pair[1].disruption_rank == 5 else -1,
            _siis_position(pair[1]),
            pair[0],
        ),
    )
    result = [action for _, action in ranked]
    # Dependency fix-up: a dependent that sits before its prerequisite moves to just after it.
    for _ in range(len(result)):
        moved = False
        for index, action in enumerate(result):
            providers = [
                i
                for i, other in enumerate(result)
                if other is not action and any(_provides(other, k) for k in action.depends_on)
            ]
            if providers and max(providers) > index:
                target = max(providers)
                result.insert(target, result.pop(index))  # lands right after the provider
                moved = True
                break
        if not moved:
            break
    return _safe_mode_sequence(result)


def _safe_mode_sequence(actions: list[DraftAction]) -> list[DraftAction]:
    """Enter Safe mode, do what the article says to do in it, then leave it. Word-set dependencies
    cannot say this: "Check Gmail In Safe Mode" names safe mode as much as the restart into it does,
    and an "Exit Safe Mode" action is a restart, which ranks before safe mode."""
    enter = next(
        (
            n
            for n, a in enumerate(actions)
            if critical_kind(a) == "safe mode"
            and _ENTERS_SAFE_MODE.search(action_text(a))
            and not _EXITS_SAFE_MODE.search(_headline(a))
        ),
        None,
    )
    if enter is None:
        return actions
    entering = actions[enter]
    during, leaving, rest = [], [], []
    for n, action in enumerate(actions):
        if n == enter:
            continue
        text = action_text(action)
        # "Restart Device Normally" is a plain restart: leaving Safe mode names Safe mode
        if (
            _EXITS_SAFE_MODE.search(text)
            and "safe mode" in text.lower()
            and not _ENTERS_SAFE_MODE.search(text)
        ):
            leaving.append(action)
        elif _IN_SAFE_MODE.search(text) and not _ENTERS_SAFE_MODE.search(text):
            during.append(action)
        else:
            rest.append(action)
    if not during and not leaving:
        return actions
    at = sum(1 for a in actions[:enter] if a in rest)
    return [*rest[:at], entering, *during, *leaving, *rest[at:]]
