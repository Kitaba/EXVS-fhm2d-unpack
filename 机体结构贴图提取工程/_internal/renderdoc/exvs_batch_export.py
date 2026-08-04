"""RenderDoc 1.45 Python-shell batch scanner/exporter for EXVS model draws.

Run while the capture is open in qrenderdoc:
exec(open(r'D:\BaiduNetdiskDownload\ib1.0.4\EXVS_Texture_Toolkit\机体结构贴图提取工程\_internal\renderdoc\exvs_batch_export.py', encoding='utf-8').read())

The script uses qrenderdoc's injected ``pyrenderdoc`` and ``renderdoc`` objects.
Edit only the configuration block below.  SCAN_ONLY=True is the safe first pass.
"""

import hashlib
import json
import os
import struct
import traceback


# ---------------- configuration ----------------
# A wrapper may define EXVS_BATCH_CONFIG before exec() to avoid editing this
# file for every capture. Direct execution keeps the safe scan-only defaults.
_config = globals().get("EXVS_BATCH_CONFIG", {})
OUTPUT_DIR = _config.get("output_dir", r"E:\rendercapture\leos_model\batch_export")
SCAN_ONLY = bool(_config.get("scan_only", True))
MIN_INDEX_COUNT = int(_config.get("min_index_count", 1))
EVENT_IDS = [int(item) for item in _config.get("event_ids", [])]
REQUIRED_PS_TEXTURE_NAMES = {
    "BaseColorMap",
    "NormalMap",
    "MetallicMap",
    "RoughnessMap",
    "AmbientOcclusionMap",
    "EmissiveMap",
}
SAVE_TEXTURE_DDS_MIP0 = bool(_config.get("save_texture_dds_mip0", True))
# ------------------------------------------------


rd = renderdoc
_structured_file = None


def rid_text(resource_id):
    return str(resource_id)


def null_resource(resource_id):
    return resource_id == rd.ResourceId.Null()


def enum_text(value):
    return str(value)


def action_name(action):
    try:
        return action.GetName(_structured_file)
    except Exception:
        return str(action.customName or "E{}".format(action.eventId))


def walk(actions, marker_path=()):
    for action in actions:
        name = action_name(action)
        path = marker_path
        if action.children:
            path = marker_path + (name,)
        yield action, marker_path
        for child in walk(action.children, path):
            yield child


def safe_name(value):
    result = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in value)
    return result[:100] or "unnamed"


