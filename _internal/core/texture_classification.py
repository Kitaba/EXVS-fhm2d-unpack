"""Fast package-level classification for extracted texture assets."""

from collections import Counter, defaultdict


SPECIAL_PACKAGE_CATEGORIES = {
    "0X49235031": "select_mobile_suit_thumbnail",
}

# These names are part of the texture metadata and are more reliable than
# canvas size alone.  The dimensions remain a guard against unrelated records
# that happen to reuse a prefix.
NAME_RULES_BY_DIMENSION = {
    (458, 680): (("46XTMS_CARD_", "favorite_mobile_suit"),),
    (1020, 680): (("46XTVS_P_", "select_navigator"),),
    (1920, 1080): (("46XTMS_MS_", "select_mobile_suit"),),
    (840, 432): (
        ("46XTMS_STICKER_", "match_mobile_suit"),
        ("46XTSTICKER_FRM_", "match_card_frame"),
        ("46XTSTICKER_BG_", "match_card_background"),
        ("46XTSTICKER_EMB_", "match_symbol"),
    ),
}

AWAKENING_REQUIRED = Counter(
    {
        (800, 1000, "bc7"): 3,
        (760, 1000, "bc7"): 1,
        (1064, 1180, "bc7"): 1,
        (320, 160, "bc7"): 1,
    }
)
AWAKENING_MIN_ANCHORS = 5

MATCH_BACKGROUND_FULL_SIZE = (840, 432)
MATCH_BACKGROUND_FRAME_SIZE = (420, 216)
MATCH_BACKGROUND_MIN_FRAMES = 8
MATCH_MOBILE_SUIT_PORTRAIT_SIZE = (960, 900)


def row_dimensions(row):
    return int(row["width"]), int(row["height"])


def named_category(name, dimensions):
    """Return a metadata-name category without regular-expression overhead."""
    for prefix, category in NAME_RULES_BY_DIMENSION.get(dimensions, ()):
        if name.startswith(prefix):
            return category
    return None


def package_facts(rows):
    """Build every classification index in one sequential pass."""
    ordered_rows = []
    visible_rows = []
    by_group = defaultdict(list)
    group_signatures = defaultdict(Counter)
    by_dimensions = defaultdict(list)
    named_rows = defaultdict(list)
    dimension_counts = Counter()
    all_generic_images = True

    for row in rows:
        dimensions = row_dimensions(row)
        group = row["group_label"]
        storage_format = row.get("storage_format", "").lower()
        name = row.get("embedded_name", "").upper()

        ordered_rows.append(row)
        if dimensions != (8, 8):
            visible_rows.append(row)
        by_group[group].append(row)
        group_signatures[group][(*dimensions, storage_format)] += 1
        by_dimensions[dimensions].append(row)
        dimension_counts[dimensions] += 1
        all_generic_images = all_generic_images and name.startswith(
            "46XTIMG-"
        )

        category = named_category(name, dimensions)
        if category is not None:
            named_rows[category].append(row)

    return {
        "rows": ordered_rows,
        "visible_rows": visible_rows,
        "by_group": by_group,
        "group_signatures": group_signatures,
        "by_dimensions": by_dimensions,
        "named_rows": named_rows,
        "dimension_counts": dimension_counts,
        "all_generic_images": all_generic_images,
    }


def awakening_common_group(rows=None, facts=None):
    """Return the shared-effect group label for a likely awakening package."""
    if facts is None:
        facts = package_facts(rows)
    group_signatures = facts["group_signatures"]
    if not 2 <= len(group_signatures) <= 4:
        return None

    best = None
    for label, observed in group_signatures.items():
        anchor_count = sum(
            min(observed[key], count)
            for key, count in AWAKENING_REQUIRED.items()
        )
        if anchor_count < AWAKENING_MIN_ANCHORS:
            continue
        candidate = (
            anchor_count,
            len(facts["by_group"][label]),
            label,
        )
        if best is None or candidate > best:
            best = candidate
    return best[2] if best else None


def is_match_background_sequence(facts):
    """Recognize 840x432 card backgrounds with 420x216 effect frames."""
    counts = facts["dimension_counts"]
    full_count = counts[MATCH_BACKGROUND_FULL_SIZE]
    frame_count = counts[MATCH_BACKGROUND_FRAME_SIZE]
    return (
        facts["all_generic_images"]
        and set(counts) <= {
            MATCH_BACKGROUND_FULL_SIZE,
            MATCH_BACKGROUND_FRAME_SIZE,
        }
        and full_count >= 1
        and frame_count >= MATCH_BACKGROUND_MIN_FRAMES
        and frame_count > full_count
    )


def classification_result(category, method, rows, common_group=None):
    return {
        "category": category,
        "method": method,
        "rows": rows,
        "common_group": common_group,
    }


def classify_package_assets(package, rows):
    """Return one package category and the rows exposed by the viewer."""
    package = package.upper()
    facts = package_facts(rows)

    special = SPECIAL_PACKAGE_CATEGORIES.get(package)
    if special:
        return classification_result(
            special,
            "package_id",
            facts["visible_rows"],
        )

    # A package directory cannot belong to two categories.  Conflicting
    # semantic records therefore remain pending for manual inspection.
    named_rows = facts["named_rows"]
    if len(named_rows) == 1:
        category, selected = next(iter(named_rows.items()))
        return classification_result(category, "embedded_name", selected)
    if len(named_rows) > 1:
        return None

    match_portraits = facts["by_dimensions"].get(
        MATCH_MOBILE_SUIT_PORTRAIT_SIZE, []
    )
    if match_portraits:
        return classification_result(
            "match_mobile_suit_portrait",
            "canvas_dimensions",
            match_portraits,
        )

    if is_match_background_sequence(facts):
        return classification_result(
            "match_card_background",
            "match_background_sequence",
            facts["rows"],
        )

    common_group = awakening_common_group(facts=facts)
    if common_group is not None:
        selected = [
            row
            for row in facts["rows"]
            if row["group_label"] != common_group
            if row_dimensions(row) != (8, 8)
        ]
        return classification_result(
            "awakening",
            "awakening_structure",
            selected,
            common_group,
        )

    return None
