####################################################
# download_rgb.py # Downloads S2 Spectral Imagery. #
# ------------------------------------------------ #
# (c) Payton Jachec 2026. | harrisonjachec@usf.edu #
# Disclaimer: For research purposes only, not for  #
# clinical use.                                    #
####################################################
import os
import logging
from datetime import datetime
from multiprocessing import freeze_support
import planetary_computer
import pystac_client
import rasterio
import requests
from distributed import Client, LocalCluster
from rasterio.enums import Resampling


# CONFIGURATION

OUTPUT_DIR      = "/PATH/TO/OUTPUT/DIRECTORY/sentinel2_tiles"
BANDS           = ["B02", "B03", "B04", "B08", "B11"]
TARGET_RES      = 20  # meters
START_DATE      = "2025-04-15"
END_DATE        = "2025-05-15"
MAX_CLOUD_COVER = 20
ROI_BBOX        = [
    11.6,   # west
    -14.0,  # south
    36.5,   # east
    12.75,  # north
]

WORKERS = 16 
THREADS 1
MEMLIMIT "2GB"

# LOGGING

os.makedirs("logs", exist_ok=True)
_run_id = datetime.now().strftime("%Y%m%d_%H%M%S")

logging.basicConfig(
    filename=f"logs/run_{_run_id}.log",
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

SKIP_LOG_PATH = f"logs/skipped_{_run_id}.log"

def log(msg):
    print(msg, flush=True)
    logging.info(msg)

def log_skipped(scene_id, band, filepath):
    """Append a skipped band entry to the skip log."""
    with open(SKIP_LOG_PATH, "a") as f:
        f.write(f"{scene_id}\t{band}\t{filepath}\n")


# FOLDER SCAN — build skip set from existing files

def build_skip_set(output_dir):
    """
    Scan all subdirectories of output_dir for already-downloaded bands.
    Returns a set of (scene_id, band) tuples that can be skipped.
    Expected filename pattern: {BAND}_20m.tif
    """
    skip_set = set()

    if not os.path.isdir(output_dir):
        return skip_set

    for scene_id in os.listdir(output_dir):
        scene_dir = os.path.join(output_dir, scene_id)
        if not os.path.isdir(scene_dir):
            continue
        for band in BANDS:
            final_path = os.path.join(scene_dir, f"{band}_20m.tif")
            if os.path.exists(final_path):
                skip_set.add((scene_id, band))

    return skip_set


# STAC SEARCH — no sign_inplace; items signed fresh per scene

def get_sentinel_items():
    catalog = pystac_client.Client.open(
        "https://planetarycomputer.microsoft.com/api/stac/v1",
    )
    search = catalog.search(
        collections=["sentinel-2-l2a"],
        bbox=ROI_BBOX,
        datetime=f"{START_DATE}/{END_DATE}",
        query={"eo:cloud_cover": {"lt": MAX_CLOUD_COVER}},
    )
    items = list(search.items())
    log(f"\nFound {len(items)} Sentinel-2 scenes\n")
    return items


# RESAMPLING

def resample_to_20m(input_path, output_path):
    with rasterio.open(input_path) as src:
        scale_factor = src.res[0] / TARGET_RES
        new_width    = int(src.width  * scale_factor)
        new_height   = int(src.height * scale_factor)
        data = src.read(
            out_shape=(src.count, new_height, new_width),
            resampling=Resampling.bilinear,
        )
        transform = src.transform * src.transform.scale(
            src.width  / new_width,
            src.height / new_height,
        )
        profile = src.profile.copy()
        profile.update({"width": new_width, "height": new_height, "transform": transform})
        with rasterio.open(output_path, "w", **profile) as dst:
            dst.write(data)


# BAND DOWNLOAD

def download_and_process_band(item, band, scene_dir, skip_set):
    final_path = os.path.join(scene_dir, f"{band}_20m.tif")
    raw_path   = os.path.join(scene_dir, f"{band}_raw.tif")

    # Check skip set first (populated from folder scan before run)
    if (item.id, band) in skip_set:
        log(f"[{item.id}] {band} skipped (in skip log)")
        log_skipped(item.id, band, final_path)
        return

    # Fallback: check disk directly in case file appeared mid-run
    if os.path.exists(final_path):
        log(f"[{item.id}] {band} skipped (already on disk)")
        log_skipped(item.id, band, final_path)
        return

    # Use the already-signed href from process_scene
    asset = item.assets.get(band)
    if asset is None:
        log(f"[{item.id}] {band} missing from assets")
        return

    try:
        log(f"[{item.id}] Downloading {band}")
        response = requests.get(asset.href, stream=True, timeout=120)
        response.raise_for_status()
        with open(raw_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)
        log(f"[{item.id}] Resampling {band}")
        resample_to_20m(raw_path, final_path)
        os.remove(raw_path)
        log(f"[{item.id}] Finished {band}")
    except Exception as e:
        log(f"[{item.id}] FAILED {band}: {e}")
        for path in [raw_path, final_path]:
            if os.path.exists(path):
                os.remove(path)


# SCENE PROCESSING — sign each item fresh right before downloading

def process_scene(item, skip_set):
    # Re-sign immediately before downloading to avoid token expiry
    item = planetary_computer.sign(item)
    scene_dir = os.path.join(OUTPUT_DIR, item.id)
    os.makedirs(scene_dir, exist_ok=True)
    log(f"\n=== Processing {item.id} ===")
    for band in BANDS:
        download_and_process_band(item, band, scene_dir, skip_set)


# MAIN PIPELINE

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Scan existing folders before hitting the STAC API, this time user sees
    log("Scanning existing tiles...")
    skip_set = build_skip_set(OUTPUT_DIR)
    log(f"Found {len(skip_set)} already-downloaded band files to skip\n")

    items = get_sentinel_items()

    cluster = LocalCluster(
        n_workers=WORKERS,
        threads_per_worker=THREADS,
        memory_limit=MEMLIMIT,
    )
    client = Client(cluster)
    log("\nDask cluster started")
    log(str(client))

    futures = [client.submit(process_scene, item, skip_set) for item in items]
    log(f"Submitted {len(futures)} scenes")

    client.gather(futures)
    log(f"\nAll downloads complete. Skip log: {SKIP_LOG_PATH}")


# ENTRYPOINT

if __name__ == "__main__":
    freeze_support()
    main()
