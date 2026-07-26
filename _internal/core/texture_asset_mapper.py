#!/usr/bin/env python3
import argparse
import csv
import hashlib
import json
import math
import shutil
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image

from texture_layout import PACKAGE_CATEGORIES, PACKAGE_ROOT_NAME


MAPPING_VERSION = 1
PACKAGE_LAYOUT_VERSION = 1
BODY_CATEGORIES = {
    (872, 960): "outgame_navigator",
    (572, 572): "combat_portrait",
    (260, 180): "ingame_navigator",
}
NAVIGATOR_CATEGORIES = {"outgame_navigator", "ingame_navigator"}
CSV_FIELDS = [
    "category",
    "package",
    "group",
    "status",
    "body_texture_id",
    "body_png",
    "body_width",
    "body_height",
    "overlay_family_count",
    "overlay_texture_count",
    "preview",
    "composition",
    "notes",
]
LAYER_FIELDS = [
    "category",
    "package",
    "group",
    "texture_id",
    "role",
    "family",
    "state_index",
    "embedded_index",
    "width",
    "height",
    "anchor_x",
    "anchor_y",
    "mapping_method",
    "coverage",
    "boundary",
    "match_rmse",
    "source_png",
    "replacement_path",
]


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def stable_catalog_signature(path):
    fields = (
        "package",
        "group_label",
        "embedded_index",
        "width",
        "height",
        "storage_format",
        "pixel_sha256",
    )
    digest = hashlib.sha256()
    digest.update(b"EXVSIB_TEXTURE_CATALOG_SIGNATURE_V1\n")
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            for field in fields:
                digest.update(row.get(field, "").encode("utf-8"))
                digest.update(b"\0")
            digest.update(b"\n")
    return digest.hexdigest()


