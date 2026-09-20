from __future__ import annotations
import ast
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from partuv_modly.common import absolute_path, boolean, number, read_json, write_json, exclusive_lock
from partuv_modly.config import DEFAULTS, NODES, validated, yaml_config
from partuv_modly.paths import resolve_models_root, checkpoint_path
from partuv_modly.install import choose_lane, download_verified
from partuv_modly.correspondence import read_obj, write_obj, transfer_uv
from partuv_modly.blender import command, resolve_blender
from partuv_modly.runtime import resolve_request
import setup as setup_entry


class ContractTests(unittest.TestCase):
    def test_python_311_syntax(self):
        for path in ROOT.rglob('*.py'):
            if 'venv' not in path.parts:
                ast.parse(path.read_text(encoding='utf-8'), filename=str(path), feature_version=(3, 11))

    def test_manifest_matches_node_contract(self):
        manifest = read_json(ROOT / 'manifest.json')
        self.assertEqual(manifest['type'], 'process')
        self.assertTrue((ROOT / manifest['entry']).is_file())
        self.assertEqual({x['id'] for x in manifest['nodes']}, NODES)
        for node in manifest['nodes']:
            self.assertEqual(node['input'], 'mesh')
            self.assertEqual(node['output'], 'mesh')
            self.assertEqual(len({p['id'] for p in node['params_schema']}), len(node['params_schema']))
            validated({p['id']: p['default'] for p in node['params_schema']})

    def test_defaults_config(self):
        p = validated({})
        text = yaml_config(p)
        self.assertIn('method: "abf"', text)
        self.assertIn('threshold: 1.25', text)
        self.assertNotIn('uvpackmaster', text.lower())

    def test_nonfinite_parameters_rejected(self):
        for value in [float('nan'), float('inf'), 'NaN', '-inf']:
            with self.assertRaises(ValueError):
                validated(dict(threshold=value))

    def test_boolean_string_false(self):
        self.assertFalse(boolean('false'))
        self.assertFalse(validated(dict(pamo='false'))['pamo'])
        self.assertTrue(boolean('true'))
        with self.assertRaises(ValueError):
            boolean('maybe')

    def test_integer_not_truncated(self):
        with self.assertRaises(ValueError):
            validated(dict(threads=1.8))
        with self.assertRaises(ValueError):
            validated(dict(threads=True))

    def test_unknown_and_paid_parameters_rejected(self):
        for params in [dict(pack_method='uvpackmaster'), dict(num_atlas=4), dict(unknown=1)]:
            with self.assertRaises(ValueError):
                validated(params)

    def test_relative_paths_rejected(self):
        with self.assertRaises(ValueError):
            absolute_path('models', 'models_dir')

    def test_explicit_models_root(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            actual = resolve_models_root(dict(models_dir=str(base / 'models')), root=base / 'extension', state_file=base / 'absent', env={})
            self.assertEqual(actual, base / 'models')
            self.assertTrue(checkpoint_path(actual).is_relative_to(actual))

    def test_models_conflict(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            with self.assertRaises(ValueError):
                resolve_models_root(dict(models_dir=str(base / 'one'), modelsDir=str(base / 'two')), root=base/'ext', env={})

    def test_models_inside_extension_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            with self.assertRaises(ValueError):
                resolve_models_root(dict(models_dir=str(base / 'ext' / 'models')), root=base / 'ext', env={})

    def test_storage_settings_bound_to_installation(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            ext = base / 'extensions' / 'modly-partuv-extension'
            ext.mkdir(parents=True)
            write_json(base / 'settings.json', dict(extensionsDir=str(base / 'extensions'), modelsDir=str(base / 'custom-models')))
            actual = resolve_models_root(root=ext, state_file=base/'no-state', env={'XDG_CONFIG_HOME':str(base / 'empty')})
            self.assertEqual(actual, base/'custom-models')

    def test_runtime_state_bound_to_canonical_root(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            ext = base / 'ext'
            ext.mkdir()
            state = base / 'state.json'
            write_json(state, dict(extension_root=str(ext), models_root=str(base/'models')))
            self.assertEqual(resolve_models_root(root=ext, state_file=state, env={'XDG_CONFIG_HOME':str(base/'config')}), base/'models')

    def test_no_guessed_models_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            with self.assertRaisesRegex(ValueError, 'MODELS_DIR_MISSING'):
                resolve_models_root(root=base/'ext', state_file=base/'absent', env={'XDG_CONFIG_HOME':str(base/'config')})

    def test_setup_argument_forms(self):
        self.assertEqual(setup_entry.parse_args(['setup.py', '{"gpu_sm": 75}'])['gpu_sm'],75)
        self.assertEqual(setup_entry.parse_args(['setup.py','python','ext','75','12'])['cuda_version'],12)
        with self.assertRaises(ValueError):
            setup_entry.parse_args(['setup.py','[]'])

    def test_linux_lanes(self):
        for minor in (11,12):
            lane = choose_lane(dict(version=[3,minor,9], system='Linux', machine='x86_64'), {})
            self.assertEqual(lane['abi'], f'cp3{minor}')

    def test_windows_not_falsely_advertised(self):
        with self.assertRaisesRegex(RuntimeError, 'NATIVE_WHEEL_MISSING'):
            choose_lane(dict(version=[3,12,9], system='Windows', machine='AMD64'), {})

    def test_arm64_native_gate(self):
        with self.assertRaisesRegex(RuntimeError, 'NATIVE_WHEEL_MISSING'):
            choose_lane(dict(version=[3,12,9], system='Linux', machine='aarch64'), {})

    def test_python_313_not_selected_for_native_lane(self):
        with self.assertRaisesRegex(RuntimeError, 'PYTHON_UNSUPPORTED'):
            choose_lane(dict(version=[3,13,5], system='Linux', machine='x86_64'), {})

    def test_custom_native_wheel_requires_hash(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'partuv.whl'; p.write_bytes(b'not actually a wheel')
            with self.assertRaises(ValueError):
                choose_lane(dict(version=[3,12,9], system='Windows', machine='AMD64'), dict(native_wheel=str(p), native_wheel_sha256='0'*64))

    def test_atomic_json_roundtrip(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'folder'/'data.json'
            write_json(path, {'unicode':'Textura áéí', 'number':2})
            self.assertEqual(read_json(path)['unicode'],'Textura áéí')
            self.assertEqual(len(list(path.parent.iterdir())),1)

    def test_weight_reuse_without_network(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'model.ckpt'; path.write_bytes(b'checked')
            expected=hashlib.sha256(b'checked').hexdigest()
            with patch('urllib.request.urlopen', side_effect=AssertionError('network forbidden')):
                self.assertFalse(download_verified('https://example.invalid/checkpoint',path,expected))

    def test_bad_download_preserves_existing_checkpoint(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'model.ckpt'; path.write_bytes(b'existing')
            with patch('urllib.request.urlopen', return_value=io.BytesIO(b'corrupted')):
                with self.assertRaisesRegex(RuntimeError,'CHECKSUM_MISMATCH'):
                    download_verified('https://example.invalid/model',path,hashlib.sha256(b'correct').hexdigest())
            self.assertEqual(path.read_bytes(),b'existing')
            self.assertFalse(list(path.parent.glob('*.partial-*')))

    def test_correct_download_promotes_atomically(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'model.ckpt'
            with patch('urllib.request.urlopen', return_value=io.BytesIO(b'correct')):
                self.assertTrue(download_verified('https://example.invalid/model',path,hashlib.sha256(b'correct').hexdigest()))
            self.assertEqual(path.read_bytes(),b'correct')

    def test_blender_command_is_argument_list(self):
        args=command(Path('C:/Program Files/Blender 5.2/blender.exe'),Path('D:/Espacio á/one.json'))
        self.assertEqual(args[0],'C:/Program Files/Blender 5.2/blender.exe')
        self.assertIn('--disable-autoexec',args)
        self.assertIn('--factory-startup',args)
        self.assertIn('--python-exit-code',args)
        self.assertNotIn('pip',args)

    def test_missing_explicit_blender_not_silently_replaced(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                resolve_blender(str(Path(temp)/'missing.exe'))

    def test_processor_invalid_payload_exactly_one_error(self):
        for text in ['{}','[]','{broken','{"input":null}', '{"params":{"threshold":NaN}}']:
            proc=subprocess.run([sys.executable, str(ROOT/'processor.py')],input=text+'\n',text=True,capture_output=True)
            messages=[json.loads(x) for x in proc.stdout.splitlines()]
            terminal=[m for m in messages if m['type'] in ('done','error')]
            self.assertEqual(proc.returncode,1)
            self.assertEqual(len(terminal),1)
            self.assertEqual(terminal[0]['type'],'error')

    def test_transfer_requires_original_source(self):
        with tempfile.TemporaryDirectory() as temp:
            mesh=Path(temp)/'mesh.glb'; mesh.write_bytes(b'fixture')
            with self.assertRaises(ValueError):
                resolve_request(dict(input=dict(nodeId='transfer',filePath=str(mesh)),workspaceDir=temp))


class CorrespondenceTests(unittest.TestCase):
    def setUp(self):
        self.source=dict(vertices=[[0,0,0],[2,0,0],[0,2,0],[2,2,0]], faces=[[0,1,2],[1,3,2]])
        self.target=dict(vertices=[[2,2,0],[0,2,0],[2,0,0],[0,0,0]],
            faces=[[1,2,0],[2,1,3]], corner_uv=[[[0,1],[1,0],[1,1]], [[1,0],[0,1],[0,0]]])

    def test_vertex_face_corner_reordering(self):
        result=transfer_uv(self.source,self.target)
        self.assertEqual(result['matched_faces'],2)
        self.assertEqual(result['corner_uv'][0],[[0,0],[1,0],[0,1]])
        self.assertEqual(result['corner_uv'][1],[[1,0],[1,1],[0,1]])

    def test_normalized_mesh_restored_only_when_all_faces_match(self):
        target=copy.deepcopy(self.target)
        target['vertices']=[[(v[0]-1)*.45+.5,(v[1]-1)*.45+.5,v[2]*.45+.5] for v in target['vertices']]
        result=transfer_uv(self.source,target)
        self.assertEqual(result['coordinate_mode'],'partfield-normalization-inverted')

    def test_changed_face_count_fails(self):
        self.target['faces'].pop()
        with self.assertRaisesRegex(ValueError,'CORRESPONDENCE_FAILED'):
            transfer_uv(self.source,self.target)

    def test_geometry_change_fails(self):
        self.target['vertices'][0][2]=.1
        with self.assertRaisesRegex(ValueError,'CORRESPONDENCE_FAILED'):
            transfer_uv(self.source,self.target)

    def test_duplicate_source_faces_are_ambiguous(self):
        self.source['faces'][1]=[2,1,0]
        with self.assertRaisesRegex(ValueError,'AMBIGUOUS'):
            transfer_uv(self.source,self.target)

    def test_missing_uv_fails(self):
        self.target['corner_uv'][0][0]=None
        with self.assertRaises(ValueError):
            transfer_uv(self.source,self.target)

    def test_obj_negative_indices(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'a.obj'
            path.write_text('v 0 0 0\nv 1 0 0\nv 0 1 0\nvt 0 0\nvt 1 0\nvt 0 1\nf -3/-3 -2/-2 -1/-1\n')
            data=read_obj(path)
            self.assertEqual(data['faces'],[[0,1,2]])
            self.assertEqual(data['corner_uv'],[[[0,0],[1,0],[0,1]]])

    def test_obj_nonfinite_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'a.obj'; path.write_text('v nan 0 0\n')
            with self.assertRaises(ValueError):
                read_obj(path)

    def test_obj_geometry_roundtrip(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'a.obj'
            write_obj(path,self.source['vertices'],self.source['faces'])
            data=read_obj(path)
            self.assertEqual(data['faces'],self.source['faces'])
            self.assertEqual(data['vertices'],self.source['vertices'])


if __name__ == '__main__':
    unittest.main()
