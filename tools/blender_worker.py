"""Run ONLY through Blender --background --factory-startup --disable-autoexec.

This worker modifies temporary scenes, never the user's source file/preferences.
The first release intentionally rejects topology-changing/ambiguous UV transfers.
"""
from __future__ import annotations
from array import array
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from partuv_modly.common import read_json, write_json
from partuv_modly.correspondence import read_obj, write_obj, transfer_uv
import bpy

UV_NAME = "PartUV"
CHANNELS = ("base_color", "alpha", "roughness", "metallic", "emission", "normal", "occlusion")
SOCKETS = {"base_color": "Base Color", "alpha": "Alpha", "roughness": "Roughness",
           "metallic": "Metallic", "emission": "Emission Color"}


def clear_scene():
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)


def select(objects, active=None):
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.hide_set(False)
        obj.select_set(True)
    if objects:
        bpy.context.view_layer.objects.active = active or objects[0]


def import_mesh(path: Path):
    before = set(bpy.data.objects)
    suffix = path.suffix.lower()
    if suffix in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=str(path))
    elif suffix == ".obj":
        bpy.ops.wm.obj_import(filepath=str(path), forward_axis="NEGATIVE_Z", up_axis="Y")
    else:
        raise ValueError("Only static GLB, glTF and OBJ input is supported")
    added = [o for o in bpy.data.objects if o not in before]
    if any(o.animation_data and o.animation_data.action for o in added):
        raise ValueError("Animated inputs are not supported; export a static copy first")
    meshes = [o for o in added if o.type == "MESH"]
    if not meshes:
        raise ValueError("Input contains no mesh objects")
    return meshes


def new_material(name):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    return mat


def principled(mat):
    if not mat or not mat.use_nodes:
        raise ValueError("Non-node material is unsupported")
    outputs = [n for n in mat.node_tree.nodes if n.type == "OUTPUT_MATERIAL" and n.is_active_output]
    if len(outputs) != 1 or not outputs[0].inputs["Surface"].is_linked:
        raise ValueError("Material must have one connected active surface output")
    output = outputs[0]
    node = output.inputs["Surface"].links[0].from_node
    if node.type != "BSDF_PRINCIPLED":
        raise ValueError("Only a directly connected Principled BSDF surface is supported; mixed/custom shaders need another baker")
    if output.inputs["Displacement"].is_linked:
        raise ValueError("Displacement/height export is not implemented in this GLB rebake path")
    for key in ("Coat Weight", "Transmission Weight", "Subsurface Weight", "Sheen Weight",
                "Anisotropic IOR Level", "Thin Film Thickness"):
        sock = node.inputs.get(key)
        if sock and (sock.is_linked or abs(float(sock.default_value)) > 1e-7):
            raise ValueError(f"Material channel '{key}' is outside the supported metallic/roughness PBR subset")
    for key, default in (("IOR", 1.5), ("Specular IOR Level", .5)):
        sock = node.inputs.get(key)
        if sock and (sock.is_linked or abs(float(sock.default_value) - default) > 1e-5):
            raise ValueError(f"Nondefault {key} needs an explicit glTF extension rebake implementation")
    strength = node.inputs.get("Emission Strength")
    if strength and (strength.is_linked or not 0 <= strength.default_value <= 1):
        raise ValueError("HDR/linked emission strength is not supported by this PNG atlas path")
    emission = node.inputs.get("Emission Color")
    if emission and not emission.is_linked and any(x > 1 for x in emission.default_value[:3]):
        raise ValueError("HDR emission requires an HDR-capable export path")
    for image_node in mat.node_tree.nodes:
        if image_node.type == "TEX_IMAGE" and image_node.image:
            if image_node.image.source == "TILED":
                raise ValueError("UDIM source textures are not supported by this first single-tile atlas adapter")
            if image_node.image.file_format in ("OPEN_EXR", "OPEN_EXR_MULTILAYER", "HDR"):
                raise ValueError("HDR source images require an HDR-capable export path")
            if not image_node.image.has_data:
                raise ValueError(f"Missing texture data: {image_node.image.name}")
    return node, output


def mesh_arrays(obj):
    matrix = obj.matrix_world
    vertices = [list(matrix @ v.co) for v in obj.data.vertices]
    faces = [list(p.vertices) for p in obj.data.polygons]
    if any(len(f) != 3 for f in faces):
        raise ValueError("Expected triangulated mesh")
    return dict(vertices=vertices, faces=faces)


