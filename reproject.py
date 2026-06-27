import rasterio
from rasterio.warp import reproject, Resampling
import numpy as np

with rasterio.open("/work_bgfs/h/harrisonjachec/syphilis_hc/indices/NDVI.tif") as ref:
    ref_profile = ref.profile.copy()
    ref_transform = ref.transform
    ref_crs = ref.crs
    ref_shape = (ref.height, ref.width)

with rasterio.open("/work_bgfs/h/harrisonjachec/syphilis_hc/DEM.tif") as src:
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
with rasterio.open("/work_bgfs/h/harrisonjachec/syphilis_hc/DEM_resampled.tif", "w", **out_profile) as dst:
    dst.write(dem_resampled, 1)
