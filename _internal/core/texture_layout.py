"""Shared layout rules for extracted FHM2D texture packages."""

from pathlib import Path


PACKAGE_ROOT_NAME = "packages"
DEFAULT_PACKAGE_CATEGORY = "pending"
PACKAGE_CATEGORIES = (
    "outgame_navigator",
    "ingame_navigator",
    "combat_portrait",
    "awakening",
    "favorite_mobile_suit",
    "select_navigator",
    "select_mobile_suit_thumbnail",
    "select_mobile_suit",
    "match_mobile_suit_portrait",
    "match_mobile_suit",
    "match_card_frame",
    "match_card_background",
    "match_symbol",
    "pending",
)


def package_directory(texture_root, package, category=DEFAULT_PACKAGE_CATEGORY):
    """Return the canonical categorized directory for one package."""
    if category not in PACKAGE_CATEGORIES:
        raise ValueError(f"unknown package category: {category}")
    return Path(texture_root) / PACKAGE_ROOT_NAME / category / package


def package_prefix(package, category):
    """Return the portable path prefix used in CSV/JSON manifests."""
    if category not in PACKAGE_CATEGORIES:
        raise ValueError(f"unknown package category: {category}")
    return f"{PACKAGE_ROOT_NAME}\\{category}\\{package}\\"
