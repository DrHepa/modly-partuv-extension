"""Modly Python process protocol: one input line, exactly one terminal output."""
from __future__ import annotations
import contextlib
import json
import os
import signal
import sys
import threading
import traceback


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    output = sys.stdout
    def emit(value):
        output.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n")
        output.flush()
    cancelled = threading.Event()
    for name in ("SIGINT", "SIGTERM"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), lambda *_: cancelled.set())
    try:
        raw = sys.stdin.readline(4 * 1024 * 1024 + 1)
        if not raw or len(raw) > 4 * 1024 * 1024:
            raise ValueError("Missing or oversized Modly process input")
        payload = json.loads(raw, parse_constant=lambda x: (_ for _ in ()).throw(ValueError("Non-finite JSON number")))
        if not isinstance(payload, dict):
            raise ValueError("Modly process payload must be an object")
        os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1")
        with contextlib.redirect_stdout(sys.stderr):
            from partuv_modly.runtime import execute
            from partuv_modly.common import ROOT, exclusive_lock
            # Prevent setup from changing a venv while this job is using it.
            with exclusive_lock(ROOT / ".setup.lock"):
                result = execute(payload, emit, cancelled.is_set)
        emit(dict(type="done", result=result))
        return 0
    except BaseException as exc:
        traceback.print_exc(file=sys.stderr)
        message = f"{type(exc).__name__}: {exc}"
        for name in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
            token = os.environ.get(name)
            if token:
                message = message.replace(token, "[redacted]")
        emit(dict(type="error", message=message[:4000]))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
