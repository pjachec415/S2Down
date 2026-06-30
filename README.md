> *For research use only. Not for clinical decisions. Copyright (c) 2026, Payton Jachec*

# S2Down
A pipeline for downloading tiles, cleaning, and computing standard geospatial analyses (NDVI, 
NDWI, SAVI, MNDWI, DEM) over large extents without API keys or access credentials.

## S2Down Contents
- download_rgb.py
  - Downloads spectral bands (all except DEM) from planetary-computer for the specified extent.
- download_dem.py
  - Downloads DEM tiles for specified extent.
- download_lulc.py
  - Downloads LULC tiles for specified extent.
- clean_tiles.py
  - Removes or moves unfinished or broken tiles (allows dry-runs).
- repair_missing_bands.py
  - Lists missing band files, and retries downlaods for missing band files until all files are downloaded.
- median_composite.py
  - Collects tiles and computes median mosaic in chunks for resource management.
- compute_indices.py
  - collects mosaics from median_composite.py and computes analysis indices.
- stitch_dem.py
  - Stitches DEM tiles together.
- stitch_lulc.py
  - Stitches LULC tiles together.
- reproject.py
  - Reprojects harmonized mosaic to a differest projection using another file as a template.
- environment.yml
  - Dependencies list for virtual environment.
 
## Required Packages and Environment
### Known Working Configuration
| Package | Version |
| ----------- | ----------- |
| arrow | 1.4.0 |
| bokeh | 3.9.0 |
| dask | 2026.3.0 |
| distributed | 2026.3.0 |
| mgrs | 1.5.4 |
| numpy | 2.4.6 |
| pandas | 3.0.3 |
| planetary-computer | 1.0.0 |
| pystac-client | 0.9.0 |
| pystac | 1.14.3 |
| python | 3.11.15 |
| rasterio | 1.4.4 |
| scipy | 1.17.1 |
| stackstac | 0.5.0 |
| xarray | 2026.4.0 |

### Required Channels
- conda-forge

### Environment Setup
It is recommended to use Anaconda or Mamba to create a venv for this program suite. 
If running on an HPC, default configurations will almost certainly break the code. 

**Suggested Environment**

> ~] $ micromamba create -n ENV_NAME python=3.11.15 -c conda-forge bokeh dask distributed mgrs numpy pandas>=2.2.3 planetary-computer pystac-client rasterio scipy stackstac xarray

or using the **included environment.yml**
> ~] $ micromamba create -n ENV_NAME environment.yml

## Job Submission
### download_rgb.py
To submit a job using this script, open the script and modify the following fields:
| Field | Line | Use |
| --------- | -------- | -------- |
| OUTPUT_DIR | 22 | Specifies desired output directory |
| BANDS | 23 | Selects bands to download over the ROI |
| TARGET_RES | 24 | Resolution to resample to (set to 10 to turn off) |
| START_DATE | 25 | First Date to search for tiles inside the ROI |
| END_DATE | 26 | Last date to search for tiles inside the ROI |
| MAX_CLOUD_COVER | 27 | Sets cloud cover max. percentage |
| ROI_BOX | 28 | Modifies the bounds of the extent |
| WORKERS | 35 | Number of workers for Dask array |
| THREADS | 36 | Number of threads/worker |
| MEMLIMIT | 37 | Working memory limit for workers |

Then, simply:
> ~] $ python3 download_rgb.py

### download_dem.py
This is done similarly to download_rgb.py, but notably the DEM does not have any cloud cover, 
bands, or timescale, so there are less fields to change in download_dem.py:
| Field | Line | Use |
| -------- | -------- | -------- |
| BASE_DIR | 14 | Sets base directory for current location of all tiles |
| DEM_DIR | 15 | Specifies subdirectory name for DEM tiles |
| WEST | 17 | Sets western extent |
| SOUTH | 18 | Sets southern extent |
| EAST | 19 | Sets eastern extent |
| NORTH | 20 | Sets northern extent |

Then, simply:
> ~] $ python3 download_dem.py

### download_lulc.py
Very similar to download_dem.py.
| Field | Line | Use |
| -------- | -------- | -------- |
| OUTPUT_DIR | 11 | Sets output directory |
| BBOX | 14 | Sets side boundaries of bounding box |
| YEAR | 15 | Sets year to download |
| OVERWRITE | 18 | Toggles overwrite permissions for output image |

Then, 
> ~] $ python3 download_lulc.py

