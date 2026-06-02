####################################################
# analysis.py # Performs parallel GIS analysis.    #
# ------------------------------------------------ #
# (c) Payton Jachec 2026. | harrisonjachec@usf.edu #
# Disclaimer: For research purposes only, not for  #
# clinical use.                                    #
####################################################
"""
    python compute_indices_dask.py \\
        --tiles_dir /PATH/TO/DIRECTORY/sentinel2_tiles \\
        --out_dir   /PATH/TO/DESIRED/OUTPUT/LOCATION \\
        [--chunk_size 4096] \\
        [--n_workers 4] \\
        [--memory_limit 12GB] \\
        [--savi_L 0.5] \\
        [--nodata 0] \\
        [--scale 10000] \\
        [--keep_chunks]
"""

import argparse
import gc
import math
import shutil
import subprocess
import warnings
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.warp import reproject
from rasterio.windows import Window



warnings.filterwarnings("ignore", category=rasterio.errors.NotGeoreferencedWarning)
warnings.filterwarnings("ignore", category=UserWarning)


# Constants

BAND_ORDER  = ["B02", "B03", "B04", "B08", "B11"]
INDEX_NAMES = ["NDVI", "SAVI", "MNDWI", "NDWI"]

BAND_PATTERNS: dict[str, list[str]] = {
    "B02": ["*B02*.tif", "*_B02.tif", "*band2*.tif", "*blue*.tif"],
    "B03": ["*B03*.tif", "*_B03.tif", "*band3*.tif", "*green*.tif"],
    "B04": ["*B04*.tif", "*_B04.tif", "*band4*.tif", "*red*.tif"],
    "B08": ["*B08*.tif", "*_B08.tif", "*band8*.tif", "*nir*.tif"],
    "B11": ["*B11*.tif", "*_B11.tif", "*band11*.tif", "*swir*.tif"],
}

CHUNK_PROFILE = dict(
    driver="GTiff",
    dtype="float32",
    nodata=float("nan"),
    count=1,
    compress="lzw",
    tiled=True,
    blockxsize=256,
    blockysize=256,
)

FINAL_PROFILE_EXTRA = dict(
    compress="lzw",
    tiled=True,
    blockxsize=512,
    blockysize=512,
    bigtiff="YES",
)


# Band detection  (runs on main process only)

def find_band(tile_dir: Path, band_key: str) -> Path:
    for pattern in BAND_PATTERNS[band_key]:
        matches = sorted(tile_dir.glob(pattern))
        if matches:
            return matches[0]
    for f in sorted(tile_dir.glob("*.tif")):
        if band_key.lower() in f.stem.lower():
            return f
    raise FileNotFoundError(
        f"Band '{band_key}' not found in {tile_dir}. "
        f"Files: {[f.name for f in tile_dir.glob('*.tif')]}"
    )


def collect_tile_band_paths(tiles_dir: Path) -> list[dict[str, str]]:
    """
    Returns a list of dicts mapping band key -> str path.
    Uses str (not Path) so Dask can pickle the specs cleanly.
    """
    # Explicitly exclude known non-tile subdirectories
    SKIP_DIRS = {"DEM", "dem", "incomplete", "tmp", "temp", "_chunks"}

    tile_dirs = sorted(
        d for d in tiles_dir.iterdir()
        if d.is_dir() and d.name not in SKIP_DIRS
    )
    if not tile_dirs:
        raise RuntimeError(f"No subdirectories found in {tiles_dir}")
    tiles = []
    skipped = 0
    for td in tile_dirs:
        entry: dict[str, str] = {}
        ok = True
        for band in BAND_ORDER:
            try:
                entry[band] = str(find_band(td, band))
            except FileNotFoundError as e:
                print(f"  [SKIP] {e}")
                ok = False
                break
        if ok:
            tiles.append(entry)
        else:
            skipped += 1
    print(f"  Found {len(tiles)} complete tiles ({skipped} skipped).")
    return tiles


