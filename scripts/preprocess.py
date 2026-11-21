"""PDF page -> clean, deskewed, binarized, downscaled PNG + sidecar JSON + pixel-grid PNG."""

import argparse
import json
from pathlib import Path

import cv2
import numpy as np
import pymupdf

import plans

GRID_STEP = 100
MAX_SKEW_DEG = 10.0


def render(pdf: Path, page: int, dpi: int) -> np.ndarray:
    with pymupdf.open(pdf) as doc:
        pix = doc[page - 1].get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY, alpha=False)
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width).copy()


def skew_angle(gray: np.ndarray) -> float:
    """Dominant line angle (deg) within ±MAX_SKEW_DEG of the axes; 0.0 when no lines are found."""
    edges = cv2.Canny(gray, 50, 150)
    min_len = max(gray.shape) // 10
    lines = cv2.HoughLinesP(edges, 1, np.pi / 1800, threshold=80, minLineLength=min_len, maxLineGap=5)
    if lines is None:
        return 0.0
    x1, y1, x2, y2 = lines.reshape(-1, 4).T.astype(float)
    angles = (np.degrees(np.arctan2(y2 - y1, x2 - x1)) + 45) % 90 - 45
    angles = angles[np.abs(angles) <= MAX_SKEW_DEG]
    return float(np.median(angles)) if angles.size else 0.0


def rotate(gray: np.ndarray, deg: float) -> np.ndarray:
    h, w = gray.shape
    m = cv2.getRotationMatrix2D((w / 2, h / 2), deg, 1.0)
    return cv2.warpAffine(gray, m, (w, h), flags=cv2.INTER_CUBIC, borderValue=255)


def binarize(gray: np.ndarray, method: str) -> np.ndarray:
    """Walls black (0) on white (255)."""
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    if method == "adaptive":
        return cv2.adaptiveThreshold(blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 51, 15)
    if method != "otsu":
        raise ValueError(f"binarize must be otsu or adaptive, got {method!r}")
    return cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]


def remove_specks(binary: np.ndarray, min_px: int) -> np.ndarray:
    _, labels, stats, _ = cv2.connectedComponentsWithStats(255 - binary, connectivity=8)
    small = np.flatnonzero(stats[:, cv2.CC_STAT_AREA] < min_px)
    small = small[small != 0]  # label 0 is the white background
    out = binary.copy()
    out[np.isin(labels, small)] = 255
    return out


def downscale(binary: np.ndarray, max_side: int) -> tuple[np.ndarray, float]:
    scale = min(1.0, max_side / max(binary.shape))
    if scale == 1.0:
        return binary, 1.0
    w, h = round(binary.shape[1] * scale), round(binary.shape[0] * scale)
    small = cv2.resize(binary, (w, h), interpolation=cv2.INTER_AREA)
    return cv2.threshold(small, 160, 255, cv2.THRESH_BINARY)[1], scale


def grid(binary: np.ndarray, step: int = GRID_STEP) -> np.ndarray:
    img = cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR)
    h, w = binary.shape
    for x in range(0, w, step):
        cv2.line(img, (x, 0), (x, h - 1), (0, 0, 255), 1)
        cv2.putText(img, str(x), (x + 2, 12), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 0, 255), 1)
    for y in range(0, h, step):
        cv2.line(img, (0, y), (w - 1, y), (0, 0, 255), 1)
        cv2.putText(img, str(y), (2, y + 12), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 0, 255), 1)
    return img


def preprocess(floor: plans.Floor) -> dict:
    gray = render(floor.pdf, floor.page, floor.dpi)
    angle = skew_angle(gray)
    if abs(angle) > 0.05:
        gray = rotate(gray, angle)
    clean = remove_specks(binarize(gray, floor.binarize), floor.min_speck_px)
    out, scale = downscale(clean, floor.max_side)
    floor.png.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(floor.png), out)
    cv2.imwrite(str(floor.grid), grid(out))
    meta = {
        "pdf": str(floor.pdf),
        "page": floor.page,
        "dpi": floor.dpi,
        "deskew_deg": round(angle, 3),
        "downscale": scale,
        "width": out.shape[1],
        "height": out.shape[0],
    }
    floor.meta.write_text(json.dumps(meta, indent=2) + "\n")
    return meta


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--plans", type=Path, default=plans.ROOT / "plans.yaml")
    p.add_argument("--only", help="process only this floor name")
    a = p.parse_args()
    for floor in plans.load(a.plans):
        if a.only and floor.name != a.only:
            continue
        meta = preprocess(floor)
        print(f"{floor.name}: {floor.png} {meta['width']}x{meta['height']} deskew={meta['deskew_deg']}°")


if __name__ == "__main__":
    main()
