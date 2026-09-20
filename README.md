# PartUV for Modly — development preview

**Status: initial implementation, NOT a completed cross-platform release.**

The Python adapter, setup provisioner, free Blender worker and contract tests are
implemented. Full PartUV/CUDA inference and Blender texture fidelity were **not
executed in the authoring environment**. Native Windows and Linux ARM64 builds
are not supplied. Do not publish this preview as a tested Windows extension.

## Scope and upstream baseline

This is a `type: process` extension, author **DrHepa**, for the process protocol
in Modly **0.4.2**, main commit
`1476fd0b1c19c9ab177c1ca3ee4d1842119e9f65`. That upstream's bundled Python is
**3.11.9**. Setup uses the exact host interpreter passed by Modly; private
**Python 3.12** installations get a separate ABI-compatible venv. It does not
pick another Python from PATH.

PartUV's published package is pinned to **0.1.2**. The source repository's
`pyproject.toml` says `0.1.2.2`, which is NOT the reviewed PyPI release. The
checkpoint and the Linux cp311/cp312 wheels have independently recorded SHA256
hashes in `upstream.lock.json`.

### License: no paid plugin does not mean unrestricted commercial use

PartUV's project code is Apache-2.0, but its vendored PartField implementation
has the NVIDIA restriction to **non-commercial research and educational use**.
This wrapper does not remove that restriction. Other dependencies retain their
own licenses. See `THIRD_PARTY_NOTICES.md` and the linked upstream license.
No UVPackMaster purchase, license activation, SDK or add-on is installed or
invoked. BlenderProc and the `bpy` pip package are not dependencies.

## Nodes

| Node | Input → output | What it does |
| --- | --- | --- |
| PartUV · Unwrap + Rebake | mesh → mesh | Snapshot original, PartField hierarchy, actual PartUV unwrap, free Blender pack, strict UV correspondence, PBR-subset rebake, GLB export. |
| PartUV · Unwrap (untextured) | mesh → mesh | Same unwrap/packing but deliberately outputs a neutral untextured material. The original material snapshot remains alongside the result. |
| PartUV · Repack + Rebake | mesh → mesh | Repack existing UV islands using Blender and rebake. Does not run PartField or change seam layout using PartUV. |
| PartUV · Transfer Textures | mesh → mesh | Input is the already re-UVed target; `source_mesh` is the original textured mesh. Transfers target UVs to the preserved source surface and rebakes. One source/target object in this standalone node. |

Modly's reviewed process API has one `filePath`, not an arbitrary two-mesh input
bundle. Therefore the standalone transfer node uses an explicit original-source
path parameter. The main node does not require any additional extraction node.
A future richer host mesh-input contract could replace that parameter.

**Free packing is not equivalent to the paid demonstrations:** this wrapper
packs a single tile per original mesh object, using Blender's own packer. It
does not claim UVPackMaster's semantic part-group grouping or automatic
hierarchy-based multi-atlas assignment. Multiple independent objects may each
have their own tile/material; this is not that paid multi-atlas algorithm.

## Why saving texture images alone is insufficient

A texture's meaning depends on its UV coordinates, material slots, texture
transforms and shader channels. After UV unwrapping, reconnecting the same image
can scramble the appearance. The worker saves a packed `source.blend` before
PartUV preprocessing, and uses it to evaluate the original material on the new
UV layout. Tangent-space normals are **rebaked**, not copied as RGB pixels.
Old implicit image/normal UV references are made explicit before new UVs become
active. The user's source file is never overwritten.

The current safety policy is **strict geometric correspondence**, not nearest
face projection. It tolerates duplicated/reordered seam vertices and reordered
faces/corners. It tests original coordinates and the inverse of PartField's
known normalization, then requires a complete unique triangle bijection. It
keeps the source's static triangulated surface and applies only the new UVs.
If repair changes the surface or coincident triangles make the mapping
ambiguous, the job fails with `CORRESPONDENCE_FAILED` or
`CORRESPONDENCE_AMBIGUOUS`. It does not silently substitute the wrong surface.
A general cage/ray-projection rebaker for changed topology is **not implemented**.

## Blender: use the user's installation

