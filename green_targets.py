"""Presence detection of three green crosshairs; no puzzle-content analysis."""
from itertools import combinations
from pathlib import Path
import cv2
import numpy as np

PROFILE = 'three-green-targets-v1'
SCALES = [0.75, 0.875, 1, 1.125, 1.25, 1.5, 1.75, 2]
ASSET = Path(__file__).resolve().parent / 'assets' / 'green-target-1.png'


def green_mask(frame):
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    return cv2.inRange(hsv, (35, 95, 60), (90, 255, 255))


def config_for_region(region):
    return dict(region=dict(region), profile=PROFILE, threshold=0.88,
                scales=list(SCALES), interval=0.25, hits=1, misses=8, cooldown=30)


def load_variants(region, scales):
    masks = []
    for i in [1, 2, 3]:
        path = ASSET.parent / ('green-target-%d.png' % i)
        mask = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
        if mask is None or np.count_nonzero(mask) < 20:
            raise ValueError('内置绿色准头模板损坏，请重新解压或打包程序。')
        masks.append(mask)
    variants = []
    for scale in scales:
        targets = [cv2.GaussianBlur(cv2.resize(m, None, fx=scale, fy=scale), (3, 3), 0.7) for m in masks]
        target = targets[0]
        h, w = target.shape
        if w * 5 <= region['width'] and h <= region['height']:
            variants.append(dict(kind='green-targets', mask=target, masks=targets, scale=scale))
    if not variants:
        raise ValueError('游戏区域太小，请选择整个游戏区域。')
    return variants


def aligned(a, b, c, size):
    xs = [a[0], b[0], c[0]]
    ys = [a[1], b[1], c[1]]
    d1, d2 = xs[1] - xs[0], xs[2] - xs[1]
    return (max(ys) - min(ys) <= size * 0.35 and
            1.5 * size <= d1 <= 6 * size and
            1.5 * size <= d2 <= 6 * size and
            min(d1, d2) / max(d1, d2) >= 0.75)


def green_score(frame, variants):
    mask = cv2.GaussianBlur(green_mask(frame), (3, 3), 0.7)
    best = 0.0
    for variant in variants:
        target = variant['mask']
        h, w = target.shape
        scores = np.maximum.reduce([cv2.matchTemplate(mask, t, cv2.TM_CCOEFF_NORMED) for t in variant['masks']])
        scores = np.nan_to_num(scores, nan=0, posinf=0, neginf=0)
        candidates = []
        for _ in range(24):
            _, value, _, (x, y) = cv2.minMaxLoc(scores)
            if value < max(0.65, best):
                break
            candidates.append((x + w / 2, y + h / 2, value))
            scores[max(0,y-h//2):y+h//2+1, max(0,x-w//2):x+w//2+1] = 0
        candidates.sort(key=lambda point: point[0])
        for a, b, c in combinations(candidates, 3):
            if aligned(a, b, c, max(h, w)):
                best = max(best, min(a[2], b[2], c[2]))
    return float(best)