### clean_tiles.py
Similarly to the download scripts, this script requires you to change some values within it to run properly. 
| Field | Line | Use |
| -------- | -------- | -------- |
| DEFAULT_TILES_DIR | 17 | Changes default master imagery directory |
| REQUIRED_BAND_PATTERNS | 21 | Add, remove, or change entries to change required image patterns |

Then, simply:
> ~] $ python3 clean_tiles.py

**Flags**
1. --tiles-dir /PATH/TO/TILES/DIR

   - Specifies alternative filepath to master tiles folder default is "./sentinel2_tiles"

2. --dry-run
 
   - Validates code before moving all unfinished folders to "incomplete"

### repair_missing_bands.py
This script searches for missing band files and redownloads then.

| Field | Line | Use |
| -------- | -------- | -------- |
| OUTPUT_DIR | 26 | Sets file path to search for for tiles |
| BANDS | 28 | Sets band names to search for in each scene folder |
| TARGET_RES | 30 | Sets target resolution in meters at equator |
| N_WORKERS | 31 | Sets number of workers for task parallelization |
| MEMORY_LIMIT | 32 | Sets memory limit per worker |

Then,
> ~] $ python3 repair_missing_bands.py

### median_composite.py
This script should be submitted with the following flags:
| Flag | Req./Opt. | Use |
| -------- | -------- | -------- |
| --tiles_dir | Req. | Sets path to parent tile directory |
| --out_dir | Req. | Sets output directory |
| --chunk_size | Opt. | Sets chunk side length in pixels |
| --n_workers | Opt. | Sets number of workers for parallel processing |
| --memory_limit | Opt. | Sets RAM limit *per worker* |
| --nodata | Opt. | Sets default value for pixels with no data |
| --scale | Opt. | Sets scale for input images to scale down to 0-1 range |
| --keep_chunks | Opt. | If used, chunks will not be deleted |

Example submission:
> ~] $ python3 median_composite.py --tiles_dir /PATH/TO/TILES/DIRECTORY/ --out_dir /PATH/TO/OUTPUT/DIRECTORY/ \
>      --chunk_size 4096 --n_workers 4 --memory_limit 12GB --nodata 0 --scale 10000 --keep_chunks

### compute_indices.py
This script computes the analysis indices from the mosaiced images created by median_composite.py

| Field | Line | Use |
| -------- | -------- | -------- |
| BAND_PATHS | 14-19 | Sets filepaths to individual band images |
| OUT_DIR | 23 | Sets output directory |
| COMPUTE | 26-32 | Selects indices to compute |
| MNDVI_C1 | 36 | Sets multiplier for MNDVI numerator |
| MNDVI_C2 | 37 | Sets multiplier for MNDVI denominator |
| SAVI_L | 40 | Sets SAVI L value |
| SABI_L | 43 | Sets SABI L value |
| CHUNK_ROWS | 46 | Sets chunk (row) height | 
| NODATA_OUT | 47 | Sets value to fill for nodata pixels |
| CLAMP | 48 | Toggles clamping on/off |
| COMPRESS | 49 | Sets compression method |
| OVERWRITE | 50 | Toggles overwriting existing output files |

Then, 
> ~] $ python3 compute_indicies.py

### stitch_dem.py
This script stitches the DEM tiles downloaded with download_dem.py together into a composite image.

| Field | Line | Use |
| -------- | -------- | -------- |
| DEM_DIR | 22 | Sets DEM tile source directory |
| OUT_PATH | 25 | Sets output path |
| NODATA_IN | 28 | Sets default value to fill for NaN pixels on input |
| NODATA_OUT | 29 | Sets default value to fill for NaN pixels on output |
| OUT_DTYPE | 33 | Sets output data format |
| COMPRESS | 36 | Sets compression method |
| OVERWRITE | 39 | Toggles overwriting of existing files in output directory |

Then,
> ~] $ python3 stitch_dem.py

### stitch_lulc.py
This script stitches the LULC files downloaded with download_lulc.py together into a composite image.

| Field | Line | Use |
| -------- | -------- | -------- |
| LULC_DIR | 11 | Sets source directory for LULC tiles |
| OUT_FILE | 12 | Sets desired output filepath |
| YEAR | 13 | sets year to harmonize tiles of a specific year |

Then,
> ~] $ python3 stitch_lulc.py

### reproject.py
This script rreprojects an image to another projection using a third file as a format template.

| Line | Use |
| -------- | -------- |
| 13 | Sets filepath for image to pull formatting from |
| 19 | Sets filepath to source file to reproject |
| 33 | Sets path for output file |

Then,
> ~] $ python3 reproject.py

> *For research use only. Not for clinical decisions. Copyright (c) 2026, Payton Jachec*
