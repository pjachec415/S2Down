#####################################################################
# repair_missing_bands.py # Finds and downloads missing band files. #
# ----------------------------------------------------------------- #
# (c) Payton Jachec 2026. | harrisonjachec@usf.edu                  #
# Disclaimer: For research purposes only, not for clinical use.     #
#####################################################################

import os
import time
import logging
from datetime import datetime
from multiprocessing import freeze_support

import planetary_computer
import pystac_client
import rasterio
import requests

from distributed import Client, LocalCluster
from rasterio.enums import Resampling

# -----------------------------------------------------------------
# CONFIG
# -----------------------------------------------------------------

OUTPUT_DIR = "/PATH/TO/TILE/DIRECTORY"

BANDS = ["B02", "B03", "B04", "B08", "B11"]

TARGET_RES = 20   # in M at eq.
N_WORKERS = 9    
MEMORY_LIMIT = "6GB"  # per worker, expect 1-2GB overhead per worker

# ----------------------------------------------------------------
# END CONFIG 
# ----------------------------------------------------------------

os.makedirs("logs", exist_ok=True)

RUN_ID = datetime.now().strftime("%Y%m%d_%H%M%S")

logging.basicConfig(
    filename=f"logs/repair_missing_bands_{RUN_ID}.log",
    level=logging.INFO,
    format="%(asctime)s %(message)s",
)

def log(msg):
    print(msg, flush=True)
    logging.info(msg)

def resample_to_20m(input_path, output_path):
    with rasterio.open(input_path) as src:
        scale_factor = src.res[0] / TARGET_RES

        new_width = int(src.width * scale_factor)
        new_height = int(src.height * scale_factor)

        data = src.read(
            out_shape=(src.count, new_height, new_width),
            resampling=Resampling.bilinear,
        )

        transform = src.transform * src.transform.scale(
            src.width / new_width,
            src.height / new_height,
        )

        profile = src.profile.copy()
        profile.update(
            width=new_width,
            height=new_height,
            transform=transform,
        )

        with rasterio.open(output_path, "w", **profile) as dst:
            dst.write(data)

def get_incomplete_items():
    catalog = pystac_client.Client.open(
        "https://planetarycomputer.microsoft.com/api/stac/v1"
    )

    scene_ids = []

    for scene_id in os.listdir(OUTPUT_DIR):

        scene_dir = os.path.join(OUTPUT_DIR, scene_id)

        if not os.path.isdir(scene_dir):
            continue

        if not scene_id.startswith(("S2A_", "S2B_", "S2C_")):
            continue

        missing = [
            band for band in BANDS
            if not os.path.exists(
                os.path.join(scene_dir, f"{band}_20m.tif")
            )
        ]

        if missing:
            scene_ids.append(scene_id)
            log(f"{scene_id} missing {missing}")

    log(f"Found {len(scene_ids)} incomplete scenes")
    items = []

    for scene_id in scene_ids:
        try:
            search = catalog.search(
                collections=["sentinel-2-l2a"],
                ids=[scene_id]
            )

            found = list(search.items())
            
            log(
                f"{scene_id}: "
                f"found {len(found)} matching items"
            )

            if found:
                items.append(found[0])
            else:
                log(f"Scene not found: {scene_id}")

        except Exception as e:
            log(f"Lookup failed for {scene_id}: {e}")

    return items

def download_band(item, band, scene_dir):

    final_path = os.path.join(scene_dir, f"{band}_20m.tif")
    raw_path = os.path.join(scene_dir, f"{band}_raw.tif")

    if os.path.exists(final_path):
        return

    asset = item.assets.get(band)

    if asset is None:
        log(f"[{item.id}] Missing asset {band}")
        return

    for attempt in range(5):

        try:

            response = requests.get(
                asset.href,
                stream=True,
                timeout=(30, 300),
            )

            response.raise_for_status()

            with open(raw_path, "wb") as f:
                for chunk in response.iter_content(
                    chunk_size=1024 * 1024
                ):
                    f.write(chunk)

            resample_to_20m(raw_path, final_path)

            if os.path.exists(raw_path):
                os.remove(raw_path)

            log(f"[{item.id}] Finished {band}")
            return

        except Exception as e:

            log(
                f"[{item.id}] FAILED {band} "
                f"(attempt {attempt+1}/5): {e}"
            )

            for p in (raw_path, final_path):
                if os.path.exists(p):
                    os.remove(p)

            if attempt < 4:
                time.sleep(2 ** attempt)

    log(f"[{item.id}] GAVE UP ON {band}")

def process_scene(item):

    item = planetary_computer.sign(item)

    scene_dir = os.path.join(OUTPUT_DIR, item.id)

    missing_bands = [
        band for band in BANDS
        if not os.path.exists(
            os.path.join(scene_dir, f"{band}_20m.tif")
        )
    ]

    if not missing_bands:
        return

    log(f"Repairing {item.id}: {missing_bands}")

    for band in missing_bands:
        download_band(item, band, scene_dir)

def main():

    items = get_incomplete_items()

    cluster = LocalCluster(
        n_workers=N_WORKERS,
        threads_per_worker=1,
        memory_limit=MEMORY_LIMIT,
    )

    client = Client(cluster)

    futures = [
        client.submit(process_scene, item)
        for item in items
    ]

    client.gather(futures)

    log("Repair complete")

if __name__ == "__main__":
    freeze_support()
    main()

