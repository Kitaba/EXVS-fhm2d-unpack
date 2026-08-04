"""Import the reconstructed Leos model and build EXVS materials in Blender 4.5.

Run from Blender's Scripting workspace.  The script imports the anchor-local
OBJ, replaces the four MTL materials with Principled BSDF node trees, and uses
the RenderDoc-observed channel/constant rules.
"""

import csv
import json
import os
from pathlib import Path

import bpy


# ---------------- configuration ----------------
_project_path = globals().get("EXVS_BLENDER_PROJECT")
_project = {}
if _project_path:
    _project = json.loads(Path(_project_path).read_text(encoding="utf-8"))

OBJ_PATH = Path(_project.get(
    "obj_path", r"E:\rendercapture\leos_model\analysis_output\leos_assembly\leos_anchor_local.obj"
))
PROJECT_DIR = Path(_project.get(
    "texture_project_dir", r"E:\rendercapture\leos_model\analysis_output\leos_fhm2d_project\0x8241702F"
))
OUTPUT_BLEND = Path(_project.get(
    "output_blend", r"E:\rendercapture\leos_model\analysis_output\leos_assembly\leos_pbr.blend"
))
IMPORT_MODEL = bool(_project.get("import_model", True))
PACK_RESOURCES = bool(_project.get("pack_resources", True))
SAVE_BLEND = bool(_project.get("save_blend", True))
INVERT_NORMAL_GREEN = bool(_project.get("invert_normal_green", True))
SET_STANDARD_COLOR_MANAGEMENT = bool(_project.get("set_standard_color_management", True))
# ------------------------------------------------


DEFAULT_MATERIALS = {
    "R134920": {"label": "emi", "base_resource": "134920", "emissive_scale": 9.0, "shadow_receiver": False},
    "R134997": {"label": "pbr3", "base_resource": "134997", "emissive_scale": 0.0, "shadow_receiver": True},
    "R134898": {"label": "pbr1", "base_resource": "134898", "emissive_scale": 0.0, "shadow_receiver": True},
    "R134923": {"label": "pbr2", "base_resource": "134923", "emissive_scale": 0.0, "shadow_receiver": True},
}
MATERIALS = _project.get("materials", DEFAULT_MATERIALS)

SEMANTIC_KEYS = {
    "BaseColorMap": "base_color",
    "NormalMap": "normal",
    "MetallicMap": "metallic",
    "RoughnessMap": "roughness",
    "AmbientOcclusionMap": "ao",
    "EmissiveMap": "emissive",
}


