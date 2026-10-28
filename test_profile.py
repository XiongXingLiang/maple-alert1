import unittest
from pathlib import Path
from unittest.mock import patch
import tempfile
import json

import alert
import detector_profile as profile
import cv2
import numpy as np


class BuiltinProfileTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        alert.libraries()
        cls.region = dict(left=0, top=0, width=640, height=400)
        cls.variants = profile.load_variants(cls.region, profile.SCALES)

    def composed(self, scale=1, safety=True, shifted=False):
        v = profile.load_variants(self.region, [scale])[0]
        frame = np.zeros((400, 640, 3), dtype=np.uint8)
        x, y = 40, 30
        t = cv2.resize(profile.read_asset('lie-title.png'), None, fx=scale, fy=scale)
        h, w = t.shape
        frame[y:y+h, x:x+w] = t[:, :, None]
        if safety:
            dx, dy = v['offset']
            a = cv2.resize(profile.read_asset('safe-place.png'), None, fx=scale, fy=scale)
            ah, aw = a.shape
            yy = y + dy + (40 if shifted else 0)
            frame[yy:yy+ah, x+dx:x+dx+aw] = a[:, :, None]
        return frame

    def test_paired_anchors_detect_at_supported_scales(self):
        for scale in [0.75, 0.875, 1, 1.125, 1.25, 1.5, 1.75]:
            with self.subTest(scale=scale):
                self.assertGreater(alert.score(self.composed(scale), self.variants), 0.88)

    def test_title_alone_is_not_enough(self):
        self.assertLess(alert.score(self.composed(safety=False), self.variants), 0.88)

    def test_safety_at_wrong_location_is_not_enough(self):
        self.assertLess(alert.score(self.composed(shifted=True), self.variants), 0.88)

    def test_blank_and_noise_do_not_trigger(self):
        blank = np.zeros((400, 640, 3), dtype=np.uint8)
        noise = np.random.default_rng(42).integers(0, 255, blank.shape, dtype=np.uint8)
        for frame in [blank, noise]:
            self.assertLess(alert.score(frame, self.variants), 0.88)

    def test_builtin_load_needs_no_personal_template(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / 'config.json'
            config_path.write_text(json.dumps(profile.config_for_region(self.region)), encoding='utf-8')
            with patch.object(alert, 'CONFIG', config_path):
                cfg, variants = alert.load()
            self.assertEqual(cfg['profile'], profile.PROFILE)
            self.assertTrue(variants)
            self.assertNotIn('templates', cfg)
            gate = alert.Gate(cfg['hits'], cfg['misses'], cfg['cooldown'])
            self.assertTrue(gate.update(True, 0))

    def test_small_region_rejected(self):
        with self.assertRaises(ValueError):
            profile.load_variants(dict(width=30, height=30), [1])


if __name__ == '__main__':
    unittest.main()
