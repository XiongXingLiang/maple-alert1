"""Paired fixed-text image anchors. No OCR, challenge images or answers."""
from pathlib import Path
import cv2
import numpy as np

PROFILE = 'tw-prelude-v1'
ASSETS = Path(__file__).resolve().parent / 'assets'
SCALES = [0.75, 0.875, 1.0, 1.125, 1.25, 1.5, 1.75, 2.0]
OFFSET = (101, 67)  # Safety text position relative to title, in supplied-image pixels.


def config_for_region(region):
    return dict(region=dict(region), profile=PROFILE, threshold=0.88,
                scales=list(SCALES), interval=0.25, hits=1, misses=8, cooldown=30)


def read_asset(name):
    image = cv2.imdecode(np.fromfile(str(ASSETS / name), dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    if image is None or image.std() < 5:
        raise ValueError('内置模板损坏，请重新解压或重新打包程序：' + name)
    return image


def load_variants(region, scales):
    title, safety = read_asset('lie-title.png'), read_asset('safe-place.png')
    variants = []
    for scale in scales:
        t = cv2.resize(title, None, fx=scale, fy=scale)
        a = cv2.resize(safety, None, fx=scale, fy=scale)
        t = cv2.GaussianBlur(t, (3, 3), 0.6)
        a = cv2.GaussianBlur(a, (3, 3), 0.6)
        dx, dy = (round(v * scale) for v in OFFSET)
        if max(t.shape[1], dx + a.shape[1]) <= region['width'] and max(t.shape[0], dy + a.shape[0]) <= region['height']:
            variants.append(dict(title=t, safety=a, offset=(dx, dy), scale=scale))
    if not variants:
        raise ValueError('游戏区域太小，无法容纳内置模板；请重新选择整个游戏区域。')
    return variants


def paired_score(frame, variants):
    gray = cv2.GaussianBlur(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (3, 3), 0.6)
    best = 0.0
    for v in variants:
        title, safety = v['title'], v['safety']
        dx, dy = v['offset']
        ah, aw = safety.shape
        scores = cv2.matchTemplate(gray, title, cv2.TM_CCOEFF_NORMED)
        scores = np.nan_to_num(scores, nan=-1, posinf=-1, neginf=-1)
        # Check up to three distinct title candidates, to tolerate unrelated UI text.
        for _ in range(3):
            _, title_score, _, (x, y) = cv2.minMaxLoc(scores)
            if title_score <= best:
                break
            # A little position tolerance accounts for integer rounding during scaling.
            tolerance = max(2, round(v['scale'] * 2))
            x1, y1 = max(0, x + dx - tolerance), max(0, y + dy - tolerance)
            x2 = min(gray.shape[1], x + dx + aw + tolerance)
            y2 = min(gray.shape[0], y + dy + ah + tolerance)
            if x2 - x1 >= aw and y2 - y1 >= ah:
                roi = gray[y1:y2, x1:x2]
                anchor_score = float(cv2.minMaxLoc(cv2.matchTemplate(roi, safety, cv2.TM_CCOEFF_NORMED))[1])
                if np.isfinite(anchor_score):
                    best = max(best, min(title_score, anchor_score))
            th, tw = title.shape
            scores[max(0, y - th//2):y + th//2 + 1, max(0, x - tw//2):x + tw//2 + 1] = -1
    return float(best)
