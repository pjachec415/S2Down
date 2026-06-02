####################################################
# clean_tiles.py # Cleans up unfinished tiles.     #
# ------------------------------------------------ #
# (c) Payton Jachec 2026. | harrisonjachec@usf.edu #
# Disclaimer: For research purposes only, not for  #
# clinical use.                                    #
####################################################

import argparse
import glob
import os
import shutil
import sys

# Config

DEFAULT_TILES_DIR = "./sentinel2_tiles/"

# One pattern per expected band. A tile is "complete" only if every pattern
# matches at least one file inside the tile folder.
REQUIRED_BAND_PATTERNS = [
    "*B02*.tif",
    "*B03*.tif",
    "*B04*.tif",
    "*B08*.tif",
    "*B11*.tif",
]

# Helpers

def get_missing_bands(tile_path: str) -> list[str]:
    """Return the band patterns that have no matching file in tile_path."""
    missing = []
    for pattern in REQUIRED_BAND_PATTERNS:
        matches = glob.glob(os.path.join(tile_path, "**", pattern), recursive=True)
        if not matches:
            missing.append(pattern)
    return missing


def scan_and_move(tiles_dir: str, dry_run: bool) -> None:
    if not os.path.isdir(tiles_dir):
        print(f"ERROR: tiles directory not found: {tiles_dir}", file=sys.stderr)
        sys.exit(1)

    incomplete_dir = os.path.join(tiles_dir, "incomplete")

    tile_folders = sorted(
        entry.path
        for entry in os.scandir(tiles_dir)
        if entry.is_dir() and entry.name != "incomplete"
    )

    if not tile_folders:
        print("No tile folders found. Nothing to do.")
        return

    print(f"Scanning {len(tile_folders)} tile folder(s) in:\n  {tiles_dir}\n")

    complete_count   = 0
    incomplete_count = 0

    for tile_path in tile_folders:
        tile_name = os.path.basename(tile_path)
        missing   = get_missing_bands(tile_path)

        if not missing:
            print(f"  [OK]       {tile_name}")
            complete_count += 1
        else:
            band_labels = ", ".join(
                p.replace("*", "").replace(".tif", "") for p in missing
            )
            print(f"  [INCOMPLETE] {tile_name}  —  missing: {band_labels}")
            incomplete_count += 1

            if not dry_run:
                os.makedirs(incomplete_dir, exist_ok=True)
                dest = os.path.join(incomplete_dir, tile_name)
                if os.path.exists(dest):
                    print(f"             WARNING: destination already exists, skipping move: {dest}")
                else:
                    shutil.move(tile_path, dest)
                    print(f"             → moved to incomplete/")

    # Summary
    print()
    print("=" * 50)
    print(f"  Total scanned : {len(tile_folders)}")
    print(f"  Complete      : {complete_count}")
    print(f"  Incomplete    : {incomplete_count}")
    if dry_run:
        print("  (DRY RUN — no folders were moved)")
    else:
        print(f"  Incomplete folders moved to: {incomplete_dir}")
    print("=" * 50)


# CLI

def main():
    parser = argparse.ArgumentParser(
        description="Move incomplete Sentinel-2 tile folders to an 'incomplete' subfolder."
    )
    parser.add_argument(
        "--tiles-dir",
        default=DEFAULT_TILES_DIR,
        help=f"Path to the Sentinel-2 tiles directory (default: {DEFAULT_TILES_DIR})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be moved without actually moving anything.",
    )
    args = parser.parse_args()

    scan_and_move(tiles_dir=args.tiles_dir, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
