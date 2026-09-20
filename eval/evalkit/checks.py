"""Rule checks for one ContextDeeplinkResponse and one results.jsonl line.

Sources: FAQ Theme 2 Q6, Q7, Q9, Q10, Q15, Q16 and the Theme 2 spec section 4.

Independent of the engine on purpose: nothing here imports app.compiler or app.pipeline, so a bug in
the engine's own scrub or trimmer shows up as a failure here instead of being checked by itself.
Only the official app/schema.py is shared, because that is what the organisers validate against.
"""

from __future__ import annotations

import functools
import json
import math
import re
from collections.abc import Iterator
from dataclasses import asdict, dataclass

from evalkit.paths import DEPENDENCIES_PATH, use_api_package
from evalkit.stats import norm_query, tokens

FAIL = "fail"
WARN = "warn"


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str  # fail | warn
    path: str  # e.g. contexts[0].actions[1].description
    message: str

    @property
    def block(self) -> str:
        return CODE_BLOCK.get(self.code, "A1")

    def to_dict(self) -> dict:
        return {**asdict(self), "block": self.block}


# Which scorer block each finding belongs to. A1 findings are further grouped into A1_RULES.
CODE_BLOCK = {
    "SCHEMA": "G4",
    "URL_LEAK": "G5",
    "AUTO_NO_LINK": "A2",
    "AUTO_PARTIAL_LINK": "A2",
    "DL_UNKNOWN": "A2",
    "DL_ALTERED": "A2",
    "VAL_UNKNOWN": "A2",
    "VAL_NOT_OWN": "A2",
    "VAL_ALTERED": "A2",
    "VAL_WITHOUT_CATALOG_ACT": "A2",
    "DUMMY_TEXT": "A2",
    "DUMMY_WITH_VALIDATION": "A2",
    "VAR_COUNT": "A5",
    "VAR_UNIQUE": "A5",
    "VAR_EMPTY": "A5",
    "VAR_IS_QUERY": "A5",
}

# A1 is "schema validity, format rules (goal regex, title word count, description rule),
# no URL leaks, score in range" (FAQ Q10). Each finding code maps to one of these rules.
A1_RULES = {
    "schema": {"SCHEMA"},
    "goal": {"GOAL_FORMAT", "GOAL_PERIOD"},
    "title": {"TITLE_WORDS"},
    "description": {"DESC_PREFIX", "DESC_WORDS"},
    "url": {"URL_LEAK"},
    "score": {"SCORE_RANGE"},
}

GOAL_STRICT = re.compile(
    r"^Follow these steps to perform this (?P<topic>.+?) (?P<kind>Troubleshooting|Configuration)\.$"
)
GOAL_LENIENT = re.compile(
    r"^Follow these steps to perform this (?P<topic>.+?) (?P<kind>Troubleshooting|Configuration)\.?$"
)

URL_PATTERNS = {
    "scheme": re.compile(r"\b[a-z][a-z0-9+.\-]{1,20}://", re.IGNORECASE),
    "www": re.compile(r"\bwww\.", re.IGNORECASE),
    "domain": re.compile(
        r"\b[a-z0-9][a-z0-9\-]*(?:\.[a-z0-9\-]+)*\.(?:com|net|org|gov|edu|info|io|ly|html?|php|aspx?|jsp)\b",
        re.IGNORECASE,
    ),
    "email": re.compile(r"[a-z0-9._%+\-]+@[a-z0-9\-]+(?:\.[a-z0-9\-]+)+", re.IGNORECASE),
    "markdown_link": re.compile(r"\[[^\]\n]*\]\([^)\n]*\)"),
    "markdown_image": re.compile(r"!\["),
    "html_link": re.compile(r"<\s*(?:a|img|link|iframe)\b|\bhref\s*=|\bsrc\s*=", re.IGNORECASE),
}
_CATALOG_URI = re.compile(r"^bixby://[A-Za-z0-9_./\-]+$")
_DEEPLINK_FIELD = re.compile(r"\.(?:actionableDeeplink|validationDeeplink)\.deeplink$")

