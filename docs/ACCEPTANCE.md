# Release acceptance — not completed in this environment

## Contract tests

Run `python -m unittest discover -s tests -v` under actual Python 3.11.9 and
3.12 on both Windows and Linux. The supplied CI only runs these dependency-free
checks. Do not label this a GPU inference matrix.

## Setup and storage

Test fresh installation, repeated Repair without re-downloading the verified
checkpoint, an interrupted download, a corrupt checkpoint, custom models_dir,
Unicode and space-containing paths, moved extensions, a different Python base
interpreter, concurrent setup requests and insufficient disk space. Check no
model file appears inside extension source or venv. Verify pip's resolved
versions and preserve `.installed-requirements.txt`.

## Blender feature and material fixtures

Use a copied user Blender 5.2 configuration-independent executable invocation.
The worker must not modify user preferences, add-ons or original files. The
built-in probe performs only packing, emission baking and GLB export.

Required real fixtures include an asymmetric checkerboard cube, a smooth object
with directional tangent-space normal details, roughness/metallic wedges,
separate emissive and AO maps, a transparent object, a masked leaf, multiple
materials, two transformed mesh objects, duplicated instances, UV seams,
nonmanifold cases, and inputs with intentionally missing textures.

Render before/after from identical cameras/light/color-management settings.
Inspect base-color continuity, texel density, padding and mipmap bleeding,
roughness/metallic/occlusion channel assignment, tangent normal orientation,
alpha treatment, seams and hidden/back-facing surfaces. A masked leaf is an
explicit known semantic limitation in this preview, not an expected pass.

Independently measure UV overlap, zero-area UV triangles and out-of-range UVs.
The current worker checks finite coordinates/tile bounds and uses Blender's
packer, but does not independently prove overlap absence. PartUV's reported
distortion is preserved as native metadata; do not compare it to a different
metric without matching definitions.

## Geometry and native inference

Verify every correspondence decision against the original face-corner map.
Reordered/seam-duplicated geometry should pass; changed surface geometry,
coincident ambiguous triangles and an incorrect normalization must fail without
claiming texture preservation. A full surface-projection fallback is future
implementation work, not a currently available option.

For each promised OS/Python/GPU tuple, execute PartField feature extraction,
hierarchy, the PartUV native unwrap and PAMO both on/off as applicable. Record
peak memory, driver/Torch/CUDA/compiler versions and exact wheel hashes. Test
large meshes and memory failures; lowering sample_batch_size must remain an
explicit user choice, not a silent result-changing retry.

## Publication gate

Do not advertise a native Windows or ARM64 release until wheels and the complete
native smoke tests pass. Do not advertise Blender 5.2 material-fidelity support
until the fixtures pass or the remaining limits are clearly accepted/documented.
Contract tests alone do not close either gate.
