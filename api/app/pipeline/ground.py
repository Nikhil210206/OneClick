"""Component 6: step-level (embedding + shared content term) and action-level grounding.

A step survives only if it is close in meaning to a sentence it cites (or a clause of one: "Tap
Connections." cites "Alternatively, go to Settings, tap Connections, and then tap Wi-Fi.") **and**
shares a content term with the cited text. This is the "never invent a step" rule in code.
"""

import re

import numpy as np

from app.config import settings
from app.models import DraftAction, DraftStep, SiisSentence
from app.pipeline.text import content_terms
from app.retrieval import dense

_CLAUSE_BREAK = re.compile(r",\s*|;\s*|\s+and then\s+|\s+then\s+|:\s*", re.IGNORECASE)


def _key(text: str) -> str:
    return " ".join(re.findall(r"\w+", text.lower()))


def verbatim(step: str, cited: list[str]) -> bool:
    """The step is the article's own words: a cited sentence, or a run of whole words inside one
    ("Tap OK." from "Tap Clear data, and then tap OK."). Nothing in it can be invented, so it is
    grounded without the embedding and shared-term tests, which a two-word clause cannot pass."""
    key = _key(step)
    return bool(key) and any(f" {key} " in f" {_key(text)} " for text in cited)


def _clauses(text: str) -> list[str]:
    parts = [p.strip() for p in _CLAUSE_BREAK.split(text) if len(p.strip().split()) >= 2]
    return [text, *parts] if len(parts) > 1 else [text]


def _similarities(actions: list[DraftAction], by_id: dict[str, SiisSentence]) -> dict[tuple[int, int], float]:
    """Max cosine of each step against its cited sentences and their clauses, embedded in one batch."""
    steps: list[tuple[tuple[int, int], str, list[str]]] = []
    out: dict[tuple[int, int], float] = {}
    for ai, action in enumerate(actions):
        for si, step in enumerate(action.steps):
            cited = [by_id[i].text for i in step.src_ids if i in by_id]
            if not cited:
                continue
            if verbatim(step.text, cited):
                out[(ai, si)] = 1.0  # the article's own words: nothing to embed
                continue
            steps.append(((ai, si), step.text, [c for text in cited for c in _clauses(text)]))
    if not steps:
        return out
    candidates = sorted({c for _, _, cs in steps for c in cs})
    position = {text: n for n, text in enumerate(candidates)}
    vectors = np.asarray(dense.embed([text for _, text, _ in steps] + candidates), dtype=np.float32)
    step_vectors, candidate_vectors = vectors[: len(steps)], vectors[len(steps) :]
    for row, (key, _, cs) in enumerate(steps):
        rows = [position[c] for c in cs]
        out[key] = float(np.max(candidate_vectors[rows] @ step_vectors[row]))
    return out


def _screen_in_sections(
    action: DraftAction, cited_sections: set[str], by_id: dict[str, SiisSentence]
) -> bool:
    """Action level: the screen the action names must appear in the section(s) its steps cite."""
    if not action.screen_path:
        return True
    leaf = re.split(r">|/", action.screen_path)[-1]
    leaf_terms = content_terms(leaf)
    if not leaf_terms:
        return True
    section_text = " ".join(s.text for s in by_id.values() if s.section in cited_sections)
    return bool(leaf_terms & content_terms(section_text))


def ground_with_report(
    actions: list[DraftAction], sentences: list[SiisSentence]
) -> tuple[list[DraftAction], dict]:
    """(grounded actions, the numbers the stream's `ground` event shows, incl. coverage per intent)."""
    by_id = {s.id: s for s in sentences}
    threshold = settings.grounding_cos_threshold
    sims = _similarities(actions, by_id)
    kept_actions: list[DraftAction] = []
    dropped: list[dict] = []
    unlinked: list[str] = []
    proposed_by_intent: dict[int, int] = {}
    kept_by_intent: dict[int, int] = {}
    for ai, action in enumerate(actions):
        kept_steps: list[DraftStep] = []
        for si, step in enumerate(action.steps):
            proposed_by_intent[action.intent_index] = proposed_by_intent.get(action.intent_index, 0) + 1
            valid_ids = [i for i in step.src_ids if i in by_id]
            score = round(sims.get((ai, si), 0.0), 2)
            source_terms = (
                set().union(*(content_terms(by_id[i].text) for i in valid_ids)) if valid_ids else set()
            )
            shared = sorted(content_terms(step.text) & source_terms)
            reasons = []
            if not valid_ids:
                reasons.append("unknown_source")
            elif verbatim(step.text, [by_id[i].text for i in valid_ids]):
                pass
            else:
                if score < threshold:
                    reasons.append("below_threshold")
                if len(shared) < settings.grounding_min_shared_terms:
                    reasons.append("no_shared_term")
            if reasons:
                dropped.append(
                    {
                        "action": action.name or "",
                        "text": step.text,
                        "src_ids": step.src_ids,
                        "grounding_score": score,
                        "reason": "_and_".join(reasons),
                        "shared_terms": shared,
                    }
                )
                continue
            kept_steps.append(
                step.model_copy(update={"src_ids": valid_ids, "grounded": True, "grounding_score": score})
            )
            kept_by_intent[action.intent_index] = kept_by_intent.get(action.intent_index, 0) + 1
        if not kept_steps:
            continue
        grounded = action.model_copy(update={"steps": kept_steps})
        cited_sections = {by_id[i].section for s in kept_steps for i in s.src_ids}
        if not _screen_in_sections(grounded, cited_sections, by_id):
            unlinked.append(action.name or action.screen_path or "")
            grounded = grounded.model_copy(update={"screen_path": None})
        kept_actions.append(grounded)
    proposed = sum(proposed_by_intent.values())
    kept = sum(kept_by_intent.values())
    report = {
        "threshold": threshold,
        "proposed_steps": proposed,
        "kept_steps": kept,
        "coverage": round(kept / proposed, 2) if proposed else 0.0,
        "coverage_by_intent": {
            index: round(kept_by_intent.get(index, 0) / n, 2) for index, n in proposed_by_intent.items() if n
        },
        "dropped_steps": dropped,
        "screen_not_in_section": unlinked,
    }
    return kept_actions, report


def ground(actions: list[DraftAction], sentences: list[SiisSentence]) -> list[DraftAction]:
    kept, _ = ground_with_report(actions, sentences)
    return kept
