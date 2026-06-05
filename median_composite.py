####################################################
# median_composite.py # Mosaic & median s2 images  #
# ------------------------------------------------ #
# (c) Payton Jachec 2026. | harrisonjachec@usf.edu #
# Disclaimer: For research purposes only, not for  #
# clinical use.                                    #
####################################################
"""
Usage
-----
    python median_composite.py \\
        --tiles_dir /PATH/TO/TILES/DIRECTORY \\
        --out_dir   /PATH/TO/OUTPUT/DIRECTORY \\
        [--chunk_size 4096] \\
        [--n_workers 4] \\
        [--memory_limit 12GB] \\
        [--nodata 0] \\
        [--scale 10000] \\
        [--keep_chunks]
"""

import argparse
import gc
import math
import multiprocessing
import shutil
import subprocess
import warnings
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.warp import reproject, transform_bounds

warnings.filterwarnings("ignore", category=rasterio.errors.NotGeoreferencedWarning)
warnings.filterwarnings("ignore", category=UserWarning)



# Constants


BANDS = ["B02", "B03", "B04", "B08", "B11"]
RES_DEG = 20.0 / 111320.0  # ~0.00017966° — 20 m at equator in degrees
SKIP_DIRS = {"DEM", "dem", "incomplete", "tmp", "temp", "_chunks"}

BAND_PATTERNS: dict[str, list[str]] = {
    "B02": ["*B02*.tif", "*_B02.tif", "*band2*.tif", "*blue*.tif"],
    "B03": ["*B03*.tif", "*_B03.tif", "*band3*.tif", "*green*.tif"],
    "B04": ["*B04*.tif", "*_B04.tif", "*band4*.tif", "*red*.tif"],
    "B08": ["*B08*.tif", "*_B08.tif", "*band8*.tif", "*nir*.tif"],
    "B11": ["*B11*.tif", "*_B11.tif", "*band11*.tif", "*swir*.tif"],
}


