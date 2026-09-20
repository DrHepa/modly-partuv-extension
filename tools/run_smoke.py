"""Run with the prepared extension venv; this invokes REAL inference/Blender."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mesh', type=Path, required=True)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--blender', type=Path, required=True)
    parser.add_argument('--node', choices=['unwrap_rebake','unwrap','repack_rebake','transfer'], default='unwrap_rebake')
    parser.add_argument('--source-mesh', type=Path)
    parser.add_argument('--disable-pamo', action='store_true')
    args = parser.parse_args()
    if not args.mesh.is_file():
        parser.error('mesh does not exist')
    before = hashlib.sha256(args.mesh.read_bytes()).hexdigest()
    params = dict(blender_path=str(args.blender.resolve()), texture_size=512, bake_margin=8, pamo=not args.disable_pamo)
    if args.source_mesh:
        params['source_mesh'] = str(args.source_mesh.resolve())
    payload = dict(input=dict(filePath=str(args.mesh.resolve()), nodeId=args.node),
                   params=params, workspaceDir=str(args.workspace.resolve()), tempDir=str(args.workspace.resolve()))
    proc = subprocess.run([sys.executable, str(ROOT/'processor.py')], input=json.dumps(payload)+'\n',
                           capture_output=True, text=True, cwd=ROOT)
    print(proc.stdout)
    print(proc.stderr, file=sys.stderr)
    if proc.returncode:
        return proc.returncode
    messages = [json.loads(line) for line in proc.stdout.splitlines()]
    terminal = [m for m in messages if m['type'] in ('done','error')]
    if len(terminal) != 1 or terminal[0]['type'] != 'done':
        raise RuntimeError('Bad process terminal framing')
    path = Path(terminal[0]['result']['filePath'])
    with path.open('rb') as stream:
        if stream.read(4) != b'glTF':
            raise RuntimeError('Output is not a GLB')
    if hashlib.sha256(args.mesh.read_bytes()).hexdigest() != before:
        raise RuntimeError('Input file was modified')
    print('Real run produced a GLB and left input unchanged. Visual/material/UV-overlap acceptance is still separate.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
