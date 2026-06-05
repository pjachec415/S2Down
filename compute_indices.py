####################################################
# compute_indices.py # Computes GeoSp. Indices     #
# ------------------------------------------------ #
# (c) Payton Jachec 2026. | harrisonjachec@usf.edu #
# Disclaimer: For research purposes only, not for  #
# clinical use.                                    #
####################################################



#  CONFIG — edit everything in this section

# ── Input band GeoTIFFs (output of median_composite.py)
BAND_PATHS = {
    "B02": "/PATH/TO/BAND/B02.tif",  # Blue
    "B03": "/PATH/TO/BAND/B03.tif",  # Green
    "B04": "/PATH/TO/BAND/B04.tif",  # Red
    "B08": "/PATH/TO/BAND/B08.tif",  # NIR
    "B11": "/PATH/TO/BAND/B11.tif",  # SWIR
}

# ── Output directory
OUT_DIR = "/PATH/TO/OUTPUT/DIRECTORY"

# ── Toggle indices on/off
COMPUTE = {
    "NDVI":  True,
    "NDWI":  True,
    "MNDVI": True,
    "MNDWI": True,
    "SAVI":  True,
    "SABI":  True,
}

# ── MNDVI parameters
MNDVI_C1 = 1.0   # Numerator scaling constant   : c1 * (NIR - Red)
MNDVI_C2 = 1.0   # Denominator scaling constant : c2 * (NIR + Red)

# ── SAVI parameter
SAVI_L = 0.5     # Soil brightness correction factor (0 = dense veg, 1 = bare soil)

# ── SABI parameter
SABI_L = 0.5     # Soil brightness correction factor (independent of SAVI_L)

# ── Processing
CHUNK_ROWS   = 4096   # Rows to process at once — reduce if RAM is tight
NODATA_OUT   = float("nan")   # Output nodata value
CLAMP        = True           # Clamp output to [-1, 1]  (set False to keep raw values)
COMPRESS     = "lzw"          # GeoTIFF compression: "lzw", "deflate", "none"
OVERWRITE    = False          # Overwrite existing output files

#  END CONFIG


import math
import warnings
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling

warnings.filterwarnings("ignore")



# Index formulae
# Each function receives named float32 arrays (blue, green, red, nir, swir)
# and returns a float32 array. Division by zero → NaN automatically because
# inputs are float32 and numpy's divide behavior is set below.


np.seterr(divide="ignore", invalid="ignore")


def compute_ndvi(*, red, nir, **_) -> np.ndarray:
    return (nir - red) / (nir + red)


def compute_ndwi(*, green, nir, **_) -> np.ndarray:
    return (green - nir) / (green + nir)


def compute_mndvi(*, red, nir, c1=MNDVI_C1, c2=MNDVI_C2, **_) -> np.ndarray:
    return (c1 * (nir - red)) / (c2 * (nir + red))


def compute_mndwi(*, green, swir, **_) -> np.ndarray:
    return (green - swir) / (green + swir)


def compute_savi(*, red, nir, L=SAVI_L, **_) -> np.ndarray:
    return ((nir - red) / (nir + red + L)) * (1.0 + L)


def compute_sabi(*, nir, swir, L=SABI_L, **_) -> np.ndarray:
    return ((swir - nir) / (swir + nir + L)) * (1.0 + L)


INDEX_FUNCS = {
    "NDVI":  compute_ndvi,
    "NDWI":  compute_ndwi,
    "MNDVI": compute_mndvi,
    "MNDWI": compute_mndwi,
    "SAVI":  compute_savi,
    "SABI":  compute_sabi,
}

# Which input bands each index requires
INDEX_BANDS = {
    "NDVI":  ["red", "nir"],
    "NDWI":  ["green", "nir"],
    "MNDVI": ["red", "nir"],
    "MNDWI": ["green", "swir"],
    "SAVI":  ["red", "nir"],
    "SABI":  ["nir", "swir"],
}

BAND_KEYS = {
    "B02": "blue",
    "B03": "green",
    "B04": "red",
    "B08": "nir",
    "B11": "swir",
}

# Helpers

def _required_bands(active_indices: list[str]) -> set[str]:
    """Return the set of semantic band names needed by the active indices."""
    needed: set[str] = set()
    for idx in active_indices:
        needed.update(INDEX_BANDS[idx])
    return needed


def _sentinel_band_for(semantic: str) -> str:
    """Map semantic name → Sentinel band key."""
    inv = {v: k for k, v in BAND_KEYS.items()}
    return inv[semantic]


