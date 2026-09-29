"""Test isolation: the pipeline writes solved plans to SQLite, and tests must never warm the dev cache.

The API loads `cache.sqlite` at boot, so a test run that wrote into `api/cache.sqlite` would make the
next local gate-replica run start warm (its first calls would not be cold). Point every test at a
throwaway file, set before any test module imports the app.
"""

import os
import tempfile
from pathlib import Path

from app.config import settings

settings.sqlite_path = str(Path(tempfile.mkdtemp(prefix="oneclick-tests-")) / "cache.sqlite")

# Tests run the rules-only path and never spend LLM quota, whatever the developer's .env holds.
# An empty variable counts as "no key", and load_dotenv(override=False) leaves it empty. Every
# provider's key is blanked, so a key added for a new provider cannot leak in either.
os.environ["GEMINI_API_KEY"] = ""
for _spec in settings.llm_providers.values():
    os.environ[_spec["key_env"]] = ""
