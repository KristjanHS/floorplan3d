"""Synthetic double-line walls: L and T junctions, a door gap, diagonal hatching between the lines."""

import cv2
import numpy as np

PX_PER_CM = 0.8
# Drawn centre lines (px) + thickness (px); each rectangle overhangs its ends by thickness/2 (square corners).
DRAWN = [
    ((100, 100), (500, 100), 16),  # top: L with left, T with middle
    ((100, 100), (100, 500), 16),  # left: L top and bottom
    ((300, 100), (300, 400), 10),  # middle: T into top, free end
    ((100, 500), (500, 500), 16),  # bottom: door gap
]
DOOR_X = (220, 290)  # 70 px = 87.5 cm, cut from the bottom wall
# What detect() should return: junction ends on the centre-line crossing, free ends at the drawn overhang.
EXPECTED = [
    ((100, 100), (508, 100), 16),
    ((100, 100), (100, 500), 16),
    ((300, 100), (300, 405), 10),
    ((100, 500), (508, 500), 16),
]


def plan_image() -> np.ndarray:
    mask = np.zeros((600, 600), np.uint8)
    for (x1, y1), (x2, y2), t in DRAWN:
        h = t // 2
        cv2.rectangle(mask, (min(x1, x2) - h, min(y1, y2) - h), (max(x1, x2) + h, max(y1, y2) + h), 255, -1)
    mask[480:520, DOOR_X[0] : DOOR_X[1]] = 0
    hatch = np.zeros_like(mask)
    for c in range(-600, 1200, 6):
        cv2.line(hatch, (c, 0), (c + 600, 600), 255, 1)
    img = np.full_like(mask, 255)
    img[(hatch > 0) & (mask > 0)] = 0
    contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(img, contours, -1, 0, 2)
    return img
