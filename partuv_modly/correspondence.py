"""Exact triangle-corner UV transfer. Never guesses by vertex index/nearest face.

PartUV may duplicate/reorder vertices and normalize geometry. The transfer is
accepted only if ALL triangles form a unique geometric bijection. A repaired
or remeshed surface that fails this test needs a different projection backend.
"""
from __future__ import annotations
from collections import defaultdict
from itertools import permutations
import math
from pathlib import Path


def read_obj(path: Path) -> dict:
    vertices, uv, faces, corner_uv = [], [], [], []
    def index(text, size):
        value = int(text)
        value = value - 1 if value > 0 else size + value
        if not 0 <= value < size:
            raise ValueError("OBJ index outside array")
        return value
    with path.open(encoding="utf-8", errors="strict") as stream:
        for line in stream:
            fields = line.split("#", 1)[0].split()
            if not fields:
                continue
            if fields[0] == "v":
                if len(fields) < 4:
                    raise ValueError("Malformed OBJ vertex")
                v = list(map(float, fields[1:4]))
                if not all(map(math.isfinite, v)):
                    raise ValueError("Non-finite OBJ coordinates")
                vertices.append(v)
            elif fields[0] == "vt":
                if len(fields) < 3:
                    raise ValueError("Malformed OBJ UV")
                t = list(map(float, fields[1:3]))
                if not all(map(math.isfinite, t)):
                    raise ValueError("Non-finite OBJ UV")
                uv.append(t)
            elif fields[0] == "f":
                if len(fields) != 4:
                    raise ValueError("Only triangulated OBJ faces are accepted")
                vf, tf = [], []
                for field in fields[1:]:
                    parts = field.split("/")
                    vf.append(index(parts[0], len(vertices)))
                    tf.append(uv[index(parts[1], len(uv))] if len(parts) > 1 and parts[1] else None)
                faces.append(vf)
                corner_uv.append(tf)
    if not vertices or not faces:
        raise ValueError("Empty OBJ mesh")
    return dict(vertices=vertices, faces=faces, corner_uv=corner_uv)


def write_obj(path: Path, vertices, faces) -> None:
    with path.open("w", encoding="utf-8") as output:
        for v in vertices:
            output.write("v " + " ".join(format(float(x), ".17g") for x in v) + "\n")
        for f in faces:
            if len(f) != 3:
                raise ValueError("Triangles required")
            output.write("f " + " ".join(str(int(x) + 1) for x in f) + "\n")


def transfer_uv(source: dict, target: dict, *, allow_partfield_normalization=True) -> dict:
    sv, sf = source["vertices"], source["faces"]
    tv, tf, tu = target["vertices"], target["faces"], target["corner_uv"]
    if not sf or len(sf) != len(tf) or len(tu) != len(tf):
        raise ValueError("[CORRESPONDENCE_FAILED] Face count changed; no safe UV-only transfer")
    if any(any(t is None or len(t) < 2 or not all(map(math.isfinite, t[:2])) for t in face) for face in tu):
        raise ValueError("Target has incomplete/non-finite corner UVs")
    lo = [min(v[i] for v in sv) for i in range(3)]
    hi = [max(v[i] for v in sv) for i in range(3)]
    extent = max(hi[i] - lo[i] for i in range(3))
    if not math.isfinite(extent) or extent <= 0:
        raise ValueError("Degenerate source bounds")
    center = [(lo[i] + hi[i]) / 2 for i in range(3)]
    tolerance = extent * 2e-6
    def point_key(v):
        if not all(map(math.isfinite, v)):
            raise ValueError("Non-finite geometry")
        return tuple(round((v[i] - center[i]) / tolerance) for i in range(3))
    def face_key(vs, f):
        keys = tuple(sorted(point_key(vs[i]) for i in f))
        if len(set(keys)) < 3:
            raise ValueError("[CORRESPONDENCE_FAILED] Degenerate or numerically indistinguishable triangle")
        return keys
    source_keys = {}
    for idx, f in enumerate(sf):
        key = face_key(sv, f)
        if key in source_keys:
            raise ValueError("[CORRESPONDENCE_AMBIGUOUS] Coincident source triangles; object/corner provenance needed")
        source_keys[key] = idx
    candidates = [("original-space", tv)]
    if allow_partfield_normalization:
        inv_scale = extent / .9
        candidates.append(("partfield-normalization-inverted", [
            [(v[i] - .5) * inv_scale + center[i] for i in range(3)] for v in tv]))
    for name, vertices in candidates:
        assigned, result, max_error = set(), [None] * len(sf), 0.0
        try:
            for target_i, face in enumerate(tf):
                source_i = source_keys[face_key(vertices, face)]
                if source_i in assigned:
                    raise KeyError("Repeated target triangle")
                source_face = sf[source_i]
                valid = []
                for order in permutations(range(3)):
                    error = max(math.dist(sv[source_face[c]], vertices[face[order[c]]]) for c in range(3))
                    if error <= tolerance * 2:
                        valid.append((order, error))
                if len(valid) != 1:
                    raise KeyError("Ambiguous corners")
                order, error = valid[0]
                max_error = max(max_error, error)
                result[source_i] = [list(tu[target_i][order[c]][:2]) for c in range(3)]
                assigned.add(source_i)
            if len(assigned) == len(sf):
                return dict(corner_uv=result, coordinate_mode=name, matched_faces=len(sf),
                            max_position_error=max_error, tolerance=tolerance)
        except (KeyError, ValueError, IndexError):
            continue
    raise ValueError("[CORRESPONDENCE_FAILED] Geometry changed or face identity is ambiguous. Original is preserved. A validated surface-projection rebaker is required for this input; images were not simply reattached.")