# Band detection

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
    Collect band file paths for every complete tile directory.
    Returns a list of dicts mapping band name → absolute path (as str).
    Incomplete tiles (missing any band) are skipped with a warning.
    """
    tile_dirs = sorted(
        d for d in tiles_dir.iterdir()
        if d.is_dir() and d.name not in SKIP_DIRS
    )
    if not tile_dirs:
        raise RuntimeError(f"No tile subdirectories found in {tiles_dir}")

    tiles, skipped = [], 0
    for td in tile_dirs:
        entry: dict[str, str] = {}
        ok = True
        for band in BANDS:
            try:
                entry[band] = str(find_band(td, band))
            except FileNotFoundError as e:
                print(f"  [SKIP] {e}", flush=True)
                ok = False
                break
        if ok:
            tiles.append(entry)
        else:
            skipped += 1

    print(f"  Found {len(tiles)} complete tiles ({skipped} skipped).", flush=True)
    return tiles


# Spatial index — built once on the main process

def build_spatial_index(
    tiles: list[dict[str, str]],
    target_crs: str = "EPSG:4326",
) -> tuple[dict, list[tuple[float, float, float, float]]]:
    """
    Read only the B02 header of each tile to get its extent, reproject to
    target_crs, and return:
      grid dict: CRS, pixel resolution, full mosaic dimensions & bounds
      and tile_bounds: list of (left, bottom, right, top) in target_crs, --> same order as `tiles`
    Called once on the main process; workers never touch this.
    """
    out_crs = CRS.from_string(target_crs)
    out_crs_wkt = out_crs.to_wkt()
    unique_epsg: set[int] = set()
    tile_bounds: list[tuple[float, float, float, float]] = []

    print(f"  Scanning {len(tiles)} tile headers → reprojecting to {target_crs}...",
          flush=True)

    for tile in tiles:
        with rasterio.open(tile["B02"]) as ds:
            unique_epsg.add(ds.crs.to_epsg())
            l, b, r, t = transform_bounds(ds.crs, out_crs, *ds.bounds)
            tile_bounds.append((l, b, r, t))

    left   = min(b[0] for b in tile_bounds)
    bottom = min(b[1] for b in tile_bounds)
    right  = max(b[2] for b in tile_bounds)
    top    = max(b[3] for b in tile_bounds)

    width  = math.ceil((right - left)  / RES_DEG)
    height = math.ceil((top   - bottom) / RES_DEG)

    grid = dict(
        crs_wkt=out_crs_wkt,
        res_x=RES_DEG, res_y=RES_DEG,
        left=left, top=top, right=right, bottom=bottom,
        width=width, height=height,
    )

    print(f"  Source UTM zones : {sorted(unique_epsg)}", flush=True)
    print(f"  Output CRS       : {target_crs}", flush=True)
    print(f"  Extent           : L={left:.4f}  B={bottom:.4f}  "
          f"R={right:.4f}  T={top:.4f}", flush=True)
    print(f"  Mosaic size      : {width:,} × {height:,} px  "
          f"({RES_DEG * 111320:.1f} m res at equator)", flush=True)
    print(f"  Uncompressed/band: ~{width * height * 4 / 1e9:.2f} GB", flush=True)

    return grid, tile_bounds


def intersecting_tiles(
    tiles: list[dict[str, str]],
    tile_bounds: list[tuple[float, float, float, float]],
    chunk_left: float,
    chunk_bot: float,
    chunk_right: float,
    chunk_top: float,
) -> list[dict[str, str]]:
    # Return only the tiles whose extent overlaps the given chunk bbox
    return [
        tile for tile, (l, b, r, t) in zip(tiles, tile_bounds)
        if r > chunk_left and l < chunk_right
        and t > chunk_bot  and b < chunk_top
    ]


# Memory helpers

def _parse_memory_limit(limit: str) -> int:
    limit = limit.upper().strip()
    for suffix, factor in [("TB", 1024**4), ("GB", 1024**3),
                            ("MB", 1024**2), ("KB", 1024)]:
        if limit.endswith(suffix):
            return int(float(limit[:-len(suffix)]) * factor)
    return int(limit)


# Per-chunk worker

def process_chunk(
    tiles: list[dict[str, str]],
    row_off: int,
    col_off: int,
    chunk_h: int,
    chunk_w: int,
    grid: dict,
    nodata: float,
    scale: float,
    tmp_dir: str,
    memory_limit: str,
) -> dict[str, str]:

    import gc
    import resource
    import time
    import warnings

    import numpy as np
    import rasterio
    from pathlib import Path
    from rasterio.crs import CRS
    from rasterio.enums import Resampling
    from rasterio.transform import from_origin
    from rasterio.warp import reproject

    warnings.filterwarnings("ignore")

    # Apply per-worker memory cap via OS rlimit
    if memory_limit:
        try:
            cap = _parse_memory_limit(memory_limit)
            resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
        except Exception:
            pass

    def log(msg: str) -> None:
        print(msg, flush=True)

    chunk_tag = f"row={row_off:07d} col={col_off:07d}"
    t0 = time.time()
    log(f"\n[CHUNK START] {chunk_tag}  ({chunk_h}×{chunk_w}px)  "
        f"{len(tiles)} tiles to load")

    # Compute geographic extent of this chunk
    gt_left  = grid["left"] + col_off * grid["res_x"]
    gt_top   = grid["top"]  - row_off * grid["res_y"]
    gt_right = gt_left + chunk_w * grid["res_x"]
    gt_bot   = gt_top  - chunk_h * grid["res_y"]
    chunk_transform = from_origin(gt_left, gt_top, grid["res_x"], grid["res_y"])
    crs = CRS.from_wkt(grid["crs_wkt"])

    log(f"  Extent: L={gt_left:.4f} B={gt_bot:.4f} "
        f"R={gt_right:.4f} T={gt_top:.4f}")

    out_paths: dict[str, str] = {}
    tag = f"r{row_off:07d}_c{col_off:07d}"

    profile = dict(
        driver="GTiff", dtype="float32", nodata=float("nan"), count=1,
        compress="lzw", tiled=True, blockxsize=256, blockysize=256,
        crs=crs, transform=chunk_transform, width=chunk_w, height=chunk_h,
    )

    all_nodata = True

    for band in BANDS:
        t_band = time.time()
        log(f"  [BAND {band}] Reprojecting {len(tiles)} tiles...")
        stack = []

        for tile in tiles:
            tile_name = Path(tile[band]).parent.name
            with rasterio.open(tile[band]) as ds:
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
                    log(f"    ✓ {tile_name} / {band}")
                except Exception as e:
                    log(f"    ✗ {tile_name} / {band}: {e}")

        if stack:
            log(f"  [BAND {band}] Computing median over {len(stack)} tiles...")
            arr = np.nanmedian(np.stack(stack, axis=0), axis=0).astype(np.float32)
        else:
            log(f"  [BAND {band}] No data.")
            arr = np.full((chunk_h, chunk_w), np.nan, dtype=np.float32)

        del stack
        gc.collect()

        # Scale to [0, 1] reflectance; out-of-range values → NaN
        arr = arr / scale
        arr = np.where((arr < 0) | (arr > 1), np.nan, arr).astype(np.float32)

        valid_px = int(np.sum(~np.isnan(arr)))
        log(f"  [BAND {band}] Done. valid_px={valid_px:,}  "
            f"({time.time() - t_band:.1f}s)")

        if valid_px > 0:
            all_nodata = False

        # Write band chunk — arr is 2-D (H, W); pass directly with band index 1
        p = str(Path(tmp_dir) / f"chunk_{band}_{tag}.tif")
        log(f"  [WRITE] {band} → {p}")
        with rasterio.open(p, "w", **profile) as dst:
            dst.write(arr, 1)  # arr is (H, W) — no newaxis needed
        out_paths[band] = p

    if all_nodata:
        log(f"[CHUNK SKIP] {chunk_tag} — all NaN.")
        # Remove the written (empty) files so they don't pollute the VRT
        for p in out_paths.values():
            Path(p).unlink(missing_ok=True)
        return {}

    log(f"[CHUNK DONE] {chunk_tag}  elapsed={time.time() - t0:.1f}s")
    return out_paths


# VRT assembly + final translate

def assemble_vrt_and_translate(
    chunk_paths_by_band: dict[str, list[str]],
    out_dir: Path,
    grid: dict,
) -> dict[str, Path]:

    # Merge per-band chunk GeoTIFFs into final mosaics via gdalbuildvrt +
    # gdal_translate (preferred) or rasterio.merge (fallback).
    out_dir.mkdir(parents=True, exist_ok=True)
    final_paths: dict[str, Path] = {}
    gdal_available = shutil.which("gdalbuildvrt") is not None

    for band, chunk_files in chunk_paths_by_band.items():
        if not chunk_files:
            print(f"  [WARN] No chunks for {band} — skipping.", flush=True)
            continue

        final_path = out_dir / f"{band}.tif"

        if gdal_available:
            vrt_path  = out_dir / f"{band}.vrt"
            vrt_list  = out_dir / f"{band}_chunks.txt"
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
            print(f" gdalbuildvrt not found — using rasterio merge for {band}",
                  flush=True)
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

        size_gb = final_path.stat().st_size / 1e9
        print(f"  {band}.tif  →  {size_gb:.2f} GB", flush=True)
        final_paths[band] = final_path

    return final_paths


# Main pipeline

def run(
    tiles_dir: Path,
    out_dir: Path,
    chunk_size: int,
    n_workers: int,
    memory_limit: str,
    nodata: float,
    scale: float,
    keep_chunks: bool,
) -> None:

    print("\n=== Sentinel-2 Per-Band Median Composite Pipeline ===\n", flush=True)

    # Collect tile band paths
    print("Step 1 — Collecting tile band paths...", flush=True)
    tiles = collect_tile_band_paths(tiles_dir)

    # Build spatial index (one header read per tile, main process only)
    print("\nStep 2 — Building spatial index...", flush=True)
    grid, tile_bounds = build_spatial_index(tiles)

    # Enumerate chunks and pre-filter tiles per chunk
    print("\nStep 3 — Pre-filtering tiles per chunk...", flush=True)
    n_chunks_x   = math.ceil(grid["width"]  / chunk_size)
    n_chunks_y   = math.ceil(grid["height"] / chunk_size)
    total_chunks = n_chunks_x * n_chunks_y
    chunk_deg    = chunk_size * grid["res_x"]

    tmp_dir = out_dir / "_chunks"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    chunk_args = []
    tile_counts = []
    for row_chunk in range(n_chunks_y):
        row_off = row_chunk * chunk_size
        chunk_h = min(chunk_size, grid["height"] - row_off)
        for col_chunk in range(n_chunks_x):
            col_off = col_chunk * chunk_size
            chunk_w = min(chunk_size, grid["width"] - col_off)

            cl = grid["left"] + col_off * grid["res_x"]
            ct = grid["top"]  - row_off * grid["res_y"]
            cr = cl + chunk_w * grid["res_x"]
            cb = ct - chunk_h * grid["res_y"]

            chunk_tiles = intersecting_tiles(tiles, tile_bounds, cl, cb, cr, ct)
            tile_counts.append(len(chunk_tiles))

            chunk_args.append((
                chunk_tiles,
                row_off, col_off,
                chunk_h, chunk_w,
                grid, nodata, scale,
                str(tmp_dir),
                memory_limit,
            ))

    nonempty  = sum(1 for c in tile_counts if c > 0)
    avg_tiles = sum(tile_counts) / max(nonempty, 1)
    max_tiles = max(tile_counts)

    print(f"  Total chunks     : {total_chunks:,}  "
          f"({n_chunks_y} rows × {n_chunks_x} cols)", flush=True)
    print(f"  Non-empty chunks : {nonempty:,}  "
          f"({total_chunks - nonempty:,} fully outside ROI — will be skipped)",
          flush=True)
    print(f"  Tiles/chunk      : avg={avg_tiles:.1f}  max={max_tiles}", flush=True)
    print(f"  Chunk size       : {chunk_size}px (~{chunk_deg * 111:.0f}km at equator)",
          flush=True)
    print(f"  Memory limit     : {memory_limit} per worker", flush=True)
    mem_est = chunk_size**2 * avg_tiles * 4 / 1e9
    print(f"  Est. peak RAM/worker: {mem_est:.1f} GB  "
          f"(total: {mem_est * n_workers:.1f} GB)", flush=True)

    # 4. Submit chunks to worker pool
    print(f"\nStep 4 — Processing {nonempty:,} non-empty chunks "
          f"across {n_workers} workers...\n", flush=True)

    ctx = multiprocessing.get_context("spawn")
    chunk_paths_by_band: dict[str, list[str]] = {b: [] for b in BANDS}
    done = skipped = 0

    with ProcessPoolExecutor(max_workers=n_workers, mp_context=ctx) as pool:
        future_map = {
            pool.submit(process_chunk, *args): i
            for i, args in enumerate(chunk_args)
            if args[0]  # skip chunks with no intersecting tiles
        }
        for fut in as_completed(future_map):
            done += 1
            try:
                result: dict[str, str] = fut.result()
                if result:
                    for band, path in result.items():
                        chunk_paths_by_band[band].append(path)
                else:
                    skipped += 1
            except Exception as e:
                print(f"\n  [ERROR] Chunk {future_map[fut]} failed: {e}", flush=True)
                skipped += 1

            if done % 10 == 0 or done == len(future_map):
                pct = done / len(future_map) * 100
                n_written = (sum(len(v) for v in chunk_paths_by_band.values())
                             // len(BANDS))
                print(f"  [{done:>6}/{len(future_map)}  {pct:5.1f}%]  "
                      f"written={n_written}  nodata_skip={skipped}", flush=True)

    # Assemble final GeoTIFFs
    print(f"\nStep 5 — Assembling final GeoTIFFs...", flush=True)
    final_paths = assemble_vrt_and_translate(chunk_paths_by_band, out_dir, grid)

    # Clean up
    if not keep_chunks:
        print("\nStep 6 — Cleaning up chunk files...", flush=True)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        print(f"  Removed {tmp_dir}", flush=True)
    else:
        print(f"\n  --keep_chunks set: retained in {tmp_dir}", flush=True)

    print("\n=== Pipeline complete ===", flush=True)
    print(f"Outputs: {out_dir}", flush=True)
    for band, p in final_paths.items():
        print(f"  {p}", flush=True)


# CLI

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Memory-safe Sentinel-2 per-band median compositing "
                    "with spatial pre-indexing."
    )
    parser.add_argument(
        "--tiles_dir", type=Path,
        default=Path("/work_bgfs/h/harrisonjachec/drc_ebola/sentinel2_tiles"),
    )
    parser.add_argument(
        "--out_dir", type=Path,
        default=Path("/work_bgfs/h/harrisonjachec/drc_ebola/median_bands"),
    )
    parser.add_argument(
        "--chunk_size", type=int, default=4096,
        help="Chunk edge in pixels (default 4096 ≈ 82km). Reduce to 2048 if OOM.",
    )
    parser.add_argument(
        "--n_workers", type=int, default=4,
        help="Number of parallel worker processes (default 4).",
    )
    parser.add_argument(
        "--memory_limit", type=str, default="12GB",
        help="Memory cap per worker process (default 12GB).",
    )
    parser.add_argument("--nodata", type=float, default=0)
    parser.add_argument("--scale",  type=float, default=10000.0)
    parser.add_argument(
        "--keep_chunks", action="store_true",
        help="Retain intermediate chunk GeoTIFFs after final assembly.",
    )
    args = parser.parse_args()

    run(
        tiles_dir=args.tiles_dir,
        out_dir=args.out_dir,
        chunk_size=args.chunk_size,
        n_workers=args.n_workers,
        memory_limit=args.memory_limit,
        nodata=args.nodata,
        scale=args.scale,
        keep_chunks=args.keep_chunks,
    )
