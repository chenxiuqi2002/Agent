import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent.parent
load_dotenv(BASE_DIR / ".env")

VAULT_PATH = Path(os.environ.get("VAULT_PATH", str(BASE_DIR / "vault"))).resolve()
ATTACHMENTS_PATH = VAULT_PATH / "attachments"
LOG_DIR = BASE_DIR / "logs"
AUDIT_DIR = LOG_DIR / "audit"
MODEL_OUTPUT_DIR = LOG_DIR / "model_outputs"
KNOWLEDGE_GRAPH_DIR = VAULT_PATH / ".knowledge_graph"

ALLOWED_EXTENSIONS = {".md", ".markdown", ".txt", ".canvas"}
IMPORT_EXTENSIONS = {".pdf", ".docx", ".doc", ".txt", ".rtf"}

SANDBOX_ENABLED = os.environ.get("SANDBOX_ENABLED", "true").lower() == "true"
SANDBOX_ALLOWED_PATHS = [str(VAULT_PATH)]
SANDBOX_BLOCKED_PATTERNS = [
    "rm -rf", "del /", "format", "shutdown", "rmdir /s",
    "sudo", "chmod", "chown", "> /dev", "mkfs",
]

WEB_SEARCH_ENABLED = os.environ.get("WEB_SEARCH_ENABLED", "true").lower() == "true"

ANTHROPIC_BASE_URL = os.environ.get("ANTHROPIC_BASE_URL", "")
ANTHROPIC_AUTH_TOKEN = os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
MODEL = os.environ.get("MODEL", "")