def load_texture_paths():
    textures_csv = PROJECT_DIR / "textures.csv"
    runtime_csv = PROJECT_DIR / "runtime_texture_map.csv"
    png_dir = PROJECT_DIR / "png_edit"
    if not textures_csv.exists():
        raise FileNotFoundError("Texture project manifests are missing under {}".format(PROJECT_DIR))

    by_embedded = {}
    with textures_csv.open("r", encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            filename = "{group}_{index:05d}_{width}x{height}_{storage}.png".format(
                group=row["group_label"],
                index=int(row["texture_index"]),
                width=int(row["width"]),
                height=int(row["height"]),
                storage=row["storage_format"],
            )
            png_path = png_dir / filename
            dds_path = PROJECT_DIR / row.get("dds_output", "")
            by_embedded[row["embedded_name"]] = (
                png_path if png_path.is_file() else dds_path
            )

    by_resource = {}
    if runtime_csv.exists():
        with runtime_csv.open("r", encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                path = by_embedded.get(row["embedded_name"])
                if path is not None:
                    by_resource[(row["resource_id"], row["semantic"])] = path

    material_textures = {}
    for material_name, info in MATERIALS.items():
        texture_prefix = info.get("texture_prefix")
        if texture_prefix:
            paths = {}
            for embedded_name, path in by_embedded.items():
                if not embedded_name.startswith(texture_prefix + "_"):
                    continue
                suffix = embedded_name[len(texture_prefix) + 1:].lower()
                semantic_key = {
                    "basecolor": "base_color", "normal": "normal", "metallic": "metallic",
                    "roughness": "roughness", "ambientocclusion": "ao", "emissive": "emissive",
                }.get(suffix)
                if semantic_key:
                    paths[semantic_key] = path
            material_textures[material_name] = paths
            continue
        if not runtime_csv.exists():
            # Direct FHM2D projects do not have RenderDoc's runtime map.
            # Materials without a unique texture prefix intentionally use
            # their recovered shader constants / neutral fallback instead.
            material_textures[material_name] = {}
            continue
        base_resource = info["base_resource"]
        base_row = None
        with runtime_csv.open("r", encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                if row["resource_id"] == base_resource and row["semantic"] == "BaseColorMap":
                    base_row = row
                    break
        if base_row is None:
            raise RuntimeError("No BaseColorMap mapping for {}".format(material_name))
        prefix = base_row["embedded_name"].rsplit("_basecolor", 1)[0]
        paths = {}
        with runtime_csv.open("r", encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                if not row["embedded_name"].startswith(prefix + "_"):
                    continue
                semantic_key = SEMANTIC_KEYS.get(row["semantic"])
                path = by_resource.get((row["resource_id"], row["semantic"]))
                if semantic_key and path:
                    paths[semantic_key] = path
        missing = sorted(set(SEMANTIC_KEYS.values()) - set(paths))
        if missing:
            raise RuntimeError("{} is missing textures: {}".format(material_name, ", ".join(missing)))
        material_textures[material_name] = paths
    return material_textures


def image_node(nodes, path, label, color_space, x, y):
    if not path.exists():
        raise FileNotFoundError(path)
    image = bpy.data.images.load(str(path), check_existing=True)
    try:
        image.colorspace_settings.name = color_space
    except TypeError:
        if color_space == "Non-Color":
            image.colorspace_settings.name = "Non-Color"
    node = nodes.new("ShaderNodeTexImage")
    node.name = label
    node.label = label
    node.image = image
    node.interpolation = "Linear"
    node.extension = "REPEAT"
    node.location = (x, y)
    return node


def shader_input(shader, *names):
    for name in names:
        socket = shader.inputs.get(name)
        if socket is not None:
            return socket
    raise KeyError("Principled BSDF input not found: {}".format(names))


def build_material(material, paths, settings):
    material.use_nodes = True
    material.use_backface_culling = True
    material.diffuse_color = (1.0, 1.0, 1.0, 1.0)
    material["exvs_material"] = settings["label"]
    material["exvs_emissive_scale"] = settings["emissive_scale"]
    material["exvs_shadow_receiver"] = settings["shadow_receiver"]
    material["exvs_source_material"] = settings.get("material_name", material.name)
    material["exvs_shader"] = json.dumps(settings.get("shader", []), ensure_ascii=False)
    material["exvs_logical_textures"] = json.dumps(
        settings.get("logical_textures", {}), ensure_ascii=False
    )
    material["exvs_extended_binding"] = bool(settings.get("has_extended_binding", False))
    material["exvs_uv_transform"] = "[[1,0,0,0],[0,1,0,0]]"
    material["exvs_metallic_channel"] = "R"
    material["exvs_roughness_channel"] = "R"
    material["exvs_ao_channel"] = "R"

    nodes = material.node_tree.nodes
    links = material.node_tree.links
    nodes.clear()

    output = nodes.new("ShaderNodeOutputMaterial")
    output.location = (950, 100)
    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.location = (650, 100)
    links.new(principled.outputs["BSDF"], output.inputs["Surface"])

    base = image_node(nodes, paths["base_color"], "EXVS BaseColor (sRGB)", "sRGB", -900, 500) if "base_color" in paths else None
    ao = image_node(nodes, paths["ao"], "EXVS AO (R)", "Non-Color", -900, 250) if "ao" in paths else None
    multiply = nodes.new("ShaderNodeMixRGB")
    multiply.name = "EXVS BaseColor x AO"
    multiply.label = "BaseColor × AO"
    multiply.blend_type = "MULTIPLY"
    multiply.inputs[0].default_value = 1.0
    multiply.location = (-350, 450)
    if base:
        links.new(base.outputs["Color"], multiply.inputs[1])
    if base and ao:
        links.new(ao.outputs["Color"], multiply.inputs[2])
        links.new(multiply.outputs["Color"], shader_input(principled, "Base Color"))
    elif base:
        links.new(base.outputs["Color"], shader_input(principled, "Base Color"))
    else:
        shader_input(principled, "Base Color").default_value = (0.5, 0.5, 0.5, 1.0)

    metallic = image_node(nodes, paths["metallic"], "EXVS Metallic (R)", "Non-Color", -900, 0) if "metallic" in paths else None
    metallic_sep = nodes.new("ShaderNodeSeparateColor")
    metallic_sep.mode = "RGB"
    metallic_sep.location = (-350, 0)
    if metallic:
        links.new(metallic.outputs["Color"], metallic_sep.inputs["Color"])
        links.new(metallic_sep.outputs["Red"], shader_input(principled, "Metallic"))
    else:
        shader_input(principled, "Metallic").default_value = 0.0

    roughness = image_node(nodes, paths["roughness"], "EXVS Roughness (R)", "Non-Color", -900, -250) if "roughness" in paths else None
    roughness_sep = nodes.new("ShaderNodeSeparateColor")
    roughness_sep.mode = "RGB"
    roughness_sep.location = (-350, -250)
    if roughness:
        links.new(roughness.outputs["Color"], roughness_sep.inputs["Color"])
        links.new(roughness_sep.outputs["Red"], shader_input(principled, "Roughness"))
    else:
        shader_input(principled, "Roughness").default_value = 0.5

    normal = image_node(nodes, paths["normal"], "EXVS Normal RGB", "Non-Color", -900, -550) if "normal" in paths else None
    normal_color = normal.outputs["Color"] if normal else None
    if normal and INVERT_NORMAL_GREEN:
        separate = nodes.new("ShaderNodeSeparateColor")
        separate.mode = "RGB"
        separate.location = (-600, -550)
        invert_green = nodes.new("ShaderNodeMath")
        invert_green.operation = "SUBTRACT"
        invert_green.inputs[0].default_value = 1.0
        invert_green.location = (-350, -600)
        combine = nodes.new("ShaderNodeCombineColor")
        combine.mode = "RGB"
        combine.location = (-100, -550)
        links.new(normal.outputs["Color"], separate.inputs["Color"])
        links.new(separate.outputs["Red"], combine.inputs["Red"])
        links.new(separate.outputs["Green"], invert_green.inputs[1])
        links.new(invert_green.outputs[0], combine.inputs["Green"])
        links.new(separate.outputs["Blue"], combine.inputs["Blue"])
        normal_color = combine.outputs["Color"]
    if normal:
        normal_map = nodes.new("ShaderNodeNormalMap")
        normal_map.space = "TANGENT"
        normal_map.inputs["Strength"].default_value = 1.0
        normal_map.location = (200, -500)
        links.new(normal_color, normal_map.inputs["Color"])
        links.new(normal_map.outputs["Normal"], shader_input(principled, "Normal"))

    if "emissive" in paths:
        emissive = image_node(nodes, paths["emissive"], "EXVS Emissive (sRGB)", "sRGB", -900, -850)
        links.new(emissive.outputs["Color"], shader_input(principled, "Emission Color", "Emission"))
        shader_input(principled, "Emission Strength").default_value = settings["emissive_scale"]

    # Captured constants: F0=(0,0,0), Roughness multiplier=1, specular
    # multiplier=1, diffuse override weight=0, alpha test disabled.
    shader_input(principled, "Alpha").default_value = 1.0


def import_obj():
    if not OBJ_PATH.exists():
        raise FileNotFoundError(OBJ_PATH)
    before = set(bpy.data.objects)
    bpy.ops.wm.obj_import(
        filepath=str(OBJ_PATH),
        forward_axis="NEGATIVE_Z",
        up_axis="Y",
        use_split_objects=True,
        use_split_groups=False,
        validate_meshes=True,
    )
    return [obj for obj in bpy.data.objects if obj not in before]


def main():
    texture_paths = load_texture_paths()
    imported = import_obj() if IMPORT_MODEL else []
    if not imported:
        imported = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]

    used_materials = set()
    for obj in imported:
        if obj.type != "MESH":
            continue
        for slot in obj.material_slots:
            material = slot.material
            if material is None:
                continue
            # Blender may append .001 when an earlier import exists.
            base_name = material.name.split(".", 1)[0]
            if base_name not in MATERIALS:
                continue
            build_material(material, texture_paths[base_name], MATERIALS[base_name])
            material.name = "{}_{}".format(base_name, MATERIALS[base_name]["label"])
            used_materials.add(base_name)

    missing_materials = sorted(set(MATERIALS) - used_materials)
    if missing_materials:
        raise RuntimeError("Imported objects did not use materials: {}".format(", ".join(missing_materials)))

    if SET_STANDARD_COLOR_MANAGEMENT:
        scene = bpy.context.scene
        scene.view_settings.view_transform = "Standard"
        scene.view_settings.look = "Medium High Contrast"
        scene.view_settings.exposure = 0.0
        scene.view_settings.gamma = 1.0

    if PACK_RESOURCES:
        bpy.ops.file.pack_all()
    if SAVE_BLEND:
        OUTPUT_BLEND.parent.mkdir(parents=True, exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(OUTPUT_BLEND))
    print("EXVS Blender import complete: objects={} materials={} output={}".format(
        len(imported), len(used_materials), OUTPUT_BLEND if SAVE_BLEND else "not saved"
    ))


if __name__ == "__main__":
    main()
