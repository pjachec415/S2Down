> *For research use only. Not for clinical decisions. Copyright (c) 2026, Payton Jachec*

# S2Down
A pipeline for downloading tiles, cleaning, and computing standard geospatial analyses (NDVI, 
NDWI, SAVI, MNDWI, DEM) over large extents without API keys or access credentials.

## S2Down Contents
- download_rgb.py
  - Downloads spectral bands (all except DEM) from planetary-computer for the specified extent.
- download_dem.py
  - Downloads DEM tiles for specified extent.
- clean_tiles.py
  - Removes or moves unfinished or broken tiles (allows dry-runs).
- analysis.py
  - Calculates median value of tiles, mosaics tiles, and performs NDVI, NDWI, MNDWI, SAVI, and DEM modeling.
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
### clean_tiles.py
Similarly to the download files, this file requires you to change some values within it to run properly. 
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

### analysis.py
**Available Flags**
| Flag | Use |
| -------- | -------- |
| tiles_dir | Specifies tile source directory |
| out_dir | Specifies output directory |
| chunk_size | specifies chunk side length in px |
| n_workers | specifies amount of workers for parallel chunk processing |
| memory_limit | specifies memory limit per worker (actual amount used depends on number of stacked tiles) |
| savi_L | specifies the value of L for SAVI analyis |
| nodata | specifies the value to fill in for pixels with no found data |
| keep_chunks | if used, program will not delete individual chunks at the end of processing |

**Required Flags**

All jobs submitted with analysis.py require the following flags: tiles_dir, out_dir.
##
Due to the need to re-specify flags every time this file is run, this script is submitted from the terminal. See example below.
> ~] $ python3 analysis.py --tiles_dir /PATH/TO/DIRECTORY/sentinel2_tiles --out_dir /PATH/TO/OUTPUT/DIRECTORY --chunk_size 4096 --n_workers 4 --memory_limit 12GB --nodata 0

> *For research use only. Not for clinical decisions. Copyright (c) 2026, Payton Jachec*
