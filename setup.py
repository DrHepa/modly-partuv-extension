"""Modly process setup entry. This is not a setuptools package installer."""
from __future__ import annotations
import json
import os
from pathlib import Path
import sys
import traceback
from partuv_modly.common import ROOT, STATE, absolute_path, read_json, write_json, exclusive_lock
from partuv_modly.install import probe, choose_lane, ensure_venv, install_packages, download_verified
from partuv_modly.paths import resolve_models_root, checkpoint_path


def parse_args(argv):
    if len(argv) == 2:
        value = json.loads(argv[1])
        if not isinstance(value, dict):
            raise ValueError("Modly setup argument must be a JSON object")
        return value
    if len(argv) in (4, 5):
        result = dict(python_exe=argv[1], ext_dir=argv[2], gpu_sm=int(argv[3]))
        if len(argv) == 5:
            result["cuda_version"] = int(argv[4])
        return result
    raise ValueError("Expected Modly JSON argument or <python_exe> <ext_dir> <gpu_sm> [cuda_version]")


def setup(context):
    if absolute_path(context.get("ext_dir", str(ROOT)), "ext_dir") != ROOT:
        raise ValueError("ext_dir does not match this extension")
    config_path = ROOT / "setup-config.json"
    settings = {**(read_json(config_path) if config_path.is_file() else {}), **context}
    base = absolute_path(settings.get("python_exe"), "python_exe")
    if not base.is_file():
        raise ValueError("Missing exact host Python executable")
    identity = probe(base)
    # Fail unsupported native lanes BEFORE downloading packages or checkpoints.
    lane = choose_lane(identity, settings)
    models = resolve_models_root(settings)
    print("PartField is limited to NON-COMMERCIAL RESEARCH AND EDUCATION. See THIRD_PARTY_NOTICES.md.", flush=True)
    print(f"Exact runtime: {identity}; Models: {models}", flush=True)
    os.chdir(ROOT)
    python = ensure_venv(base, identity)
    install_packages(python, lane)
    # Blender is a separate executable, never pip's bpy module.
    from partuv_modly.blender import resolve_blender, probe_blender
    blender = resolve_blender(settings.get("blender_path", ""))
    health = probe_blender(blender, ROOT / ".health")
    ckpt = read_json(ROOT / "upstream.lock.json")["checkpoint"]
    url = f"https://huggingface.co/{ckpt['repo']}/resolve/{ckpt['revision']}/{ckpt['filename']}"
    download_verified(url, checkpoint_path(models), ckpt["sha256"])
    write_json(STATE, dict(schema_version=1, extension_root=str(ROOT), models_root=str(models),
        interpreter=identity, lane=lane, blender_path=str(blender), blender_probe=health,
        checkpoint_sha256=ckpt["sha256"], setup_completed=True,
        end_to_end_inference_validated=False))
    print("Setup completed; checkpoint verified. Full-mesh inference and visual rebake acceptance still require a local test.", flush=True)


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        with exclusive_lock(ROOT / ".setup.lock"):
            setup(parse_args(sys.argv))
        return 0
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
