"""Component 11 (compiler, meta): the one-sentence reason beside an answer.

The official schema carries only `contexts`, so an empty or unusual answer cannot explain itself there.
`meta.reason` is a stable code and `meta.message` the sentence a person testing the API (or the console)
reads. Built by rule, never by an LLM, and link-free like every other string in the body (hard rule 3).
"""

NO_ARTICLE = "no_article"
NO_ARTICLE_CACHED_PLAN = "no_article_cached_plan"
NO_ARTICLE_RETRIEVED = "no_article_retrieved"
ARTICLE_MISMATCH = "article_mismatch"
NO_GROUNDED_FIX = "no_grounded_fix"
UNREADABLE_QUERY = "unreadable_query"
INVALID_REQUEST = "invalid_request"
RULES_ONLY = "rules_only"

REASONS: dict[str, str] = {
    NO_ARTICLE: (
        "No support article was provided. Attach the support article for this complaint to get a "
        "troubleshooting plan."
    ),
    NO_ARTICLE_CACHED_PLAN: (
        "No support article was provided. This plan comes from a previously solved question with the "
        "same meaning."
    ),
    NO_ARTICLE_RETRIEVED: (
        "No support article was provided. This plan was built from a stored support article that matches "
        "the complaint."
    ),
    ARTICLE_MISMATCH: (
        "The support article does not match the complaint. Please provide a support article about this "
        "problem."
    ),
    NO_GROUNDED_FIX: (
        "The support article has no steps for this complaint, so no plan is returned rather than "
        "inventing one."
    ),
    UNREADABLE_QUERY: "The complaint could not be read. Please describe the problem in a sentence.",
    INVALID_REQUEST: 'The request could not be read. Send JSON with a "query" and a "siis_response".',
    RULES_ONLY: "Answered from the support article's own instructions without a language model.",
}


def message(reason: str | None) -> str | None:
    return REASONS.get(reason) if reason else None
