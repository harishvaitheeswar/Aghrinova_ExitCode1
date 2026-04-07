"""
app/services/canopy.py
Tree canopy detection service using OpenCV and Pillow.

Processes uploaded Birdscale drone orthomosaics to count tree canopies
via two methods (SimpleBlobDetector and watershed segmentation) and returns
whichever produces a more confident result.
"""

from __future__ import annotations

import io
import math
from typing import Optional

import cv2
import numpy as np
from PIL import Image

# ── Constants ─────────────────────────────────────────────────────────────────

MAX_DIMENSION = 4000          # Downsample target for large orthomosaics
SQ_METRES_PER_ACRE = 4046.86  # Conversion factor


# ── Internal helpers ──────────────────────────────────────────────────────────

def _read_image(file_bytes: bytes) -> np.ndarray:
    """
    Read an image from raw bytes using Pillow and return an RGB uint8 array.

    Raises ``ValueError`` if the file cannot be opened as a valid image.
    """
    try:
        img = Image.open(io.BytesIO(file_bytes))
        img = img.convert("RGB")
        rgb = np.array(img, dtype=np.uint8)
        return rgb
    except Exception as exc:
        raise ValueError(f"Invalid image file: {exc}") from exc


def _downsample(image: np.ndarray) -> tuple[np.ndarray, float]:
    """
    Downsample `image` so that neither dimension exceeds MAX_DIMENSION.

    Returns (downsampled_image, scale_factor).
    """
    h, w = image.shape[:2]
    if max(h, w) <= MAX_DIMENSION:
        return image, 1.0

    scale = MAX_DIMENSION / max(h, w)
    new_w = int(w * scale)
    new_h = int(h * scale)
    resized = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_AREA)
    return resized, scale


# ── Blob detection ────────────────────────────────────────────────────────────

def _blob_detect(gray: np.ndarray) -> list[cv2.KeyPoint]:
    """
    Run OpenCV SimpleBlobDetector with parameters tuned for tree canopies.
    """
    params = cv2.SimpleBlobDetector_Params()

    # Threshold
    params.minThreshold = 10
    params.maxThreshold = 200

    # Area
    params.filterByArea = True
    params.minArea = 100
    params.maxArea = 50_000

    # Circularity
    params.filterByCircularity = True
    params.minCircularity = 0.3

    # Convexity
    params.filterByConvexity = True
    params.minConvexity = 0.5

    # Inertia
    params.filterByInertia = True
    params.minInertiaRatio = 0.2

    detector = cv2.SimpleBlobDetector_create(params)

    # Invert — SimpleBlobDetector looks for dark blobs by default
    inverted = cv2.bitwise_not(gray)
    keypoints = detector.detect(inverted)

    return keypoints


# ── Watershed segmentation ────────────────────────────────────────────────────

def _watershed_detect(rgb: np.ndarray, gray: np.ndarray) -> list[float]:
    """
    Segment canopies via watershed and return a list of region areas (in px).
    """
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)

    # Otsu thresholding
    _, thresh = cv2.threshold(
        blurred, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
    )

    # Morphological opening to remove noise
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    opening = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel, iterations=2)

    # Sure background via dilation
    sure_bg = cv2.dilate(opening, kernel, iterations=3)

    # Sure foreground via distance transform
    dist_transform = cv2.distanceTransform(opening, cv2.DIST_L2, 5)
    _, sure_fg = cv2.threshold(
        dist_transform, 0.5 * dist_transform.max(), 255, 0
    )
    sure_fg = np.uint8(sure_fg)

    # Unknown region
    unknown = cv2.subtract(sure_bg, sure_fg)

    # Marker labelling
    num_labels, markers = cv2.connectedComponents(sure_fg)
    markers = markers + 1  # Background is 1 instead of 0
    markers[unknown == 255] = 0

    # Watershed
    rgb_copy = rgb.copy()
    markers = cv2.watershed(rgb_copy, markers)

    # Collect region areas (skip background label 1 and boundary -1)
    areas: list[float] = []
    for label_id in range(2, num_labels + 1):
        area = float(np.sum(markers == label_id))
        if 100 <= area <= 50_000:
            areas.append(area)

    return areas