def prepare(job):
    clear_scene()
    objects = import_mesh(Path(job["source"]))
    entries = []
    needs_materials = job["node"] != "unwrap"
    for index, obj in enumerate(objects):
        if obj.data.shape_keys or obj.vertex_groups or obj.modifiers:
            raise ValueError("Rigged, shape-key or modifier-dependent meshes are not supported; export a static copy first")
        obj.data = obj.data.copy()  # independent copies for instances
        select([obj])
        modifier = obj.modifiers.new("PartUVTemporaryTriangulation", "TRIANGULATE")
        if hasattr(modifier, "keep_custom_normals"):
            modifier.keep_custom_normals = True
        bpy.ops.object.modifier_apply(modifier=modifier.name)
        if not obj.data.materials:
            obj.data.materials.append(new_material("PartUVOriginalDefault"))
        if needs_materials:
            for mat in obj.data.materials:
                principled(mat)
        original_name = obj.name
        ident = f"object_{index:04d}"
        obj.name = "PartUVSource_" + ident
        folder = Path(job["work"]) / ident
        folder.mkdir(parents=True, exist_ok=True)
        arrays = mesh_arrays(obj)
        obj_path = folder / "source.obj"
        write_obj(obj_path, arrays["vertices"], arrays["faces"])
        write_json(folder / "source-geometry.json", arrays)
        entries.append(dict(id=ident, object_name=obj.name, original_name=original_name,
                            obj_path=str(obj_path), geometry_path=str(folder / "source-geometry.json")))
    bpy.ops.file.pack_all()
    snapshot = Path(job["work"]) / "source.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(snapshot))
    write_json(Path(job["work"]) / "prepared.json", dict(objects=entries, snapshot=str(snapshot)))


def set_uv(obj, corner_uv):
    layer = obj.data.uv_layers.get(UV_NAME) or obj.data.uv_layers.new(name=UV_NAME)
    if len(corner_uv) != len(obj.data.polygons):
        raise ValueError("UV transfer face count mismatch")
    for poly, coords in zip(obj.data.polygons, corner_uv):
        for loop_index, uv in zip(poly.loop_indices, coords):
            layer.data[loop_index].uv = uv
    obj.data.uv_layers.active_index = list(obj.data.uv_layers).index(layer)
    for uv in obj.data.uv_layers:
        uv.active_render = uv == layer


def pack(obj, resolution, margin):
    select([obj])
    bpy.ops.object.mode_set(mode="EDIT")
    try:
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.uv.select_all(action="SELECT")
        bpy.ops.uv.average_islands_scale()
        parameters = dict(rotate=True, scale=True, margin_method="FRACTION",
                          margin=(2 * margin + 4) / resolution)
        props = bpy.ops.uv.pack_islands.get_rna_type().properties
        if "shape_method" in props:
            parameters["shape_method"] = "CONCAVE"
        result = bpy.ops.uv.pack_islands(**parameters)
        if "FINISHED" not in result:
            raise RuntimeError("UV packing did not finish")
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")
    uv = obj.data.uv_layers.active
    if any(not math.isfinite(x) or x < -1e-5 or x > 1.00001 for loop in uv.data for x in loop.uv):
        raise RuntimeError("Packing produced non-finite/out-of-tile UV coordinates")


def freeze_source_uv(mat, uv_name):
    """Freeze implicit image/normal coordinates BEFORE making new UV active."""
    tree = mat.node_tree
    for node in list(tree.nodes):
        if node.type == "TEX_IMAGE" and not node.inputs["Vector"].is_linked:
            if not uv_name:
                raise ValueError("Image texture has no original UV map")
            uv_node = tree.nodes.new("ShaderNodeUVMap")
            uv_node.uv_map = uv_name
            tree.links.new(uv_node.outputs["UV"], node.inputs["Vector"])
        elif node.type == "TEX_COORD" and node.outputs["UV"].is_linked:
            if not uv_name:
                raise ValueError("Shader references a missing original UV map")
            uv_node = tree.nodes.new("ShaderNodeUVMap")
            uv_node.uv_map = uv_name
            for link in list(node.outputs["UV"].links):
                tree.links.new(uv_node.outputs["UV"], link.to_socket)
        elif node.type in ("NORMAL_MAP", "TANGENT") and hasattr(node, "uv_map") and not node.uv_map:
            node.uv_map = uv_name