# Mosaic grid  (metadata only, runs on main process)

def compute_mosaic_grid(tiles: list[dict[str, str]], target_crs: str = "EPSG:4326") -> dict:
    from rasterio.crs import CRS
    from rasterio.warp import transform_bounds

    print(f"  Scanning tile extents → reprojecting to {target_crs}...")
    out_crs     = CRS.from_string(target_crs)
    out_crs_wkt = out_crs.to_wkt()

    # 20m in degrees at equator ≈ 20 / 111320
    # Using a fixed value keeps the grid consistent regardless of latitude
    RES_DEG = 20.0 / 111320.0   # ~0.00017966°

    all_bounds_4326 = []
    n_unique_crs = set()

    for tile in tiles:
        with rasterio.open(tile["B02"]) as ds:
            n_unique_crs.add(ds.crs.to_epsg())
            # transform_bounds reprojects the bounding box corners
            left, bottom, right, top = transform_bounds(
                ds.crs, out_crs, *ds.bounds
            )
            all_bounds_4326.append((left, bottom, right, top))

    left   = min(b[0] for b in all_bounds_4326)
    bottom = min(b[1] for b in all_bounds_4326)
    right  = max(b[2] for b in all_bounds_4326)
    top    = max(b[3] for b in all_bounds_4326)

    width  = math.ceil((right - left)  / RES_DEG)
    height = math.ceil((top   - bottom) / RES_DEG)

    # Store tile bounds in output CRS for fast chunk intersection tests
    tile_bounds_4326 = all_bounds_4326   # (left, bottom, right, top) per tile

    print(f"  Source UTM zones found : {sorted(n_unique_crs)}")
    print(f"  Output CRS : {target_crs}")
    print(f"  Extent     : L={left:.4f}  B={bottom:.4f}  R={right:.4f}  T={top:.4f}")
    print(f"  Size       : {width:,} × {height:,} px  ({RES_DEG*111320:.1f}m res at equator)")
    print(f"  ~{width * height * 4 / 1e9:.2f} GB per output band (uncompressed float32)")

    return dict(
        crs_wkt=out_crs_wkt,
        res_x=RES_DEG, res_y=RES_DEG,
        left=left, top=top, right=right, bottom=bottom,
        width=width, height=height,
        tile_bounds=tile_bounds_4326,   # list of (L,B,R,T) in output CRS
    )


def make_transform(grid: dict):
    return from_origin(grid["left"], grid["top"], grid["res_x"], grid["res_y"])


# Index formulas  (pure numpy, runs in workers)

def _ratio(num: np.ndarray, den: np.ndarray) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(den != 0, num / den, np.nan).astype(np.float32)

def calc_ndvi(nir, red):          return _ratio(nir - red,          nir + red)
def calc_savi(nir, red, L=0.5):   return _ratio((nir - red)*(1+L),  nir + red + L)
def calc_mndwi(green, swir):       return _ratio(green - swir,       green + swir)
def calc_ndwi(green, nir):         return _ratio(green - nir,        green + nir)


# Memory helpers

def _parse_memory_limit(limit: str) -> int:
    limit = limit.upper().strip()

    units = {
        "KB": 1024,
        "MB": 1024**2,
        "GB": 1024**3,
        "TB": 1024**4,
    }

    for suffix, factor in units.items():
        if limit.endswith(suffix):
            return int(float(limit[:-len(suffix)]) * factor)

    return int(limit)


# Per-chunk worker function  (runs inside Dask workers)

