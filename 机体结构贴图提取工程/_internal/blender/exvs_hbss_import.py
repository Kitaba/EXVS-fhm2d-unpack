"""Import an EXVS direct-HBSS multi-part project in Blender 4.5."""

import importlib.util
import json
import re
from pathlib import Path

import bpy
from mathutils import Matrix


PROJECT_PATH = Path(globals()["EXVS_HBSS_PROJECT"])
PROJECT = json.loads(PROJECT_PATH.read_text(encoding="utf-8"))
RUNTIME_PATH = Path(PROJECT["material_runtime"])
PART_FILTER = set(globals().get("EXVS_PART_FILTER", []))
OUTPUT_BLEND_OVERRIDE = globals().get("EXVS_OUTPUT_BLEND")
# OBJ is imported from game Y-up/-Z-forward space into Blender Z-up/-Y-forward.
# LEKS bind matrices must undergo the same basis conversion.
SOURCE_TO_BLENDER = Matrix((
    (1.0, 0.0, 0.0, 0.0),
    (0.0, 0.0, -1.0, 0.0),
    (0.0, 1.0, 0.0, 0.0),
    (0.0, 0.0, 0.0, 1.0),
))


def load_material_runtime():
    spec = importlib.util.spec_from_file_location("exvs_material_runtime", RUNTIME_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.PROJECT_DIR = Path(PROJECT["texture_project_dir"]) if PROJECT.get("texture_project_dir") else None
    module.MATERIALS = PROJECT["materials"]
    module.INVERT_NORMAL_GREEN = bool(PROJECT.get("invert_normal_green", True))
    return module


def ensure_collection(path):
    parent = bpy.context.scene.collection
    full_name = "EXVS"
    root = bpy.data.collections.get(full_name)
    if root is None:
        root = bpy.data.collections.new(full_name)
        parent.children.link(root)
    parent = root
    for component in path.split("/"):
        full_name += "/" + component
        child = bpy.data.collections.get(full_name)
        if child is None:
            child = bpy.data.collections.new(full_name)
            parent.children.link(child)
        parent = child
    return parent


def move_to_collection(obj, collection):
    for current in list(obj.users_collection):
        current.objects.unlink(obj)
    collection.objects.link(obj)


def canonical_material(name):
    value = name.split(".", 1)[0]
    if value in PROJECT["materials"]:
        return value
    for key in PROJECT["materials"]:
        if key.lower() == value.lower():
            return key
    return re.sub(r"(?:exb)?Mtl$", "", value, flags=re.IGNORECASE).lower()


def import_part(part, assembly_root, standalone_root):
    before = set(bpy.data.objects)
    bpy.ops.wm.obj_import(
        filepath=part["obj_path"], forward_axis="NEGATIVE_Z", up_axis="Y",
        use_split_objects=False, use_split_groups=False, validate_meshes=True,
    )
    objects = [obj for obj in bpy.data.objects if obj not in before]
    collection = ensure_collection(part["collection"])
    for obj in objects:
        move_to_collection(obj, collection)
        obj["exvs_part"] = part["name"]
        obj["exvs_part_kind"] = part["kind"]
        placement = part.get("placement", {})
        obj.parent = standalone_root if placement.get("mode") == "standalone_local" else assembly_root
        if part.get("attachment_bone"):
            obj["exvs_attachment_bone"] = part["attachment_bone"]
        obj["exvs_placement_mode"] = placement.get("mode", "model_space")
        obj["exvs_placement_resolved"] = bool(placement.get("resolved", False))
        if placement.get("mode") == "bone_bind":
            # LEKS stores row-major matrices with translation in the last row;
            # Blender's Matrix representation uses the transposed convention.
            obj.matrix_parent_inverse = Matrix.Identity(4)
            game_matrix = Matrix(placement["matrix_row_major"]).transposed()
            obj.matrix_basis = SOURCE_TO_BLENDER @ game_matrix @ SOURCE_TO_BLENDER.inverted()
        obj.hide_render = not part["default_visible"]
        obj.hide_set(not part["default_visible"])
    collection.hide_render = not part["default_visible"]
    collection.hide_viewport = not part["default_visible"]
    return objects


def main():
    runtime = load_material_runtime()
    texture_paths = (
        runtime.load_texture_paths() if PROJECT.get("texture_project_dir")
        else {key: {} for key in PROJECT["materials"]}
    )
    selected_parts = [
        part for part in PROJECT["parts"]
        if not PART_FILTER or part["name"] in PART_FILTER
    ]
    if PART_FILTER and not selected_parts:
        raise RuntimeError("EXVS_PART_FILTER matched no project parts: {}".format(sorted(PART_FILTER)))
    imported = []
    assembly_root = bpy.data.objects.new("EXVS_ASSEMBLY_ROOT", None)
    bpy.context.scene.collection.objects.link(assembly_root)
    assembly_root["exvs_anchor_part"] = PROJECT.get("assembly", {}).get("anchor_part") or ""
    standalone_root = bpy.data.objects.new("EXVS_STANDALONE_ROOT", None)
    bpy.context.scene.collection.objects.link(standalone_root)
    standalone_root["exvs_role"] = "independent components; not part of body assembly"
    for part in selected_parts:
        imported.extend(import_part(part, assembly_root, standalone_root))

    built = set()
    for obj in imported:
        if obj.type != "MESH":
            continue
        for slot in obj.material_slots:
            if slot.material is None:
                continue
            key = canonical_material(slot.material.name)
            if key not in PROJECT["materials"]:
                continue
            runtime.build_material(slot.material, texture_paths[key], PROJECT["materials"][key])
            slot.material.name = "EXVS_{}".format(key)
            built.add(key)

    required_materials = {
        material for part in selected_parts for material in part.get("materials", [])
        if material in PROJECT["materials"]
    }
    missing = sorted(required_materials - built)
    if missing:
        raise RuntimeError("materials not assigned by imported OBJ: {}".format(", ".join(missing)))
    scene = bpy.context.scene
    scene["exvs_source_package"] = PROJECT["source_package"]
    scene["exvs_project_manifest"] = str(PROJECT_PATH)
    scene["exvs_unresolved_placements"] = json.dumps(
        PROJECT.get("assembly", {}).get("unresolved_parts", []), ensure_ascii=False
    )
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "Medium High Contrast"
    if PROJECT.get("pack_resources", True):
        bpy.ops.file.pack_all()
    if PROJECT.get("save_blend", True):
        output = Path(OUTPUT_BLEND_OVERRIDE or PROJECT["output_blend"])
        output.parent.mkdir(parents=True, exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(output))
    print("EXVS HBSS import complete: parts={} objects={} materials={}".format(
        len(selected_parts), len(imported), len(built)
    ))


if __name__ == "__main__":
    main()
