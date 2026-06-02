####################################################
# download_dem.py # Downloads Copernicus DEM data. #
# ------------------------------------------------ #
# (c) Payton Jachec 2026. | harrisonjachec@usf.edu #
# Disclaimer: For research purposes only, not for  #
# clinical use.                                    #
####################################################
import os
import re
import math
import requests

# ── Configuration ─────────────────────────────────────────────────────────────
BASE_DIR = "/work_bgfs/h/harrisonjachec/drc_ebola/sentinel2_tiles"
DEM_DIR  = os.path.join(BASE_DIR, "DEM")

WEST = 11.6
SOUTH = -14.0
EAST = 36.5
NORTH = 12.75

# ── Helpers ───────────────────────────────────────────────────────────────────
def log(msg):
    print(msg, flush=True)

def aoi_to_dem_tiles(west, east, south, north):
    """Return all COP-DEM tile identifiers covering the AOI."""
    tiles = set()
    for lat in range(int(math.floor(south)), int(math.ceil(north))):
        for lon in range(int(math.floor(west)), int(math.ceil(east))):
            lat_hem = "N" if lat >= 0 else "S"
            lon_hem = "E" if lon >= 0 else "W"
            tiles.add((lat_hem, abs(lat), lon_hem, abs(lon)))
    return tiles

def tile_filename(lat_hem, lat_tile, lon_hem, lon_tile):
    return (
        f"Copernicus_DSM_COG_10_{lat_hem}{lat_tile:02d}_00"
        f"_{lon_hem}{lon_tile:03d}_00_DEM.tif"
    )

def download_dem_tile(lat_hem, lat_tile, lon_hem, lon_tile):
    name     = f"Copernicus_DSM_COG_10_{lat_hem}{lat_tile:02d}_00_{lon_hem}{lon_tile:03d}_00_DEM"
    filename = f"{name}.tif"
    url      = f"https://copernicus-dem-30m.s3.amazonaws.com/{name}/{filename}"
    out_path = os.path.join(DEM_DIR, filename)

    if os.path.exists(out_path):
        log(f"  Already exists: {filename}")
        return
    log(f"  Downloading: {filename}")
    try:
        r = requests.get(url, stream=True, timeout=120)
        r.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(1024 * 1024):
                f.write(chunk)
        log(f"  Done: {filename}")
    except Exception as e:
        log(f"  FAILED {filename}: {e}")

# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    os.makedirs(DEM_DIR, exist_ok=True)

    tiles = aoi_to_dem_tiles(WEST, EAST, SOUTH, NORTH)
    log(f"Total DEM tiles to download: {len(tiles)}")

    for tile in sorted(tiles):
        download_dem_tile(*tile)

    log("Done.")
