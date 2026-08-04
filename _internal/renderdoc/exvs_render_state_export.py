"""Export EXVS draw render state from the qrenderdoc Python shell.

RenderDoc 1.45 usage (with a capture open)::

    exec(open(r'D:\BaiduNetdiskDownload\ib1.0.4\EXVS_Texture_Toolkit\_internal\renderdoc\exvs_render_state_export.py', encoding='utf-8').read())

The exporter is deliberately independent from ``exvs_batch_export.py``.  The
mesh exporter is already proven and should remain stable; this script records
the material/shader state needed to reproduce a draw.  Every optional API call
is isolated so one unavailable RenderDoc field does not abort the capture.
"""

import hashlib
import json
import os
import struct
import traceback


# ---------------- configuration ----------------
_config = globals().get("EXVS_RENDER_STATE_CONFIG", {})
OUTPUT_DIR = _config.get("output_dir", r"E:\rendercapture\leos_model\render_state")
EVENT_IDS = [int(item) for item in _config.get(
    "event_ids", [5528, 5601, 5681, 5689, 5694, 5707, 5934, 5947]
)]
EXPORT_STAGES = tuple(_config.get("export_stages", ("Vertex", "Pixel")))
SAVE_SHADER_DISASSEMBLY = bool(_config.get("save_shader_disassembly", True))
SAVE_CONSTANT_BUFFER_RAW = bool(_config.get("save_constant_buffer_raw", True))
# ------------------------------------------------


rd = renderdoc
_structured_file = None


def enum_text(value):
    return str(value)


def rid_text(resource_id):
    return str(resource_id)


def null_resource(resource_id):
    return resource_id == rd.ResourceId.Null()


def safe_name(value):
    result = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in str(value))
    return result[:120] or "unnamed"


def relpath(path):
    return os.path.relpath(path, OUTPUT_DIR).replace("\\", "/")


