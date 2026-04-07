"""
app/routes/canopy.py
POST /canopy-count — tree and canopy detection from a Birdscale GeoTIFF
orthomosaic using OpenCV.

Data source:
  • Uploaded Birdscale drone orthomosaic (GeoTIFF)
  • Detection via OpenCV SimpleBlobDetector + Watershed segmentation
"""

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.models.canopy import CanopyResponse
from app.services.canopy import detect_canopies

router = APIRouter(tags=["Canopy Intelligence"])


@router.post(
    "/canopy-count",
    response_model=CanopyResponse,
    summary="Tree canopy detection from a GeoTIFF orthomosaic",
    description=(
        "Accepts a multipart file upload of a **Birdscale GeoTIFF** drone "
        "orthomosaic. Reads the image using rasterio, down-samples if "
        "necessary, then runs two detection algorithms — OpenCV "
        "**SimpleBlobDetector** and **watershed segmentation** — returning "
        "the result from whichever method yields higher confidence. "
        "Provides total canopy count, density per acre, mean canopy area, "
        "stressed-canopy count, and a confidence score."
    ),
)
async def post_canopy_count(
    file: UploadFile = File(
        ...,
        description="GeoTIFF orthomosaic image captured by Birdscale drone",
    ),
) -> CanopyResponse:
    """
    ### Accepts
    - **file** — multipart-uploaded GeoTIFF orthomosaic

    ### Returns
    - **total_canopy_count** — detected tree canopies
    - **density_per_acre** — count / approximate image area in acres
    - **mean_canopy_area_px** — average canopy size in pixels
    - **stressed_canopy_count** — canopies flagged as potentially stressed
    - **confidence_score** — 0.0 → 1.0
    - **detection_method** — 'blob' or 'watershed'
    """

    # ── Validate content type ─────────────────────────────────────────────────
    allowed_types = {
        "image/tiff",
        "image/geotiff",
        "image/x-geotiff",
        "application/octet-stream",
        "application/x-geotiff",
    }
    content_type = (file.content_type or "").lower()
    if content_type not in allowed_types and not file.filename.lower().endswith(
        (".tif", ".tiff")
    ):
        raise HTTPException(
            status_code=422,
            detail=(
                f"Uploaded file does not appear to be a GeoTIFF "
                f"(content_type={content_type!r}). "
                "Please upload a valid .tif / .tiff file."
            ),
        )

    # ── Read file bytes ───────────────────────────────────────────────────────
    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(
            status_code=422,
            detail="Uploaded file is empty.",
        )

    # ── Service call ──────────────────────────────────────────────────────────
    try:
        data = await detect_canopies(file_bytes)

    except ValueError as exc:
        # Invalid GeoTIFF — client's fault
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        )

    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                f"OpenCV processing failed: {exc}. "
                "The image may be corrupt or unsupported. Please try again."
            ),
        )

    except Exception as exc:
        error_name = type(exc).__name__
        raise HTTPException(
            status_code=503,
            detail=(
                f"Canopy detection failed ({error_name}): {exc}. "
                "Please try again with a different image."
            ),
        )

    return CanopyResponse(**data)
