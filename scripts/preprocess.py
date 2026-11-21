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
FRAME_MARGIN = 8  # work px between the building's silhouette and its outline frame
FRAME_BRIDGE = 101  # work px: exterior door gaps narrower than this close before the silhouette fills
MIN_OUTLINE_COVER = 0.97


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


def wall_mask(binary: np.ndarray, min_px: int, min_area: int) -> np.ndarray:
    """Only strokes >= min_px wide (opening by a min_px ellipse) in components >= min_area px; text and dimension
    lines go. No closing op: door gaps stay open."""
    ink = (binary == 0).astype(np.uint8)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (min_px, min_px))
    a = min_px // 2  # an even kernel is not point-symmetric: dilate by its reflection about the mirrored anchor
    flipped = np.ascontiguousarray(kernel[::-1, ::-1])
    core = cv2.dilate(cv2.erode(ink, kernel, anchor=(a, a)), flipped, anchor=(min_px - 1 - a,) * 2) & ink
    _, labels, stats, _ = cv2.connectedComponentsWithStats(core, connectivity=8)
    keep = np.flatnonzero(stats[:, cv2.CC_STAT_AREA] >= min_area)
    keep = keep[keep != 0]  # label 0 is the background
    return np.where(np.isin(labels, keep), 0, 255).astype(np.uint8)


def outline_cover(binary: np.ndarray) -> float:
    """Largest external contour's bbox over the whole ink bbox (min of x and y extent ratio); 1.0 without ink."""
    contours, _ = cv2.findContours((binary == 0).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return 1.0
    _, _, w, h = cv2.boundingRect(max(contours, key=cv2.contourArea))
    _, _, w_all, h_all = cv2.boundingRect(np.vstack(contours))
    return min(w / w_all, h / h_all)


def outline_frame(binary: np.ndarray, margin: int = FRAME_MARGIN) -> np.ndarray:
    """A 3-px outline `margin` px outside the building's filled silhouette (convex hull when that stays in pieces).

    Upstream keeps only walls inside the largest gray<230 contour and floors that contour; with dimension lines
    masked away that contour would be the outer wall itself and drop it. Upstream's wall opening erases 3-px lines,
    so the frame shapes the floor and the filter without becoming a wall.
    """
    ink = (binary == 0).astype(np.uint8)
    pad = FRAME_BRIDGE  # pad so the closing's erosion sees no ink beyond the image edge
    bridge = cv2.getStructuringElement(cv2.MORPH_RECT, (FRAME_BRIDGE, FRAME_BRIDGE))
    closed = cv2.morphologyEx(
        cv2.copyMakeBorder(ink, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=0), cv2.MORPH_CLOSE, bridge
    )[pad:-pad, pad:-pad]
    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return binary
    silhouette = np.zeros_like(ink)
    cv2.drawContours(silhouette, contours, -1, 1, cv2.FILLED)
    grown = cv2.dilate(silhouette, cv2.getStructuringElement(cv2.MORPH_RECT, (2 * margin + 1, 2 * margin + 1)))
    outer, _ = cv2.findContours(grown, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if len(outer) > 1:
        outer = (cv2.convexHull(np.vstack(outer)),)
    out = binary.copy()
    cv2.drawContours(out, outer, -1, 0, 2)
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


def read_override(path: Path, shape: tuple[int, ...]) -> np.ndarray:
    """The hand-drawn walls PNG, binarized at 128; it must match the auto work PNG it was drawn on."""
    gray = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise ValueError(f"{path}: not a readable image")
    if gray.shape != shape:
        raise ValueError(
            f"{path} is {gray.shape[1]}x{gray.shape[0]} px but the auto work PNG is {shape[1]}x{shape[0]}: "
            "redraw it on a copy of the current work PNG (max_side or dpi changed?)"
        )
    return cv2.threshold(gray, 127, 255, cv2.THRESH_BINARY)[1]


def preprocess(floor: plans.Floor) -> dict:
    gray = render(floor.pdf, floor.page, floor.dpi)
    angle = skew_angle(gray)
    if abs(angle) > 0.05:
        gray = rotate(gray, angle)
    clean = remove_specks(binarize(gray, floor.binarize), floor.min_speck_px)
    masked = wall_mask(clean, floor.wall_min_px, floor.wall_min_area) if floor.wall_min_px else clean
    ink = np.count_nonzero(clean == 0)
    mask_ink_ratio = np.count_nonzero(masked == 0) / ink if ink else 0.0
    out, scale = downscale(masked, floor.max_side)
    cover = outline_cover(out)
    if cover < MIN_OUTLINE_COVER:
        print(f"WARNING {floor.name}: outline_cover {cover:.3f} < {MIN_OUTLINE_COVER} (largest wall block)")
    if floor.wall_min_px:
        out = outline_frame(out)
    walls = "auto"
    if floor.walls_override.is_file():
        out, walls = read_override(floor.walls_override, out.shape), "override"
    floor.png.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(floor.png), out)
    cv2.imwrite(str(floor.grid), grid(out))
    meta = {
        "pdf": str(floor.pdf),
        "page": floor.page,
        "dpi": floor.dpi,
        "deskew_deg": round(angle, 3),
        "downscale": scale,
        "wall_min_px": floor.wall_min_px,
        "mask_ink_ratio": round(mask_ink_ratio, 4),
        "outline_cover": round(cover, 4),
        "walls": walls,
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