def write_csv(path, fields, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(
            {field: row.get(field, "") for field in fields} for row in rows
        )


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def resolve_mapping_reference(mapping_root, mapping, key, relative_key, fallback):
    relative = mapping.get(relative_key)
    if relative:
        candidate = (mapping_root / relative).resolve()
        if candidate.exists():
            return candidate
    stored = Path(mapping[key])
    if stored.exists():
        return stored
    candidate = (mapping_root / fallback).resolve()
    return candidate


def row_dimensions(row):
    return int(row["width"]), int(row["height"])


def texture_id(row):
    return (
        f"{row['package']}/{row['group_label']}/"
        f"{int(row['embedded_index']):05d}"
    )


def replacement_relative(category, row):
    return str(
        Path(category)
        / row["package"]
        / row["group_label"]
        / Path(row["png_output"]).name
    )


def image_path(texture_root, row):
    return texture_root / Path(row["png_output"])


def load_rgba(path):
    with Image.open(path) as image:
        return image.convert("RGBA")


def validate_rgba(image, expected_size, path):
    if image.mode != "RGBA":
        raise ValueError(f"{path}: mode {image.mode} is not RGBA")
    if image.size != expected_size:
        raise ValueError(
            f"{path}: size {image.size} does not match {expected_size}"
        )


def integral(array):
    return np.pad(
        array.cumsum(axis=0, dtype=np.int32).cumsum(axis=1, dtype=np.int32),
        ((1, 0), (1, 0)),
    )


def locate_navigator_slot(image, width, height, y_ratio):
    rgba = np.asarray(image, dtype=np.uint8)
    alpha = rgba[:, :, 3]
    gap = (alpha <= 16).astype(np.int32)
    opaque = (alpha > 16).astype(np.int32)
    canvas_height, canvas_width = alpha.shape
    if width >= canvas_width - 2 or height >= canvas_height - 2:
        return None

    gap_integral = integral(gap)
    coverage = (
        gap_integral[height:, width:]
        - gap_integral[:-height, width:]
        - gap_integral[height:, :-width]
        + gap_integral[:-height, :-width]
    ) / (width * height)
    row_integral = np.pad(
        opaque.cumsum(axis=1, dtype=np.int32), ((0, 0), (1, 0))
    )
    column_integral = np.pad(
        opaque.cumsum(axis=0, dtype=np.int32), ((1, 0), (0, 0))
    )

    max_y = min(
        canvas_height - height - 1,
        int(canvas_height * y_ratio) - height,
    )
    xs = np.arange(1, canvas_width - width - 1)
    ys = np.arange(1, max_y + 1)
    if len(xs) == 0 or len(ys) == 0:
        return None
    y_grid, x_grid = np.ix_(ys, xs)
    boundary = (
        row_integral[y_grid - 1, x_grid + width]
        - row_integral[y_grid - 1, x_grid]
        + row_integral[y_grid + height, x_grid + width]
        - row_integral[y_grid + height, x_grid]
        + column_integral[y_grid + height, x_grid - 1]
        - column_integral[y_grid, x_grid - 1]
        + column_integral[y_grid + height, x_grid + width]
        - column_integral[y_grid, x_grid + width]
    ) / (2 * width + 2 * height)
    score = coverage[np.ix_(ys, xs)] + 0.5 * boundary
    best = np.unravel_index(np.argmax(score), score.shape)
    y = int(ys[best[0]])
    x = int(xs[best[1]])
    return {
        "x": x,
        "y": y,
        "coverage": float(coverage[y, x]),
        "boundary": float(boundary[best]),
        "score": float(score[best]),
        "method": "alpha_gap_rectangle",
    }


def fft_correlate_valid(image, template):
    image_height, image_width = image.shape
    template_height, template_width = template.shape
    shape = (
        1 << (image_height + template_height - 2).bit_length(),
        1 << (image_width + template_width - 2).bit_length(),
    )
    result = np.fft.irfft2(
        np.fft.rfft2(image, shape)
        * np.fft.rfft2(template[::-1, ::-1], shape),
        shape,
    )
    return result[
        template_height - 1 : image_height,
        template_width - 1 : image_width,
    ]


def combat_match_family(body_image, overlay_images):
    body = np.asarray(body_image, dtype=np.float32)
    overlays = [
        np.asarray(overlay_image, dtype=np.float32)
        for overlay_image in overlay_images
    ]
    overlay = overlays[0]
    overlay_stack = np.stack(overlays)
    stable = (
        (overlay_stack.max(axis=0) - overlay_stack.min(axis=0)).max(axis=2)
        <= 12
    )
    overlay_height, overlay_width = overlay.shape[:2]
    band = max(4, min(12, min(overlay_height, overlay_width) // 8))
    border = np.zeros((overlay_height, overlay_width), dtype=bool)
    border[:band, :] = True
    border[-band:, :] = True
    border[:, :band] = True
    border[:, -band:] = True
    weight = (overlay[:, :, 3] / 255.0) * stable * border
    weight_sum = float(weight.sum())
    if weight_sum < 1:
        raise ValueError("combat overlay family has no stable border pixels")

    error = fft_correlate_valid(
        np.sum(body * body, axis=2), weight
    )
    for channel in range(4):
        error -= 2 * fft_correlate_valid(
            body[:, :, channel], overlay[:, :, channel] * weight
        )
    error += float((np.sum(overlay * overlay, axis=2) * weight).sum())
    error = np.maximum(error, 0)

    min_y = 100 if overlay_height > 200 else 0
    max_y = int(body.shape[0] * 0.72) - overlay_height
    if max_y < 0:
        return None
    if min_y:
        error[:min_y, :] = np.inf
    if max_y + 1 < error.shape[0]:
        error[max_y + 1 :, :] = np.inf
    y, x = np.unravel_index(np.argmin(error), error.shape)
    border_rmse = math.sqrt(float(error[y, x]) / (weight_sum * 4))
    full_rmse = []
    for candidate in overlays:
        candidate_weight = candidate[:, :, 3] / 255.0
        candidate_weight_sum = float(candidate_weight.sum())
        patch = body[y : y + overlay_height, x : x + overlay_width]
        difference = patch - candidate
        full_rmse.append(
            math.sqrt(
                float(
                    (
                        difference
                        * difference
                        * candidate_weight[:, :, None]
                    ).sum()
                )
                / (candidate_weight_sum * 4)
            )
        )
    baseline_index = min(range(len(full_rmse)), key=full_rmse.__getitem__)
    return {
        "x": int(x),
        "y": int(y),
        "rmse": border_rmse,
        "full_rmse": full_rmse[baseline_index],
        "baseline_index": baseline_index,
        "stable_border_pixels": weight_sum,
        "method": "stable_border_rgba_ssd",
    }


def classify_groups(rows):
    groups = defaultdict(list)
    for row in rows:
        row = dict(row)
        row["group"] = row["group_label"]
        groups[(row["package"], row["group_label"])].append(row)

    classified = []
    exceptions = []
    for (package, group), group_rows in sorted(groups.items()):
        targets = defaultdict(list)
        for row in group_rows:
            dimensions = row_dimensions(row)
            if dimensions in BODY_CATEGORIES:
                targets[BODY_CATEGORIES[dimensions]].append(row)
        if not targets:
            continue

        for category, bodies in targets.items():
            if category == "combat_portrait":
                valid_bodies = [
                    row for row in bodies if row.get("storage_format") == "bc7"
                ]
                companions = [
                    row
                    for row in group_rows
                    if row not in bodies and row_dimensions(row) != (8, 8)
                ]
                dimension_counts = Counter(
                    row_dimensions(row) for row in companions
                )
                structurally_valid = (
                    len(valid_bodies) == 1
                    and 1 <= len(dimension_counts) <= 2
                    and all(count >= 2 for count in dimension_counts.values())
                    and all(
                        width < 400 and height < 400
                        for width, height in dimension_counts
                    )
                )
            elif category == "ingame_navigator":
                valid_bodies = [
                    row for row in bodies if row.get("storage_format") == "bc7"
                ]
                structurally_valid = (
                    len(valid_bodies) == 1
                    and int(valid_bodies[0]["embedded_index"]) == 0
                )
            else:
                valid_bodies = [
                    row for row in bodies if row.get("storage_format") == "bc7"
                ]
                structurally_valid = len(valid_bodies) == 1

            if structurally_valid:
                classified.append(
                    {
                        "category": category,
                        "package": package,
                        "group": group,
                        "body": valid_bodies[0],
                        "rows": group_rows,
                    }
                )
            else:
                exceptions.append(
                    {
                        "package": package,
                        "group": group,
                        "candidate_category": category,
                        "body_count": len(bodies),
                        "bc7_body_count": len(valid_bodies),
                        "group_texture_count": len(group_rows),
                        "reason": "target dimensions found but group structure is ambiguous",
                    }
                )
    return classified, exceptions


def overlay_families(item):
    body = item["body"]
    families = defaultdict(list)
    for row in item["rows"]:
        if row is body or row_dimensions(row) == (8, 8):
            continue
        width, height = row_dimensions(row)
        if width * height < 64:
            continue
        if width >= int(body["width"]) * 0.7:
            continue
        if height >= int(body["height"]) * 0.5:
            continue
        families[(width, height)].append(row)
    return families


def layer_record(item, row, role, family, state_index, anchor, method):
    return {
        "category": item["category"],
        "package": item["package"],
        "group": item["group"],
        "texture_id": texture_id(row),
        "role": role,
        "family": family,
        "state_index": state_index,
        "embedded_index": int(row["embedded_index"]),
        "width": int(row["width"]),
        "height": int(row["height"]),
        "anchor_x": anchor["x"],
        "anchor_y": anchor["y"],
        "mapping_method": method,
        "coverage": anchor.get("coverage", ""),
        "boundary": anchor.get("boundary", ""),
        "match_rmse": anchor.get("rmse", ""),
        "source_png": row["png_output"],
        "replacement_path": replacement_relative(item["category"], row),
    }


def build_composition(item, texture_root):
    body = item["body"]
    body_path = image_path(texture_root, body)
    body_image = load_rgba(body_path)
    validate_rgba(
        body_image,
        (int(body["width"]), int(body["height"])),
        body_path,
    )
    families = overlay_families(item)
    mapped_families = []
    warnings = []

    if item["category"] in NAVIGATOR_CATEGORIES:
        y_ratio = 0.6 if item["category"] == "outgame_navigator" else 0.95
        located = []
        for dimensions, family_rows in families.items():
            anchor = locate_navigator_slot(
                body_image, dimensions[0], dimensions[1], y_ratio
            )
            if (
                anchor is None
                or anchor["coverage"] < 0.70
                or anchor["boundary"] < 0.45
            ):
                warnings.append(
                    f"unmapped overlay family {dimensions[0]}x{dimensions[1]}"
                )
                continue
            located.append((dimensions, family_rows, anchor))
        located.sort(key=lambda value: value[0][0] * value[0][1])
        for index, (dimensions, family_rows, anchor) in enumerate(located):
            if len(located) >= 2:
                role = "mouth" if index == 0 else "face_eyes"
            else:
                role = "expression"
            mapped_families.append(
                {
                    "family": role,
                    "role": role,
                    "dimensions": list(dimensions),
                    "anchor": anchor,
                    "baseline_texture_id": texture_id(family_rows[0]),
                    "states": family_rows,
                }
            )
    else:
        matched = []
        for dimensions, family_rows in families.items():
            family_images = []
            for row in family_rows:
                overlay_path = image_path(texture_root, row)
                overlay_image = load_rgba(overlay_path)
                validate_rgba(overlay_image, dimensions, overlay_path)
                family_images.append(overlay_image)
            anchor = combat_match_family(body_image, family_images)
            if anchor is None:
                warnings.append(
                    f"unmapped mouth family {dimensions[0]}x{dimensions[1]}"
                )
                continue
            baseline = family_rows[anchor.pop("baseline_index")]
            matched.append((anchor["x"], dimensions, family_rows, baseline, anchor))
        matched.sort(key=lambda value: value[0])
        for index, (_, dimensions, family_rows, baseline, anchor) in enumerate(
            matched, 1
        ):
            role = "mouth" if len(matched) == 1 else f"mouth_{index}"
            mapped_families.append(
                {
                    "family": role,
                    "role": role,
                    "dimensions": list(dimensions),
                    "anchor": anchor,
                    "baseline_texture_id": texture_id(baseline),
                    "states": family_rows,
                }
            )
            if anchor["rmse"] > 60:
                warnings.append(
                    f"{role} stable-border RMSE is high: "
                    f"{anchor['rmse']:.2f}"
                )

    body_layer = {
        "texture_id": texture_id(body),
        "role": "body",
        "embedded_index": int(body["embedded_index"]),
        "width": int(body["width"]),
        "height": int(body["height"]),
        "source_png": body["png_output"],
        "replacement_path": replacement_relative(item["category"], body),
    }
    family_output = []
    flat_layers = []
    for family in mapped_families:
        states = []
        for state_index, row in enumerate(family["states"]):
            state = layer_record(
                item,
                row,
                family["role"],
                family["family"],
                state_index,
                family["anchor"],
                family["anchor"]["method"],
            )
            states.append(state)
            flat_layers.append(state)
        family_output.append(
            {
                "family": family["family"],
                "role": family["role"],
                "dimensions": family["dimensions"],
                "anchor": family["anchor"],
                "baseline_texture_id": family["baseline_texture_id"],
                "states": states,
            }
        )

    return {
        "mapping_version": MAPPING_VERSION,
        "category": item["category"],
        "package": item["package"],
        "group": item["group"],
        "canvas": {
            "width": int(body["width"]),
            "height": int(body["height"]),
        },
        "body": body_layer,
        "families": family_output,
        "draw_order": ["body", *[family["role"] for family in family_output]],
        "blend": "straight_alpha_source_over",
        "warnings": warnings,
    }, flat_layers


def select_image(texture_root, replacement_root, layer):
    replacement = replacement_root / Path(layer["replacement_path"])
    source = texture_root / Path(layer["source_png"])
    selected = replacement if replacement.is_file() else source
    image = load_rgba(selected)
    validate_rgba(
        image, (int(layer["width"]), int(layer["height"])), selected
    )
    return image, selected


def render_composition(
    composition, texture_root, replacement_root=None, max_size=None
):
    if replacement_root is None:
        replacement_root = Path("__no_replacements__")
    body_image, _ = select_image(
        texture_root, replacement_root, composition["body"]
    )
    canvas = body_image.copy()
    for family in composition["families"]:
        baseline_id = family["baseline_texture_id"]
        state = next(
            item for item in family["states"] if item["texture_id"] == baseline_id
        )
        overlay, _ = select_image(texture_root, replacement_root, state)
        anchor = family["anchor"]
        canvas.alpha_composite(overlay, (int(anchor["x"]), int(anchor["y"])))
    if max_size:
        canvas.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
    return canvas


def build_command(args):
    texture_root = Path(args.texture_root)
    catalog_path = texture_root / "inventory" / "textures.csv"
    output_root = Path(args.output)
    if not catalog_path.is_file():
        raise FileNotFoundError(f"missing texture catalog: {catalog_path}")
    if output_root.exists() and args.force:
        resolved_output = output_root.resolve()
        resolved_workspace = Path.cwd().resolve()
        if (
            resolved_output == resolved_workspace
            or resolved_workspace not in resolved_output.parents
        ):
            raise ValueError(f"refusing to clear unsafe output: {output_root}")
        replacements = output_root / "replacements"
        replacement_backup = (
            output_root.parent / f".{output_root.name}.replacements-backup"
        )
        if replacement_backup.exists():
            raise ValueError(
                f"stale replacement backup exists: {replacement_backup}"
            )
        if replacements.is_dir():
            shutil.move(replacements, replacement_backup)
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    if "replacement_backup" in locals() and replacement_backup.is_dir():
        shutil.move(replacement_backup, output_root / "replacements")

    rows = read_csv(catalog_path)
    classified, exceptions = classify_groups(rows)
    compositions = []
    group_rows = []
    layer_rows = []
    errors = []
    for index, item in enumerate(classified, 1):
        try:
            composition, layers = build_composition(item, texture_root)
            relative_composition = (
                Path("projects")
                / item["category"]
                / item["package"]
                / item["group"]
                / "composition.json"
            )
            write_json(output_root / relative_composition, composition)
            preview_relative = (
                Path("previews")
                / item["category"]
                / f"{item['package']}_{item['group']}.png"
            )
            preview = render_composition(
                composition, texture_root, max_size=args.preview_size
            )
            preview_path = output_root / preview_relative
            preview_path.parent.mkdir(parents=True, exist_ok=True)
            preview.save(preview_path, "PNG", optimize=False)
            compositions.append(
                {
                    "category": item["category"],
                    "package": item["package"],
                    "group": item["group"],
                    "composition": str(relative_composition),
                }
            )
            body = item["body"]
            layer_rows.append(
                {
                    "category": item["category"],
                    "package": item["package"],
                    "group": item["group"],
                    "texture_id": texture_id(body),
                    "role": "body",
                    "family": "body",
                    "state_index": 0,
                    "embedded_index": int(body["embedded_index"]),
                    "width": int(body["width"]),
                    "height": int(body["height"]),
                    "anchor_x": 0,
                    "anchor_y": 0,
                    "mapping_method": "canvas",
                    "source_png": body["png_output"],
                    "replacement_path": replacement_relative(
                        item["category"], body
                    ),
                }
            )
            layer_rows.extend(layers)
            group_rows.append(
                {
                    "category": item["category"],
                    "package": item["package"],
                    "group": item["group"],
                    "status": (
                        "mapped"
                        if not composition["warnings"]
                        else "mapped_with_warning"
                    ),
                    "body_texture_id": texture_id(body),
                    "body_png": body["png_output"],
                    "body_width": body["width"],
                    "body_height": body["height"],
                    "overlay_family_count": len(composition["families"]),
                    "overlay_texture_count": sum(
                        len(family["states"])
                        for family in composition["families"]
                    ),
                    "preview": str(preview_relative),
                    "composition": str(relative_composition),
                    "notes": "; ".join(composition["warnings"]),
                }
            )
        except Exception as exc:
            errors.append(
                {
                    "package": item["package"],
                    "group": item["group"],
                    "candidate_category": item["category"],
                    "body_count": 1,
                    "bc7_body_count": 1,
                    "group_texture_count": len(item["rows"]),
                    "reason": f"{type(exc).__name__}: {exc}",
                }
            )
        if index % 100 == 0:
            print(f"mapping={index}/{len(classified)}", flush=True)

    exceptions.extend(errors)
    write_csv(output_root / "groups.csv", CSV_FIELDS, group_rows)
    write_csv(output_root / "layers.csv", LAYER_FIELDS, layer_rows)
    exception_fields = [
        "package",
        "group",
        "candidate_category",
        "body_count",
        "bc7_body_count",
        "group_texture_count",
        "reason",
    ]
    write_csv(output_root / "exceptions.csv", exception_fields, exceptions)
    category_counts = Counter(row["category"] for row in group_rows)
    report = {
        "mapping_version": MAPPING_VERSION,
        "created_utc": utc_now(),
        "texture_root": str(texture_root.resolve()),
        "texture_root_relative": str(
            Path("..") / Path(texture_root.resolve()).name
        ),
        "source_catalog": str(catalog_path.resolve()),
        "source_catalog_relative": str(
            Path("..")
            / Path(texture_root.resolve()).name
            / "inventory"
            / "textures.csv"
        ),
        "source_catalog_sha256": sha256_file(catalog_path),
        "source_catalog_signature": stable_catalog_signature(catalog_path),
        "group_count": len(group_rows),
        "layer_count": len(layer_rows),
        "category_counts": dict(sorted(category_counts.items())),
        "exception_count": len(exceptions),
        "mapped_with_warning_count": sum(
            row["status"] == "mapped_with_warning" for row in group_rows
        ),
        "groups_manifest": "groups.csv",
        "layers_manifest": "layers.csv",
        "exceptions_manifest": "exceptions.csv",
        "projects_directory": "projects",
        "previews_directory": "previews",
        "replacement_directory": "replacements",
        "package_layout_version": PACKAGE_LAYOUT_VERSION,
        "package_directory_template": (
            f"{PACKAGE_ROOT_NAME}/{{category}}/{{package}}"
        ),
        "package_category_directories": {
            category: category for category in PACKAGE_CATEGORIES
        },
        "compositions": compositions,
    }
    write_json(output_root / "mapping.json", report)
    summary = {
        key: value for key, value in report.items() if key != "compositions"
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if errors else 0


def render_command(args):
    mapping_path = Path(args.mapping)
    mapping_root = mapping_path.parent
    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    texture_root = resolve_mapping_reference(
        mapping_root,
        mapping,
        "texture_root",
        "texture_root_relative",
        "../all-textures",
    )
    replacement_root = Path(args.replacements)
    output_root = Path(args.output)
    output_root.mkdir(parents=True, exist_ok=True)
    rendered = 0
    for item in mapping["compositions"]:
        if args.category and item["category"] != args.category:
            continue
        if args.package and item["package"] != args.package:
            continue
        if args.group and item["group"] != args.group:
            continue
        composition = json.loads(
            (mapping_root / item["composition"]).read_text(encoding="utf-8")
        )
        preview = render_composition(
            composition,
            texture_root,
            replacement_root=replacement_root,
            max_size=args.preview_size,
        )
        output = (
            output_root
            / item["category"]
            / f"{item['package']}_{item['group']}.png"
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        preview.save(output, "PNG", optimize=False)
        rendered += 1
        if rendered % 100 == 0:
            print(f"rendered={rendered}", flush=True)
    print(f"rendered={rendered} output={output_root}")
    return 0


def validate_command(args):
    mapping_path = Path(args.mapping)
    mapping_root = mapping_path.parent
    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    texture_root = resolve_mapping_reference(
        mapping_root,
        mapping,
        "texture_root",
        "texture_root_relative",
        "../all-textures",
    )
    problems = []
    warnings = []
    texture_ids = set()
    replacement_paths = set()
    family_counts = Counter()
    preview_count = 0
    source_count = 0
    groups = read_csv(mapping_root / mapping["groups_manifest"])
    group_index = {
        (row["category"], row["package"], row["group"]): row
        for row in groups
    }

    catalog_path = resolve_mapping_reference(
        mapping_root,
        mapping,
        "source_catalog",
        "source_catalog_relative",
        "../all-textures/inventory/textures.csv",
    )
    if not catalog_path.is_file():
        problems.append(f"missing source catalog: {catalog_path}")
    elif mapping.get("source_catalog_signature"):
        if (
            stable_catalog_signature(catalog_path)
            != mapping["source_catalog_signature"]
        ):
            problems.append(
                "stable source catalog signature differs from mapping.json"
            )
    elif sha256_file(catalog_path) != mapping["source_catalog_sha256"]:
        problems.append("source catalog SHA256 differs from mapping.json")

    for item in mapping["compositions"]:
        composition_path = mapping_root / item["composition"]
        if not composition_path.is_file():
            problems.append(f"missing composition: {composition_path}")
            continue
        composition = json.loads(composition_path.read_text(encoding="utf-8"))
        prefix = (
            f"{composition['category']}/"
            f"{composition['package']}/{composition['group']}"
        )
        canvas = (
            int(composition["canvas"]["width"]),
            int(composition["canvas"]["height"]),
        )
        expected_category = BODY_CATEGORIES.get(canvas)
        if expected_category != composition["category"]:
            problems.append(
                f"{prefix}: canvas {canvas} conflicts with category"
            )

        layers = [composition["body"]]
        for family in composition["families"]:
            family_counts[
                f"{composition['category']}:{family['family']}"
            ] += 1
            anchor = family["anchor"]
            width, height = map(int, family["dimensions"])
            x, y = int(anchor["x"]), int(anchor["y"])
            if x < 0 or y < 0 or x + width > canvas[0] or y + height > canvas[1]:
                problems.append(
                    f"{prefix}/{family['family']}: anchor is outside canvas"
                )
            baseline_ids = {
                state["texture_id"] for state in family["states"]
            }
            if family["baseline_texture_id"] not in baseline_ids:
                problems.append(
                    f"{prefix}/{family['family']}: baseline state is missing"
                )
            layers.extend(family["states"])

        for layer in layers:
            layer_id = layer["texture_id"]
            replacement = layer["replacement_path"].replace("\\", "/")
            if layer_id in texture_ids:
                problems.append(f"duplicate texture ID: {layer_id}")
            texture_ids.add(layer_id)
            if replacement in replacement_paths:
                problems.append(f"duplicate replacement path: {replacement}")
            replacement_paths.add(replacement)
            source = texture_root / Path(layer["source_png"])
            if not source.is_file():
                problems.append(f"{prefix}: missing source PNG {source}")
                continue
            source_count += 1
            try:
                with Image.open(source) as image:
                    expected_size = (
                        int(layer["width"]),
                        int(layer["height"]),
                    )
                    if image.mode != "RGBA" or image.size != expected_size:
                        problems.append(
                            f"{prefix}: invalid source {source} "
                            f"({image.mode}, {image.size})"
                        )
            except OSError as exc:
                problems.append(f"{prefix}: unreadable source {source}: {exc}")

        group_row = group_index.get(
            (
                composition["category"],
                composition["package"],
                composition["group"],
            )
        )
        if group_row is None:
            problems.append(f"{prefix}: missing groups.csv row")
            continue
        preview = mapping_root / group_row["preview"]
        if not preview.is_file() and mapping.get("previews_mode") != "lazy":
            problems.append(f"{prefix}: missing preview {preview}")
        elif preview.is_file():
            preview_count += 1
            try:
                with Image.open(preview) as image:
                    if image.mode != "RGBA":
                        problems.append(
                            f"{prefix}: preview mode is {image.mode}, not RGBA"
                        )
                    if max(image.size) > int(args.preview_size):
                        problems.append(
                            f"{prefix}: preview exceeds {args.preview_size}px"
                        )
            except OSError as exc:
                problems.append(f"{prefix}: unreadable preview: {exc}")
        warnings.extend(
            f"{prefix}: {warning}" for warning in composition["warnings"]
        )

    layers = read_csv(mapping_root / mapping["layers_manifest"])
    exceptions = read_csv(mapping_root / mapping["exceptions_manifest"])
    if len(groups) != mapping["group_count"]:
        problems.append("groups.csv count differs from mapping.json")
    if len(layers) != mapping["layer_count"]:
        problems.append("layers.csv count differs from mapping.json")
    if len(exceptions) != mapping["exception_count"]:
        problems.append("exceptions.csv count differs from mapping.json")
    if len(mapping["compositions"]) != mapping["group_count"]:
        problems.append("composition count differs from mapping.json")
    if len(texture_ids) != mapping["layer_count"]:
        problems.append(
            "validated unique layer count differs from mapping.json"
        )

    report = {
        "mapping_version": mapping["mapping_version"],
        "validated_utc": utc_now(),
        "mapping": str(mapping_path.resolve()),
        "status": "ok" if not problems else "failed",
        "group_count": len(mapping["compositions"]),
        "layer_count": len(texture_ids),
        "source_png_count": source_count,
        "preview_count": preview_count,
        "category_counts": mapping["category_counts"],
        "family_counts": dict(sorted(family_counts.items())),
        "warning_count": len(warnings),
        "warnings": warnings,
        "exception_count": len(exceptions),
        "exceptions": exceptions,
        "problem_count": len(problems),
        "problems": problems,
    }
    output = Path(args.output) if args.output else mapping_root / "validation.json"
    write_json(output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if problems else 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Classify EXVSIB character textures, recover overlay anchors, "
            "and render deterministic replacement previews."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser("build")
    build_parser.add_argument(
        "--texture-root", default="patch-edit/all-textures"
    )
    build_parser.add_argument(
        "--output", default="patch-edit/asset-mapping"
    )
    build_parser.add_argument("--preview-size", type=int, default=480)
    build_parser.add_argument("--force", action="store_true")
    build_parser.set_defaults(func=build_command)

    render_parser = subparsers.add_parser("render")
    render_parser.add_argument(
        "--mapping", default="patch-edit/asset-mapping/mapping.json"
    )
    render_parser.add_argument(
        "--replacements", default="patch-edit/asset-mapping/replacements"
    )
    render_parser.add_argument(
        "--output", default="patch-edit/asset-mapping/replacement-previews"
    )
    render_parser.add_argument("--preview-size", type=int, default=480)
    render_parser.add_argument("--category")
    render_parser.add_argument("--package")
    render_parser.add_argument("--group")
    render_parser.set_defaults(func=render_command)

    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument(
        "--mapping", default="patch-edit/asset-mapping/mapping.json"
    )
    validate_parser.add_argument("--preview-size", type=int, default=480)
    validate_parser.add_argument("--output")
    validate_parser.set_defaults(func=validate_command)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (FileNotFoundError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