def process_chunk(
    tiles: list[dict[str, str]],
    row_off: int,
    col_off: int,
    chunk_h: int,
    chunk_w: int,
    grid: dict,
    nodata: float,
    scale: float,
    savi_L: float,
    tmp_dir: str,
    memory_limit: str,
) -> dict[str, str]:
    """
    Process one spatial chunk.  Fully self-contained — opens its own
    file handles so it is safe to run in a separate process.

    Returns dict mapping index name -> path of the written chunk GeoTIFF.
    Returns an empty dict if the chunk contains only nodata.
    """
    import gc
    import sys
    import time
    import warnings
    import numpy as np
    import rasterio
    import resource
    from rasterio.crs import CRS
    from rasterio.enums import Resampling
    from rasterio.transform import from_origin
    from rasterio.warp import reproject, transform_bounds as _tb
    warnings.filterwarnings("ignore")
    
    if memory_limit:
        bytes_limit = _parse_memory_limit(memory_limit)
        resource.setrlimit(
            resource.RLIMIT_AS,
            (bytes_limit, bytes_limit),
        )

    def log(msg: str) -> None:
        """Print immediately, even under SLURM buffering."""
        print(msg, flush=True)

    chunk_tag = f"row={row_off:07d} col={col_off:07d}"
    t_chunk_start = time.time()
    log(f"\n[CHUNK START] {chunk_tag}  size=({chunk_h}×{chunk_w}px)")

    gt_left  = grid["left"]  + col_off * grid["res_x"]
    gt_top   = grid["top"]   - row_off * grid["res_y"]
    gt_right = gt_left + chunk_w * grid["res_x"]
    gt_bot   = gt_top  - chunk_h * grid["res_y"]
    chunk_transform = from_origin(gt_left, gt_top, grid["res_x"], grid["res_y"])
    crs = CRS.from_wkt(grid["crs_wkt"])

    log(f"  Extent: L={gt_left:.4f} B={gt_bot:.4f} R={gt_right:.4f} T={gt_top:.4f}")

    def _safe_ratio(num, den):
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.where(den != 0, num / den, np.nan).astype(np.float32)

    band_data: dict[str, np.ndarray] = {}

    for band in ["B02", "B03", "B04", "B08", "B11"]:
        log(f"  [BAND {band}] Scanning {len(tiles)} tiles for overlap...")
        t_band = time.time()
        stack = []
        n_overlap = 0
        n_skip = 0

        for tile in tiles:
            tile_name = Path(tile[band]).parent.name
            with rasterio.open(tile[band]) as ds:
                try:
                    tl, tbot, tr, tt = _tb(ds.crs, crs, *ds.bounds)
                except Exception:
                    tl, tbot, tr, tt = ds.bounds

                if tr <= gt_left or tl >= gt_right or tt <= gt_bot or tbot >= gt_top:
                    n_skip += 1
                    continue

                n_overlap += 1
                log(f"    Loading tile {tile_name} / {band}  "
                    f"(tile {n_overlap} intersects chunk)")

                dst = np.full((chunk_h, chunk_w), np.nan, dtype=np.float32)
                try:
                    reproject(
                        source=rasterio.band(ds, 1),
                        destination=dst,
                        src_transform=ds.transform,
                        src_crs=ds.crs,
                        dst_transform=chunk_transform,
                        dst_crs=crs,
                        resampling=Resampling.bilinear,
                        src_nodata=nodata,
                        dst_nodata=np.nan,
                    )
                    stack.append(dst)
                    log(f"    ✓ Reprojected {tile_name} / {band}")
                except Exception as e:
                    log(f"    ✗ Reproject failed for {tile_name} / {band}: {e}")

        log(f"  [BAND {band}] {n_overlap} tiles loaded, {n_skip} skipped  "
            f"({time.time() - t_band:.1f}s)")

        if stack:
            log(f"  [BAND {band}] Computing median over {len(stack)} tiles...")
            arr = np.nanmedian(np.stack(stack, axis=0), axis=0).astype(np.float32)
        else:
            log(f"  [BAND {band}] No data for this chunk.")
            arr = np.full((chunk_h, chunk_w), np.nan, dtype=np.float32)

        del stack
        arr = arr / scale
        arr = np.where((arr < 0) | (arr > 1), np.nan, arr).astype(np.float32)
        band_data[band] = arr
        log(f"  [BAND {band}] Done. valid_px={int(np.sum(~np.isnan(arr))):,}")

    gc.collect()

    # Check for all-NaN chunk
    if np.all(np.isnan(band_data["B08"])):
        log(f"[CHUNK SKIP] {chunk_tag} — all NaN, no output written.")
        return {}

    nir   = band_data["B08"]
    red   = band_data["B04"]
    green = band_data["B03"]
    swir  = band_data["B11"]

    log(f"  [INDICES] Computing NDVI, SAVI, MNDWI, NDWI...")
    indices = {
        "NDVI":  _safe_ratio(nir - red,              nir + red),
        "SAVI":  _safe_ratio((nir - red)*(1+savi_L), nir + red + savi_L),
        "MNDWI": _safe_ratio(green - swir,            green + swir),
        "NDWI":  _safe_ratio(green - nir,             green + nir),
    }
    log(f"  [INDICES] Done.")

    del band_data, nir, red, green, swir
    gc.collect()

    profile = dict(
        driver="GTiff", dtype="float32", nodata=float("nan"), count=1,
        compress="lzw", tiled=True, blockxsize=256, blockysize=256,
        crs=crs, transform=chunk_transform,
        width=chunk_w, height=chunk_h,
    )

    out_paths: dict[str, str] = {}
    tag = f"r{row_off:07d}_c{col_off:07d}"
    for name, arr in indices.items():
        p = str(Path(tmp_dir) / f"chunk_{name}_{tag}.tif")
        log(f"  [WRITE] {name} → {p}")
        with rasterio.open(p, "w", **profile) as dst:
            dst.write(arr[np.newaxis, :, :], 1)
        out_paths[name] = p

    t_total = time.time() - t_chunk_start
    log(f"[CHUNK DONE] {chunk_tag}  elapsed={t_total:.1f}s")
    return out_paths


