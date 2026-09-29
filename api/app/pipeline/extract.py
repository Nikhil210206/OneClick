"""Component 5: Stage 2 LLM call — actions with cited steps, screen_path, verb, draft description.

Two extractors with one output shape:
  llm    call B through the router (JSON-schema output); every step cites sentence ids.
         select mode (settings.extract_mode, the free-tier default, prompt extract.v2): the model
           lists the sentence ids of each action and the intents it sees; the steps are the
           article's own sentences, split into single instructions.
         rewrite mode (extract.v1): the model writes each step and cites its sentence ids.
  rules  no LLM: instruction sentences of the relevant sections become steps citing themselves.
         Grounded by construction. Used when the LLM path fails or runs out of budget, and in CI
         (no keys), so every SIIS request still gets a non-empty, honest answer.
"""

import copy
import re
import time
from concurrent.futures import Future, ThreadPoolExecutor

from app.config import settings
from app.models import DraftAction, DraftStep, Intent, SiisSentence
from app.pipeline.text import content_terms, word_block

# ---- rules-only extractor ---------------------------------------------------------------------------
_IMPERATIVE_VERBS = word_block(
    """
    tap touch go open press hold select swipe turn check restart reboot back update clear uninstall
    reinstall remove try make ensure connect disconnect disable enable charge contact visit navigate
    launch find choose enter reset install use wait clean inspect follow drag slide adjust set
    toggle sign delete verify confirm perform keep plug unplug insert take bring request switch move
    allow close force download log add power scroll long run start stop edit change look
    shine examine access avoid wipe dry schedule call rotate pull push attach detach pair unpair
    reconnect replace repeat return send share save scan search type sync transfer view place put
    lift increase decrease reduce lower raise boot register release wake lock unlock recharge test
    point let leave give reach ask report submit restore format free empty copy rename consider
    retry review reopen relaunch unmount mount archive disconnect re-enable learn attempt
    """
)
_LEAD_IN = re.compile(
    r"^(?:first(?: of all)?|then|next|finally|also|now|alternatively|afterwards|additionally|lastly|"
    r"once done|to do (?:this|so)|if so|in that case|in this case|otherwise|meanwhile|instead|"
    r"if (?:this|that|it) (?:doesn't|does not|didn't|did not) (?:help|work)|if needed|if necessary|"
    r"please|simply|just|kindly)\b\s*,?\s*",
    re.IGNORECASE,
)
# "You can also try forcing a restart...", "you may need to check the charger...": an instruction in
# the article's polite form. The step keeps the article's words from the verb on.
_MODAL = re.compile(
    r"^(?:(?:we (?:recommend|suggest)(?: that)? you|you)\s+(?:can|could|should|may|might|will|must|"
    r"need to|have to|want to|are advised to)\s+(?:also\s+|first\s+|then\s+|still\s+)?"
    r"(?:need to\s+|want to\s+|have to\s+)?(?:also\s+)?"
    r"|it(?:'s| is) (?:crucial|important|essential|recommended|advisable|best|a good idea) (?:that you |to ))",
    re.IGNORECASE,
)
_ADVERB = re.compile(r"^[A-Za-z]+ly\s+")  # "carefully inspect", "gently wipe"
_CONDITION = re.compile(
    r"^(?:even if|even when|if|when|to|once|after|before|while|on|for|in|from|with|using|during|unless)\b",
    re.IGNORECASE,
)
# "If your screen is not rotating, please follow these steps:" introduces steps; it is not one.
_INTRODUCES_STEPS = re.compile(
    r"\bfollow (?:these|the following|the steps|the instructions)\b[^.]*:\s*$|\btry the following steps\b"
    r"|^\W*(?:please )?(?:consider|see|note|do|check|try) the following\W*$",
    re.IGNORECASE,
)
_MAX_CONDITION_COMMAS = 3  # "For fast, quality repairs you can trust and rely on, visit ..."
_HEADING_NUMBER = re.compile(r"^\s*(?:step\s*\d+\s*[:.)-]|\d+\s*[.):-])\s*", re.IGNORECASE)
_SETTINGS_ENTRY = re.compile(
    r"\b(?:go to|open|navigate to|launch|access)\s+(?:the\s+)?settings\b", re.IGNORECASE
)
_TAP_TARGET = re.compile(
    r"\b(?i:tap|touch)\s+(?i:on\s+)?(?i:the\s+)?(?P<target>[A-Z0-9][\w'’\-]*(?:\s+(?:[A-Za-z0-9][\w'’\-]*))*?)"
    r"(?=\s*(?:,|\.|;|$|\band then\b|\bthen\b|\bto\b|\bif\b|\bfrom\b|\bin\b|\bunder\b|\bon\b|\bor\b))"
)
# "tap the switch next to Touch sensitivity": the setting, not the switch, is the path's last part.
_SWITCH_TARGET = re.compile(
    r"\b(?i:switch(?:es)?|toggles?|sliders?)\s+(?i:next to|beside|for)\s+(?:the\s+)?"
    r"(?P<target>[A-Z0-9\"“][\w'’\-]*(?:\s+(?:[A-Za-z0-9][\w'’\-]*))*?)"
    r"(?=[\"”]?\s*(?:,|\.|;|$|\band then\b|\bthen\b|\bto\b|\bif\b|\bin\b|\bor\b))"
)
# Last taps that press a button rather than open a screen: not part of the screen path.
_BUTTON_WORDS = re.compile(
    r"^(?:ok|done|save|apply|confirm|clear|delete|remove|reset|restart|uninstall|force stop|turn|"
    r"power off|allow|install|update|download|back up|restore|start|stop|sign|next|continue|"
    r"yes|no)\b",
    re.IGNORECASE,
)
_ENABLE = re.compile(r"\b(?:turn on|enable|switch on|activate)\b", re.IGNORECASE)
_DISABLE = re.compile(r"\b(?:turn off|disable|switch off|deactivate)\b", re.IGNORECASE)
_RESTART = re.compile(r"\b(?:restart|reboot)\b", re.IGNORECASE)
_RESET = re.compile(r"\bfactory (?:data )?reset\b|\breset\b", re.IGNORECASE)
_VISIT = re.compile(r"\bservice cent|\bcontact\b|\bvisit\b|samsung support", re.IGNORECASE)


# "For devices with a Power button: Press and hold..." / "Wireless transfer: On the new device, tap...":
# a short label before the instruction. The step keeps it; it says which case the step is for.
_LABEL = re.compile(r"^(?!note\b)[^:.]{2,60}:\s+(?P<rest>.+)$", re.IGNORECASE)


def instruction(text: str) -> str | None:
    """The sentence as a step when it instructs the reader, else None ("Note: data is lost." is not)."""
    label = _LABEL.match(text.strip())
    if label and _instruction(label.group("rest")):
        return text.strip()
    return _instruction(text)


def _strip_lead_in(text: str) -> str:
    """ "First, please carefully inspect..." -> "carefully inspect...": connectives, politeness and the
    modal form ("You can also try...") come off; the article's words from there on stay."""
    body = text.strip()
    for _ in range(4):  # "Now, please ...", "First, you may need to ..."
        stripped = _MODAL.sub("", _LEAD_IN.sub("", body))
        if stripped == body:
            break
        body = stripped
    return body


def _imperative(text: str) -> bool:
    """True when `text` opens with an imperative verb, allowing one adverb before it."""
    first = re.match(r"[A-Za-z'\-]+", _ADVERB.sub("", text))
    return bool(first) and first.group(0).lower() in _IMPERATIVE_VERBS


# Lead-ins that say how a step relates to the ones before it: kept in the step ("Alternatively, swipe
# down..." is another way, not the next step). Every other lead-in is filler and comes off.
_MEANINGFUL_LEAD = re.compile(r"^(?:alternatively|otherwise|instead)\s*,\s*", re.IGNORECASE)


def _instruction(text: str) -> str | None:
    if _INTRODUCES_STEPS.search(text):
        return None
    body = _strip_lead_in(text)
    if body and _imperative(body):
        kept = _MEANINGFUL_LEAD.match(text.strip())
        if kept:
            return f"{kept.group(0)[0].upper()}{kept.group(0)[1:]}{body}"
        return body[0].upper() + body[1:]
    if _CONDITION.match(body):
        # The condition may hold commas of its own: try each of its first few commas as its end.
        for comma in [m.end() for m in re.finditer(r",\s*", body)][:_MAX_CONDITION_COMMAS]:
            head, rest = body[:comma], _strip_lead_in(body[comma:])
            if len(head) <= 160 and rest and _imperative(rest):
                return f"{head[0].upper()}{head[1:]}{rest}"
    return None