def write_json(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def write_text(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as stream:
        stream.write(value)
    data = value.encode("utf-8")
    return {"path": relpath(path), "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def write_bytes(path, value):
    data = bytes(value)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as stream:
        stream.write(data)
    return {"path": relpath(path), "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def error_record(exc):
    return {"error": "{}: {}".format(type(exc).__name__, exc)}


def action_name(action):
    try:
        return action.GetName(_structured_file)
    except Exception:
        return str(action.customName or "E{}".format(action.eventId))


def walk(actions, marker_path=()):
    for action in actions:
        name = action_name(action)
        child_path = marker_path + (name,) if action.children else marker_path
        yield action, marker_path
        for child in walk(action.children, child_path):
            yield child


def stage_value(name):
    return getattr(rd.ShaderStage, name)


def value_attr(value, name, default=None):
    try:
        return getattr(value, name)
    except Exception:
        return default


def scalar(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return enum_text(value)


def sequence(value):
    try:
        return list(value)
    except Exception:
        return []


def indexed_sequence(value, count):
    items = sequence(value)
    if items:
        return items[:count]
    result = []
    for index in range(count):
        try:
            result.append(value[index])
        except Exception:
            break
    return result


def describe_format(fmt):
    result = {"text": enum_text(fmt)}
    for name in ("compCount", "compByteWidth", "compType", "type", "bgraOrder", "srgbCorrected"):
        if hasattr(fmt, name):
            result[name] = scalar(getattr(fmt, name))
    return result


def describe_object(value, attributes):
    if value is None:
        return None
    result = {"text": enum_text(value)}
    for name in attributes:
        if not hasattr(value, name):
            continue
        item = getattr(value, name)
        if isinstance(item, (bool, int, float, str)) or item is None:
            result[name] = item
        elif name.lower().endswith("format"):
            result[name] = describe_format(item)
        elif hasattr(item, "__iter__") and not isinstance(item, str):
            result[name] = [scalar(entry) for entry in sequence(item)]
        else:
            result[name] = scalar(item)
    return result


def describe_resource_description(desc):
    if desc is None:
        return None
    return describe_object(
        desc,
        (
            "resourceId", "name", "type", "width", "height", "depth", "arraysize", "mips",
            "msSamp", "msQual", "format", "byteSize", "length", "creationFlags",
        ),
    )


def describe_bound_view(view):
    if view is None:
        return None
    result = describe_object(
        view,
        (
            "resourceId", "resource", "byteOffset", "byteSize", "firstMip", "numMips",
            "firstSlice", "numSlices", "viewFormat", "typeCast", "swizzle",
        ),
    )
    for name in ("resourceId", "resource"):
        if hasattr(view, name):
            result[name] = rid_text(getattr(view, name))
    return result


def describe_signature_parameter(parameter):
    return describe_object(
        parameter,
        (
            "varName", "semanticName", "semanticIdxName", "semanticIndex", "regIndex", "systemValue",
            "compType", "regChannelMask", "channelUsedMask", "stream", "needSemanticIndex",
        ),
    )


def describe_shader_type(shader_type, depth=0):
    if shader_type is None:
        return None
    result = describe_object(
        shader_type,
        (
            "name", "baseType", "rows", "columns", "elements", "arrayByteStride", "matrixByteStride",
            "pointerTypeID", "type", "flags",
        ),
    )
    if depth < 8:
        members = sequence(value_attr(shader_type, "members", []))
        if members:
            result["members"] = [describe_shader_constant(member, depth + 1) for member in members]
    return result


def describe_shader_constant(constant, depth=0):
    result = describe_object(constant, ("name", "byteOffset", "bitFieldOffset", "bitFieldSize"))
    result["type"] = describe_shader_type(value_attr(constant, "type"), depth)
    default_value = value_attr(constant, "defaultValue")
    if default_value is not None:
        result["default_value"] = [scalar(item) for item in sequence(default_value)]
    return result


def describe_constant_block(block):
    result = describe_object(
        block,
        ("name", "fixedBindNumber", "fixedBindSetOrSpace", "byteSize", "bufferBacked", "compileConstants"),
    )
    result["variables"] = [describe_shader_constant(item) for item in sequence(value_attr(block, "variables", []))]
    return result


def describe_reflected_resource(resource):
    result = describe_object(
        resource,
        (
            "name", "fixedBindNumber", "fixedBindSetOrSpace", "bindArraySize", "isTexture",
            "isReadOnly", "variableType", "resType", "textureType",
        ),
    )
    if hasattr(resource, "returnType"):
        result["returnType"] = describe_shader_type(resource.returnType)
    return result


def describe_reflection(reflection):
    if reflection is None:
        return None
    return {
        "resource_id": rid_text(value_attr(reflection, "resourceId", rd.ResourceId.Null())),
        "entry_point": scalar(value_attr(reflection, "entryPoint", "")),
        "encoding": scalar(value_attr(reflection, "encoding", "")),
        "debug_info": describe_object(value_attr(reflection, "debugInfo"), ("entrySourceName", "encoding", "compileFlags")),
        "input_signature": [describe_signature_parameter(item) for item in sequence(value_attr(reflection, "inputSignature", []))],
        "output_signature": [describe_signature_parameter(item) for item in sequence(value_attr(reflection, "outputSignature", []))],
        "constant_blocks": [describe_constant_block(item) for item in sequence(value_attr(reflection, "constantBlocks", []))],
        "read_only_resources": [describe_reflected_resource(item) for item in sequence(value_attr(reflection, "readOnlyResources", []))],
        "read_write_resources": [describe_reflected_resource(item) for item in sequence(value_attr(reflection, "readWriteResources", []))],
        "samplers": [describe_reflected_resource(item) for item in sequence(value_attr(reflection, "samplers", []))],
    }


def variable_values(variable):
    value = value_attr(variable, "value")
    if value is None:
        return {}
    count = max(1, int(value_attr(variable, "rows", 1)) * int(value_attr(variable, "columns", 1)))
    count = min(count, 64)
    result = {}
    value_fields = (
        ("f32v", "f32"),
        ("s32v", "s32"),
        ("u32v", "u32"),
        ("f64v", "f64"),
        ("f16v", "f16"),
        ("s64v", "s64"),
        ("u64v", "u64"),
        ("s16v", "s16"),
        ("u16v", "u16"),
        ("s8v", "s8"),
        ("u8v", "u8"),
        # Pre-descriptor-store RenderDoc aliases, retained for compatibility.
        ("fv", "f32"),
        ("iv", "s32"),
        ("uv", "u32"),
        ("dv", "f64"),
    )
    for field_name, output_name in value_fields:
        array = value_attr(value, field_name)
        if array is None:
            continue
        values = indexed_sequence(array, count)
        if values and output_name not in result:
            result[output_name] = [scalar(item) for item in values]
    return result


def describe_shader_variable(variable, depth=0):
    result = describe_object(variable, ("name", "rows", "columns", "type", "flags"))
    result["value"] = variable_values(variable)
    if depth < 12:
        members = sequence(value_attr(variable, "members", []))
        if members:
            result["members"] = [describe_shader_variable(item, depth + 1) for item in members]
    return result


def get_bindpoint_mapping(pipe, stage):
    if not hasattr(pipe, "GetBindpointMapping"):
        return None
    try:
        return pipe.GetBindpointMapping(stage)
    except Exception:
        return None


def reflected_bind(reflected, mapping_items, ordinal):
    if ordinal < len(mapping_items):
        mapped = mapping_items[ordinal]
        if hasattr(mapped, "bind"):
            return int(mapped.bind)
    return int(value_attr(reflected, "fixedBindNumber", ordinal))


def describe_descriptor(descriptor):
    if descriptor is None:
        return None
    result = describe_object(
        descriptor,
        (
            "resource", "secondary", "view", "byteOffset", "byteSize", "type", "flags", "format",
            "firstMip", "numMips", "firstSlice", "numSlices", "minLODClamp",
        ),
    )
    for name in ("resource", "secondary"):
        if hasattr(descriptor, name):
            result[name] = rid_text(getattr(descriptor, name))
    return result


def describe_sampler_descriptor(descriptor):
    return describe_object(
        descriptor,
        (
            "type", "addressU", "addressV", "addressW", "filter", "compareFunction", "minLOD", "maxLOD",
            "mipBias", "maxAnisotropy", "borderColor", "seamlessCubeMap",
        ),
    )


def describe_stencil_face(face):
    return describe_object(
        face,
        (
            "function", "failOperation", "depthFailOperation", "passOperation", "compareMask",
            "writeMask", "reference",
        ),
    )


def describe_blend_equation(equation):
    return describe_object(equation, ("source", "destination", "operation"))


def describe_color_blend(blend):
    result = describe_object(
        blend,
        ("enabled", "logicOperationEnabled", "logicOperation", "writeMask"),
    )
    for name in ("colorBlend", "alphaBlend"):
        if hasattr(blend, name):
            result[name] = describe_blend_equation(getattr(blend, name))
    return result


def export_bound_resources(pipe, stage, reflection, field_name, getter_name, mapping_name):
    reflected = sequence(value_attr(reflection, field_name, []))
    if not reflected or not hasattr(pipe, getter_name):
        return []
    bound = sequence(getattr(pipe, getter_name)(stage))
    mapping = get_bindpoint_mapping(pipe, stage)
    mapping_items = sequence(value_attr(mapping, mapping_name, [])) if mapping is not None else []

    if mapping is not None:
        rows = []
        for ordinal, resource in enumerate(reflected):
            bind = reflected_bind(resource, mapping_items, ordinal)
            row = {
                "name": scalar(value_attr(resource, "name", "")),
                "reflection_ordinal": ordinal,
                "bind": bind,
                "bind_space": int(value_attr(resource, "fixedBindSetOrSpace", 0)),
                "descriptors": [],
            }
            if 0 <= bind < len(bound):
                legacy = bound[bind]
                for item in sequence(value_attr(legacy, "resources", [])):
                    row["descriptors"].append(describe_bound_view(item))
            rows.append(row)
        return rows

    rows_by_ordinal = {}
    for used in bound:
        access = value_attr(used, "access")
        ordinal = int(value_attr(access, "index", -1))
        if ordinal < 0 or ordinal >= len(reflected):
            continue
        resource = reflected[ordinal]
        row = rows_by_ordinal.setdefault(
            ordinal,
            {
                "name": scalar(value_attr(resource, "name", "")),
                "reflection_ordinal": ordinal,
                "bind": int(value_attr(resource, "fixedBindNumber", ordinal)),
                "bind_space": int(value_attr(resource, "fixedBindSetOrSpace", 0)),
                "descriptors": [],
            },
        )
        row["descriptors"].append(describe_descriptor(value_attr(used, "descriptor")))
    return [rows_by_ordinal[index] for index in sorted(rows_by_ordinal)]


def export_samplers(pipe, stage, reflection):
    reflected = sequence(value_attr(reflection, "samplers", []))
    if not reflected or not hasattr(pipe, "GetSamplers"):
        return []
    bound = sequence(pipe.GetSamplers(stage))
    mapping = get_bindpoint_mapping(pipe, stage)
    mapping_items = sequence(value_attr(mapping, "samplers", [])) if mapping is not None else []

    if mapping is not None:
        rows = []
        for ordinal, sampler in enumerate(reflected):
            bind = reflected_bind(sampler, mapping_items, ordinal)
            descriptors = []
            if 0 <= bind < len(bound):
                legacy = bound[bind]
                resources = sequence(value_attr(legacy, "resources", []))
                if resources:
                    descriptors = [describe_sampler_descriptor(item) for item in resources]
                else:
                    descriptors = [describe_sampler_descriptor(legacy)]
            rows.append({
                "name": scalar(value_attr(sampler, "name", "")),
                "reflection_ordinal": ordinal,
                "bind": bind,
                "bind_space": int(value_attr(sampler, "fixedBindSetOrSpace", 0)),
                "descriptors": descriptors,
            })
        return rows

    rows_by_ordinal = {}
    for used in bound:
        access = value_attr(used, "access")
        ordinal = int(value_attr(access, "index", -1))
        if ordinal < 0 or ordinal >= len(reflected):
            continue
        sampler = reflected[ordinal]
        row = rows_by_ordinal.setdefault(
            ordinal,
            {
                "name": scalar(value_attr(sampler, "name", "")),
                "reflection_ordinal": ordinal,
                "bind": int(value_attr(sampler, "fixedBindNumber", ordinal)),
                "bind_space": int(value_attr(sampler, "fixedBindSetOrSpace", 0)),
                "descriptors": [],
            },
        )
        # RenderDoc's descriptor-store API keeps the resource descriptor and
        # sampler payload separately on UsedDescriptor.
        descriptor = value_attr(used, "sampler", value_attr(used, "descriptor"))
        row["descriptors"].append(describe_sampler_descriptor(descriptor))
    return [rows_by_ordinal[index] for index in sorted(rows_by_ordinal)]


def bound_constant_blocks(pipe, stage, reflection):
    blocks = sequence(value_attr(reflection, "constantBlocks", []))
    mapping = get_bindpoint_mapping(pipe, stage)
    if mapping is not None:
        mapped = sequence(value_attr(mapping, "constantBlocks", []))
        rows = []
        for ordinal, block in enumerate(blocks):
            bind = reflected_bind(block, mapped, ordinal)
            try:
                bound = pipe.GetConstantBuffer(stage, bind, 0)
                rows.append((ordinal, block, bind, bound.resourceId, int(bound.byteOffset), int(bound.byteSize)))
            except Exception as exc:
                rows.append((ordinal, block, bind, rd.ResourceId.Null(), 0, 0, error_record(exc)))
        return rows

    rows = []
    for used in sequence(pipe.GetConstantBlocks(stage)):
        access = value_attr(used, "access")
        ordinal = int(value_attr(access, "index", -1))
        if ordinal < 0 or ordinal >= len(blocks):
            continue
        descriptor = value_attr(used, "descriptor")
        rows.append(
            (
                ordinal,
                blocks[ordinal],
                int(value_attr(blocks[ordinal], "fixedBindNumber", ordinal)),
                value_attr(descriptor, "resource", rd.ResourceId.Null()),
                int(value_attr(descriptor, "byteOffset", 0)),
                int(value_attr(descriptor, "byteSize", 0)),
            )
        )
    return rows


def decode_cbuffer(controller, pipe, stage, reflection, ordinal, bind, resource_id, byte_offset, byte_size):
    pipeline_object = pipe.GetGraphicsPipelineObject()
    shader = pipe.GetShader(stage)
    entry_point = pipe.GetShaderEntryPoint(stage)
    attempts = [ordinal]
    if bind != ordinal:
        attempts.append(bind)
    errors = []
    for slot in attempts:
        try:
            variables = controller.GetCBufferVariableContents(
                pipeline_object,
                shader,
                stage,
                entry_point,
                slot,
                resource_id,
                byte_offset,
                byte_size,
            )
            return {
                "slot_argument": slot,
                "variables": [describe_shader_variable(item) for item in sequence(variables)],
            }
        except Exception as exc:
            errors.append("slot {}: {}: {}".format(slot, type(exc).__name__, exc))
    return {"error": "; ".join(errors)}


def raw_float_views(data):
    raw = bytes(data)
    count = len(raw) // 4
    if count <= 0:
        return {"float4_rows": [], "uint4_rows": [], "int4_rows": []}
    usable = raw[: count * 4]
    floats = struct.unpack("<{}f".format(count), usable)
    uints = struct.unpack("<{}I".format(count), usable)
    ints = struct.unpack("<{}i".format(count), usable)
    return {
        "float4_rows": [list(floats[index:index + 4]) for index in range(0, count, 4)],
        "uint4_rows": [list(uints[index:index + 4]) for index in range(0, count, 4)],
        "int4_rows": [list(ints[index:index + 4]) for index in range(0, count, 4)],
    }


def export_constant_buffers(controller, pipe, stage, reflection, event_dir, buffer_descs, raw_cache):
    rows = []
    for bound in bound_constant_blocks(pipe, stage, reflection):
        if len(bound) == 7:
            ordinal, block, bind, resource_id, byte_offset, byte_size, bind_error = bound
        else:
            ordinal, block, bind, resource_id, byte_offset, byte_size = bound
            bind_error = None
        rid = rid_text(resource_id)
        reflected_size = int(value_attr(block, "byteSize", 0))
        size = int(byte_size) if int(byte_size) > 0 else reflected_size
        desc = buffer_descs.get(rid)
        if size <= 0 and desc is not None:
            size = max(0, int(value_attr(desc, "length", 0)) - int(byte_offset))
        row = {
            "stage": enum_text(stage),
            "name": scalar(value_attr(block, "name", "")),
            "reflection_ordinal": ordinal,
            "bind": bind,
            "bind_space": int(value_attr(block, "fixedBindSetOrSpace", 0)),
            "resource": rid,
            "byte_offset": int(byte_offset),
            "descriptor_size": int(byte_size),
            "reflected_size": reflected_size,
            "read_size": size,
            "reflection": describe_constant_block(block),
        }
        if bind_error is not None:
            row["binding_error"] = bind_error

        raw_data = None
        if SAVE_CONSTANT_BUFFER_RAW and not null_resource(resource_id) and size > 0:
            key = (rid, int(byte_offset), size)
            try:
                if key not in raw_cache:
                    raw_cache[key] = bytes(controller.GetBufferData(resource_id, int(byte_offset), size))
                raw_data = raw_cache[key]
                filename = "cb_{}_b{}_{}_o{}_s{}.bin".format(
                    safe_name(enum_text(stage)), bind, safe_name(row["name"]), byte_offset, size
                )
                row["raw"] = write_bytes(os.path.join(event_dir, "cbuffers", filename), raw_data)
                row["raw_views"] = raw_float_views(raw_data)
            except Exception as exc:
                row["raw_error"] = error_record(exc)

        try:
            row["decoded"] = decode_cbuffer(
                controller, pipe, stage, reflection, ordinal, bind, resource_id, int(byte_offset), size
            )
        except Exception as exc:
            row["decoded"] = error_record(exc)
        rows.append(row)
    return rows


def disassembly_targets(controller):
    if not hasattr(controller, "GetDisassemblyTargets"):
        return []
    try:
        return sequence(controller.GetDisassemblyTargets(True))
    except TypeError:
        return sequence(controller.GetDisassemblyTargets())


def export_shader(controller, pipe, stage, shader_dir):
    shader_id = pipe.GetShader(stage)
    reflection = pipe.GetShaderReflection(stage)
    if reflection is None or null_resource(shader_id):
        return None
    entry_point = pipe.GetShaderEntryPoint(stage)
    key = "{}_{}".format(safe_name(enum_text(stage)), safe_name(rid_text(shader_id)))
    json_path = os.path.join(shader_dir, key + ".json")
    result = {
        "stage": enum_text(stage),
        "resource_id": rid_text(shader_id),
        "entry_point": scalar(entry_point),
        "reflection_file": relpath(json_path),
    }
    if not os.path.exists(json_path):
        write_json(json_path, describe_reflection(reflection))

    if SAVE_SHADER_DISASSEMBLY:
        targets = disassembly_targets(controller)
        result["disassembly_targets"] = [scalar(item) for item in targets]
        attempts = targets[:1] if targets else [""]
        errors = []
        for target in attempts:
            try:
                text = controller.DisassembleShader(pipe.GetGraphicsPipelineObject(), reflection, target)
                path = os.path.join(shader_dir, key + ".txt")
                result["disassembly"] = write_text(path, text)
                result["disassembly"]["target"] = scalar(target)
                break
            except Exception as exc:
                errors.append("{}: {}: {}".format(target, type(exc).__name__, exc))
        if "disassembly" not in result:
            result["disassembly_error"] = "; ".join(errors)
    return result


def describe_d3d12_pipeline(controller):
    if not hasattr(controller, "GetD3D12PipelineState"):
        return {"error": "GetD3D12PipelineState unavailable"}
    try:
        state = controller.GetD3D12PipelineState()
    except Exception as exc:
        return error_record(exc)

    result = describe_object(state, ("pipelineResourceId", "rootSignatureResourceId"))
    rasterizer = value_attr(state, "rasterizer")
    rasterizer_state = value_attr(rasterizer, "state", rasterizer)
    result["rasterizer"] = describe_object(
        rasterizer_state,
        (
            "fillMode", "cullMode", "frontCCW", "depthBias", "depthBiasClamp", "slopeScaledDepthBias",
            "depthClip", "multisampleEnable", "antialiasedLines", "forcedSampleCount",
            "conservativeRasterization",
        ),
    )

    output_merger = value_attr(state, "outputMerger")
    result["output_merger"] = describe_object(
        output_merger,
        ("depthReadOnly", "stencilReadOnly", "depthTarget", "sampleMask"),
    ) or {}
    depth = value_attr(
        output_merger,
        "depthStencilState",
        value_attr(output_merger, "depthStencil"),
    )
    result["output_merger"]["depth_stencil"] = describe_object(
        depth,
        (
            "depthEnable", "depthWrites", "depthFunction", "depthBoundsEnable", "minDepthBounds",
            "maxDepthBounds", "stencilEnable",
        ),
    ) or {}
    if depth is not None:
        result["output_merger"]["depth_stencil"]["front_face"] = describe_stencil_face(value_attr(depth, "frontFace"))
        result["output_merger"]["depth_stencil"]["back_face"] = describe_stencil_face(value_attr(depth, "backFace"))

    blend_state = value_attr(output_merger, "blendState")
    result["output_merger"]["blend_state"] = describe_object(
        blend_state,
        ("alphaToCoverage", "independentBlend", "blendFactor"),
    ) or {}
    blends = sequence(value_attr(blend_state, "blends", value_attr(output_merger, "blends", [])))
    result["output_merger"]["blend_state"]["blends"] = [describe_color_blend(item) for item in blends]
    return result


def describe_pipeline(controller, pipe, texture_descs):
    result = {"topology": scalar(pipe.GetPrimitiveTopology())}
    calls = {
        "viewport": ("GetViewport", (0,), ("x", "y", "width", "height", "minDepth", "maxDepth")),
        "scissor": ("GetScissor", (0,), ("x", "y", "width", "height", "enabled")),
        "rasterizer": (
            "GetRasterizer", (),
            ("fillMode", "cullMode", "frontCCW", "depthBias", "depthBiasClamp", "slopeScaledDepthBias", "depthClip"),
        ),
        "depth_stencil": (
            "GetDepthStencil", (),
            ("depthEnable", "depthWrites", "depthFunction", "stencilEnable", "frontFace", "backFace"),
        ),
    }
    for key, (method_name, args, attrs) in calls.items():
        if not hasattr(pipe, method_name):
            result[key] = {"error": "{} unavailable".format(method_name)}
            continue
        try:
            result[key] = describe_object(getattr(pipe, method_name)(*args), attrs)
        except Exception as exc:
            result[key] = error_record(exc)

    try:
        result["color_blends"] = [describe_color_blend(item) for item in sequence(pipe.GetColorBlends())]
    except Exception as exc:
        result["color_blends"] = error_record(exc)

    outputs = []
    try:
        for index, target in enumerate(sequence(pipe.GetOutputTargets())):
            item = describe_bound_view(target)
            resource_id = value_attr(target, "resourceId", value_attr(target, "resource", rd.ResourceId.Null()))
            item["slot"] = index
            item["description"] = describe_resource_description(texture_descs.get(rid_text(resource_id)))
            outputs.append(item)
    except Exception as exc:
        outputs = [error_record(exc)]
    result["output_targets"] = outputs

    try:
        depth = pipe.GetDepthTarget()
        resource_id = value_attr(depth, "resourceId", value_attr(depth, "resource", rd.ResourceId.Null()))
        result["depth_target"] = describe_bound_view(depth)
        result["depth_target"]["description"] = describe_resource_description(texture_descs.get(rid_text(resource_id)))
    except Exception as exc:
        result["depth_target"] = error_record(exc)
    result["d3d12"] = describe_d3d12_pipeline(controller)
    d3d12 = result["d3d12"] if isinstance(result["d3d12"], dict) else {}
    if "error" in (result.get("rasterizer") or {}) and d3d12.get("rasterizer"):
        result["rasterizer"] = d3d12["rasterizer"]
        result["rasterizer"]["source"] = "D3D12PipelineState"
    d3d12_depth = d3d12.get("output_merger", {}).get("depth_stencil")
    if "error" in (result.get("depth_stencil") or {}) and d3d12_depth:
        result["depth_stencil"] = d3d12_depth
        result["depth_stencil"]["source"] = "D3D12PipelineState"
    return result


def export_event(controller, action, markers, buffer_descs, texture_descs, raw_cache, shader_dir):
    event_id = int(action.eventId)
    event_dir = os.path.join(OUTPUT_DIR, "E{}".format(event_id))
    os.makedirs(event_dir, exist_ok=True)
    controller.SetFrameEvent(event_id, True)
    pipe = controller.GetPipelineState()
    row = {
        "schema": "exvs-renderdoc-render-state-event/v1",
        "event_id": event_id,
        "name": action_name(action),
        "marker_path": list(markers),
        "draw": {
            "num_indices": int(action.numIndices),
            "num_instances": int(action.numInstances),
            "index_offset": int(action.indexOffset),
            "base_vertex": int(action.baseVertex),
            "vertex_offset": int(value_attr(action, "vertexOffset", 0)),
            "instance_offset": int(value_attr(action, "instanceOffset", 0)),
        },
        "pipeline": describe_pipeline(controller, pipe, texture_descs),
        "stages": [],
    }

    for stage_name in EXPORT_STAGES:
        stage = stage_value(stage_name)
        reflection = pipe.GetShaderReflection(stage)
        shader_row = export_shader(controller, pipe, stage, shader_dir)
        if reflection is None and shader_row is None:
            continue
        stage_row = {
            "stage": enum_text(stage),
            "shader": shader_row,
            "read_only_resources": [],
            "read_write_resources": [],
            "samplers": [],
            "constant_buffers": [],
        }
        try:
            stage_row["read_only_resources"] = export_bound_resources(
                pipe, stage, reflection, "readOnlyResources", "GetReadOnlyResources", "readOnlyResources"
            )
        except Exception as exc:
            stage_row["read_only_resources"] = error_record(exc)
        try:
            stage_row["read_write_resources"] = export_bound_resources(
                pipe, stage, reflection, "readWriteResources", "GetReadWriteResources", "readWriteResources"
            )
        except Exception as exc:
            stage_row["read_write_resources"] = error_record(exc)
        try:
            stage_row["samplers"] = export_samplers(pipe, stage, reflection)
        except Exception as exc:
            stage_row["samplers"] = error_record(exc)
        try:
            stage_row["constant_buffers"] = export_constant_buffers(
                controller, pipe, stage, reflection, event_dir, buffer_descs, raw_cache
            )
        except Exception as exc:
            stage_row["constant_buffers"] = {"error": str(exc), "traceback": traceback.format_exc()}
        row["stages"].append(stage_row)

    path = os.path.join(event_dir, "render_state.json")
    write_json(path, row)
    return {
        "event_id": event_id,
        "name": row["name"],
        "num_indices": row["draw"]["num_indices"],
        "path": relpath(path),
        "error_count": count_errors(row),
    }


def count_errors(value):
    if isinstance(value, dict):
        return (1 if "error" in value else 0) + sum(count_errors(item) for item in value.values())
    if isinstance(value, list):
        return sum(count_errors(item) for item in value)
    return 0


def capture_metadata(controller):
    result = {"renderdoc_version": scalar(value_attr(rd, "GetVersionString", lambda: "")())}
    for key, method_name in (("api_properties", "GetAPIProperties"), ("driver_information", "GetDriverInformation")):
        if not hasattr(controller, method_name):
            continue
        try:
            result[key] = describe_object(
                getattr(controller, method_name)(),
                (
                    "pipelineType", "localRenderer", "degraded", "shaderDebugging", "pixelHistory", "vendor",
                    "version", "renderer", "driverName",
                ),
            )
        except Exception as exc:
            result[key] = error_record(exc)
    return result


def run(controller):
    global _structured_file
    _structured_file = controller.GetStructuredFile()
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    shader_dir = os.path.join(OUTPUT_DIR, "shaders")
    os.makedirs(shader_dir, exist_ok=True)
    buffer_descs = {rid_text(item.resourceId): item for item in controller.GetBuffers()}
    texture_descs = {rid_text(item.resourceId): item for item in controller.GetTextures()}
    raw_cache = {}

    wanted = set(int(item) for item in EVENT_IDS)
    found = []
    for action, markers in walk(controller.GetRootActions()):
        if int(action.eventId) in wanted:
            found.append((action, markers))

    print("EXVS render state: {} requested, {} found".format(len(wanted), len(found)))
    events = []
    for current, (action, markers) in enumerate(found, 1):
        try:
            item = export_event(
                controller, action, markers, buffer_descs, texture_descs, raw_cache, shader_dir
            )
            events.append(item)
            print("[{}/{}] E{} state exported ({} recoverable errors)".format(
                current, len(found), action.eventId, item["error_count"]
            ))
        except Exception as exc:
            events.append({
                "event_id": int(action.eventId),
                "error": "{}: {}".format(type(exc).__name__, exc),
                "traceback": traceback.format_exc(),
            })
            print("E{} ERROR {}".format(action.eventId, exc))

    missing = sorted(wanted - {int(action.eventId) for action, _ in found})
    manifest = {
        "schema": "exvs-renderdoc-render-state/v1",
        "capture": capture_metadata(controller),
        "requested_event_ids": sorted(wanted),
        "missing_event_ids": missing,
        "events": events,
        "resource_counts": {"buffers": len(buffer_descs), "textures": len(texture_descs)},
    }
    manifest_path = os.path.join(OUTPUT_DIR, "render_state_manifest.json")
    write_json(manifest_path, manifest)
    print("EXVS render state complete: {}".format(manifest_path))


if "pyrenderdoc" not in globals() or "renderdoc" not in globals():
    raise RuntimeError("Run this file from qrenderdoc's Python Shell with a capture open")
pyrenderdoc.Replay().BlockInvoke(run)