_STEP_NUMBERING = re.compile(r"^\s*(?:\d+\s*[.):-]|step\s*\d+\s*[:.)-]?|[-*•])\s*", re.IGNORECASE)
_TITLE_SMALL_WORDS = {"a", "an", "the", "and", "or", "of", "to", "in", "on", "for", "with", "at", "by", "via"}
# Words that stay capitalised inside a sentence-case title.
_PROPER_NOUNS = {
    "galaxy", "samsung", "wi-fi", "wifi", "bluetooth", "android", "one", "ui", "smart", "switch", "view",
    "members", "cloud", "google", "gmail", "flip", "fold", "secure", "folder", "edge", "dex", "pen", "bixby",
    "quick", "share", "tv", "sim", "usb", "nfc", "gps", "sd", "os", "ai",
}  # fmt: skip


def f(code: str, severity: str, path: str, message: str) -> Finding:
    return Finding(code=code, severity=severity, path=path, message=message)


def iter_strings(obj: object, path: str = "") -> Iterator[tuple[str, str]]:
    """Every string value in a JSON-like object, with its dotted path."""
    if isinstance(obj, str):
        yield path, obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from iter_strings(v, f"{path}.{k}" if path else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from iter_strings(v, f"{path}[{i}]")


def url_leaks(text: str) -> list[str]:
    """Names of the URL patterns found in one string."""
    return [name for name, rx in URL_PATTERNS.items() if rx.search(text)]


def check_url_leaks(obj: object, root: str = "") -> list[Finding]:
    """G5: zero URLs anywhere. Catalog-style bixby URIs are allowed only inside deeplink fields."""
    out = []
    for path, text in iter_strings(obj, root):
        if _DEEPLINK_FIELD.search(path) and _CATALOG_URI.match(text):
            continue
        hits = url_leaks(text)
        if hits:
            snippet = text if len(text) <= 80 else text[:77] + "..."
            out.append(f("URL_LEAK", FAIL, path, f"{', '.join(hits)} in {snippet!r}"))
    return out


def check_schema(body: object) -> list[Finding]:
    use_api_package()
    from app.schema import ContextDeeplinkResponse
    from pydantic import ValidationError

    if not isinstance(body, dict):
        return [f("SCHEMA", FAIL, "", f"body is {type(body).__name__}, expected an object")]
    try:
        ContextDeeplinkResponse.model_validate(body)
    except ValidationError as e:
        first = e.errors()[0]
        loc = ".".join(str(p) for p in first["loc"])
        return [f("SCHEMA", FAIL, loc, f"{first['msg']} ({e.error_count()} error(s))")]
    return []


def _words(text: str) -> list[str]:
    return text.split()


def check_goal_text(goal: object, path: str, goal_mode: str = "strict") -> list[Finding]:
    if not isinstance(goal, str):
        return [f("GOAL_FORMAT", FAIL, path, "goal is not a string")]
    m = GOAL_LENIENT.match(goal)
    if not m:
        return [
            f(
                "GOAL_FORMAT",
                FAIL,
                path,
                f"does not match 'Follow these steps to perform this <Name> Troubleshooting.': {goal!r}",
            )
        ]
    out = []
    if not GOAL_STRICT.match(goal):
        sev = FAIL if goal_mode == "strict" else WARN
        out.append(
            f(
                "GOAL_PERIOD",
                sev,
                path,
                "missing the trailing period of the FAQ Q6 form "
                "(sample_output.json omits it; open question with organisers)",
            )
        )
    if re.search(r"\b(Troubleshooting|Configuration)$", m.group("topic")):
        out.append(f("GOAL_TOPIC_DUP", WARN, path, "topic already ends in Troubleshooting/Configuration"))
    return out


def check_title(title: object, path: str) -> list[Finding]:
    if not isinstance(title, str) or not title.strip():
        return [f("TITLE_WORDS", FAIL, path, "title is empty")]
    words = _words(title)
    out = []
    if not 2 <= len(words) <= 3:
        out.append(f("TITLE_WORDS", FAIL, path, f"{len(words)} words, need 2-3: {title!r}"))
    if not words[0][:1].isupper():
        out.append(f("TITLE_CASE", WARN, path, f"not sentence case (first word lowercase): {title!r}"))
    for w in words[1:]:
        bare = w.strip(".,:;()").lower()
        if w[:1].isupper() and not w.isupper() and bare not in _PROPER_NOUNS:
            out.append(f("TITLE_CASE", WARN, path, f"not sentence case ({w!r} capitalised): {title!r}"))
            break
    return out


def check_description(desc: object, path: str) -> list[Finding]:
    if not isinstance(desc, str):
        return [f("DESC_PREFIX", FAIL, path, "description is not a string")]
    out = []
    if not desc.startswith("It will "):
        out.append(f("DESC_PREFIX", FAIL, path, f"must start with 'It will': {desc!r}"))
    n = len(_words(desc))
    if not 5 <= n <= 7:
        out.append(f("DESC_WORDS", FAIL, path, f"{n} words incl. 'It will', need 5-7: {desc!r}"))
    return out


def check_action_name(name: object, path: str) -> list[Finding]:
    if not isinstance(name, str) or not name.strip():
        return [f("ACTION_NAME_EMPTY", FAIL, path, "actionName is empty")]
    for i, w in enumerate(_words(name)):
        if (i == 0 or w.lower() not in _TITLE_SMALL_WORDS) and w[:1].isalpha() and not w[:1].isupper():
            return [f("ACTION_NAME_CASE", WARN, path, f"not Title Case ({w!r}): {name!r}")]
    return []


def check_steps(groups: object, path: str) -> list[Finding]:
    if not isinstance(groups, list) or not groups:
        return [f("STEPS_EMPTY", FAIL, path, "stepGroups is empty")]
    out = []
    for gi, group in enumerate(groups):
        gpath = f"{path}[{gi}].steps"
        steps = group.get("steps") if isinstance(group, dict) else None
        if not isinstance(steps, list) or not steps:
            out.append(f("STEPS_EMPTY", FAIL, gpath, "steps list is empty"))
            continue
        for si, step in enumerate(steps):
            spath = f"{gpath}[{si}]"
            if not isinstance(step, str) or not step.strip():
                out.append(f("STEPS_EMPTY", FAIL, spath, "blank step"))
                continue
            if _STEP_NUMBERING.match(step):
                out.append(f("STEP_NUMBERED", WARN, spath, f"leading numbering or bullet: {step!r}"))
            if not step.rstrip().endswith("."):
                out.append(f("STEP_PERIOD", WARN, spath, f"no trailing period: {step!r}"))
    return out


def check_score(score: object, path: str) -> list[Finding]:
    ok = isinstance(score, (int, float)) and not isinstance(score, bool) and math.isfinite(score)
    if not ok or not 0.0 <= score <= 1.0:
        return [f("SCORE_RANGE", FAIL, path, f"score must be a float in 0-1, got {score!r}")]
    return []


def _has_link(group: object) -> bool:
    return isinstance(group, dict) and isinstance(group.get("actionableDeeplink"), dict)


_DEP_STOPWORDS = {"in", "the", "a", "an", "of", "to"}


@functools.lru_cache(maxsize=1)
def _dependency_edges() -> tuple[tuple[frozenset, frozenset], ...]:
    """(before_words, after_words) from data/dependencies.json, e.g. safe mode -> uninstall in safe mode.

    Word sets rather than a literal phrase: real action text ("Uninstall the email app while in Safe
    mode") shares words with the edge ("uninstall in safe mode") without repeating it verbatim.
    """
    try:
        data = json.loads(DEPENDENCIES_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return ()
    return tuple(
        (tokens(e["before"]) - _DEP_STOPWORDS, tokens(e["after"]) - _DEP_STOPWORDS)
        for e in data.get("edges", [])
    )


def _action_text(action: dict) -> str:
    parts = [str(action.get("actionName", ""))]
    for group in action.get("stepGroups") or []:
        if isinstance(group, dict):
            parts += [s for s in group.get("steps") or [] if isinstance(s, str)]
    return " ".join(parts)


def check_categories(actions: list, path: str) -> list[Finding]:
    """auto needs a link (FAQ Q7); manual carries none (spec 4.1); critical goes last unless a step
    depends on it, per data/dependencies.json (design doc: "Critical actions stay last unless a
    dependency requires otherwise" -- e.g. safe mode restart before uninstalling in safe mode).
    """
    out = []
    critical_words: list[frozenset] = []
    for ai, action in enumerate(actions):
        if not isinstance(action, dict):
            continue
        apath = f"{path}[{ai}]"
        cat = action.get("category") or "manual"
        groups = action.get("stepGroups") if isinstance(action.get("stepGroups"), list) else []
        linked = sum(_has_link(g) for g in groups)
        if cat == "auto" and linked == 0:
            out.append(f("AUTO_NO_LINK", FAIL, apath, "auto action without an actionableDeeplink"))
        elif cat == "auto" and linked < len(groups):
            out.append(f("AUTO_PARTIAL_LINK", WARN, apath, f"only {linked}/{len(groups)} stepGroups linked"))
        if cat == "manual" and linked:
            out.append(f("MANUAL_HAS_LINK", WARN, apath, "manual action carries a deeplink (spec 4.1)"))
        if cat == "critical":
            critical_words.append(tokens(_action_text(action)))
        elif critical_words:
            words = tokens(_action_text(action))
            expected = any(
                after <= words and any(before <= cw for cw in critical_words)
                for before, after in _dependency_edges()
                if before and after
            )
            if not expected:
                out.append(f("CRITICAL_NOT_LAST", WARN, apath, f"{cat} action after a critical one"))
    return out


def check_response(body: object, goal_mode: str = "strict") -> list[Finding]:
    """Every per-response rule except catalog validity (see evalkit.catalog)."""
    out = check_schema(body)
    out += check_url_leaks(body)
    if not isinstance(body, dict) or not isinstance(body.get("contexts", []), list):
        return out
    seen_actions: dict[str, str] = {}
    for gi, goal in enumerate(body.get("contexts", [])):
        gpath = f"contexts[{gi}]"
        if not isinstance(goal, dict):
            continue
        out += check_goal_text(goal.get("goal"), f"{gpath}.goal", goal_mode)
        out += check_title(goal.get("title"), f"{gpath}.title")
        out += check_score(goal.get("score"), f"{gpath}.score")
        actions = goal.get("actions") if isinstance(goal.get("actions"), list) else []
        if not actions:
            out.append(f("GOAL_NO_ACTIONS", WARN, f"{gpath}.actions", "goal has no actions"))
        names_here: set[str] = set()
        for ai, action in enumerate(actions):
            if not isinstance(action, dict):
                continue
            apath = f"{gpath}.actions[{ai}]"
            name = action.get("actionName")
            out += check_action_name(name, f"{apath}.actionName")
            out += check_description(action.get("description"), f"{apath}.description")
            out += check_steps(action.get("stepGroups"), f"{apath}.stepGroups")
            key = name.strip().lower() if isinstance(name, str) else ""
            if key and key in names_here:
                out.append(f("ACTION_NAME_DUP", WARN, f"{apath}.actionName", f"duplicate in goal: {name!r}"))
            elif key and key in seen_actions:
                out.append(
                    f(
                        "ACTION_REPEATED",
                        WARN,
                        f"{apath}.actionName",
                        f"also in {seen_actions[key]}; multi-intent should keep it once",
                    )
                )
            names_here.add(key)
            seen_actions.setdefault(key, gpath)
        out += check_categories(actions, f"{gpath}.actions")
    return out


def check_results_line(line: object) -> list[Finding]:
    """Line-level rules for results.jsonl (FAQ Q16, Q17): query, 8-10 unique variations, response present."""
    if not isinstance(line, dict):
        return [f("SCHEMA", FAIL, "", "line is not a JSON object")]
    out = []
    query = line.get("query")
    if not isinstance(query, str) or not query.strip():
        out.append(f("QUERY_MISSING", FAIL, "query", "missing query"))
        query = ""
    if not isinstance(line.get("response"), dict):
        out.append(f("SCHEMA", FAIL, "response", "missing response object"))
    variations = line.get("query_variations")
    if not isinstance(variations, list):
        return out + [f("VAR_COUNT", FAIL, "query_variations", "missing query_variations list")]
    texts = [v for v in variations if isinstance(v, str) and v.strip()]
    if len(texts) != len(variations):
        out.append(f("VAR_EMPTY", FAIL, "query_variations", "blank or non-string variation"))
    keys = [norm_query(v) for v in texts]
    unique = set(keys)
    if not (8 <= len(unique) <= 10 and 8 <= len(variations) <= 10):
        out.append(
            f(
                "VAR_COUNT",
                FAIL,
                "query_variations",
                f"{len(variations)} given, {len(unique)} unique; need 8-10",
            )
        )
    if len(unique) != len(keys):
        out.append(f("VAR_UNIQUE", FAIL, "query_variations", f"{len(keys) - len(unique)} duplicate(s)"))
    if query and norm_query(query) in unique:
        out.append(f("VAR_IS_QUERY", WARN, "query_variations", "a variation repeats the original query"))
    out += check_url_leaks(variations, "query_variations")
    return out


def a1_rule_pass(findings: list[Finding]) -> dict[str, bool]:
    """Per A1 rule: True when no fail-severity finding of that rule is present."""
    failed = {x.code for x in findings if x.severity == FAIL}
    return {rule: not (codes & failed) for rule, codes in A1_RULES.items()}
