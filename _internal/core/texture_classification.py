"""Package-level rules for image types that do not use a fixed body canvas."""

from collections import Counter, defaultdict


SPECIAL_PACKAGE_CATEGORIES = {
    "0X49235031": "select_mobile_suit_thumbnail",
}

SINGLE_TEXTURE_DIMENSIONS = (
    ((458, 680), "favorite_mobile_suit"),
    ((1020, 680), "select_navigator"),
    ((1920, 1080), "select_mobile_suit"),
    ((840, 432), "match_mobile_suit"),
)

AWAKENING_REQUIRED = Counter(
    {
        (800, 1000, "bc7"): 3,
        (760, 1000, "bc7"): 1,
        (1064, 1180, "bc7"): 1,
        (320, 160, "bc7"): 1,
    }
)
AWAKENING_MIN_ANCHORS = 5


def row_dimensions(row):
    return int(row["width"]), int(row["height"])


def rows_by_group(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[row["group_label"]].append(row)
    return groups


def awakening_common_group(rows):
    """Return the shared-effect group label for a likely awakening package."""
    groups = rows_by_group(rows)
    if not 2 <= len(groups) <= 4:
        return None
    best = None
    for label, group_rows in groups.items():
        observed = Counter(
            (
                int(row["width"]),
                int(row["height"]),
                row.get("storage_format", "").lower(),
            )
            for row in group_rows
        )
        anchor_count = sum(
            min(observed[key], count)
            for key, count in AWAKENING_REQUIRED.items()
        )
        if anchor_count < AWAKENING_MIN_ANCHORS:
            continue
        candidate = (anchor_count, len(group_rows), label)
        if best is None or candidate > best:
            best = candidate
    return best[2] if best else None


def classify_package_assets(package, rows):
    """Return one package category and the rows exposed by the viewer."""
    package = package.upper()
    special = SPECIAL_PACKAGE_CATEGORIES.get(package)
    if special:
        selected = [row for row in rows if row_dimensions(row) != (8, 8)]
        return {
            "category": special,
            "method": "package_id",
            "rows": selected,
            "common_group": None,
        }

    common_group = awakening_common_group(rows)
    if common_group is not None:
        selected = [
            row
            for row in rows
            if row["group_label"] != common_group
            and row_dimensions(row) != (8, 8)
        ]
        return {
            "category": "awakening",
            "method": "awakening_structure",
            "rows": selected,
            "common_group": common_group,
        }

    for dimensions, category in SINGLE_TEXTURE_DIMENSIONS:
        selected = [row for row in rows if row_dimensions(row) == dimensions]
        if selected:
            return {
                "category": category,
                "method": "exact_dimensions",
                "rows": selected,
                "common_group": None,
            }
    return None
