import glob
import subprocess

LULC_DIR = "/work_bgfs/h/harrisonjachec/drc_ebola/sentinel2_tiles/LULC"
OUT_FILE = "/work_bgfs/h/harrisonjachec/drc_ebola/LULC_2022_mosaic.tif"

tiles = sorted(glob.glob(f"{LULC_DIR}/*-2022.tif"))
print(f"Found {len(tiles)} tiles")

subprocess.run([
    "gdalwarp",
    "-t_srs", "EPSG:4326",      # reproject all tiles to common WGS84
    "-r", "near",                # nearest neighbour — correct for categorical LULC
    "-srcnodata", "0",
    "-dstnodata", "0",
    "-co", "COMPRESS=LZW",
    "-co", "TILED=YES",
    "-co", "BIGTIFF=YES",
    "-co", "NUM_THREADS=4",
    "-wo", "NUM_THREADS=4",      # warp threads separate from I/O threads
] + tiles + [OUT_FILE], check=True)

print(f"Done → {OUT_FILE}")
