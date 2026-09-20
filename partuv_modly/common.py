"""Small, dependency-free host utilities (Python 3.11/3.12)."""
from __future__ import annotations
import contextlib
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
EXTENSION_ID = "modly-partuv-extension"
STATE = ROOT / ".runtime-state.json"


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path.name}")
    return value


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


def absolute_path(value, label: str) -> Path:
    if not isinstance(value, (str, os.PathLike)) or not str(value):
        raise ValueError(f"{label} must be a nonempty absolute path")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{label} must be absolute")
    return path.resolve()


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def boolean(value) -> bool:
    if isinstance(value, bool):
        return value
    if value in ("true", "1", 1):
        return True
    if value in ("false", "0", 0):
        return False
    raise ValueError(f"Invalid boolean: {value!r}")


def number(value, low: float, high: float, *, integer=False):
    if isinstance(value, bool):
        raise ValueError("Boolean is not a numeric parameter")
    number = float(value)
    if not math.isfinite(number) or not low <= number <= high:
        raise ValueError(f"Parameter must be finite and within [{low}, {high}]")
    if integer and number != int(number):
        raise ValueError("Parameter must be an integer")
    return int(number) if integer else number


def clean_env() -> dict:
    env = os.environ.copy()
    for name in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
        env.pop(name, None)
    env.update(PYTHONUTF8="1", PYTHONNOUSERSITE="1")
    return env


@contextlib.contextmanager
def exclusive_lock(path: Path):
    """Advisory OS lock; stale lock files do not imply a live lock."""
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a+b")
    locked = False
    try:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError as exc:
            raise RuntimeError("Another setup/weight operation is running") from exc
        yield
    finally:
        if locked:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_UN)
        stream.close()


def run_logged(args, logfile: Path, *, cwd=None, env=None,
               cancelled: Callable[[], bool] = lambda: False,
               timeout: float = 14400) -> None:
    """No shell. Native output goes to a file, not Modly's JSON channel."""
    logfile.parent.mkdir(parents=True, exist_ok=True)
    with logfile.open("w", encoding="utf-8") as stream:
        process = subprocess.Popen([str(x) for x in args], cwd=cwd, env=env or clean_env(),
                                   stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT)
        started = time.monotonic()
        try:
            while process.poll() is None:
                if cancelled():
                    raise InterruptedError("Operation cancelled")
                if time.monotonic() - started > timeout:
                    raise TimeoutError(f"Worker timeout; inspect {logfile}")
                time.sleep(0.1)
            if process.returncode != 0:
                tail = logfile.read_text(encoding="utf-8", errors="replace")[-3000:]
                raise RuntimeError(f"Worker exited {process.returncode}. Log: {logfile}\n{tail}")
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
