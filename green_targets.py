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
    return dict(region=dict(region), profile=PROFILE, threshold=0.80,
                scales=list(SCALES), interval=0.1, hits=1, misses=8, cooldown=30)


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
            coarse_targets = [cv2.resize(t, None, fx=.25, fy=.25, interpolation=cv2.INTER_AREA) for t in targets]
            variants.append(dict(kind='green-targets', mask=target, masks=targets, coarse_masks=coarse_targets, scale=scale))
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


def green_score(frame, variants, threshold=None):
    raw = green_mask(frame)
    if not np.any(raw):
        return 0.0
    mask = cv2.GaussianBlur(raw, (3, 3), 0.7)
    # Coarse correlation only proposes locations. The returned confidence is
    # always measured against the original full-resolution template.
    factor = 0.25
    small = cv2.resize(mask, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA)
    best = 0.0
    for variant in variants:
        target = variant['mask']
        h, w = target.shape
        coarse_targets = variant['coarse_masks']
        coarse = np.maximum.reduce([cv2.matchTemplate(small, t, cv2.TM_CCOEFF_NORMED) for t in coarse_targets])
        coarse = np.nan_to_num(coarse, nan=0, posinf=0, neginf=0)
        # Keep every local peak above a deliberately loose proposal threshold.
        # Flat plateaus are compressed by connected components, without a
        # fixed top-N cutoff that could discard the real dialog in clutter.
        maxima = ((coarse >= cv2.dilate(coarse, np.ones((3,3),np.uint8))) & (coarse >= .30)).astype(np.uint8)
        count, labels, stats, centers = cv2.connectedComponentsWithStats(maxima, connectivity=8)
        proposals = []
        for x, y, rw, rh, area in stats[1:]:
            # A plateau can contain multiple equally good positions. Cover
            # its entire extent rather than using only its centroid.
            left=max(0, int(x/factor)-8);top=max(0, int(y/factor)-8)
            right=min(mask.shape[1], int((x+rw)/factor)+w+8)
            bottom=min(mask.shape[0], int((y+rh)/factor)+h+8)
            proposals.append((left,top,right,bottom))
        # Pathological clutter must not cause thousands of tiny native calls.
        # Full-frame refinement is bounded by the previous implementation cost.
        fallback = len(proposals) > 256 or sum((r-l)*(b-t) for l,t,r,b in proposals) > raw.size//2
        if fallback:
            proposals=[(0,0,mask.shape[1],mask.shape[0])]
        candidates = []
        for left,top,right,bottom in proposals:
            roi=mask[top:bottom,left:right]
            if roi.shape[0]<h or roi.shape[1]<w:
                continue
            scores=np.maximum.reduce([cv2.matchTemplate(roi,t,cv2.TM_CCOEFF_NORMED) for t in variant['masks']])
            scores=np.nan_to_num(scores,nan=0,posinf=0,neginf=0)
            # Several full-resolution peaks may correspond to one coarse
            # location. Preserve them before global suppression.
            for _ in range(24 if fallback else 4):
                _,value,_,(x,y)=cv2.minMaxLoc(scores)
                if value<max(.65,best):
                    break
                candidates.append((left+x+w/2,top+y+h/2,value))
                scores[max(0,y-h//2):y+h//2+1,max(0,x-w//2):x+w//2+1]=0
        chosen=[]
        for candidate in sorted(candidates,key=lambda p:p[2],reverse=True):
            if any(abs(candidate[0]-p[0])<=w//2 and abs(candidate[1]-p[1])<=h//2 for p in chosen):
                continue
            chosen.append(candidate)
            if len(chosen)==24:
                break
        chosen.sort(key=lambda p:p[0])
        for a,b,c in combinations(chosen,3):
            if aligned(a,b,c,max(h,w)):
                best=max(best,min(a[2],b[2],c[2]))
        if threshold is not None and best>=threshold:
            break
    return float(best)
