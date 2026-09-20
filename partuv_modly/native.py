"""Real PartUV API adapter. Run as an isolated worker; never use the demo CLI."""
from __future__ import annotations
import argparse
import math
from pathlib import Path
import random
import sys
from .common import ROOT, read_json, write_json, digest
from .config import validated, yaml_config


def health():
    import inspect
    import torch
    import torch_scatter
    import partuv
    from partuv.preprocess import preprocess, save_results, PFInferenceModel
    if not torch.cuda.is_available():
        raise RuntimeError("[CUDA_REQUIRED] Full PartField inference lane requires NVIDIA CUDA. No tested CPU fallback is declared.")
    if not callable(getattr(partuv, "pipeline_numpy", None)):
        raise RuntimeError("Missing PartUV pipeline_numpy API")
    for name in ("mesh_path", "pf_model", "sample_batch_size", "merge_vertices_epsilon"):
        if name not in inspect.signature(preprocess).parameters:
            raise RuntimeError("PartUV preprocess API changed: " + name)
    if "checkpoint_path" not in inspect.signature(PFInferenceModel).parameters:
        raise RuntimeError("PartField explicit checkpoint API changed")
    values = torch.tensor([1., 2.], device="cuda")
    indices = torch.tensor([0, 0], device="cuda")
    result = torch_scatter.scatter_mean(values, indices)
    if float(result.cpu()[0]) != 1.5:
        raise RuntimeError("torch-scatter CUDA kernel failed")
    print(f"Native imports/kernel passed; torch {torch.__version__}, CUDA {torch.version.cuda}, GPU {torch.cuda.get_device_name(0)}")
    return dict(torch=torch.__version__, cuda=torch.version.cuda,
                gpu=torch.cuda.get_device_name(0), compute_capability=list(torch.cuda.get_device_capability(0)))


def execute(job: dict):
    import torch
    import numpy as np
    import partuv
    from partuv.preprocess import preprocess, save_results, PFInferenceModel
    p = validated(job["params"])
    checkpoint = Path(job["checkpoint"])
    expected = read_json(ROOT / "upstream.lock.json")["checkpoint"]["sha256"]
    # torch.load upstream uses pickle. Only the pinned reviewed checkpoint is accepted.
    if not checkpoint.is_file() or digest(checkpoint) != expected:
        raise RuntimeError("[CHECKPOINT_MISSING_OR_CORRUPT] Run Repair; inference never downloads weights")
    device_info = health()
    random.seed(p["seed"])
    np.random.seed(p["seed"])
    torch.manual_seed(p["seed"])
    model = PFInferenceModel(checkpoint_path=str(checkpoint), device="cuda")
    work = Path(job["work"])
    config = work / "partuv-config.yaml"
    config.write_text(yaml_config(p), encoding="utf-8")
    results = []
    with torch.inference_mode():
        for item in job["objects"]:
            mesh_path = Path(item["obj_path"])
            directory = work / item["id"] / "native"
            directory.mkdir(parents=True, exist_ok=True)
            # Upstream rewrites output_path to dirname(output_path)/mesh_stem.
            preprocess_path = directory / "preprocess" / mesh_path.stem
            preprocess_path.mkdir(parents=True, exist_ok=True)
            mesh, tree_file, tree, timings = preprocess(
                str(mesh_path), model, str(preprocess_path),
                save_tree_file=True, save_processed_mesh=True,
                sample_on_faces=p["sample_on_faces"], sample_batch_size=p["sample_batch_size"],
                merge_vertices_epsilon=p["merge_epsilon"] or None)
            final, parts = partuv.pipeline_numpy(V=mesh.vertices, F=mesh.faces, tree_dict=tree,
                                                 configPath=str(config), threshold=p["threshold"])
            if int(final.num_components) <= 0:
                raise RuntimeError("PartUV returned no UV charts")
            distortion = float(final.distortion)
            if not math.isfinite(distortion):
                raise RuntimeError("PartUV returned non-finite distortion")
            save_results(str(directory), final, parts)
            output = directory / "final_components.obj"
            if not output.is_file() or output.stat().st_size == 0:
                raise RuntimeError("PartUV did not write its declared output")
            results.append(dict(id=item["id"], obj_path=str(output),
                charts=int(final.num_components), distortion=distortion,
                tree_file=str(tree_file), timings={k: float(v) for k, v in timings.items()}))
    write_json(work / "native-result.json", dict(objects=results, device=device_info))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--health", action="store_true")
    parser.add_argument("--job")
    args = parser.parse_args()
    if args.health:
        health()
    elif args.job:
        execute(read_json(Path(args.job)))
    else:
        parser.error("Pass --health or --job")


if __name__ == "__main__":
    main()
