####################################################
# clean_tiles.py # Cleans up unfinished tiles.     #
# ------------------------------------------------ #
# (c) Payton Jachec 2026. | harrisonjachec@usf.edu #
# Disclaimer: For research purposes only, not for  #
# clinical use.                                    #
####################################################

import rasterio
from rasterio.warp import reproject, Resampling
import numpy as np

with rasterio.open("/PATH/TO/IMAGE/TO/PULL/FORMAT/FROM.tif") as ref:
    ref_profile = ref.profile.copy()
    ref_transform = ref.transform
    ref_crs = ref.crs
    ref_shape = (ref.height, ref.width)

with rasterio.open("/PATH/TO/FILE/TO/REPROJECT.tif") as src:
    dem_resampled = np.empty(ref_shape, dtype=np.float32)
    reproject(
        source=rasterio.band(src, 1),
        destination=dem_resampled,
        src_transform=src.transform,
        src_crs=src.crs,
        dst_transform=ref_transform,
        dst_crs=ref_crs,
        resampling=Resampling.bilinear,
    )

out_profile = ref_profile.copy()
out_profile.update(dtype="float32", count=1, nodata=-9999)
with rasterio.open("/PATH/TO/OUTPUT/FILE.tif", "w", **out_profile) as dst:
    dst.write(dem_resampled, 1)