Set `blender_path` to an existing Blender executable, for example the user's
Blender **5.2**, including a portable installation. The selection order is:
explicit node/setup path, `MODLY_PARTUV_BLENDER`, PATH, then detected Windows
Blender Foundation installations.

Blender is invoked with `--background --factory-startup --disable-autoexec
--python-exit-code 1`. Its bundled Python is independent of Modly's 3.11/3.12
venv. No add-ons, preferences or user startup file are modified.

The adapter requires APIs available from Blender 4.2 onward. Setup and each run
perform a small **UV pack, Cycles emission bake and GLB export** feature probe.
This is not a certification of all Blender 5.2 behavior. In particular,
normal-map fidelity and imported/exported material semantics still need real
fixtures and visual acceptance. This author's environment had no Blender.

## Install / Repair

Place the repository in Modly's actual extension directory, preserving the
root `manifest.json`, `setup.py`, and `processor.py`. Use Modly's normal setup
entry once the local test prerequisites are met. There is no published GitHub
repository or ready-to-install registry entry in this delivery.

`setup.py` accepts the reviewed legacy positional shape and a JSON context:

```text
<host-python> setup.py <absolute-host-python> <absolute-extension-dir> <gpu-sm> [cuda-version]
```

Example of a **configuration shape**, not a machine-specific path:

```json
{
  "models_dir": "/absolute/path/from/Modly/Settings/Storage/Models",
  "blender_path": "/absolute/path/to/blender"
}
```

Save that object as `setup-config.json` in the extension only when automatic
storage discovery or executable discovery is insufficient. Windows paths use
JSON escaping, e.g. `C:\\Program Files\\Blender Foundation\\Blender 5.2\\blender.exe`.
The host's setup context overrides this local config. An explicit `MODELS_DIR`
or `MODLY_MODELS_DIR` is also supported. Automatic settings discovery must match
this extension to the current installation's `extensionsDir`; it does not pick
an unrelated Modly installation just because a settings file exists.

The exact setup sequence is native-lane preflight, venv, pinned baseline
packages, PartUV/scatter import and CUDA kernel health, Blender feature probe,
then checkpoint download/verification. An error leaves setup unsuccessful and
keeps existing checkpoints. Unsupported native platforms fail before large
downloads. An existing wrong-Python venv is not silently erased.

Weights are stored only at:

```text
<Modly models_dir>/modly-partuv-extension/partfield/model_objaverse.ckpt
```

The reviewed PartField checkpoint is approximately 1.24 GB. Setup uses a pinned
Hub revision, a cross-process file lock, streaming download, SHA256 validation,
and atomic replacement. A valid existing checkpoint is reused without network
access. Runtime validates the checkpoint before upstream's pickle-based load,
sets Hugging Face offline flags and never downloads missing weights. Checkpoint
files are not put in the venv, extension source, workspace or home HF cache.

Python package wheels/cache are runtime dependencies, not model weights.
Dependencies beyond the pinned core baseline use bounded requirements and the
resolved environment is recorded in `.installed-requirements.txt`; this preview
is **not a fully reproducible transitive dependency lock**.

## Platform status — deliberately separate from Python syntax support

| Target | State in this delivery |
| --- | --- |
| Linux x86-64, Python 3.11 | Upstream native wheel exists; setup lane implemented; full inference not run here. |
| Linux x86-64, Python 3.12 | Upstream native wheel exists; setup lane implemented; full inference not run here. |
| Windows x86-64, Python 3.11/3.12 | Portable wrapper written; no upstream PartUV Windows wheel; end-to-end target BLOCKED pending a native build. |
| Linux ARM64/CUDA | Not supported by this initial installer; needs a separate complete native dependency lane. |
| CPU-only | No full PartField/PartUV CPU fallback is advertised. Blender baking deliberately uses Cycles CPU independently of Torch. |
| Blender 5.2 | External-executable design supports trying it; not installed/tested in this environment. |

For a developer-owned Windows build, `native_wheel` plus
`native_wheel_sha256` can be supplied in `setup-config.json`. Pip checks its
wheel tags and the health probe checks imports/scatter CUDA, but **accepting a
wheel is not evidence that its PartUV/PAMO kernels work**. A Windows port and
validated binaries are not included. See `docs/NATIVE_PORT.md`.