# VRT assembly + final translate  (runs on main process)

def assemble_vrt_and_translate(
    chunk_paths_by_index: dict[str, list[str]],
    out_dir: Path,
    grid: dict,
) -> dict[str, Path]:
    """
    Build a VRT from all chunk files for each index, then gdal_translate
    to a final compressed BigTIFF.  Falls back to rasterio-based merge
    if gdal CLI tools are not in PATH.
    """
    from rasterio.crs import CRS

    out_dir.mkdir(parents=True, exist_ok=True)
    final_paths: dict[str, Path] = {}

    gdal_available = shutil.which("gdalbuildvrt") is not None

    for name, chunk_files in chunk_paths_by_index.items():
        if not chunk_files:
            print(f"  [WARN] No chunks for {name} — skipping.")
            continue

        vrt_path   = out_dir / f"{name}.vrt"
        final_path = out_dir / f"{name}.tif"

        if gdal_available:
            # GDAL path (fast)
            vrt_list = out_dir / f"{name}_chunks.txt"
            vrt_list.write_text("\n".join(chunk_files))

            subprocess.run(
                ["gdalbuildvrt", "-input_file_list", str(vrt_list),
                 "-resolution", "highest", "-r", "bilinear", str(vrt_path)],
                check=True, capture_output=True,
            )
            subprocess.run(
                ["gdal_translate",
                 "-co", "COMPRESS=LZW", "-co", "TILED=YES",
                 "-co", "BLOCKXSIZE=512", "-co", "BLOCKYSIZE=512",
                 "-co", "BIGTIFF=YES",
                 str(vrt_path), str(final_path)],
                check=True, capture_output=True,
            )
            vrt_path.unlink(missing_ok=True)
            vrt_list.unlink(missing_ok=True)

        else:
            # rasterio fallback (streaming merge)
            print(f"  gdalbuildvrt not found — using rasterio merge for {name}")
            from rasterio.merge import merge as rio_merge

            datasets = [rasterio.open(p) for p in chunk_files]
            mosaic, mosaic_transform = rio_merge(datasets, method="first",
                                                  nodata=float("nan"))
            profile = datasets[0].profile.copy()
            profile.update(
                driver="GTiff", compress="lzw", tiled=True,
                blockxsize=512, blockysize=512, bigtiff="YES",
                width=mosaic.shape[2], height=mosaic.shape[1],
                transform=mosaic_transform, count=1,
            )
            with rasterio.open(final_path, "w", **profile) as dst:
                dst.write(mosaic[0:1])
            for ds in datasets:
                ds.close()

        final_paths[name] = final_path
        size_gb = final_path.stat().st_size / 1e9
        print(f"  {name}.tif  →  {size_gb:.2f} GB")

    return final_paths


