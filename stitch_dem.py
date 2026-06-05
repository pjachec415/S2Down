####################################################
# stitch_dem.py # Mosaics DEM tiles from ESA S2.   #
# ------------------------------------------------ #
# (c) Payton Jachec 2026. | harrisonjachec@usf.edu #
# Disclaimer: For research purposes only, not for  #
# clinical use.                                    #
####################################################
"""
Assumes all tiles are:
  - GeoTIFF format (.tif)
  - EPSG:4326, ~0.0002695° (~30m) resolution
  - Consistent nodata value (default -9999 for Copernicus GLO-30)

Usage
-----
  Edit the CONFIG section below, then:
      python stitch_dem.py
"""

#  CONFIG

# ── Input DEM tile directory
DEM_DIR = "/PATH/TO/DEM/TILE/PARENT/DIRECTORY"

# ── Output path (same folder as spectral indices)
OUT_PATH = "/PATH/TO/DESIRED/OUTPUT/DIRECTORY/DEM.tif"

# ── Copernicus GLO-30 nodata value
NODATA_IN  = -9999.0
NODATA_OUT = -9999.0

# ── Output dtype
# "float32" preserves sub-metre precision; "int16" halves file size (±32767m range)
OUT_DTYPE = "float32"

# ── Compression
COMPRESS = "lzw" # Leave the same unless you know what you're doing

# ── Overwrite existing output
OVERWRITE = False

#  END CONFIG


import shutil
import subprocess
import warnings
from pathlib import Path

import numpy as np
import rasterio
from rasterio.merge import merge as rio_merge

warnings.filterwarnings("ignore")


def stitch_with_gdal(tile_paths: list[Path], out_path: Path) -> None:
    # Stitch via gdalbuildvrt + gdal_translate (preferred — fast, no RAM spike).
    tmp_dir  = out_path.parent / "_dem_tmp"
    tmp_dir.mkdir(exist_ok=True)
    vrt_path = tmp_dir / "dem.vrt"
    lst_path = tmp_dir / "dem_tiles.txt"

    lst_path.write_text("\n".join(str(p) for p in tile_paths))

    print("  Building VRT...", flush=True)
    subprocess.run(
        ["gdalbuildvrt",
         "-input_file_list", str(lst_path),
         "-resolution", "highest",
         "-r", "bilinear",
         "-srcnodata", str(NODATA_IN),
         "-vrtnodata", str(NODATA_OUT),
         str(vrt_path)],
        check=True, capture_output=True,
    )

    print(f"  Translating to GeoTIFF → {out_path}...", flush=True)
    subprocess.run(
        ["gdal_translate",
         "-ot", "Float32" if OUT_DTYPE == "float32" else "Int16",
         "-co", f"COMPRESS={COMPRESS.upper()}",
         "-co", "TILED=YES",
         "-co", "BLOCKXSIZE=512",
         "-co", "BLOCKYSIZE=512",
         "-co", "BIGTIFF=YES",
         "-a_nodata", str(NODATA_OUT),
         str(vrt_path), str(out_path)],
        check=True, capture_output=True,
    )

    shutil.rmtree(tmp_dir, ignore_errors=True)


def stitch_with_rasterio(tile_paths: list[Path], out_path: Path) -> None:
    # Fallback stitch via rasterio.merge (loads all tiles into RAM).
    print("  gdalbuildvrt not found — using rasterio merge (higher RAM usage)...",
          flush=True)
    datasets = [rasterio.open(p) for p in tile_paths]
    mosaic, mosaic_transform = rio_merge(
        datasets, method="first", nodata=NODATA_IN
    )
    profile = datasets[0].profile.copy()
    profile.update(
        driver="GTiff",
        dtype=OUT_DTYPE,
        count=1,
        compress=COMPRESS,
        tiled=True,
        blockxsize=512,
        blockysize=512,
        bigtiff="YES",
        transform=mosaic_transform,
        width=mosaic.shape[2],
        height=mosaic.shape[1],
        nodata=NODATA_OUT,
    )
    with rasterio.open(out_path, "w", **profile) as dst:
        dst.write(mosaic[0].astype(OUT_DTYPE), 1)
    for ds in datasets:
        ds.close()


def run() -> None:
    print("\n=== Copernicus GLO-30 DEM Stitch ===\n", flush=True)

    out_path = Path(OUT_PATH)

    if out_path.exists() and not OVERWRITE:
        print(f"Output already exists: {out_path}\n"
              f"Set OVERWRITE=True to replace it.", flush=True)
        return

    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Collect tiles
    dem_dir    = Path(DEM_DIR)
    tile_paths = sorted(dem_dir.rglob("*.tif"))

    if not tile_paths:
        raise RuntimeError(f"No .tif files found in {dem_dir}")

    print(f"  Found {len(tile_paths):,} DEM tiles in {dem_dir}", flush=True)

    # Quick sanity check on first tile
    with rasterio.open(tile_paths[0]) as ds:
        print(f"  Sample tile CRS      : {ds.crs}", flush=True)
        print(f"  Sample tile res      : {abs(ds.transform.a):.7f}° "
              f"(~{abs(ds.transform.a) * 111320:.1f}m)", flush=True)
        print(f"  Sample tile nodata   : {ds.nodata}", flush=True)
        print(f"  Sample tile size     : {ds.width}×{ds.height}px", flush=True)

    print(f"\n  Output dtype         : {OUT_DTYPE}", flush=True)
    print(f"  Compression          : {COMPRESS}", flush=True)
    print(f"  Output path          : {out_path}\n", flush=True)

    # Stitch
    if shutil.which("gdalbuildvrt"):
        stitch_with_gdal(tile_paths, out_path)
    else:
        stitch_with_rasterio(tile_paths, out_path)

    # Report
    size_gb = out_path.stat().st_size / 1e9
    with rasterio.open(out_path) as ds:
        print(f"\n  ✓ DEM.tif  {ds.width:,}×{ds.height:,}px  "
              f"{size_gb:.2f} GB  CRS={ds.crs}", flush=True)
        arr = ds.read(1)
        valid = arr[arr != NODATA_OUT]
        if len(valid):
            print(f"  Elevation range: {valid.min():.1f}m – {valid.max():.1f}m  "
                  f"mean={valid.mean():.1f}m", flush=True)

    print("\n=== Done ===", flush=True)


if __name__ == "__main__":
    run()
