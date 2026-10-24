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

# Layer 4b: outgoing mail. The defaults point at Mailpit from docker-compose.yml (no TLS, no login);
# a real provider sets host, port, STARTTLS and credentials. MAIL_FROM empty = the practice email.
SMTP_HOST = os.environ.get("SMTP_HOST", "localhost")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "1025"))
SMTP_STARTTLS = os.environ.get("SMTP_STARTTLS", "false").strip().lower() in ("1", "true", "yes")
SMTP_USER = os.environ.get("SMTP_USER") or None
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD") or None
SMTP_TIMEOUT_SECONDS = float(os.environ.get("SMTP_TIMEOUT_SECONDS", "10"))
MAIL_FROM = os.environ.get("MAIL_FROM", "")

# Layer 4b: the sweep (scripts/run_sweep.py). An acknowledgment claimed longer than
# SENDING_TIMEOUT_MINUTES ago with no result is resent, at most MAX_SEND_ATTEMPTS claims in all;
# a lead older than NO_REPLY_SWEEP_MINUTES with no first reply gets the fallback acknowledgment.
SENDING_TIMEOUT_MINUTES = int(os.environ.get("SENDING_TIMEOUT_MINUTES", "10"))
MAX_SEND_ATTEMPTS = int(os.environ.get("MAX_SEND_ATTEMPTS", "3"))
NO_REPLY_SWEEP_MINUTES = int(os.environ.get("NO_REPLY_SWEEP_MINUTES", "5"))