# Main pipeline

def run(
    tiles_dir: Path,
    out_dir: Path,
    chunk_size: int,
    n_workers: int,
    memory_limit: str,
    savi_L: float,
    nodata: float,
    scale: float,
    keep_chunks: bool,
) -> None:

    print("\n=== Dask-Parallelised Sentinel-2 Spectral Index Pipeline ===\n")

    # 1. Collect tile band paths
    print("Step 1 — Collecting tile band paths...")
    tiles = collect_tile_band_paths(tiles_dir)

    # 2. Mosaic grid from metadata
    print("\nStep 2 — Computing mosaic grid...")
    grid = compute_mosaic_grid(tiles)

    # 3. Build chunk specs
    n_chunks_x = math.ceil(grid["width"]  / chunk_size)
    n_chunks_y = math.ceil(grid["height"] / chunk_size)
    total_chunks = n_chunks_x * n_chunks_y

    tmp_dir = out_dir / "_chunks"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    chunk_specs = []
    for row_chunk in range(n_chunks_y):
        row_off = row_chunk * chunk_size
        chunk_h = min(chunk_size, grid["height"] - row_off)
        for col_chunk in range(n_chunks_x):
            col_off = col_chunk * chunk_size
            chunk_w = min(chunk_size, grid["width"] - col_off)
            chunk_specs.append(
                dict(row_off=row_off, col_off=col_off,
                     chunk_h=chunk_h, chunk_w=chunk_w)
            )

    # Estimate tiles overlapping a typical central chunk for memory reporting.
    # Uses the tile_bounds list (in output CRS) stored by compute_mosaic_grid.
    chunk_deg = chunk_size * grid["res_x"]
    cx = (grid["left"] + grid["right"]) / 2
    cy = (grid["bottom"] + grid["top"]) / 2
    sample_overlap = sum(
        1 for (l, b, r, t) in grid["tile_bounds"]
        if r > cx - chunk_deg / 2 and l < cx + chunk_deg / 2
        and t > cy - chunk_deg / 2 and b < cy + chunk_deg / 2
    )
    sample_overlap = max(sample_overlap, 1)

    print(f"\nStep 3 — Submitting {total_chunks:,} chunks to {n_workers} workers...")
    print(f"  Chunk size        : {chunk_size}px (~{chunk_deg * 111:.0f}km at equator)")
    print(f"  Memory limit      : {memory_limit} per worker")
    mem_est_gb = chunk_size ** 2 * sample_overlap * 4 / 1e9
    print(f"  Tiles/chunk (est) : ~{sample_overlap} overlapping a central chunk")
    print(f"  Est. peak RAM/worker: {mem_est_gb:.1f} GB  "
          f"(total: {mem_est_gb * n_workers:.1f} GB)")

    # 4. Build argument tuples for each chunk (one per worker call)
    chunk_args = [
        (
            tiles,
            spec["row_off"], spec["col_off"],
            spec["chunk_h"], spec["chunk_w"],
            grid, nodata, scale, savi_L,
            str(tmp_dir),
        )
        for spec in chunk_specs
    ]

    # 5. Process chunks with ProcessPoolExecutor.
    #    This uses plain Python multiprocessing — no network stack, no Dask
    #    scheduler ports, works cleanly on SLURM/HPC systems.
    #    Each worker process is fully independent: opens its own file handles,
    #    has its own GDAL environment, no shared state.
    from concurrent.futures import ProcessPoolExecutor, as_completed as cf_as_completed
    import multiprocessing

    # 'spawn' avoids inheriting parent GDAL/rasterio state into workers,
    # which can cause silent corruption on some HPC GDAL builds.
    ctx = multiprocessing.get_context("spawn")

    chunk_paths_by_index: dict[str, list[str]] = {n: [] for n in INDEX_NAMES}
    done = 0
    skipped = 0

    print(f"\n  Processing...\n")
    with ProcessPoolExecutor(max_workers=n_workers, mp_context=ctx) as pool:
        future_map = {
            pool.submit(process_chunk, *args): i
            for i, args in enumerate(chunk_args)
        }
        for fut in cf_as_completed(future_map):
            done += 1
            try:
                result: dict[str, str] = fut.result()
                if result:
                    for name, path in result.items():
                        chunk_paths_by_index[name].append(path)
                else:
                    skipped += 1
            except Exception as e:
                print(f"\n  [ERROR] Chunk {future_map[fut]} failed: {e}")
                skipped += 1

            if done % 10 == 0 or done == total_chunks:
                pct = done / total_chunks * 100
                n_written = sum(len(v) for v in chunk_paths_by_index.values()) // len(INDEX_NAMES)
                print(f"  [{done:>6}/{total_chunks}  {pct:5.1f}%]  "
                      f"written={n_written}  nodata_skip={skipped}", flush=True)

    # 7. Assemble final outputs
    print(f"\nStep 4 — Assembling final GeoTIFFs from chunk files...")
    final_paths = assemble_vrt_and_translate(chunk_paths_by_index, out_dir, grid)

    # 8. Clean up temp chunks
    if not keep_chunks:
        print("\nStep 5 — Cleaning up chunk files...")
        shutil.rmtree(tmp_dir, ignore_errors=True)
        print(f"  Removed {tmp_dir}")
    else:
        print(f"\n  --keep_chunks set: chunk files retained in {tmp_dir}")

    print("\n=== Pipeline complete ===")
    print(f"Outputs: {out_dir}")
    for name, p in final_paths.items():
        print(f"  {p}")


