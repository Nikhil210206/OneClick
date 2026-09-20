"""Pre-warmed no-SIIS lookup table (built offline from the kit). SIIS cache is never pre-warmed.

Named no_siis.py rather than lookup.py: a module named `lookup` inside the package shadows
`cache.lookup()`, the function the pipeline calls.
"""


def lookup_no_siis(norm_query: str) -> dict | None:
    raise NotImplementedError
