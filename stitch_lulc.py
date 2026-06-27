####################################################
# stitch_lulc.py # Stitches LULC tiles by year.    #
# ------------------------------------------------ #
# (c) Payton Jachec 2026. | harrisonjachec@usf.edu #
# Disclaimer: For research purposes only, not for  #
# clinical use.                                    #
####################################################
import glob
import subprocess

LULC_DIR = "/PATH/TO/LULC/TILE/DIRECTORY"
OUT_FILE = "/PATH/TO/OUTPUT/MOSAIC.tif"
YEAR = 2022

tiles = sorted(glob.glob(f"{LULC_DIR}/*-{YEAR}.tif"))
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