PAMO is optional and exposed in the UI. The reviewed native CMake contains a
limited hard-coded set of GPU targets; do not infer support for another GPU
from Torch detecting it. Try the explicit `pamo=false` setting for a local
compatibility test, but this does not establish that the remaining native
backend works. No silent optimization fallback is applied.

## Material and asset limitations

The first rebake implementation handles a directly connected Principled BSDF
metallic/roughness subset: base-color with alpha, metallic, roughness, emission,
tangent normal, and AO when represented by Blender's conventional glTF Material
Output/Occlusion node. AO is transferred, not newly calculated. Color channels
use sRGB; scalar/normal channels use Non-Color. Atlas PNGs plus a textured GLB are
written. Materials may be consolidated per object and numeric values undergo
texture rasterization; this is not lossless preservation of the shader graph.

Rigging, animations, shape keys, displacement, UDIM, mixed/custom BSDF surfaces,
nondefault specular/IOR extensions, transmission/coat/subsurface/sheen,
HDR emission and arbitrary topology-changing repair are not supported by the
rebake path. Detected unsupported cases fail rather than claim preservation.
Standalone transfer is one object to one object. The main unwrap and repack
nodes process multiple static mesh objects independently. Triangulation is an
explicit preprocessing step, not animation-ready retopology.

**Remaining acceptance concerns:** alpha masking vs blending semantics, complex
node-group texture coordinates, normal-map orientation and Blender/glTF channel
packing have not been validated on real fixtures. Alpha is rebuilt as a blended
material; original per-material alpha-cutoff semantics are not preserved. The
worker records that visual acceptance and an independent overlap test are not
complete. Do not treat the presence of a GLB as a guarantee of equal appearance.

Outputs live in unique `workspaceDir/Workflows/PartUV/<node>-<uuid>/` directories:
`source.blend`, per-object geometry and native charts, textures, `result.glb`,
`result.partuv.json`, and worker logs. Completed intermediates are preserved on
failure. There is no destructive temporary-folder cleanup of source assets.

## Tests and honest validation

```bash
python -m unittest discover -s tests -v
```

The delivered test log records **37 passing tests under Python 3.13.5 on Linux**
(the authoring interpreter), including parsing all source using Python 3.11's
syntax grammar. That syntax check is NOT execution under Python 3.11/3.12.
CI is configured for Windows/Linux × Python 3.11/3.12 **contract tests only** and
has not been run on GitHub. No network/GPU/Blender is needed for these tests.

The tests cover process framing, state/storage identity, rejecting guessed
paths, safe boolean/numeric parsing, corrupt download rejection, atomic
promotion, checksum reuse, missing native lane errors, Unicode/spaced command
arguments, OBJ parsing, normalized geometry, face/corner reordering and
ambiguous correspondence. They do not test inference quality.

Run `tools/run_smoke.py` with a real static mesh and the extension venv on a
supported test machine; it invokes the real processor, not a fake inference
backend. Use `docs/ACCEPTANCE.md` for the required fixture and release gates.
The reviewed Modly Python process runner's `terminate()` is a no-op. Our own
workers handle process signals/timeouts, but **reliable cancellation from the
Modly UI is an upstream host integration gap**, not something claimed here.

## Sources reviewed

- Modly process runner: https://github.com/lightningpixel/modly/blob/main/electron/main/process-runner.ts
- Bundled Python: https://github.com/lightningpixel/modly/blob/main/scripts/download-python-embed.js
- PartUV repository/API: https://github.com/EricWang12/PartUV
- PartUV wheel inventory: https://pypi.org/project/partuv/0.1.2/
- PartUV preprocessing: https://github.com/EricWang12/PartUV/blob/main/partuv/preprocess.py
- Native CMake: https://github.com/EricWang12/PartUV/blob/main/CMakeLists.txt
- PartField license: https://github.com/EricWang12/PartUV/blob/main/LICENSE
- PartField checkpoint: https://huggingface.co/mikaelaangel/partfield-ckpt/blob/f8cda8fd7dcef0596654015a482cc89407977a29/model_objaverse.ckpt
- Torch-scatter wheel inventory: https://data.pyg.org/whl/torch-2.7.0%2Bcu128.html