def source_socket(mat, channel):
    shader, _ = principled(mat)
    if channel == "occlusion":
        for node in mat.node_tree.nodes:
            if node.type == "GROUP" and node.node_tree and node.node_tree.name.startswith("glTF Material Output"):
                socket = node.inputs.get("Occlusion")
                if socket:
                    return socket, 1.0
        return None, 1.0
    socket = shader.inputs[SOCKETS[channel]]
    strength = shader.inputs.get("Emission Strength")
    factor = float(strength.default_value) if channel == "emission" and strength else 1.0
    return socket, factor


def make_image(name, size, channel):
    image = bpy.data.images.new(name, width=size, height=size, alpha=True, float_buffer=False)
    image.colorspace_settings.name = "sRGB" if channel in ("base_color", "emission") else "Non-Color"
    return image


def bake_channel(obj, materials, channel, size, margin):
    image = make_image(obj.name + "_" + channel, size, channel)
    restores, temporary = [], []
    try:
        for mat in materials:
            tree = mat.node_tree
            shader, output = principled(mat)
            target = tree.nodes.new("ShaderNodeTexImage")
            target.image = image
            for node in tree.nodes:
                node.select = False
            target.select = True
            tree.nodes.active = target
            temporary.append((tree, target))
            if channel != "normal":
                socket, factor = source_socket(mat, channel)
                original = output.inputs["Surface"].links[0].from_socket
                restores.append((tree, output, original))
                emit = tree.nodes.new("ShaderNodeEmission")
                temporary.append((tree, emit))
                emit.inputs["Strength"].default_value = factor
                if socket and socket.is_linked:
                    tree.links.new(socket.links[0].from_socket, emit.inputs["Color"])
                elif socket:
                    value = socket.default_value
                    if isinstance(value, (float, int)):
                        emit.inputs["Color"].default_value = (value, value, value, 1)
                    else:
                        emit.inputs["Color"].default_value = tuple(value)
                else:
                    emit.inputs["Color"].default_value = (1, 1, 1, 1)
                tree.links.new(emit.outputs[0], output.inputs["Surface"])
        select([obj])
        scene = bpy.context.scene
        scene.render.engine = "CYCLES"
        scene.cycles.device = "CPU"  # independent of Torch CUDA; no GPU addon setup
        scene.cycles.samples = 1
        scene.render.bake.use_selected_to_active = False
        scene.render.bake.margin = margin
        result = bpy.ops.object.bake(type="NORMAL" if channel == "normal" else "EMIT",
                                     uv_layer=UV_NAME, use_clear=True, margin=margin,
                                     normal_space="TANGENT")
        if "FINISHED" not in result:
            raise RuntimeError(f"Bake did not finish: {channel}")
        return image
    finally:
        for tree, output, original in restores:
            tree.links.new(original, output.inputs["Surface"])
        for tree, node in reversed(temporary):
            tree.nodes.remove(node)


def assemble_material(obj, images):
    mat = new_material(obj.name + "_PBR")
    tree = mat.node_tree
    shader = next(n for n in tree.nodes if n.type == "BSDF_PRINCIPLED")
    for channel, socket_name in SOCKETS.items():
        if channel == "alpha":
            continue  # alpha is merged into base-color image below
        node = tree.nodes.new("ShaderNodeTexImage")
        node.image = images[channel]
        tree.links.new(node.outputs["Color"], shader.inputs[socket_name])
        if channel == "base_color":
            tree.links.new(node.outputs["Alpha"], shader.inputs["Alpha"])
    normal_image = tree.nodes.new("ShaderNodeTexImage")
    normal_image.image = images["normal"]
    normal = tree.nodes.new("ShaderNodeNormalMap")
    normal.uv_map = UV_NAME
    tree.links.new(normal_image.outputs["Color"], normal.inputs["Color"])
    tree.links.new(normal.outputs["Normal"], shader.inputs["Normal"])
    # Blender's glTF exporter recognizes this conventional AO-only node group.
    group = bpy.data.node_groups.new("glTF Material Output", "ShaderNodeTree")
    group.interface.new_socket(name="Occlusion", in_out="INPUT", socket_type="NodeSocketFloat")
    group_node = tree.nodes.new("ShaderNodeGroup")
    group_node.node_tree = group
    ao_image = tree.nodes.new("ShaderNodeTexImage")
    ao_image.image = images["occlusion"]
    tree.links.new(ao_image.outputs["Color"], group_node.inputs["Occlusion"])
    if hasattr(mat, "surface_render_method"):
        mat.surface_render_method = "DITHERED"
    elif hasattr(mat, "blend_method"):
        mat.blend_method = "BLEND"
    obj.data.materials.clear()
    obj.data.materials.append(mat)
    for poly in obj.data.polygons:
        poly.material_index = 0


