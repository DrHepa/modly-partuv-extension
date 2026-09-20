from __future__ import annotations
from .common import boolean, number

NODES = {"unwrap_rebake", "unwrap", "repack_rebake", "transfer"}
DEFAULTS = dict(method="abf", threshold=1.25, pamo=True, abf_iters=5,
    agg_parts=10, parallel_depth=10, component_max_depth=10, threads=8,
    cuda_streams=10, pamo_face_threshold=1000, check_self_intersection=False,
    check_non_manifold=True, sample_on_faces=10, sample_batch_size=100000,
    merge_epsilon=0.0, texture_size=2048, bake_margin=16, seed=42,
    blender_path="", source_mesh="")


def validated(params: dict) -> dict:
    if not isinstance(params, dict):
        raise ValueError("params must be an object")
    if any(k in params for k in ("pack_method", "num_atlas", "uvpackmaster")):
        raise ValueError("Only free Blender single-atlas-per-object packing is supported")
    unknown = set(params) - set(DEFAULTS)
    if unknown:
        raise ValueError("Unknown parameters: " + ", ".join(sorted(unknown)))
    p = {**DEFAULTS, **params}
    if p["method"] not in ("abf", "lscm"):
        raise ValueError("method must be abf or lscm")
    for key in ("pamo", "check_self_intersection", "check_non_manifold"):
        p[key] = boolean(p[key])
    ranges = {"threshold": (1, 100), "merge_epsilon": (0, .001),
        "abf_iters": (1, 100), "agg_parts": (1, 100), "parallel_depth": (0, 30),
        "component_max_depth": (1, 30), "threads": (1, 128), "cuda_streams": (1, 64),
        "pamo_face_threshold": (1, 10000000), "sample_on_faces": (1, 100),
        "sample_batch_size": (1000, 1000000), "texture_size": (256, 8192),
        "bake_margin": (2, 128), "seed": (0, 2147483647)}
    for key, (low, high) in ranges.items():
        p[key] = number(p[key], low, high, integer=key not in ("threshold", "merge_epsilon"))
    if p["bake_margin"] * 4 >= p["texture_size"]:
        raise ValueError("Bake margin leaves too little texture area")
    for key in ("blender_path", "source_mesh"):
        if not isinstance(p[key], str):
            raise ValueError(f"{key} must be a string")
    return p


def yaml_config(p: dict) -> str:
    # Controlled scalar values only. No user-provided YAML or executable tags.
    b = lambda x: "true" if x else "false"
    return f'''verbose: false
save_stuff: false
num_cuda_streams: {p['cuda_streams']}
pipeline:
  threshold: {p['threshold']}
  parallelDepth: {p['parallel_depth']}
  component_maxDepth: {p['component_max_depth']}
  checkSelfIntersection: {b(p['check_self_intersection'])}
  checkNon2Manifold: {b(p['check_non_manifold'])}
  num_omp_threads: {p['threads']}
  num_cuda_streams: {p['cuda_streams']}
unwrap:
  method: "{p['method']}"
  abf_iters: {p['abf_iters']}
  agg_parts: {p['agg_parts']}
  pamo: {b(p['pamo'])}
  usePamoFaceThreshold: {p['pamo_face_threshold']}
'''