# CLI

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Dask-parallelised Sentinel-2 spectral indices over a large ROI."
    )
    parser.add_argument(
        "--tiles_dir", type=Path,
        default=Path("/work_bgfs/h/harrisonjachec/drc_ebola/sentinel2_tiles"),
    )
    parser.add_argument(
        "--out_dir", type=Path,
        default=Path("/work_bgfs/h/harrisonjachec/drc_ebola/indices"),
    )
    parser.add_argument(
        "--chunk_size", type=int, default=4096,
        help="Chunk edge in pixels (default 4096). Reduce to 2048 if OOM.",
    )
    parser.add_argument(
        "--n_workers", type=int, default=4,
        help="Number of parallel worker processes (default 4).",
    )
    parser.add_argument(
        "--memory_limit",
        type=str,
        default="12GB",
        help="Maximum memory per worker process.",
    )
    parser.add_argument("--savi_L",  type=float, default=0.5)
    parser.add_argument("--nodata",  type=float, default=0)
    parser.add_argument("--scale",   type=float, default=10000.0)
    parser.add_argument(
        "--keep_chunks", action="store_true",
        help="Do not delete intermediate chunk GeoTIFFs after assembly.",
    )
    args = parser.parse_args()

    run(
        tiles_dir=args.tiles_dir,
        out_dir=args.out_dir,
        chunk_size=args.chunk_size,
        n_workers=args.n_workers,
        memory_limit=args.memory_limit,
        savi_L=args.savi_L,
        nodata=args.nodata,
        scale=args.scale,
        keep_chunks=args.keep_chunks,
    )