def _open_bands(
    active_indices: list[str],
) -> tuple[dict[str, rasterio.DatasetReader], rasterio.profiles.Profile]:
    """
    Open only the band files required by the active indices.
    Returns datasets dict keyed by semantic name, reference profile.
    Raises if any required file is missing.
    """
    needed = _required_bands(active_indices)
    datasets: dict[str, rasterio.DatasetReader] = {}
    profile = None

    for semantic in sorted(needed):
        band_key = _sentinel_band_for(semantic)
        path = Path(BAND_PATHS[band_key])
        if not path.exists():
            raise FileNotFoundError(
                f"Required band file not found: {path}\n"
                f"  (needed for: {[i for i in active_indices if semantic in INDEX_BANDS[i]]})"
            )
        ds = rasterio.open(path)
        datasets[semantic] = ds
        if profile is None:
            profile = ds.profile.copy()

    return datasets, profile


# Main

def run() -> None:
    print("\n=== Spectral Index Computation ===\n", flush=True)

    # Validate config
    active = [name for name, enabled in COMPUTE.items() if enabled]
    if not active:
        print("No indices enabled in COMPUTE — nothing to do.", flush=True)
        return

    print(f"Indices to compute : {', '.join(active)}", flush=True)
    print(f"MNDVI c1={MNDVI_C1}  c2={MNDVI_C2}", flush=True)
    print(f"SAVI  L={SAVI_L}    SABI L={SABI_L}", flush=True)
    print(f"Clamp to [-1,1]   : {CLAMP}", flush=True)
    print(f"Overwrite existing : {OVERWRITE}\n", flush=True)

    out_dir = Path(OUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Open required input bands
    print("Opening input bands...", flush=True)
    datasets, ref_profile = _open_bands(active)
    for semantic, ds in datasets.items():
        print(f"  {semantic:6s} ({_sentinel_band_for(semantic)})  "
              f"{ds.width}×{ds.height}px  {ds.crs}", flush=True)

    height = ref_profile["height"]
    width  = ref_profile["width"]
    n_chunks = math.ceil(height / CHUNK_ROWS)

    # Prepare output files
    out_profile = ref_profile.copy()
    out_profile.update(
        dtype="float32",
        count=1,
        compress=COMPRESS if COMPRESS != "none" else None,
        tiled=True,
        blockxsize=512,
        blockysize=512,
        nodata=NODATA_OUT,
        bigtiff="YES",
    )

    writers: dict[str, rasterio.DatasetWriter] = {}
    for name in active:
        out_path = out_dir / f"{name}.tif"
        if out_path.exists() and not OVERWRITE:
            print(f"  [SKIP] {out_path} already exists (set OVERWRITE=True to replace).",
                  flush=True)
            continue
        writers[name] = rasterio.open(out_path, "w", **out_profile)
        print(f"  [OUT]  {out_path}", flush=True)

    if not writers:
        print("\nAll outputs already exist — nothing to write.", flush=True)
        for ds in datasets.values():
            ds.close()
        return

    # Process in row-chunks
    print(f"\nProcessing {n_chunks} chunks of {CHUNK_ROWS} rows...\n", flush=True)

    for chunk_idx in range(n_chunks):
        row_off  = chunk_idx * CHUNK_ROWS
        row_end  = min(row_off + CHUNK_ROWS, height)
        rows     = row_end - row_off
        window   = rasterio.windows.Window(0, row_off, width, rows)

        pct = (chunk_idx + 1) / n_chunks * 100
        print(f"  [{chunk_idx + 1:>4}/{n_chunks}  {pct:5.1f}%]  "
              f"rows {row_off}–{row_end}", flush=True)

        # Load required bands for this window
        arrays: dict[str, np.ndarray] = {}
        for semantic, ds in datasets.items():
            arr = ds.read(1, window=window).astype(np.float32)
            # Mask nodata (0 or NaN from upstream)
            arr = np.where((arr == 0) | np.isnan(arr), np.nan, arr)
            arrays[semantic] = arr

        # Compute each active index
        for name, fn in INDEX_FUNCS.items():
            if name not in writers:
                continue

            result = fn(**arrays).astype(np.float32)

            if CLAMP:
                result = np.clip(result, -1.0, 1.0)

            # Propagate NaN where any input band was NaN
            nan_mask = np.zeros((rows, width), dtype=bool)
            for semantic in INDEX_BANDS[name]:
                nan_mask |= np.isnan(arrays[semantic])
            result[nan_mask] = NODATA_OUT

            writers[name].write(result, 1, window=window)

    # Close everything
    for ds in datasets.values():
        ds.close()
    for name, w in writers.items():
        w.close()
        size_mb = (out_dir / f"{name}.tif").stat().st_size / 1e6
        print(f"  ✓ {name}.tif  {size_mb:.1f} MB", flush=True)

    print("\n=== Done ===", flush=True)
    print(f"Outputs in: {out_dir}", flush=True)


if __name__ == "__main__":
    run()