def merge_alpha(base, alpha):
    count = len(base.pixels)
    color_pixels = array("f", [0.0]) * count
    alpha_pixels = array("f", [0.0]) * count
    base.pixels.foreach_get(color_pixels)
    alpha.pixels.foreach_get(alpha_pixels)
    for i in range(3, count, 4):
        color_pixels[i] = min(1.0, max(0.0, alpha_pixels[i - 3]))
    base.pixels.foreach_set(color_pixels)
    base.update()


def duplicate_source(source, original_name, preserve_materials=True):
    obj = source.copy()
    obj.data = source.data.copy()
    bpy.context.collection.objects.link(obj)
    world = obj.matrix_world.copy()
    obj.parent = None
    obj.matrix_world = world
    obj.name = original_name + "_PartUV"
    materials = []
    render_uv = next((u for u in source.data.uv_layers if u.active_render), source.data.uv_layers.active)
    original_uv = render_uv.name if render_uv else ""
    renamed_layer = ""
    if obj.data.uv_layers.get(UV_NAME):
        existing = obj.data.uv_layers[UV_NAME]
        existing.name = "PartUV_Source"
        renamed_layer = existing.name
        if original_uv == UV_NAME:
            original_uv = renamed_layer
    if not preserve_materials:
        obj.data.materials.clear()
        obj.data.materials.append(new_material("PartUVUntextured"))
        return obj, []
    for slot in obj.material_slots:
        slot.material = slot.material.copy()
        for node in slot.material.node_tree.nodes:
            if node.type == "UVMAP" and node.uv_map == UV_NAME and renamed_layer:
                node.uv_map = renamed_layer
            if node.type in ("NORMAL_MAP", "TANGENT") and hasattr(node, "uv_map") and node.uv_map == UV_NAME and renamed_layer:
                node.uv_map = renamed_layer
        freeze_source_uv(slot.material, original_uv)
        materials.append(slot.material)
    return obj, materials


def export_glb(objects, path):
    select(objects)
    result = bpy.ops.export_scene.gltf(filepath=str(path), export_format="GLB", use_selection=True,
                                       export_animations=False, export_texcoords=True, export_normals=True,
                                       export_tangents=True, export_materials="EXPORT")
    if "FINISHED" not in result or not Path(path).is_file():
        raise RuntimeError("GLB export failed")