_CLAUSE_SPLIT = re.compile(r",\s*(?:and\s+)?(?:then\s+)?|;\s*|\s+and then\s+", re.IGNORECASE)


def sentence_steps(text: str) -> list[str]:
    """Steps from one article sentence: "Go to Settings, tap Display, and then tap Navigation bar."
    becomes three steps. Split only when every part is itself an instruction, so a conditional
    ("If X, tap Y") or a purpose clause ("To confirm..., try...") stays whole. [] when the sentence
    does not instruct at all."""
    body = instruction(text)
    if body is None:
        return []
    parts = [p.strip(" .") for p in _CLAUSE_SPLIT.split(body.rstrip(".")) if p.strip(" .")]
    if len(parts) > 1 and all(_starts_with_verb(p) for p in parts):
        return [f"{p[0].upper()}{p[1:]}." for p in parts]
    return [body]


def _starts_with_verb(text: str) -> bool:
    return _imperative(_strip_lead_in(text))


def screen_path(steps: list[str]) -> str | None:
    """`Settings > A > B` from "Go to Settings, tap A, then tap B." across an action's steps."""
    joined = " ".join(steps)
    entry = _SETTINGS_ENTRY.search(joined)
    if not entry:
        return None
    targets = []
    rest = joined[entry.end() :]
    matches = sorted([*_TAP_TARGET.finditer(rest), *_SWITCH_TARGET.finditer(rest)], key=lambda m: m.start())
    for match in matches:
        target = match.group("target").strip(" '’\"“”")
        if target and target.lower() != "settings" and target not in targets:
            targets.append(target)
    while targets and _BUTTON_WORDS.match(targets[-1]):
        targets.pop()
    return " > ".join(["Settings", *targets[:4]])


def intent_verb(steps: list[str], path: str | None) -> str | None:
    text = " ".join(steps)
    for verb, pattern in (("enable", _ENABLE), ("disable", _DISABLE)):
        if pattern.search(text) and path:
            return verb
    if _RESTART.search(text):
        return "restart"
    if _RESET.search(text):
        return "reset"
    if _VISIT.search(text) and not path:
        return "visit"
    return "open" if path else None


def heading_name(heading: str) -> str:
    return _HEADING_NUMBER.sub("", heading or "").strip(" :.-")


# Descriptions for the critical kinds a heading can name ("8. Factory Data Reset"), and for a heading
# that only names a subject ("Charger Issues"): what the reader gets, not "It will address ...".
_KIND_DESCRIPTION = (
    (re.compile(r"factory (?:data )?reset", re.IGNORECASE), "It will restore the factory settings"),
    (re.compile(r"safe mode", re.IGNORECASE), "It will start the device in safe mode"),
    (re.compile(r"software update|updates?\b", re.IGNORECASE), "It will check for the latest updates"),
    (re.compile(r"restart|reboot", re.IGNORECASE), "It will restart the device"),
    (
        re.compile(r"assistance|support|service|repair|contact", re.IGNORECASE),
        "It will get help from Samsung Support",
    ),
)


def rules_name(heading: str) -> str:
    """An action name from a section heading: "Check Email Access on a PC" as written, a subject
    ("Charger Issues") as a check ("Check Charger Issues"), a critical kind as it is."""
    name = heading_name(heading)
    words = name.split()
    if not words or words[0].lower() in _IMPERATIVE_VERBS or words[0].lower().endswith("ing"):
        return name
    if "check" in name.lower() or any(pattern.search(name) for pattern, _ in _KIND_DESCRIPTION[:4]):
        return name
    if _KIND_DESCRIPTION[4][0].search(name):
        return f"Get {name}"  # "Further Assistance"
    return f"Check {name}"


def rules_description(name: str) -> str:
    """ "It will ..." from a section heading: "Check Email Access" -> "It will check email access"."""
    words = name.split()
    if not words:
        return "It will help resolve this issue"
    first = words[0].lower()
    if first in _IMPERATIVE_VERBS:
        return f"It will {name[0].lower()}{name[1:]}"
    for pattern, description in _KIND_DESCRIPTION:
        if pattern.search(name):
            return description
    if "check" in name.lower():  # "Some things to check first"
        return "It will run these checks first"
    if first.endswith("ing"):
        return f"It will help with {name.lower()}"
    return f"It will check {name.lower()}"


def _topic(intent: Intent) -> str:
    return intent.title or " ".join(intent.text.split()[:3])


def extract_rules(
    intents: list[Intent], sentences: list[SiisSentence], sections: list[dict] | None = None
) -> tuple[list[DraftAction], list[str]]:
    """(actions, topics). One action per relevant section with at least one instruction sentence.

    When the relevant sections hold no instructions (a misspelt complaint can rank only the intro as
    relevant), the whole article is used: its instructions are still the only grounded answer.
    """
    table = sections or _sections_from_sentences(sentences, len(intents))
    best = max((max(s.get("relevance") or [0.0]) for s in table), default=0.0)
    if sections and best < settings.rules_min_relevance:
        return [], [_topic(i) for i in intents]  # the article is about something else
    relevant = [s for s in table if s.get("relevant")]
    actions = _rules_actions(intents, sentences, relevant) if relevant else []
    if not actions:
        actions = _rules_actions(intents, sentences, table)
    return _most_relevant(actions, table), [_topic(i) for i in intents]


def _most_relevant(actions: list[DraftAction], table: list[dict]) -> list[DraftAction]:
    """At most `settings.rules_max_actions` rules actions: those from the most relevant sections, in
    article order. A loosely matching article (row 7: a black screen, the Multi window guide) would
    otherwise turn every one of its sections into an action."""
    if len(actions) <= settings.rules_max_actions:
        return actions
    heading_of = {sid: row["heading"] for row in table for sid in row["sentence_ids"]}
    relevance = {row["heading"]: max(row.get("relevance") or [0.0]) for row in table}

    def score(action: DraftAction) -> float:
        heads = {heading_of.get(i) for step in action.steps for i in step.src_ids}
        return max((relevance.get(h, 0.0) for h in heads), default=0.0)

    keep = sorted(range(len(actions)), key=lambda n: (-score(actions[n]), n))[: settings.rules_max_actions]
    return [actions[n] for n in sorted(keep)]


def _rules_actions(
    intents: list[Intent], sentences: list[SiisSentence], table: list[dict]
) -> list[DraftAction]:
    by_id = {s.id: s for s in sentences}
    actions: list[DraftAction] = []
    for section in table:
        scores = section.get("relevance") or [0.0]
        intent_index = max(range(len(scores)), key=lambda i: scores[i]) if intents else 0
        steps = []
        for sid in section["sentence_ids"]:
            steps += [DraftStep(text=t, src_ids=[sid]) for t in sentence_steps(by_id[sid].text)]
        if not steps:
            continue
        texts = [s.text for s in steps]
        path = screen_path(texts)
        verb = intent_verb(texts, path)
        name = rules_name(section["heading"]) or " ".join(texts[0].split()[:4])
        actions.append(
            DraftAction(
                steps=steps,
                screen_path=path,
                intent_verb=verb,
                name=name,
                description=rules_description(name),
                intent_index=min(intent_index, max(len(intents) - 1, 0)),
            )
        )
    return actions


def _sections_from_sentences(sentences: list[SiisSentence], n_intents: int) -> list[dict]:
    table: list[dict] = []
    for s in sentences:
        if not table or table[-1]["heading"] != s.section:
            table.append(
                {"heading": s.section, "sentence_ids": [], "relevance": [s.relevance] * max(n_intents, 1)}
            )
        table[-1]["sentence_ids"].append(s.id)
    for row in table:
        row["relevant"] = True
    return table


# ---- LLM path (call B) ------------------------------------------------------------------------------
VERBS = ["open", "enable", "disable", "set", "check", "restart", "reset", "visit", "none"]
SCHEMA = {
    "type": "object",
    "properties": {
        "goals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "intent_index": {"type": "integer"},
                    "topic": {"type": "string"},
                    "actions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "description": {"type": "string"},
                                "screen_path": {"type": "string"},
                                "intent_verb": {"type": "string", "enum": VERBS},
                                "category_hint": {"type": "string", "enum": ["auto", "manual", "critical"]},
                                "steps": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "text": {"type": "string"},
                                            "src_ids": {"type": "array", "items": {"type": "string"}},
                                        },
                                        "required": ["text", "src_ids"],
                                        "additionalProperties": False,
                                    },
                                },
                            },
                            "required": [
                                "name",
                                "description",
                                "screen_path",
                                "intent_verb",
                                "category_hint",
                                "steps",
                            ],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["intent_index", "topic", "actions"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["goals"],
    "additionalProperties": False,
}
_SRC_ID = re.compile(r"^S\d+$")


