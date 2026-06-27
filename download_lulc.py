####################################################
# download_lulc.py # Downloads 10m LULC.           #
# ------------------------------------------------ #
# (c) Payton Jachec 2026. | harrisonjachec@usf.edu #
# Disclaimer: For research purposes only, not for  #
# clinical use.                                    #
####################################################
# ══════════════════════════════════════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════════════════════════════════════
OUTPUT_DIR = "/PATH/TO/OUTPUT/DIRECTORY"

# DRC bounding box [west, south, east, north]
BBOX = [W, S, E, N]
YEAR = 2023

# Overwrite tiles that already exist on disk
OVERWRITE = False
# ══════════════════════════════════════════════════════════════════════════════
#  END CONFIG
# ══════════════════════════════════════════════════════════════════════════════
import os
from pathlib import Path

import planetary_computer
import pystac_client
import requests

# ── Output directory ──────────────────────────────────────────────────────────
out_dir = Path(OUTPUT_DIR)
out_dir.mkdir(parents=True, exist_ok=True)

# ── Connect to Planetary Computer ─────────────────────────────────────────────
print("\n=== LULC Tile Download ===\n", flush=True)
print("  Connecting to Planetary Computer...", flush=True)

catalog = pystac_client.Client.open(
    "https://planetarycomputer.microsoft.com/api/stac/v1",
    modifier=planetary_computer.sign_inplace,
)

search = catalog.search(
    collections=["io-lulc-9-class"],
    bbox=BBOX,
)

items = list(search.items())
print(f"  Found {len(items)} LULC tile(s) for year {YEAR}", flush=True)

if not items:
    raise RuntimeError("No matching LULC tiles found. Check BBOX and YEAR.")

# ── Download ──────────────────────────────────────────────────────────────────
skipped  = 0
downloaded = 0

for item in items:
    signed = planetary_computer.sign(item)
    asset  = signed.assets["data"]

    # Derive filename from item id, preserving original extension
    suffix   = Path(asset.href.split("?")[0]).suffix or ".tif"
    out_path = out_dir / f"{item.id}{suffix}"

    if out_path.exists() and not OVERWRITE:
        print(f"  [skip]  {out_path.name} already exists", flush=True)
        skipped += 1
        continue

    print(f"  [dl]    {item.id}", flush=True)
    response = requests.get(asset.href, stream=True, timeout=120)
    response.raise_for_status()

    with open(out_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=1 << 20):  # 1 MB chunks
            f.write(chunk)

    downloaded += 1

# ── Summary ───────────────────────────────────────────────────────────────────
total_gb = sum(p.stat().st_size for p in out_dir.glob("*.tif")) / 1e9
print(f"\n  Downloaded : {downloaded}", flush=True)
print(f"  Skipped    : {skipped}", flush=True)
print(f"  Directory  : {out_dir}", flush=True)
print(f"  Total size : {total_gb:.2f} GB", flush=True)
print("\n=== Done ===\n", flush=True)
