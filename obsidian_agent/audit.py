import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .config import AUDIT_DIR, MODEL_OUTPUT_DIR


class AuditLogger:
    _instance: Optional["AuditLogger"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
        self.audit_file = AUDIT_DIR / f"session_{self.session_id}.jsonl"
        self.model_output_file = MODEL_OUTPUT_DIR / f"session_{self.session_id}.jsonl"
        AUDIT_DIR.mkdir(parents=True, exist_ok=True)
        MODEL_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    def log_operation(
        self,
        operation: str,
        tool_name: str,
        tool_input: dict,
        tool_output: Any,
        user_prompt: str = "",
        duration_ms: float = 0,
        status: str = "success",
        error: str = "",
    ):
        entry = {
            "timestamp": datetime.now().isoformat(),
            "session_id": self.session_id,
            "operation": operation,
            "tool_name": tool_name,
            "tool_input": _sanitize(tool_input),
            "tool_output": _sanitize(tool_output) if not isinstance(tool_output, str) else tool_output[:2000],
            "user_prompt": user_prompt[:500],
            "duration_ms": round(duration_ms, 2),
            "status": status,
            "error": error[:500] if error else "",
        }
        self._write(self.audit_file, entry)

    def log_model_output(
        self,
        prompt: str,
        response: str,
        model: str = "",
        tool_calls: list | None = None,
        tokens_used: dict | None = None,
    ):
        entry = {
            "timestamp": datetime.now().isoformat(),
            "session_id": self.session_id,
            "model": model,
            "prompt": prompt[:2000],
            "response": response[:5000],
            "tool_calls": tool_calls or [],
            "tokens_used": tokens_used or {},
        }
        self._write(self.model_output_file, entry)

    def log_query(self, user_input: str, response_summary: str):
        entry = {
            "timestamp": datetime.now().isoformat(),
            "session_id": self.session_id,
            "type": "user_query",
            "user_input": user_input[:1000],
            "response_summary": response_summary[:1000],
        }
        self._write(self.audit_file, entry)

    def _write(self, path: Path, entry: dict):
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def get_session_log(self) -> list[dict]:
        entries = []
        if not self.audit_file.exists():
            return entries
        with open(self.audit_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return entries


def _sanitize(obj: Any, max_depth: int = 5) -> Any:
    if max_depth <= 0:
        return "..."
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        if isinstance(obj, str) and len(obj) > 2000:
            return obj[:2000] + "...[truncated]"
        return obj
    if isinstance(obj, dict):
        return {str(k): _sanitize(v, max_depth - 1) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(item, max_depth - 1) for item in obj[:50]]
    return str(obj)[:500]


audit_logger = AuditLogger()