def format_intents(intents: list[Intent]) -> str:
    return "\n".join(f"{n}. {i.text} (title: {i.title or '-'})" for n, i in enumerate(intents))


def format_sentences(sentences: list[SiisSentence], sections: list[dict] | None) -> str:
    """Sentences grouped under their section headings; sections off-topic for every intent are marked."""
    relevant = {s["heading"]: s.get("relevant", True) for s in sections or []}
    lines: list[str] = []
    current = None
    for sentence in sentences:
        if sentence.section != current:
            current = sentence.section
            mark = "" if relevant.get(current, True) else "  [probably unrelated to the complaint]"
            lines.append(f"\n### {current or 'Article'}{mark}")
        lines.append(f"[{sentence.id}] {sentence.text}")
    return "\n".join(lines).strip()


def actions_from_answer(
    answer: dict, intents: list[Intent], sentences: list[SiisSentence]
) -> tuple[list[DraftAction], list[str]]:
    """DraftActions from call B's JSON. Unknown sentence ids are dropped here; grounding judges the rest."""
    known = {s.id for s in sentences}
    topics = [_topic(i) for i in intents]
    actions: list[DraftAction] = []
    for goal in answer.get("goals") or []:
        index = goal.get("intent_index")
        index = index if isinstance(index, int) and 0 <= index < len(intents) else 0
        topic = " ".join(str(goal.get("topic") or "").split())
        if topic:
            topics[index] = topic
        for raw in goal.get("actions") or []:
            steps = []
            for step in raw.get("steps") or []:
                text = " ".join(str(step.get("text") or "").split())
                ids = [
                    i
                    for i in step.get("src_ids") or []
                    if isinstance(i, str) and _SRC_ID.match(i) and i in known
                ]
                if text:
                    steps.append(DraftStep(text=text, src_ids=ids))
            if not steps:
                continue
            verb = raw.get("intent_verb") if raw.get("intent_verb") in VERBS else "none"
            path = " ".join(str(raw.get("screen_path") or "").split()) or None
            actions.append(
                DraftAction(
                    steps=steps,
                    screen_path=path,
                    intent_verb=None if verb == "none" else verb,
                    name=" ".join(str(raw.get("name") or "").split()) or None,
                    description=" ".join(str(raw.get("description") or "").split()) or None,
                    intent_index=index,
                )
            )
    return actions, topics


def extract_llm(
    query: str, intents: list[Intent], sentences: list[SiisSentence], sections: list[dict] | None
) -> tuple[list[DraftAction], list[str], dict]:
    from app.llm.router import complete_json

    info: dict = {}
    variables = {
        "query": query,
        "intents": format_intents(intents),
        "sentences": format_sentences(sentences, sections),
    }
    answer = complete_json("extract", variables, SCHEMA, stage="extract", info=info)
    actions, topics = actions_from_answer(answer, intents, sentences)
    if not actions:
        raise ValueError("the extraction answer has no usable action")
    keys = ("model", "tokens_in", "tokens_out", "cost_usd", "attempts", "prompt")
    return actions, topics, {"source": "llm", **{k: info.get(k) for k in keys}}