def write_bytes(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as stream:
        stream.write(bytes(data))
    return {
        "path": path,
        "size": len(data),
        "sha256": hashlib.sha256(bytes(data)).hexdigest(),
    }


def describe_format(fmt):
    result = {"text": str(fmt)}
    for attr in ("compCount", "compByteWidth", "compType", "type", "bgraOrder"):
        if hasattr(fmt, attr):
            result[attr] = enum_text(getattr(fmt, attr))
    return result


def shader_resources(pipe, stage):
    reflection = pipe.GetShaderReflection(stage)
    bound = pipe.GetReadOnlyResources(stage)
    result = []
    if reflection is None:
        return result
    reflected = list(reflection.readOnlyResources)
    if hasattr(pipe, "GetBindpointMapping"):
        mapping = pipe.GetBindpointMapping(stage)
        mapped = list(mapping.readOnlyResources)
        for ordinal, resource in enumerate(reflected):
            bind = mapped[ordinal].bind if ordinal < len(mapped) else ordinal
            entry = {
                "name": resource.name,
                "reflection_ordinal": ordinal,
                "bind": bind,
                "bind_space": getattr(resource, "fixedBindSetOrSpace", 0),
                "resources": [],
            }
            if 0 <= bind < len(bound):
                for item in bound[bind].resources:
                    entry["resources"].append(rid_text(item.resourceId))
            result.append(entry)
        return result

    # RenderDoc 1.45 descriptor API. Each UsedDescriptor identifies the reflection
    # entry through access.index and contains the actual resource in descriptor.
    entries = {}
    for used in bound:
        ordinal = int(used.access.index)
        if ordinal < 0 or ordinal >= len(reflected):
            continue
        resource = reflected[ordinal]
        entry = entries.setdefault(
            ordinal,
            {
                "name": resource.name,
                "reflection_ordinal": ordinal,
                "bind": int(resource.fixedBindNumber),
                "bind_space": int(resource.fixedBindSetOrSpace),
                "resources": [],
            },
        )
        rid = used.descriptor.resource
        if not null_resource(rid):
            rid_string = rid_text(rid)
            if rid_string not in entry["resources"]:
                entry["resources"].append(rid_string)
    result.extend(entries[index] for index in sorted(entries))
    return result


def export_textures(controller, event_dir, entries, texture_descs, exported):
    for entry in entries:
        for array_index, rid_string in enumerate(entry["resources"]):
            desc = texture_descs.get(rid_string)
            if desc is None:
                continue
            entry.setdefault("descriptors", []).append(
                {
                    "resource": rid_string,
                    "width": desc.width,
                    "height": desc.height,
                    "depth": desc.depth,
                    "arraysize": desc.arraysize,
                    "mips": desc.mips,
                    "format": describe_format(desc.format),
                }
            )
            if not SAVE_TEXTURE_DDS_MIP0:
                continue
            key = (rid_string, 0)
            if key in exported:
                entry.setdefault("exports", []).append(exported[key])
                continue
            filename = "t{:02d}_{}_{}_mip0.dds".format(entry["bind"], safe_name(entry["name"]), safe_name(rid_string))
            path = os.path.join(event_dir, filename)
            save = rd.TextureSave()
            save.resourceId = desc.resourceId
            save.mip = 0
            save.slice.sliceIndex = 0
            save.alpha = rd.AlphaMapping.Preserve
            save.destType = rd.FileType.DDS
            controller.SaveTexture(save, path)
            info = {"path": path, "resource": rid_string, "mip": 0}
            exported[key] = info
            entry.setdefault("exports", []).append(info)


def export_vertex_inputs(controller, pipe, action, event_dir, buffer_descs, exported):
    layouts = []
    for item in pipe.GetVertexInputs():
        layouts.append(
            {
                "name": item.name,
                "vertex_buffer": item.vertexBuffer,
                "byte_offset": item.byteOffset,
                "per_instance": item.perInstance,
                "instance_rate": item.instanceRate,
                "format": describe_format(item.format),
            }
        )

    buffers = []
    for slot, vb in enumerate(pipe.GetVBuffers()):
        if null_resource(vb.resourceId):
            continue
        rid = rid_text(vb.resourceId)
        desc = buffer_descs.get(rid)
        size = int(getattr(vb, "byteSize", 0))
        if size <= 0 and desc is not None:
            size = max(0, int(desc.length) - int(vb.byteOffset))
        info = {
            "slot": slot,
            "resource": rid,
            "byte_offset": int(vb.byteOffset),
            "byte_stride": int(vb.byteStride),
            "byte_size": size,
        }
        key = (rid, int(vb.byteOffset), size)
        if key not in exported:
            data = controller.GetBufferData(vb.resourceId, int(vb.byteOffset), size)
            path = os.path.join(event_dir, "vb{}_{}_o{}_s{}.bin".format(slot, safe_name(rid), vb.byteOffset, size))
            exported[key] = write_bytes(path, data)
        info["export"] = exported[key]
        buffers.append(info)

    index = None
    ib = pipe.GetIBuffer()
    if not null_resource(ib.resourceId):
        stride = int(ib.byteStride)
        offset = int(ib.byteOffset) + int(action.indexOffset) * stride
        size = int(action.numIndices) * stride
        rid = rid_text(ib.resourceId)
        key = (rid, offset, size)
        if key not in exported:
            data = controller.GetBufferData(ib.resourceId, offset, size)
            path = os.path.join(event_dir, "ib_{}_o{}_s{}.bin".format(safe_name(rid), offset, size))
            exported[key] = write_bytes(path, data)
        index = {
            "resource": rid,
            "byte_offset": offset,
            "byte_stride": stride,
            "byte_size": size,
            "index_offset": int(action.indexOffset),
            "base_vertex": int(action.baseVertex),
            "export": exported[key],
        }
    return {"layout": layouts, "vertex_buffers": buffers, "index_buffer": index}


def export_constant_buffers(controller, pipe, event_dir, buffer_descs, exported):
    result = []
    for stage in (rd.ShaderStage.Vertex, rd.ShaderStage.Pixel):
        reflection = pipe.GetShaderReflection(stage)
        if reflection is None:
            continue
        if hasattr(pipe, "GetBindpointMapping"):
            mapping = pipe.GetBindpointMapping(stage)
            mapped = list(mapping.constantBlocks)
            bound_blocks = []
            for ordinal, block in enumerate(reflection.constantBlocks):
                bind = mapped[ordinal].bind if ordinal < len(mapped) else ordinal
                bound = pipe.GetConstantBuffer(stage, bind, 0)
                bound_blocks.append((block, bind, bound.resourceId, bound.byteOffset, bound.byteSize))
        else:
            bound_blocks = []
            blocks = list(reflection.constantBlocks)
            for used in pipe.GetConstantBlocks(stage):
                ordinal = int(used.access.index)
                if ordinal < 0 or ordinal >= len(blocks):
                    continue
                block = blocks[ordinal]
                descriptor = used.descriptor
                bound_blocks.append(
                    (
                        block,
                        int(block.fixedBindNumber),
                        descriptor.resource,
                        int(descriptor.byteOffset),
                        int(descriptor.byteSize),
                    )
                )

        for block, bind, resource_id, byte_offset, byte_size in bound_blocks:
            if null_resource(resource_id):
                continue
            rid = rid_text(resource_id)
            desc = buffer_descs.get(rid)
            size = int(byte_size)
            if size <= 0:
                size = int(block.byteSize)
            if size <= 0 and desc is not None:
                size = max(0, int(desc.length) - int(byte_offset))
            key = (rid, int(byte_offset), size)
            if key not in exported:
                data = controller.GetBufferData(resource_id, int(byte_offset), size)
                path = os.path.join(event_dir, "cb_{}_b{}_{}_o{}_s{}.bin".format(enum_text(stage), bind, safe_name(block.name), byte_offset, size))
                exported[key] = write_bytes(path, data)
            result.append(
                {
                    "stage": enum_text(stage),
                    "name": block.name,
                    "bind": bind,
                    "bind_space": int(getattr(block, "fixedBindSetOrSpace", 0)),
                    "resource": rid,
                    "byte_offset": int(byte_offset),
                    "byte_size": size,
                    "export": exported[key],
                }
            )
    return result


def find_constant_buffer(pipe, stage, block_name):
    """Return a lightweight constant-buffer binding without exporting it."""
    reflection = pipe.GetShaderReflection(stage)
    if reflection is None:
        return None
    blocks = list(reflection.constantBlocks)
    if hasattr(pipe, "GetBindpointMapping"):
        mapping = pipe.GetBindpointMapping(stage)
        mapped = list(mapping.constantBlocks)
        for ordinal, block in enumerate(blocks):
            if block.name != block_name:
                continue
            bind = mapped[ordinal].bind if ordinal < len(mapped) else ordinal
            bound = pipe.GetConstantBuffer(stage, bind, 0)
            return bound.resourceId, int(bound.byteOffset), int(bound.byteSize), int(bind)
        return None

    for used in pipe.GetConstantBlocks(stage):
        ordinal = int(used.access.index)
        if ordinal < 0 or ordinal >= len(blocks) or blocks[ordinal].name != block_name:
            continue
        descriptor = used.descriptor
        return (
            descriptor.resource,
            int(descriptor.byteOffset),
            int(descriptor.byteSize),
            int(blocks[ordinal].fixedBindNumber),
        )
    return None


def lightweight_draw_state(controller, pipe):
    """Collect enough state for offline model clustering during scan-only runs."""
    result = {
        "vertex_buffers": [],
        "index_buffer": None,
        "world_transform": None,
        "shaders": {},
    }
    for slot, vb in enumerate(pipe.GetVBuffers()):
        if null_resource(vb.resourceId):
            continue
        result["vertex_buffers"].append(
            {
                "slot": slot,
                "resource": rid_text(vb.resourceId),
                "byte_offset": int(vb.byteOffset),
                "byte_stride": int(vb.byteStride),
            }
        )
    ib = pipe.GetIBuffer()
    if not null_resource(ib.resourceId):
        result["index_buffer"] = {
            "resource": rid_text(ib.resourceId),
            "byte_offset": int(ib.byteOffset),
            "byte_stride": int(ib.byteStride),
        }
    for stage in (rd.ShaderStage.Vertex, rd.ShaderStage.Pixel):
        shader = pipe.GetShader(stage)
        if not null_resource(shader):
            result["shaders"][enum_text(stage)] = rid_text(shader)

    binding = find_constant_buffer(pipe, rd.ShaderStage.Vertex, "nuPerWorldCBuffer")
    if binding is not None:
        resource_id, byte_offset, byte_size, bind = binding
        if not null_resource(resource_id):
            data = bytes(controller.GetBufferData(resource_id, byte_offset, 64))
            if len(data) >= 64:
                values = struct.unpack_from("<16f", data, 0)
                matrix = [list(values[row * 4 : row * 4 + 4]) for row in range(4)]
                result["world_transform"] = {
                    "resource": rid_text(resource_id),
                    "bind": bind,
                    "byte_offset": byte_offset,
                    "descriptor_size": byte_size,
                    "matrix": matrix,
                    "translation": matrix[3][:3],
                }
    return result


def run(controller):
    global _structured_file
    _structured_file = controller.GetStructuredFile()
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    buffer_descs = {rid_text(item.resourceId): item for item in controller.GetBuffers()}
    texture_descs = {rid_text(item.resourceId): item for item in controller.GetTextures()}
    exported_buffers = {}
    exported_textures = {}
    rows = []

    actions = list(walk(controller.GetRootActions()))
    candidates = []
    for action, markers in actions:
        if int(action.numIndices) < MIN_INDEX_COUNT:
            continue
        if EVENT_IDS and int(action.eventId) not in EVENT_IDS:
            continue
        candidates.append((action, markers))

    print("EXVS batch: {} actions, {} index-count candidates".format(len(actions), len(candidates)))
    for current, (action, markers) in enumerate(candidates, 1):
        try:
            controller.SetFrameEvent(action.eventId, True)
            pipe = controller.GetPipelineState()
            ps_resources = shader_resources(pipe, rd.ShaderStage.Pixel)
            names = {item["name"] for item in ps_resources}
            matched = REQUIRED_PS_TEXTURE_NAMES.issubset(names)
            row = {
                "event_id": int(action.eventId),
                "name": action_name(action),
                "marker_path": list(markers),
                "num_indices": int(action.numIndices),
                "num_instances": int(action.numInstances),
                "index_offset": int(action.indexOffset),
                "base_vertex": int(action.baseVertex),
                "topology": enum_text(pipe.GetPrimitiveTopology()),
                "ps_resources": ps_resources,
                "matches_exvs_pbr6": matched,
            }
            if matched:
                try:
                    row["lightweight_state"] = lightweight_draw_state(controller, pipe)
                except Exception as exc:
                    row["lightweight_state_error"] = "{}: {}".format(type(exc).__name__, exc)
            if matched and not SCAN_ONLY:
                event_dir = os.path.join(OUTPUT_DIR, "E{}".format(action.eventId))
                os.makedirs(event_dir, exist_ok=True)
                export_textures(controller, event_dir, ps_resources, texture_descs, exported_textures)
                row["vertex_input"] = export_vertex_inputs(controller, pipe, action, event_dir, buffer_descs, exported_buffers)
                row["constant_buffers"] = export_constant_buffers(controller, pipe, event_dir, buffer_descs, exported_buffers)
                with open(os.path.join(event_dir, "event.json"), "w", encoding="utf-8") as stream:
                    json.dump(row, stream, ensure_ascii=False, indent=2)
            rows.append(row)
            print("[{}/{}] E{} indices={} pbr6={}".format(current, len(candidates), action.eventId, action.numIndices, matched))
        except Exception as exc:
            rows.append({"event_id": int(action.eventId), "error": str(exc), "traceback": traceback.format_exc()})
            print("E{} ERROR {}".format(action.eventId, exc))

    manifest = {
        "schema": "exvs-renderdoc-batch/v1",
        "scan_only": SCAN_ONLY,
        "min_index_count": MIN_INDEX_COUNT,
        "required_ps_texture_names": sorted(REQUIRED_PS_TEXTURE_NAMES),
        "candidate_count": len(candidates),
        "matching_count": sum(1 for row in rows if row.get("matches_exvs_pbr6")),
        "draws": rows,
    }
    manifest_path = os.path.join(OUTPUT_DIR, "draw_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2)
    print("EXVS batch complete: {}".format(manifest_path))


if "pyrenderdoc" not in globals():
    raise RuntimeError("Run this file from qrenderdoc's Python Shell with a capture open")
pyrenderdoc.Replay().BlockInvoke(run)
