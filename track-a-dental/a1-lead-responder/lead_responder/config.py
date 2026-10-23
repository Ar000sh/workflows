"""Settings for A1. Override with environment variables (or .env, loaded by the scripts)."""
import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
DATA_DIR = PACKAGE_DIR.parent / "data"

CLASSIFIER_MODEL = os.environ.get("CLASSIFIER_MODEL", "claude-haiku-4-5")
DRAFTER_MODEL = os.environ.get("DRAFTER_MODEL", "claude-sonnet-5-5")
DRAFTER_EFFORT = os.environ.get("DRAFTER_EFFORT", "low")
AUTO_REPLY_MODE = os.environ.get("AUTO_REPLY_MODE", "always")  # or "outside_hours"

SUPPORTED_LANGUAGES = frozenset({"de", "en"})
MAX_MESSAGE_CHARS = 5000

# Layer 4a: Postgres (host port 5433, see docker-compose.yml). The _TEST database is wiped by the tests.
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql+psycopg://leads:leads@localhost:5433/leads")
DATABASE_URL_TEST = os.environ.get("DATABASE_URL_TEST", "postgresql+psycopg://leads:leads@localhost:5433/leads_test")
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "90"))  # DSGVO: personal fields are blanked after this many days