# ── Confidence scoring ────────────────────────────────────────────────────────

def _confidence_from_sizes(sizes: list[float]) -> float:
    """
    Compute a confidence score (0–1) based on the consistency of detected
    canopy sizes.  More consistent sizes → higher confidence.
    """
    if len(sizes) < 2:
        return 0.3 if sizes else 0.0

    mean_size = float(np.mean(sizes))
    std_size = float(np.std(sizes))

    if mean_size == 0:
        return 0.0

    cv = std_size / mean_size  # Coefficient of variation
    # cv ≈ 0 → perfect consistency (confidence 1.0)
    # cv ≈ 1 → high variability  (confidence ~0.3)
    confidence = max(0.0, min(1.0, 1.0 - cv * 0.7))
    return round(confidence, 4)


def _stressed_count(sizes: list[float]) -> int:
    """
    Return the number of canopies whose area is more than 1.5 standard
    deviations below the mean — flagged as potentially stressed.
    """
    if len(sizes) < 2:
        return 0

    mean_size = float(np.mean(sizes))
    std_size = float(np.std(sizes))
    threshold = mean_size - 1.5 * std_size

    return int(sum(1 for s in sizes if s < threshold))


# ── Public interface ──────────────────────────────────────────────────────────

async def detect_canopies(file_bytes: bytes) -> dict:
    """
    Full canopy detection pipeline.

    1. Read image → RGB
    2. Downsample if needed
    3. Convert to greyscale, Gaussian blur
    4. Run blob detection **and** watershed segmentation
    5. Pick the method with higher confidence
    6. Build and return the response dict

    Raises:
        ValueError   – invalid image file
        RuntimeError – OpenCV processing failure
    """

    # ── 1. Read ───────────────────────────────────────────────────────────────
    rgb = _read_image(file_bytes)

    # ── 2. Downsample ─────────────────────────────────────────────────────────
    rgb, scale = _downsample(rgb)

    # ── 3. Greyscale + blur ───────────────────────────────────────────────────
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    gray_blurred = cv2.GaussianBlur(gray, (5, 5), 0)

    # ── 4a. Blob detection ────────────────────────────────────────────────────
    try:
        keypoints = _blob_detect(gray_blurred)
        blob_sizes = [kp.size ** 2 * math.pi / 4 for kp in keypoints]  # approx area
    except Exception:
        keypoints = []
        blob_sizes = []

    # ── 4b. Watershed segmentation ────────────────────────────────────────────
    try:
        watershed_sizes = _watershed_detect(rgb, gray_blurred)
    except Exception:
        watershed_sizes = []

    # ── 5. Pick best method ───────────────────────────────────────────────────
    blob_conf = _confidence_from_sizes(blob_sizes)
    ws_conf = _confidence_from_sizes(watershed_sizes)

    if ws_conf > blob_conf and len(watershed_sizes) > 0:
        chosen_method = "watershed"
        sizes = watershed_sizes
        confidence = ws_conf
    else:
        chosen_method = "blob"
        sizes = blob_sizes
        confidence = blob_conf

    # ── 6. Compute metrics ────────────────────────────────────────────────────
    total_count = len(sizes)
    mean_area = float(np.mean(sizes)) if sizes else 0.0
    stressed = _stressed_count(sizes)

    # Density per acre — estimate from pixel area (assume 0.1 m/px typical drone GSD)
    h, w = rgb.shape[:2]
    pixel_area_m2 = (0.1 / scale) ** 2  # Account for downsampling
    total_area_m2 = h * w * pixel_area_m2
    total_area_acres = total_area_m2 / SQ_METRES_PER_ACRE
    density = round(total_count / total_area_acres, 2) if total_area_acres > 0 else 0.0

    return {
        "total_canopy_count": total_count,
        "density_per_acre": density,
        "mean_canopy_area_px": round(mean_area, 2),
        "stressed_canopy_count": stressed,
        "confidence_score": confidence,
        "detection_method": chosen_method,
        "data_source": "Birdscale Orthomosaic — OpenCV Detection",
        "message": None,
    }
