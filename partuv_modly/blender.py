"""Blender runs in its own Python process, not inside the Modly venv."""
from __future__ import annotations
import os
from pathlib import Path
import re
import shutil
from .common import ROOT, absolute_path, read_json, write_json, run_logged


def resolve_blender(explicit="", *, env=None) -> Path:
    env = os.environ if env is None else env
    chosen = explicit or env.get("MODLY_PARTUV_BLENDER", "")
    if chosen:
        path = absolute_path(chosen, "blender_path")
        if not path.is_file():
            raise ValueError("Configured Blender executable does not exist")
        return path
    path = shutil.which("blender", path=env.get("PATH"))
    if path:
        return Path(path).resolve()
    if os.name == "nt":
        candidates = []
        for key in ("PROGRAMFILES", "PROGRAMFILES(X86)"):
            if env.get(key):
                candidates.extend((Path(env[key]) / "Blender Foundation").glob("Blender */blender.exe"))
        def version(path):
            return tuple(map(int, re.findall(r"\d+", path.parent.name)))
        if candidates:
            return sorted(candidates, key=version, reverse=True)[0].resolve()
    raise RuntimeError("[BLENDER_MISSING] Configure blender_path or MODLY_PARTUV_BLENDER with the user's Blender executable. bpy/BlenderProc are not installed.")


def command(executable: Path, job: Path) -> list[str]:
    return [str(executable), "--background", "--factory-startup", "--disable-autoexec",
            "--python-exit-code", "1", "--python", str(ROOT / "tools" / "blender_worker.py"),
            "--", str(job)]


def run_blender(executable: Path, job: dict, path: Path, cancelled=lambda: False):
    write_json(path, job)
    run_logged(command(executable, path), path.with_suffix(".log"), cancelled=cancelled)


def probe_blender(executable: Path, directory: Path) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    result = directory / "blender-probe-result.json"
    result.unlink(missing_ok=True)
    run_blender(executable, dict(stage="probe", result_path=str(result)), directory / "blender-probe-job.json")
    if not result.is_file():
        raise RuntimeError("Blender exited without a feature-probe result")
    probe = read_json(result)
    if not probe.get("ok"):
        raise RuntimeError("Blender feature probe failed")
    return probe
