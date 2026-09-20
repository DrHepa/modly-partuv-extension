from __future__ import annotations
import json
from pathlib import Path
import sys
import time
import uuid
from .common import ROOT, STATE, absolute_path, read_json, write_json, digest, clean_env, run_logged, exclusive_lock
from .config import NODES, validated
from .paths import resolve_models_root, checkpoint_path
from .blender import resolve_blender, probe_blender, run_blender


def resolve_request(payload: dict):
    data = payload.get("input", {})
    if not isinstance(data, dict):
        raise ValueError("input must be an object")
    node = data.get("nodeId") or payload.get("nodeId")
    if node not in NODES:
        raise ValueError("Unknown PartUV node; expected one of: " + ", ".join(sorted(NODES)))
    params = validated(payload.get("params") or {})
    target = absolute_path(data.get("filePath"), "input.filePath")
    if not target.is_file() or target.suffix.lower() not in (".glb", ".gltf", ".obj"):
        raise ValueError("Connect an existing static GLB, glTF or OBJ mesh")
    source = absolute_path(params["source_mesh"], "source_mesh") if node == "transfer" else target
    if not source.is_file() or source.suffix.lower() not in (".glb", ".gltf", ".obj"):
        raise ValueError("Transfer requires an original static source_mesh with its original materials/UVs")
    workspace = absolute_path(payload.get("workspaceDir"), "workspaceDir")
    return node, params, source, target, workspace


def execute(payload, emit, cancelled=lambda: False):
    node, params, source, target, workspace = resolve_request(payload)
    state = read_json(STATE) if STATE.is_file() else {}
    blender = resolve_blender(params["blender_path"] or state.get("blender_path", ""))
    base = workspace / "Workflows" / "PartUV"
    if not base.resolve().is_relative_to(workspace):
        raise ValueError("Output directory escapes workspace through a symlink")
    base.mkdir(parents=True, exist_ok=True)
    work = base / (node + "-" + uuid.uuid4().hex)
    work.mkdir()
    started = time.monotonic()
    progress = lambda percent, label: emit(dict(type="progress", percent=percent, label=label))
    progress(2, "Validating local Blender and creating a non-destructive source snapshot")
    emit(dict(type="log", message=f"Artifacts and worker logs: {work}"))
    job = dict(node=node, params=params, source=str(source), target=str(target), work=str(work))
    try:
        health = probe_blender(blender, work / "health")
        run_blender(blender, {**job, "stage": "prepare"}, work / "prepare-job.json", cancelled)
        prepared = read_json(work / "prepared.json")
        if node.startswith("unwrap"):
            progress(15, "PartField hierarchy and PartUV unwrapping; original materials remain in source.blend")
            models = resolve_models_root()
            native_job = {**job, "objects": prepared["objects"], "checkpoint": str(checkpoint_path(models))}
            write_json(work / "native-job.json", native_job)
            env = clean_env()
            env.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1")
            with exclusive_lock(ROOT / ".native-inference.lock"):
                run_logged([sys.executable, "-m", "partuv_modly.native", "--job", work / "native-job.json"],
                           work / "native.log", cwd=ROOT, env=env, cancelled=cancelled)
        progress(65, "Checking triangle identity, packing free Blender UVs, and rebaking supported channels")
        run_blender(blender, {**job, "stage": "finish"}, work / "finish-job.json", cancelled)
        result = read_json(work / "blender-result.json")
        output = absolute_path(result["filePath"], "output file")
        if not output.is_relative_to(work) or not output.is_file():
            raise RuntimeError("Worker returned an invalid output file")
        manifest = dict(schema_version=1, extension="modly-partuv-extension", version="0.1.0.dev1",
            node=node, parameters=params, source=str(source), source_file_sha256=digest(source),
            source_snapshot=prepared["snapshot"], output_sha256=digest(output), blender=health,
            objects=result["objects"], elapsed_seconds=time.monotonic() - started,
            status="generated_requires_visual_acceptance", native_end_to_end_prevalidated=False)
        write_json(work / "result.partuv.json", manifest)
        progress(100, "Mesh and source snapshot saved; inspect seams, normal maps and material appearance")
        return dict(filePath=str(output))
    except BaseException as exc:
        write_json(work / "failure.json", dict(status="failed", error_type=type(exc).__name__,
                                              message=str(exc)[:4000], original_modified=False))
        raise
