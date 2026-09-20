"""Locate the current Modly installation, never invent a weights directory."""
from __future__ import annotations
import os
from pathlib import Path
from .common import ROOT, STATE, EXTENSION_ID, absolute_path, read_json


def _one(values: dict, names: tuple[str, ...]):
    paths = {absolute_path(values[name], name) for name in names if values.get(name)}
    if len(paths) > 1:
        raise ValueError("Conflicting models_dir settings")
    return next(iter(paths), None)


def _separate(path: Path, root: Path) -> Path:
    if path == root or path.is_relative_to(root) or root.is_relative_to(path):
        raise ValueError("models_dir must be separate from extension code and venv")
    return path


def resolve_models_root(context=None, *, root=ROOT, state_file=STATE, env=None) -> Path:
    context = context or {}
    env = os.environ if env is None else env
    root = root.resolve()
    for values, names in ((context, ("models_dir", "modelsDir")),
                          (env, ("MODELS_DIR", "MODLY_MODELS_DIR"))):
        explicit = _one(values, names)
        if explicit is not None:
            return _separate(explicit, root)
    candidates = [root.parent.parent / "settings.json"]
    if env.get("MODLY_USER_DATA"):
        candidates.insert(0, Path(env["MODLY_USER_DATA"]) / "settings.json")
    config_home = Path(env.get("APPDATA", str(Path.home() / "AppData/Roaming"))) if os.name == "nt" else Path(env.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    candidates.extend(config_home / name / "settings.json" for name in ("modly", "Modly"))
    matches = set()
    for candidate in dict.fromkeys(candidates):
        if not candidate.is_file():
            continue
        try:
            settings = read_json(candidate)
            ext = absolute_path(settings.get("extensionsDir", str(candidate.parent / "extensions")), "extensionsDir")
            if (ext / EXTENSION_ID).resolve() == root:
                matches.add(absolute_path(settings.get("modelsDir", str(candidate.parent / "models")), "modelsDir"))
        except (OSError, ValueError, TypeError) as exc:
            if candidate == root.parent.parent / "settings.json":
                raise ValueError("Cannot read this Modly installation's settings.json") from exc
    if len(matches) > 1:
        raise ValueError("Ambiguous Modly installations; pass models_dir explicitly")
    if matches:
        return _separate(matches.pop(), root)
    if state_file.is_file():
        state = read_json(state_file)
        if absolute_path(state.get("extension_root"), "saved extension_root") == root:
            return _separate(absolute_path(state.get("models_root"), "saved models_root"), root)
    raise ValueError("[MODELS_DIR_MISSING] Specify Modly's real Models directory in setup-config.json or MODELS_DIR, then Repair. Nothing was downloaded.")


def checkpoint_path(models: Path) -> Path:
    path = models / EXTENSION_ID / "partfield" / "model_objaverse.ckpt"
    if not path.resolve().is_relative_to(models.resolve()):
        raise ValueError("Checkpoint location escapes models_dir")
    return path