def finish(job):
    work = Path(job["work"])
    prepared = read_json(work / "prepared.json")
    bpy.ops.wm.open_mainfile(filepath=prepared["snapshot"], load_ui=False, use_scripts=False)
    entries = prepared["objects"]
    params, mode = job["params"], job["node"]
    native = {item["id"]: item for item in read_json(work / "native-result.json")["objects"]} if mode.startswith("unwrap") else {}
    transfer_target = None
    if mode == "transfer":
        target_objects = import_mesh(Path(job["target"]))
        if len(target_objects) != 1 or len(entries) != 1:
            raise ValueError("Standalone transfer currently supports one source and one target mesh; use Unwrap + Rebake for multi-object assets")
        target_obj = target_objects[0]
        select([target_obj])
        modifier = target_obj.modifiers.new("PartUVTargetTriangulate", "TRIANGULATE")
        bpy.ops.object.modifier_apply(modifier=modifier.name)
        transfer_target = mesh_arrays(target_obj)
        uv = target_obj.data.uv_layers.active
        if uv is None:
            raise ValueError("Target has no UV map")
        transfer_target["corner_uv"] = [[list(uv.data[i].uv) for i in poly.loop_indices] for poly in target_obj.data.polygons]
        target_obj.hide_render = True
        target_obj.hide_set(True)
    outputs, reports = [], []
    for entry in entries:
        source = bpy.data.objects[entry["object_name"]]
        obj, materials = duplicate_source(source, entry["original_name"], preserve_materials=mode != "unwrap")
        source.hide_render = True
        source.hide_set(True)
        source_geometry = read_json(Path(entry["geometry_path"]))
        mapping = None
        if mode.startswith("unwrap") or mode == "transfer":
            target = read_obj(Path(native[entry["id"]]["obj_path"])) if mode.startswith("unwrap") else transfer_target
            mapping = transfer_uv(source_geometry, target, allow_partfield_normalization=mode.startswith("unwrap"))
            set_uv(obj, mapping["corner_uv"])
        else:
            old = obj.data.uv_layers.active
            if old is None:
                raise ValueError("Repack needs an existing UV map")
            coordinates = [[list(old.data[i].uv) for i in poly.loop_indices] for poly in obj.data.polygons]
            set_uv(obj, coordinates)
        if mode != "transfer":
            pack(obj, params["texture_size"], params["bake_margin"])
        atlas = {}
        if mode != "unwrap":
            images = {channel: bake_channel(obj, materials, channel, params["texture_size"], params["bake_margin"])
                      for channel in CHANNELS}
            merge_alpha(images["base_color"], images["alpha"])
            directory = work / entry["id"] / "textures"
            directory.mkdir(parents=True, exist_ok=True)
            for channel, image in images.items():
                path = directory / (channel + ".png")
                image.filepath_raw = str(path)
                image.file_format = "PNG"
                image.save()
                image.pack()
                atlas[channel] = str(path)
            assemble_material(obj, images)
        else:
            obj.data.materials.clear()
            obj.data.materials.append(new_material("PartUVUntextured"))
        # Rebuilt materials only need new coordinates. Source snapshot keeps old UVs.
        for layer in list(obj.data.uv_layers):
            if layer.name != UV_NAME:
                obj.data.uv_layers.remove(layer)
        report = dict(id=entry["id"], textures=atlas, faces=len(obj.data.polygons),
                      geometry_policy="original static triangulated surface retained",
                      overlap_test="not independently measured", appearance_verified=False)
        if mapping:
            report["correspondence"] = {k: v for k, v in mapping.items() if k != "corner_uv"}
        reports.append(report)
        outputs.append(obj)
    output = work / "result.glb"
    export_glb(outputs, output)
    write_json(work / "blender-result.json", dict(filePath=str(output), objects=reports,
               blender=bpy.app.version_string, status="generated_requires_visual_acceptance"))


def probe(job):
    if bpy.app.version < (4, 2, 0):
        raise RuntimeError("Blender >=4.2 required by this adapter's operators/node interface")
    clear_scene()
    mesh = bpy.data.meshes.new("PartUVProbeMesh")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    mesh.update()
    obj = bpy.data.objects.new("PartUVProbe", mesh)
    bpy.context.collection.objects.link(obj)
    mat = new_material("PartUVProbeMaterial")
    mesh.materials.append(mat)
    shader, _ = principled(mat)
    shader.inputs["Base Color"].default_value = (.2, .4, .6, 1)
    set_uv(obj, [[(0, 0), (1, 0), (0, 1)]])
    pack(obj, 64, 2)
    image = bake_channel(obj, [mat], "base_color", 64, 2)
    if not image.has_data:
        raise RuntimeError("Cycles did not produce an image")
    # Export a minimal scene too; feature presence alone is not sufficient.
    directory = Path(job["result_path"]).parent
    export_glb([obj], directory / "blender-probe.glb")
    write_json(Path(job["result_path"]), dict(ok=True, blender=bpy.app.version_string,
        version=list(bpy.app.version), python=list(sys.version_info[:3]),
        checks=["uv_pack", "cycles_emit_bake", "glb_export"],
        note="Feature smoke test only; not a PartUV inference/normal-map fidelity test"))


def main():
    if "--" not in sys.argv:
        raise ValueError("Missing worker job")
    args = sys.argv[sys.argv.index("--") + 1:]
    if len(args) != 1:
        raise ValueError("Exactly one JSON job file is required")
    job = read_json(Path(args[0]))
    {"probe": probe, "prepare": prepare, "finish": finish}[job["stage"]](job)


if __name__ == "__main__":
    main()