# ---- LLM path, select mode (prompt extract.v2) --------------------------------------------------------
DOMAINS = ["Battery", "Display", "Camera", "Performance", "Other"]
SELECT_SCHEMA = {
    "type": "object",
    "properties": {
        "goals": {
            "type": "array",
            "maxItems": settings.max_intents,
            "items": {
                "type": "object",
                "properties": {
                    "problem": {"type": "string"},
                    "title": {"type": "string"},
                    "topic": {"type": "string"},
                    "domain": {"type": "string", "enum": DOMAINS},
                    "actions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "src_ids": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                                "name": {"type": "string"},
                                "description": {"type": "string"},
                                "screen_path": {"type": "string"},
                                "intent_verb": {"type": "string", "enum": VERBS},
                            },
                            "required": ["src_ids", "name", "description", "screen_path", "intent_verb"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["problem", "title", "topic", "domain", "actions"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["goals"],
    "additionalProperties": False,
}


# Short action keys on the wire. They repeat on every action, and were ~40% of an answer's characters:
# on the free tier each output token costs ~10 ms, and the touch-lag answer went from 504 to ~400
# tokens (8B: 5.5 s -> 4.4 s). The rest of the engine reads the long names (_long_keys).
_WIRE_KEYS = {"ids": "src_ids", "desc": "description", "path": "screen_path", "verb": "intent_verb"}


def select_schema(sentence_ids: list[str]) -> dict:
    """The select-mode schema for one article: SELECT_SCHEMA with the short wire keys, and src_ids
    limited to this article's own ids. Free to cite any string, both Ministral models sometimes filled
    src_ids with punctuation (":", ",") on a 100-sentence article; with the ids as an enum, constrained
    decoding cannot produce an id the article does not have."""
    schema = copy.deepcopy(SELECT_SCHEMA)
    action = schema["properties"]["goals"]["items"]["properties"]["actions"]["items"]
    short = {long: wire for wire, long in _WIRE_KEYS.items()}
    action["properties"] = {short.get(k, k): v for k, v in action["properties"].items()}
    action["required"] = [short.get(k, k) for k in action["required"]]
    action["properties"]["ids"]["items"] = {"type": "string", "enum": list(sentence_ids)}
    schema["properties"]["goals"]["items"]["properties"]["actions"]["maxItems"] = settings.extract_max_actions
    return schema


def _long_keys(answer: dict) -> dict:
    """A select-mode answer with the wire keys renamed to the names the engine uses."""
    goals = []
    for goal in answer.get("goals") or []:
        if isinstance(goal, dict):
            actions = [
                {_WIRE_KEYS.get(k, k): v for k, v in action.items()}
                for action in goal.get("actions") or []
                if isinstance(action, dict)
            ]
            goals.append({**goal, "actions": actions})
    return {**answer, "goals": goals}


def usable_selection(answer: dict, known: set[str]) -> bool:
    """The router's accept check: at least one action that cites a real sentence."""
    return any(
        any(isinstance(i, str) and i in known for i in action.get("src_ids") or [])
        for goal in answer.get("goals") or []
        for action in goal.get("actions") or []
    )


# A side note is never a step on its own ("Note: ... by following our troubleshooting guide").
_NOTE = re.compile(r"^\s*(?:note|tip|important)\b", re.IGNORECASE)
# A sentence that finishes what the one before it started: "Tap Restart again to confirm."
_CONTINUATION = re.compile(
    r"\bagain\b|\bto confirm\b|^(?:then|next|afterwards?|after that|to do (?:this|so))\b", re.IGNORECASE
)


def _with_lead_in(ids: list[str], sentences: list[SiisSentence]) -> list[str]:
    """The chosen ids, each sentence that finishes an instruction preceded by the one that starts it
    when the model left that out: "Tap Restart again to confirm." needs "Press and hold the Power
    button, then tap Restart."; "To do this, go to Settings, tap Display..." needs "If you wish to keep
    your screen protector on, you can try enabling Touch sensitivity." The added sentence is the
    article's own, in the same section."""
    index = {s.id: n for n, s in enumerate(sentences)}
    out: list[str] = []
    for sid in ids:
        at = index[sid]
        here = sentences[at]
        if at and _CONTINUATION.search(here.text):
            before = sentences[at - 1]
            fresh = before.id not in ids and before.id not in out
            if before.section == here.section and fresh and instruction(before.text) is not None:
                out.append(before.id)
        out.append(sid)
    return out


_ALTERNATIVES = re.compile(
    r"\b(?:two|three|several|a few|different|other)?\s*(?:methods?|ways?|options?)\b.*:\s*$", re.IGNORECASE
)


def _with_introduced(ids: list[str], sentences: list[SiisSentence]) -> list[str]:
    """The chosen ids, with each chosen introducing line ("To perform a factory data reset:", "There
    are two methods for this:") followed by the instructions it introduces. Models cite that line with
    only the last step under it (["To enter Safe mode:", "Touch and hold Power off, then tap Safe
    mode."]), which dropped the steps between. The instructions directly after the line, in its
    section, are added, up to the next one that begins a procedure of its own; after a line listing
    methods, every method is kept."""
    index = {s.id: n for n, s in enumerate(sentences)}
    out: list[str] = []
    for sid in ids:
        out.append(sid)
        line = sentences[index[sid]]
        if not line.text.rstrip().endswith(":") or instruction(line.text) is not None:
            continue
        methods = bool(_ALTERNATIVES.search(line.text))
        at = index[sid]
        for nxt in sentences[at + 1 : at + 1 + settings.extract_lead_lookback]:
            if nxt.section != line.section or instruction(nxt.text) is None:
                break
            if (
                nxt.id != sentences[at + 1].id
                and not methods
                and _STARTS_PROCEDURE.search(_LEAD_IN.sub("", nxt.text))
            ):
                break
            if nxt.id not in out and nxt.id not in ids:
                out.append(nxt.id)
    index_of = {sid: index[sid] for sid in out}
    # keep the model's order, but an introduced step sits right after its line, before later picks
    return sorted(dict.fromkeys(out), key=lambda i: index_of[i]) if len(out) > len(ids) else ids


def _with_alternatives(ids: list[str], sentences: list[SiisSentence]) -> list[str]:
    """The chosen ids, plus the other methods an article lists for the same step. "There are two
    methods for this:" is followed by both (hold Power and Volume down; or take the battery out for 60
    seconds); a model that chose the first alone left the second out. Only the instructions that
    directly follow in the same section are added."""
    if not ids:
        return ids
    index = {s.id: n for n, s in enumerate(sentences)}
    first, last = min(index[i] for i in ids), max(index[i] for i in ids)
    # "If there's no damage, let's force a restart. There are two methods for this:" can be chosen too
    span = sentences[max(0, first - 1) : last + 1]
    if not any(_ALTERNATIVES.search(x.text) and instruction(x.text) is None for x in span):
        return ids
    section = sentences[first].section
    extra = []
    for nxt in sentences[last + 1 : last + 1 + settings.extract_tail_lookahead]:
        if nxt.section != section or nxt.id in ids or instruction(nxt.text) is None:
            break
        extra.append(nxt.id)
    return [*ids, *extra]


# A sentence that begins a procedure of its own: a condition or a label ("If the touchscreen is
# completely unresponsive, force a restart...", "For devices with a Side button: ..."), or opening Settings.
_STARTS_PROCEDURE = re.compile(
    r"^(?:if|when|for|on|once|to)\b|^[^:.]{2,60}:\s|\b(?:go|navigate) to\b", re.IGNORECASE
)


def _with_procedure_start(
    ids: list[str], sentences: list[SiisSentence], name: str | None = None, path: str | None = None
) -> list[str]:
    """The chosen ids, plus the start of the procedure they finish. An article introduces a procedure
    with a line of its own ("To perform a factory data reset:") and lists its steps after it; when the
    model chose only the last of them (8B: "You will need to enter your Samsung account credentials"
    for Factory Data Reset), the instructions between that line and the chosen ones are added. Only
    when that line names what the action is about, and the chosen sentences do not begin a procedure
    of their own ("If the touchscreen is completely unresponsive, force a restart" follows the plain
    restart's steps in the same section)."""
    if not ids:
        return ids
    index = {s.id: n for n, s in enumerate(sentences)}
    at = min(index[i] for i in ids)
    if _STARTS_PROCEDURE.search(_LEAD_IN.sub("", sentences[at].text.strip())):
        return ids
    about = content_terms(" ".join(x for x in (name, path) if x))
    section = sentences[at].section
    run: list[str] = []
    for n in range(at - 1, max(-1, at - 1 - settings.extract_lead_lookback), -1):
        before = sentences[n]
        if before.section != section or before.id in ids:
            return ids
        if instruction(before.text) is not None:
            run.append(before.id)
            continue
        header = before.text.rstrip().endswith(":") and bool(content_terms(before.text) & about)
        return [*reversed(run), *ids] if run and header else ids
    return ids


def _with_tail(
    ids: list[str], sentences: list[SiisSentence], path: str | None, name: str | None
) -> list[str]:
    """The chosen ids, plus the instruction that finishes the action when the model stopped one short:
    8B picked "Navigate to Settings ... Tap Storage." for "Clear Gmail Cache" (path "... > Clear cache")
    and left out "Tap Clear cache.". Taken only when the next sentences of the same section are
    instructions and one names the action's own setting, which none of the chosen steps does."""
    if not ids or not path:
        return ids
    leaf = content_terms(path.rsplit(">", 1)[-1])
    if not leaf:
        return ids
    by_id = {s.id: s for s in sentences}
    if any(leaf <= content_terms(by_id[i].text) for i in ids):
        return ids
    index = {s.id: n for n, s in enumerate(sentences)}
    last = index[ids[-1]]
    extra: list[str] = []
    for nxt in sentences[last + 1 : last + 1 + settings.extract_tail_lookahead]:
        if nxt.section != sentences[last].section or nxt.id in ids or instruction(nxt.text) is None:
            break
        extra.append(nxt.id)
        if leaf <= content_terms(nxt.text):
            return [*ids, *extra]
    return ids


# When the model chose no instruction at all, only a question survives: it asks the reader to check
# something ("Did you drop your phone and smash the screen?"). A sentence that only explains ("Using
# Safe mode helps identify...", "Terms and Conditions apply.", "your device may require service") is
# never a step.
_QUESTION = re.compile(
    r"^\s*(?:did|do|does|is|are|has|have|was|were|can|could|will|would)\s+(?:you|your|the|it|this|there)\b"
    r"|\?\s*$",
    re.IGNORECASE,
)


_NAME_VERB = {"enable": "Enable", "disable": "Disable", "set": "Adjust", "check": "Check"}


def _consistent_name(
    name: str | None, steps: list[DraftStep], path: str | None, verb: str | None
) -> str | None:
    """The model's action name, unless it names something its own steps and screen never mention.

    Picking ids, a model can label an action after one sentence and cite another: "Remove Damaged
    Screen Protector" over the Touch sensitivity steps. When the action has a Settings screen, the
    name then comes from that screen ("Enable Touch sensitivity"); without one it stays, since a
    physical step ("Force Restart Device" over "Press and hold the Power button") often shares no word.
    """
    if not name or not path:
        return name
    if content_terms(name) & content_terms(" ".join([path, *(s.text for s in steps)])):
        return name
    leaf = path.rsplit(">", 1)[-1].strip()
    return f"{_NAME_VERB.get(verb or '', 'Open')} {leaf}" if leaf else name


_PURPOSE = re.compile(r"^\s*to\s+(?P<what>[^,:]{3,40})[,:]", re.IGNORECASE)


def _split_procedures(ids: list[str], sentences: list[SiisSentence]) -> list[list[str]]:
    """The chosen ids as one or more procedures. A model can hang a second procedure on the end of an
    action: "Enter Safe Mode" citing the four entering steps and, five sentences on, "To exit Safe mode,
    restart your phone normally." A later run of ids that begins with its own purpose ("To exit Safe
    mode, ...") after a gap of at least `settings.extract_split_gap` sentences becomes an action of its
    own, so the orderer can put it where it belongs."""
    index = {s.id: n for n, s in enumerate(sentences)}
    ordered = sorted(dict.fromkeys(ids), key=lambda i: index[i])
    runs: list[list[str]] = []
    for sid in ordered:
        close = bool(runs) and index[sid] - index[runs[-1][-1]] - 1 < settings.extract_split_gap
        if runs and (close or not _PURPOSE.match(sentences[index[sid]].text)):
            runs[-1].append(sid)
        else:
            runs.append([sid])
    return runs if len(runs) > 1 else [ids]  # one procedure: the model's own order stands


def _steps_of(ids: list[str], by_id: dict[str, SiisSentence]) -> list[DraftStep]:
    """Steps from the chosen sentences: their instructions, multi-instruction sentences split. When
    none of them instructs, only a question survives: it asks the reader to check something."""
    steps: list[DraftStep] = []
    for sid in dict.fromkeys(ids):
        steps += [DraftStep(text=t, src_ids=[sid]) for t in sentence_steps(by_id[sid].text)]
    if steps:
        return steps
    return [
        DraftStep(text=by_id[sid].text, src_ids=[sid])
        for sid in dict.fromkeys(ids)
        if _QUESTION.match(by_id[sid].text) and not _NOTE.match(by_id[sid].text)
    ]


def _split_at_setting(ids: list[str], sentences: list[SiisSentence], path: str | None) -> list[list[str]]:
    """The chosen ids as two actions when they are two fixes: one that never opens Settings, then one
    that reaches the action's own setting. 8B cited "If your screen protector is peeling..., remove
    it." with the Touch sensitivity steps under the path Settings > Display > Touch sensitivity; the
    judge marked those steps as belonging to another action. Split where the setting's own procedure
    starts (a condition, a label or "To do this, ..."), only when the part before it is an instruction
    that neither opens Settings nor names the setting."""
    if not path or len(ids) < 2:
        return [ids]
    leaf = content_terms(path.rsplit(">", 1)[-1])
    if not leaf:
        return [ids]
    index = {s.id: n for n, s in enumerate(sentences)}
    texts = [sentences[index[i]].text for i in ids]
    reach = next((n for n, t in enumerate(texts) if leaf <= content_terms(t)), None)
    if not reach:
        return [ids]
    cut = next(
        (
            n
            for n in range(reach, 0, -1)
            if _STARTS_PROCEDURE.search(_LEAD_IN.sub("", texts[n])) or _CONTINUATION.match(texts[n])
        ),
        None,
    )
    while cut and cut > 1 and _STARTS_PROCEDURE.search(_LEAD_IN.sub("", texts[cut - 1])):
        cut -= 1
    if not cut:
        return [ids]
    head = " ".join(texts[:cut])
    if _SETTINGS_ENTRY.search(head) or not any(instruction(t) for t in texts[:cut]):
        return [ids]
    return [ids[:cut], ids[cut:]]


def _purpose_name(text: str) -> str | None:
    """ "Exit Safe Mode" from "To exit Safe mode, restart your phone normally." """
    match = _PURPOSE.match(text)
    if not match:
        return None
    return " ".join(w[:1].upper() + w[1:] for w in match.group("what").split())


def _step_ids(action: DraftAction) -> list[str]:
    return list(dict.fromkeys(i for step in action.steps for i in step.src_ids))


def _merge_overlaps(actions: list[DraftAction], sentences: list[SiisSentence]) -> list[DraftAction]:
    """One goal's actions without repeats. Picking ids, a model can cite the same sentences twice:
    "Exit Safe Mode" over the very steps that enter it, or "Access Edge Panel" and "Edit Shortcuts"
    both opening the panel, which reads as a duplicate step. An action whose sentences all belong to
    another is dropped; two that share at least `settings.extract_merge_overlap` of the smaller one's
    sentences become one, in article order, under the first one's name. Actions that only repeat a
    sentence's wording from another part of the article (another procedure) are left alone."""
    position = {s.id: n for n, s in enumerate(sentences)}
    kept: list[DraftAction] = []
    for action in actions:
        ids = set(_step_ids(action))
        target = None
        for n, other in enumerate(kept):
            theirs = set(_step_ids(other))
            shared = ids & theirs
            if not shared:
                continue
            if ids <= theirs:
                target = -1  # nothing new in it
                break
            if theirs <= ids or len(shared) / min(len(ids), len(theirs)) >= settings.extract_merge_overlap:
                target = n
                break
        if target == -1:
            continue
        if target is None:
            kept.append(action)
            continue
        base = kept[target]
        seen = {(s.text, tuple(s.src_ids)) for s in base.steps}
        steps = [*base.steps, *(s for s in action.steps if (s.text, tuple(s.src_ids)) not in seen)]
        steps.sort(key=lambda s: min((position.get(i, 10**6) for i in s.src_ids), default=10**6))
        kept[target] = base.model_copy(
            update={"steps": steps, "screen_path": base.screen_path or action.screen_path}
        )
    return kept


def actions_from_selection(
    answer: dict, sentences: list[SiisSentence]
) -> tuple[list[DraftAction], list[str], list[Intent]]:
    """(actions, topics, intents) from a select-mode answer. Steps are the chosen sentences' own words
    (instruction sentences only, multi-instruction sentences split), each citing its sentence."""
    from app.compiler.trimmer import trim_title

    by_id = {s.id: s for s in sentences}
    actions: list[DraftAction] = []
    topics: list[str] = []
    intents: list[Intent] = []
    for goal in (answer.get("goals") or [])[: settings.max_intents]:
        goal_actions = []
        for raw in goal.get("actions") or []:
            verb = raw.get("intent_verb") if raw.get("intent_verb") in VERBS else "none"
            path = " ".join(str(raw.get("screen_path") or "").split()) or None
            name = " ".join(str(raw.get("name") or "").split()) or None
            ids = [i for i in raw.get("src_ids") or [] if isinstance(i, str) and i in by_id]
            ids = _with_introduced(list(dict.fromkeys(ids)), sentences)
            ids = _with_lead_in(ids, sentences)
            ids = _with_procedure_start(ids, sentences, name, path)
            ids = _with_tail(ids, sentences, path, name)
            ids = _with_alternatives(ids, sentences)
            description = " ".join(str(raw.get("description") or "").split()) or None
            runs: list[tuple[list[str], str | None, str | None, str, str | None]] = []
            first, *setting = _split_at_setting(ids, sentences, path)
            if setting:  # a fix of its own, then the setting's procedure (named after the setting)
                leaf = path.rsplit(">", 1)[-1].strip()
                runs.append((first, name, None, "none", description))
                runs.append((setting[0], f"{_NAME_VERB.get(verb, 'Open')} {leaf}", path, verb, None))
            else:
                runs.append((ids, name, path, verb, description))
            procedures = [
                (run, *meta) if n == 0 else (run, _purpose_name(by_id[run[0]].text), None, "none", None)
                for part, *meta in runs
                for n, run in enumerate(_split_procedures(part, sentences))
            ]
            for run, name, path, verb, description in procedures:
                if description is None and name and not path:
                    description = f"It will {name.lower()}"
                steps = _steps_of(run, by_id)
                if not steps:
                    continue
                goal_actions.append(
                    DraftAction(
                        steps=steps,
                        screen_path=path,
                        intent_verb=None if verb == "none" else verb,
                        name=_consistent_name(name, steps, path, None if verb == "none" else verb),
                        description=description,
                        intent_index=len(intents),
                    )
                )
        goal_actions = _merge_overlaps(goal_actions, sentences)
        if not goal_actions:
            continue
        problem = " ".join(str(goal.get("problem") or "").split())
        title = trim_title(str(goal.get("title") or problem))
        domain = goal.get("domain") if goal.get("domain") in DOMAINS else "Other"
        intents.append(Intent(text=problem or title, title=title, domain=domain))
        topics.append(" ".join(str(goal.get("topic") or "").split()) or title)
        actions += goal_actions
    return actions, topics, intents


def complete_procedure(
    actions: list[DraftAction], sentences: list[SiisSentence], sections: list[dict] | None
) -> tuple[list[DraftAction], list[str]]:
    """(actions, headings added): the steps of a numbered procedure the model followed but skipped.

    Most kit articles are one troubleshooting procedure in numbered sections ("Step 1: Check for
    Physical Damage", "Step 2: Force a Restart" ...). A model that follows two or more of them and
    leaves others out is the judge's most common complaint ("missing an article fix", 16 of 20 kit
    plans on 2026-09-26): 8B skipped the physical damage check of the black-screen article and the
    first three steps of the email one. Each skipped numbered section that holds an instruction and
    clears the relevance floor becomes an action of the goal that follows the procedure, built by the
    rules extractor (the article's own sentences, grounded by construction), up to
    `settings.extract_complete_max_actions` actions in that goal. Section relevance alone cannot make
    this call (skipped fixes and off-topic sections both score 0.5-0.7): the article's own numbering
    does.
    """
    if not actions or not sections:
        return actions, []
    numbered = [row for row in sections if _HEADING_NUMBER.match(row.get("heading") or "")]
    if len(numbered) < settings.extract_complete_min_sections:
        return actions, []
    cited: dict[str, int] = {}
    for action in actions:
        for step in action.steps:
            for sid in step.src_ids:
                cited.setdefault(sid, action.intent_index)
    followed = [row for row in numbered if any(i in cited for i in row["sentence_ids"])]
    if len(followed) < settings.extract_complete_min_followed:
        return actions, []
    goals = [cited[i] for row in followed for i in row["sentence_ids"] if i in cited]
    goal = max(set(goals), key=goals.count)
    room = settings.extract_complete_max_actions - sum(1 for a in actions if a.intent_index == goal)
    added: list[DraftAction] = []
    headings: list[str] = []
    for row in numbered:
        if room <= 0:
            break
        if any(i in cited for i in row["sentence_ids"]):
            continue
        if max(row.get("relevance") or [0.0]) < settings.section_relevance_floor:
            continue
        for action in _rules_actions([Intent(text="")], sentences, [row]):
            added.append(action.model_copy(update={"intent_index": goal}))
            headings.append(row["heading"])
            room -= 1
    return [*actions, *added], headings


# ---- call C, coverage: which instruction paragraphs help with this complaint ----------------------------
_coverage_pool = ThreadPoolExecutor(max_workers=settings.coverage_workers, thread_name_prefix="coverage")
_CONTEXT_CHARS = 200  # a context-only paragraph is shown this long
_UNIT_CHARS = 700  # an instruction paragraph is shown this long


def _heading_step(heading: str) -> str | None:
    """ "Update device software" from "3. Update Device Software": a numbered procedure's heading that
    is itself an instruction, in sentence case, for a section whose body gives no instruction."""
    if not _HEADING_NUMBER.match(heading or ""):
        return None
    name = heading_name(heading)
    words = name.split()
    if len(words) < 2 or words[0].lower() not in _IMPERATIVE_VERBS:
        return None
    from app.compiler.trimmer import _is_proper

    return " ".join([words[0].capitalize(), *(w if _is_proper(w) else w.lower() for w in words[1:])])


def paragraph_units(sentences: list[SiisSentence], sections: list[dict] | None) -> list[dict]:
    """The article's instruction paragraphs, numbered P1..Pn, for call C.

    [{id, heading, sentence_ids, instruction_ids, heading_step}]. A paragraph is one line of the
    article (segment.py). A numbered section whose heading is an instruction ("3. Update Device
    Software") but whose body only explains ("Keeping your device's software updated...") is one unit
    whose step is its heading, citing the section's first sentence, so grounding still checks it.
    """
    by_id = {s.id: s for s in sentences}
    units: list[dict] = []
    for row in sections or []:
        paragraphs = row.get("paragraphs") or [row.get("sentence_ids") or []]
        found = False
        for ids in paragraphs:
            ids = [i for i in ids if i in by_id]
            instructing = [i for i in ids if instruction(by_id[i].text) is not None]
            if instructing:
                found = True
                units.append(
                    {
                        "id": f"P{len(units) + 1}",
                        "heading": row.get("heading") or "",
                        "sentence_ids": ids,
                        "instruction_ids": instructing,
                        "heading_step": None,
                    }
                )
        step = None if found else _heading_step(row.get("heading") or "")
        if step and row.get("sentence_ids"):
            first = row["sentence_ids"][0]
            units.append(
                {
                    "id": f"P{len(units) + 1}",
                    "heading": row.get("heading") or "",
                    "sentence_ids": list(row["sentence_ids"]),
                    "instruction_ids": [first],
                    "heading_step": step,
                }
            )
    return units


def format_paragraphs(sentences: list[SiisSentence], sections: list[dict] | None, units: list[dict]) -> str:
    """The article for call C: headings, numbered instruction paragraphs, context lines without a number."""
    by_id = {s.id: s for s in sentences}
    unit_of = {u["sentence_ids"][0]: u for u in units if not u["heading_step"]}
    heading_units = {u["heading"]: u for u in units if u["heading_step"]}
    lines: list[str] = []
    for row in sections or []:
        lines.append(f"\n### {row.get('heading') or 'Article'}")
        if row.get("heading") in heading_units:
            unit = heading_units[row["heading"]]
            lines.append(f"[{unit['id']}] {unit['heading_step']}.")
        for ids in row.get("paragraphs") or [row.get("sentence_ids") or []]:
            ids = [i for i in ids if i in by_id]
            if not ids:
                continue
            text = " ".join(by_id[i].text for i in ids)
            unit = unit_of.get(ids[0])
            if unit is not None:
                lines.append(f"[{unit['id']}] {text[:_UNIT_CHARS]}")
            else:
                lines.append(f"- {text[:_CONTEXT_CHARS]}")
    return "\n".join(lines).strip()


def coverage_schema(unit_ids: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "fixes": {
                "type": "array",
                "maxItems": settings.coverage_max_fixes,
                "items": {
                    "type": "object",
                    "properties": {"p": {"type": "string", "enum": unit_ids}, "name": {"type": "string"}},
                    "required": ["p", "name"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["fixes"],
        "additionalProperties": False,
    }


def _coverage_llm(query: str, article: str, unit_ids: list[str]) -> tuple[list[dict], dict]:
    from app.llm.router import complete_json

    info: dict = {}
    started = time.perf_counter()
    answer = complete_json(
        "coverage",
        {"query": query, "paragraphs": article},
        coverage_schema(unit_ids),
        stage="coverage",
        info=info,
    )
    known = set(unit_ids)
    fixes = [
        {"p": f["p"], "name": " ".join(str(f.get("name") or "").split())}
        for f in answer.get("fixes") or []
        if isinstance(f, dict) and f.get("p") in known
    ]
    keys = ("model", "tokens_in", "tokens_out", "cost_usd", "attempts", "prompt")
    return fixes, {k: info.get(k) for k in keys} | {"ms": round((time.perf_counter() - started) * 1000, 1)}


def start_coverage(
    query: str, sentences: list[SiisSentence], sections: list[dict] | None
) -> tuple[Future | None, list[dict]]:
    """(call C running in the background, the paragraph units it chooses from); no call when there is
    nothing to choose or no model is configured."""
    from app.llm.router import available

    units = paragraph_units(sentences, sections)
    if not units or not available():
        return None, units
    article = format_paragraphs(sentences, sections, units)
    return _coverage_pool.submit(_coverage_llm, query, article, [u["id"] for u in units]), units


def finish_coverage(future: Future | None, started: float) -> tuple[list[dict] | None, dict]:
    """(chosen [{p, name}], info), waiting as settings.coverage_wait_s / coverage_grace_s allow; None
    when call C did not answer in time or failed (the caller falls back to complete_procedure)."""
    if future is None:
        return None, {}
    now = time.perf_counter()
    until = min(
        started + settings.extract_budget_s,
        max(now + settings.coverage_grace_s, started + settings.coverage_wait_s),
    )
    try:
        fixes, info = future.result(timeout=max(0.0, until - now))
    except Exception as exc:  # noqa: BLE001 - TimeoutError or LLMError: coverage is an extra
        return None, {"error": type(exc).__name__}
    return fixes, info


_NAME_WORDS = (2, 6)
# "Here's how to check the LDI:" / "To clear the app's data:" names the paragraph that follows it.
_INTRO_NAME = re.compile(r"^(?:here(?:['’]s| is) how to|to)\s+(?P<what>[^:]{3,40}):\s*$", re.IGNORECASE)


def _title(text: str) -> str:
    return " ".join(w[:1].upper() + w[1:] for w in text.split())


def _group_name(
    group: list[dict],
    names: dict[str, str],
    by_id: dict[str, SiisSentence],
    units: list[dict],
    steps: list[DraftStep],
    taken: set[str] = frozenset(),
) -> str:
    """A name for added paragraphs: call C's for the first of them; else the line that introduces
    them ("Here's how to check the LDI:" -> "Check The LDI"); else the section heading, when they
    are the whole section and it reads as a name; else the first words of the first step."""
    for unit in group:
        words = (names.get(unit["id"]) or "").split()
        if _NAME_WORDS[0] <= len(words) <= _NAME_WORDS[1]:
            return " ".join(words)
    order = [s for s in by_id]  # article order
    first = group[0]["sentence_ids"][0]
    at = order.index(first)
    if at and by_id[order[at - 1]].section == group[0]["heading"]:
        intro = _INTRO_NAME.match(by_id[order[at - 1]].text.strip())
        if intro:
            return _title(intro.group("what"))
    from_heading = rules_name(group[0]["heading"])
    step_text = " ".join(st.text for st in steps)
    fits = _NAME_WORDS[0] <= len(from_heading.split()) <= _NAME_WORDS[1] + 1
    # the heading names a critical step the paragraph does not perform ("6. Perform a Factory Data
    # Reset" over "Back up your data before proceeding"), or another action already has that name
    wrong_kind = _kind(from_heading) is not None and _kind(step_text) != _kind(from_heading)
    if fits and not wrong_kind and not _name_taken(from_heading, taken):
        return from_heading
    return _imperative_name(steps[0].text) if steps else from_heading


def _name_taken(name: str, taken: set[str]) -> bool:
    mine = content_terms(name)
    return any(mine and len(mine & content_terms(t)) / len(mine | content_terms(t)) >= 0.6 for t in taken)


def _imperative_name(step: str) -> str:
    """ "Back Up Your Personal Data" from "Before proceeding, back up your personal data to avoid
    losing it.": the instruction's verb phrase, up to 5 words, without its condition."""
    body = _strip_lead_in(step)
    if not _imperative(body):
        for comma in [m.end() for m in re.finditer(r",\s*", body)][:_MAX_CONDITION_COMMAS]:
            rest = _strip_lead_in(body[comma:])
            if _imperative(rest):
                body = rest
                break
    words = []
    for word in re.findall(r"[\w'-]+|[,.;:]", body):
        stops = {"to", "by", "and", "for", "so", "until", "if", "when", "before", "after", "while", "with"}
        if word in ",.;:" or (words and word.lower() in stops):
            break
        words.append(word)
    return _title(" ".join(words[:5]))


# Where a paragraph's continuation stops: an escalation ("If the problem persists, perform a factory data
# reset") or a step of another critical kind ("To exit Safe mode, restart ...") is an action of its own.
_ESCALATION = re.compile(
    r"^(?:if (?:the|this|that|your) (?:problem|issue)s? (?:persists?|continues?|remains?)|if (?:this|that|it) "
    r"(?:doesn't|does not|didn't|did not))",
    re.IGNORECASE,
)


_EXIT_SAFE_MODE = re.compile(r"\bexit(?:s|ing)? (?:the )?safe mode\b|\bleave safe mode\b", re.IGNORECASE)


def _kind(text: str) -> str | None:
    from app.pipeline.categorize import CRITICAL_KINDS

    found = None
    for kind, pattern in CRITICAL_KINDS:
        if pattern.search(text):
            found = kind
    return found


def _complete_partial(
    actions: list[DraftAction], by_id: dict[str, SiisSentence], units: list[dict], cited: set[str]
) -> list[DraftAction]:
    """A paragraph call C chose that call B took only the start of is continued in the action that took
    it: row 3's "Use A USB Mouse" had the mouse and keyboard but not the next sentence, "You can also
    back up your device's data by connecting it to a monitor." Only the instructions after the part
    call B took, up to an escalation or a step of another critical kind."""
    out = list(actions)
    for unit in units:
        ids = unit["instruction_ids"]
        taken = [n for n, i in enumerate(ids) if i in cited]
        if unit["heading_step"] or not taken or taken != list(range(len(taken))) or len(taken) == len(ids):
            continue  # nothing taken, a gap before the taken part, or nothing left
        holder = next(
            (n for n, a in enumerate(out) if any(ids[taken[-1]] in st.src_ids for st in a.steps)), None
        )
        if holder is None:
            continue
        head_kind = _kind(" ".join([out[holder].name or "", *(st.text for st in out[holder].steps)]))
        extra: list[DraftStep] = []
        for sid in ids[len(taken) :]:
            text = by_id[sid].text
            if (
                sid in cited
                or _ESCALATION.search(_LEAD_IN.sub("", text))
                or _EXIT_SAFE_MODE.search(text)
                or _kind(text) not in (None, head_kind)
                or _PURPOSE.match(text)  # "To clean your device, gently wipe ..." is a fix of its own
                or _LABEL.match(text)
            ):
                break
            extra += [DraftStep(text=t, src_ids=[sid]) for t in sentence_steps(text)]
        if extra:
            out[holder] = out[holder].model_copy(update={"steps": [*out[holder].steps, *extra]})
            cited.update(i for st in extra for i in st.src_ids)
    return out


def add_paragraphs(
    actions: list[DraftAction],
    sentences: list[SiisSentence],
    units: list[dict],
    chosen: list[str],
    names: dict[str, str] | None = None,
) -> tuple[list[DraftAction], list[str]]:
    """(actions, names added): each chosen paragraph unit that call B left out entirely becomes part of
    a new action of the article's own instructions (a paragraph call B took part of stays as it
    chose). Chosen paragraphs next to each other in one section are one action. It joins the goal that
    already cites its section, else the goal with the most actions, up to
    settings.extract_complete_max_actions actions per goal."""
    names = names or {}
    by_id = {s.id: s for s in sentences}
    unit_of = {u["id"]: u for u in units}
    position = {u["id"]: n for n, u in enumerate(units)}
    cited: set[str] = {sid for a in actions for st in a.steps for sid in st.src_ids}
    goal_of_section: dict[str, int] = {}
    counts: dict[int, int] = {}
    for action in actions:
        counts[action.intent_index] = counts.get(action.intent_index, 0) + 1
        for step in action.steps:
            for sid in step.src_ids:
                if sid in by_id:
                    goal_of_section.setdefault(by_id[sid].section, action.intent_index)
    default_goal = max(counts, key=lambda g: (counts[g], -g)) if counts else 0
    actions = _complete_partial(actions, by_id, [unit_of[u] for u in names if u in unit_of], cited)
    cited = {sid for a in actions for st in a.steps for sid in st.src_ids}
    seen_steps = {_text_key(st.text) for a in actions for st in a.steps}

    def repeats(unit: dict) -> bool:  # the article says it twice (row 9's repair visit): nothing new
        if unit["heading_step"]:
            return _text_key(unit["heading_step"]) in seen_steps
        texts = [t for sid in unit["instruction_ids"] for t in sentence_steps(by_id[sid].text)]
        return bool(texts) and all(_text_key(t) in seen_steps for t in texts)

    picked = sorted(
        {
            u
            for u in chosen
            if u in unit_of
            and not any(i in cited for i in unit_of[u]["sentence_ids"])
            and not repeats(unit_of[u])
        },
        key=position.get,
    )
    groups: list[list[dict]] = []
    for uid in picked:
        unit = unit_of[uid]
        last = groups[-1][-1] if groups else None
        if last and last["heading"] == unit["heading"] and position[uid] == position[last["id"]] + 1:
            groups[-1].append(unit)
        else:
            groups.append([unit])
    added: list[DraftAction] = []
    added_names: list[str] = []
    taken = {(a.name or "").lower() for a in actions}
    for group in groups:
        steps: list[DraftStep] = []
        for unit in group:
            if unit["heading_step"]:
                steps.append(DraftStep(text=f"{unit['heading_step']}.", src_ids=unit["instruction_ids"][:1]))
            else:
                steps += [
                    DraftStep(text=text, src_ids=[sid])
                    for sid in unit["instruction_ids"]
                    for text in sentence_steps(by_id[sid].text)
                ]
        if not steps or all(_text_key(st.text) in seen_steps for st in steps):
            continue  # nothing new: the article says the same thing in two places (row 9's repair visit)
        holder = _gap_holder(group, [*actions, *added], by_id)
        if holder is None:
            holder = _preparation_holder(group, steps, [*actions, *added], by_id)
        if holder is not None:  # the steps between an action's own, or the preparation of a reset
            target = [*actions, *added][holder]
            merged = sorted([*target.steps, *steps], key=lambda st: position_of(st, by_id))
            if holder < len(actions):
                actions = [
                    *actions[:holder],
                    target.model_copy(update={"steps": merged}),
                    *actions[holder + 1 :],
                ]
            else:
                added[holder - len(actions)] = target.model_copy(update={"steps": merged})
            seen_steps.update(_text_key(st.text) for st in steps)
            continue
        goal = goal_of_section.get(group[0]["heading"], default_goal)
        if counts.get(goal, 0) >= settings.extract_complete_max_actions:
            continue
        name = _group_name(group, names, by_id, units, steps, taken)
        texts = [s.text for s in steps]
        path = screen_path(texts)
        added.append(
            DraftAction(
                steps=steps,
                screen_path=path,
                intent_verb=intent_verb(texts, path),
                name=name,
                description=f"It will {name[0].lower()}{name[1:]}",
                intent_index=goal,
            )
        )
        added_names.append(name)
        taken.add(name.lower())
        seen_steps.update(_text_key(st.text) for st in steps)
        counts[goal] = counts.get(goal, 0) + 1
    return [*actions, *added], added_names


def position_of(step: DraftStep, by_id: dict[str, SiisSentence]) -> int:
    order = {sid: n for n, sid in enumerate(by_id)}
    return min((order.get(i, 10**6) for i in step.src_ids), default=10**6)


def _gap_holder(group: list[dict], actions: list[DraftAction], by_id: dict[str, SiisSentence]) -> int | None:
    """The action whose own sentences surround these paragraphs in one section: 8B took the start and
    the end of the factory reset ("Navigate to and open Settings", "Tap Delete all") and left out the
    steps between; they belong in that action, not in one of their own."""
    order = {sid: n for n, sid in enumerate(by_id)}
    ids = [i for u in group for i in u["sentence_ids"]]
    first, last = min(order[i] for i in ids), max(order[i] for i in ids)
    heading = group[0]["heading"]
    for n, action in enumerate(actions):
        mine = [
            order[i] for st in action.steps for i in st.src_ids if i in by_id and by_id[i].section == heading
        ]
        if mine and min(mine) < first and max(mine) > last:
            return n
    return None


def _preparation_holder(
    group: list[dict], steps: list[DraftStep], actions: list[DraftAction], by_id: dict[str, SiisSentence]
) -> int | None:
    """The action a paragraph prepares, when it is one: the paragraph sits in a step headed by a critical
    action ("6. Perform a Factory Data Reset") without performing it, and an action of the plan already
    performs it from the same section."""
    heading = group[0]["heading"]
    wanted = _kind(heading_name(heading))
    if wanted is None or _kind(" ".join(st.text for st in steps)) == wanted:
        return None
    for n, action in enumerate(actions):
        in_section = any(by_id[i].section == heading for st in action.steps for i in st.src_ids if i in by_id)
        if in_section and _kind(" ".join([action.name or "", *(st.text for st in action.steps)])) == wanted:
            return n
    return None


def _text_key(text: str) -> str:
    return " ".join(re.findall(r"\w+", text.lower()))


# A numbered step every device problem can use, whatever the article's topic: the checks, restarts,
# updates, resets and support a support agent always offers. A step about the article's own feature
# ("2. Adjust Screen Orientation Settings" on a distorted-screen complaint) needs call C's support.
_GENERAL_FIX = re.compile(
    r"physical damage|liquid|moisture|charg|restart|reboot|safe mode|software update|update (?:your |the )?"
    r"(?:device )?software|factory|\breset\b|contact|support|service|assistance|back ?up",
    re.IGNORECASE,
)


def general_step_paragraphs(units: list[dict], sections: list[dict] | None) -> list[str]:
    """The paragraphs of an article's numbered general-fix steps (_GENERAL_FIX), in an article written
    as a numbered procedure. call C leaves some out ("Step 1: Check for Physical Damage" for a blank
    screen while searching a stock price); the judge counts each as a missing fix."""
    numbered = [r for r in sections or [] if _HEADING_NUMBER.match(r.get("heading") or "")]
    if len(numbered) < settings.extract_complete_min_sections:
        return []
    general = {r["heading"] for r in numbered if _GENERAL_FIX.search(heading_name(r["heading"]))}
    return [u["id"] for u in units if u["heading"] in general]


def followed_step_paragraphs(
    actions: list[DraftAction], units: list[dict], sections: list[dict] | None, also: list[str] = ()
) -> list[str]:
    """The instruction paragraphs of the numbered steps the plan follows (call B cites them, or `also`,
    the paragraphs about to be added, lies in them): in "Step 1: Check for Physical Damage", the
    Liquid Damage Indicator check after the inspection (8B's coverage and 14B's extraction both skip
    it at times). Only in an article written as a numbered procedure the plan follows
    (complete_procedure's test)."""
    numbered = [r for r in sections or [] if _HEADING_NUMBER.match(r.get("heading") or "")]
    if len(numbered) < settings.extract_complete_min_sections:
        return []
    unit_heading = {u["id"]: u["heading"] for u in units}
    cited = {sid for a in actions for st in a.steps for sid in st.src_ids}
    followed = {r["heading"] for r in numbered if any(i in cited for i in r["sentence_ids"])}
    followed |= {unit_heading[u] for u in also if u in unit_heading} & {r["heading"] for r in numbered}
    if len(followed) < settings.extract_complete_min_followed:
        return []
    return [u["id"] for u in units if u["heading"] in followed and not u["heading_step"]]


def merge_unstated_goals(
    query: str,
    actions: list[DraftAction],
    topics: list[str],
    intents: list[Intent],
    sentences: list[SiisSentence] = (),
) -> tuple[list[DraftAction], list[str], list[Intent], list[str]]:
    """(actions, topics, intents, merged titles): goals the complaint does not state folded into the
    goal that matches it best. 14B split "the screen flashes and goes blank when I open an email in
    Gmail" into three problems, two of them the article's sections (email account settings, Wi-Fi), and
    ranked the customer's own problem last; 8B has stated one problem twice. A goal stays when it is
    close in meaning to the complaint or uses its words (row 19's "lines on the display": 0.63 but two
    of its three words); merged when it is a near copy of another goal or neither."""
    if len(intents) < 2:
        return actions, topics, intents, []
    import numpy as np

    from app.retrieval import dense

    vectors = np.asarray(dense.embed([query, *(i.text for i in intents)]), dtype=np.float32)
    to_query = vectors[1:] @ vectors[0]
    best = int(np.argmax(to_query))
    words = content_terms(query)
    target: dict[int, int] = {}
    for n, intent in enumerate(intents):
        if n == best:
            continue
        mine = content_terms(intent.text)
        overlap = len(mine & words) / len(mine) if mine else 0.0
        duplicate = float(vectors[1 + n] @ vectors[1 + best]) >= settings.goal_duplicate_similarity
        unstated = to_query[n] < settings.goal_stated_similarity and overlap < settings.goal_stated_overlap
        if duplicate or unstated:
            target[n] = best
    if not target:
        return actions, topics, intents, []
    keep = [n for n in range(len(intents)) if n not in target]
    new_index = {old: new for new, old in enumerate(keep)}
    moved = [
        a.model_copy(update={"intent_index": new_index[target.get(a.intent_index, a.intent_index)]})
        for a in actions
    ]
    # a folded goal usually repeats the actions of the one it joins (8B stated one problem twice)
    merged: list[DraftAction] = []
    for goal in range(len(keep)):
        merged += _merge_overlaps([a for a in moved if a.intent_index == goal], sentences)
    return merged, [topics[n] for n in keep], [intents[n] for n in keep], [intents[n].title for n in target]


def extract_select_llm(
    query: str, sentences: list[SiisSentence], sections: list[dict] | None
) -> tuple[list[DraftAction], list[str], dict]:
    from app.llm.router import LLMError, complete_json

    known = {s.id for s in sentences}
    info: dict = {}
    variables = {
        "query": query,
        "sentences": format_sentences(sentences, sections),
        "max_actions": settings.extract_max_actions,
    }
    empty: list[bool] = []  # per turned-down answer: True when it selected nothing at all

    def accept(answer: dict) -> bool:
        answer = _long_keys(answer)
        if usable_selection(answer, known):
            return True
        empty.append(not any(goal.get("actions") for goal in answer.get("goals") or []))
        return False

    started = time.perf_counter()
    coverage, units = start_coverage(query, sentences, sections)
    try:
        schema = select_schema([s.id for s in sentences])
        answer = complete_json("extract", variables, schema, stage="extract", info=info, accept=accept)
    except LLMError as exc:
        if len(empty) >= settings.extract_empty_votes and all(empty):
            # Every model that answered says the article does not address the complaint: that is the
            # answer (no_match), not a failure to fall back from.
            return [], [], {"source": "llm", "mode": "select", "no_match": True, "attempts": exc.attempts}
        raise
    actions, topics, intents = actions_from_selection(_long_keys(answer), sentences)
    if not actions:
        raise ValueError("the extraction answer has no usable action")
    actions, topics, intents, merged_goals = merge_unstated_goals(query, actions, topics, intents, sentences)
    fixes, coverage_info = finish_coverage(coverage, started)
    if fixes is not None:
        # call C's paragraphs and the numbered procedure's general fixes, then whatever else the numbered
        # steps now followed hold (the LDI check under "Step 1" once the inspection is in)
        names = {f["p"]: f["name"] for f in fixes}
        chosen = [f["p"] for f in fixes] + general_step_paragraphs(units, sections)
        chosen += followed_step_paragraphs(actions, units, sections, chosen)
        actions, completed = add_paragraphs(actions, sentences, units, chosen, names)
        coverage_info = {**coverage_info, "chosen": fixes, "added": completed}
    else:
        actions, completed = complete_procedure(actions, sentences, sections)
        actions, more = add_paragraphs(
            actions, sentences, units, followed_step_paragraphs(actions, units, sections)
        )
        completed += more
    keys = ("model", "tokens_in", "tokens_out", "cost_usd", "attempts", "prompt")
    detail = {
        "source": "llm",
        "mode": "select",
        "intents": [i.model_dump(mode="json") for i in intents],
        "completed_sections": completed,
        "merged_goals": merged_goals,
        "coverage": coverage_info,
    }
    return actions, topics, detail | {k: info.get(k) for k in keys}


# ---- entry point ------------------------------------------------------------------------------------
def extract_with_topics(
    intents: list[Intent],
    sentences: list[SiisSentence],
    *,
    sections: list[dict] | None = None,
    query: str | None = None,
) -> tuple[list[DraftAction], list[str], dict]:
    """(actions, topics, info); info says which extractor ran: {source, model, tokens_in, tokens_out, ...}.

    LLM first when a key is set; rules-only when it fails, returns nothing usable, or no key is set.
    Select mode also returns the intents it found in `info["intents"]` (the caller adopts them).
    """
    from app.llm.router import available

    rules_info = {"source": "rules", "model": None, "tokens_in": 0, "tokens_out": 0}
    if available() and sentences:
        text = query or (intents[0].text if intents else "")
        try:
            if settings.extract_mode == "select":
                return extract_select_llm(text, sentences, sections)
            return extract_llm(text, intents, sentences, sections)
        except Exception as exc:  # noqa: BLE001 - LLMError or a malformed answer: degrade to rules
            actions, topics = extract_rules(intents, sentences, sections)
            rules_info["degraded"] = f"{type(exc).__name__}: {str(exc)[:200]}"
            rules_info["attempts"] = getattr(exc, "attempts", [])
            return actions, topics, rules_info
    actions, topics = extract_rules(intents, sentences, sections)
    return actions, topics, rules_info


def extract(intents: list[Intent], sentences: list[SiisSentence]) -> list[DraftAction]:
    actions, _, _ = extract_with_topics(intents, sentences)
    return actions
