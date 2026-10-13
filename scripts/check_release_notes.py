#!/usr/bin/env python3
"""Check public release copy and print its path for --notes-file (no network)."""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "HIIIOCG/Yugo-BlenderVtubingTools"
PRODUCTS = {
    "yugo": ("yugo", "YUGO", "v"),
    "studio": ("yugu_node_studio", "STUDIO", "studio-v"),
}


def validate_body(text: str, version: str, downloads: list[str]) -> None:
    """Check presentation/routing; English and factual claims need editorial review."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    changes = f"## What's changed in {version}"
    if not lines or lines[0] != "## Downloads" or lines.count(changes) != 1:
        raise ValueError("Start with Downloads, followed by What's changed in the exact version")
    split = lines.index(changes)
    table = lines[1:split]
    if table[:2] != ["| Download | Purpose |", "| --- | --- |"]:
        raise ValueError("Downloads must use the Download / Purpose table")
    rows = table[2:]
    if len(rows) != len(downloads):
        raise ValueError("List only the main package and the actual optional package, when supplied")
    for row, url in zip(rows, downloads):
        match = re.fullmatch(r"\| \*\*\[([^\]\n]+)\]\(([^)\s]+)\)\*\* \| ([^|]+) \|", row)
        if match is None or match[2] != url or not match[3].strip():
            raise ValueError("Use the exact release download URLs, main package first, with a short purpose")
    if not lines[-1].startswith("**Compatibility:** ") or len(lines[-1]) == len("**Compatibility:** "):
        raise ValueError("End with one nonempty Compatibility line")
    bullets = lines[split + 1:-1]
    if not 1 <= len(bullets) <= 6:
        raise ValueError("Write 1-6 short node/feature change bullets")
    for bullet in bullets:
        if not re.fullmatch(r"- \*\*[^*]+\*\* [—–-] \S.+", bullet) or len(bullet.split()) > 40:
            raise ValueError("Each change must name a node/feature and describe it in at most 40 words")
    # Exactly the package links: no source archives, checksum/build links or tutorials.
    if re.findall(r"\]\(([^)]+)\)", text) != downloads:
        raise ValueError("Only installation-package download links belong in the release body")
    if re.search(r"Install from Disk|Edit\s*[→>]\s*Preferences|Source commit|SHA-?256|build provenance", text, re.I):
        raise ValueError("Keep installation instructions and engineering provenance outside the release body")


def check_release_notes(root: Path, product: str, tag: str | None = None) -> Path:
    package_id, prefix, tag_prefix = PRODUCTS[product]
    manifest = tomllib.loads((root / "addons" / package_id / "blender_manifest.toml").read_text(encoding="utf-8"))
    version = manifest["version"]
    if manifest["id"] != package_id or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Unexpected package identity/version")
    expected_tag = tag_prefix + version
    if tag is not None and tag != expected_tag:
        raise ValueError(f"Expected {expected_tag}, got {tag}")
    base = f"https://github.com/{REPOSITORY}/releases/download/{expected_tag}"
    downloads = [f"{base}/{package_id}-{version}.zip"]
    if product == "yugo":
        optional = tomllib.loads((root / "addons/yugo_physx_tracker/blender_manifest.toml").read_text(encoding="utf-8"))
        if optional["id"] != "yugo_physx_tracker" or not re.fullmatch(r"\d+\.\d+\.\d+", optional["version"]):
            raise ValueError("Unexpected optional PhysX package identity/version")
        downloads.append(f"{base}/{optional['id']}-{optional['version']}.zip")
    path = Path("docs/releases") / f"{prefix}_{version.replace('.', '_')}_NOTES.md"
    validate_body((root / path).read_text(encoding="utf-8"), version, downloads)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--product", choices=PRODUCTS, required=True)
    parser.add_argument("--tag", help="Optional tag to check against the product manifest")
    args = parser.parse_args()
    try:
        path = check_release_notes(ROOT, args.product, args.tag)
    except (OSError, ValueError, KeyError) as error:
        print(f"Release notes check failed: {error}", file=sys.stderr)
        return 1
    print(path.as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
