import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import cv2
import numpy as np
import green_targets as d
import alert


class FastDetectionTest(unittest.TestCase):
    def setUp(self):
        self.region=dict(left=0,top=0,width=1920,height=1080)
        self.variants=d.load_variants(self.region,d.SCALES)
        self.masks=[cv2.imdecode(np.fromfile(str(d.ASSET.parent/('green-target-%d.png'%i)),np.uint8),0) for i in (1,2,3)]

    def frame(self, scale=1, x=800, y=500):
        frame=np.zeros((1080,1920,3),np.uint8)
        for i,m in enumerate(self.masks):
            m=cv2.resize(m,None,fx=scale,fy=scale)
            h,w=m.shape
            frame[y:y+h,x+round(i*90*scale):x+round(i*90*scale)+w][m>127]=(0,255,0)
        return frame

    def test_all_supported_scales_and_edges(self):
        for scale in d.SCALES:
            for x,y in [(0,0),(1920-round(210*scale),1080-round(29*scale))]:
                with self.subTest(scale=scale,x=x,y=y):
                    self.assertGreater(d.green_score(self.frame(scale,x,y),self.variants),.80)

    def test_separate_regions_keep_global_coordinates(self):
        self.assertGreater(d.green_score(self.frame(2,500,500),self.variants),.80)

    def test_dense_green_rejected(self):
        self.assertLess(d.green_score(np.full((1080,1920,3),(0,255,0),np.uint8),self.variants),.80)

    def test_scattered_green_background_keeps_targets(self):
        f=self.frame()
        rng=np.random.default_rng(42)
        for x,y in rng.integers([0,0],[1920,1080],size=(300,2)):
            cv2.circle(f,(int(x),int(y)),2,(0,200,0),-1)
        self.assertGreater(d.green_score(f,self.variants),.80)

    def test_no_green_skips_template_matching(self):
        with patch.object(d.cv2,'matchTemplate',side_effect=AssertionError('blank image must skip matching')):
            self.assertEqual(d.green_score(np.zeros((1080,1920,3),np.uint8),self.variants),0)

    def test_negatives_rejected_at_actual_default(self):
        for points in [[(80,100),(80,190),(80,280)],[(40,200),(100,200),(300,200)]]:
            f=np.zeros((1080,1920,3),np.uint8)
            for x,y in points:
                f[y:y+29,x:x+29][self.masks[0]>127]=(0,255,0)
            self.assertLess(d.green_score(f,self.variants),.80)
        f=np.zeros((1080,1920,3),np.uint8)
        for x in (80,170,260):
            cv2.circle(f,(x,200),13,(0,255,0),-1)
        # Existing circle score is ~.83 at .80; latency work must not hide
        # that known limitation by changing the final matching criterion.
        self.assertLess(d.green_score(f,self.variants),.88)

    def test_existing_config_fast_cadence_preserves_settings(self):
        cfg=d.config_for_region(self.region)
        cfg.update(threshold=.91,interval=.25,hits=2,cooldown=45)
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'config.json';path.write_text(json.dumps(cfg))
            with patch.object(alert,'CONFIG',path):
                loaded,templates=alert.load()
        self.assertEqual(loaded['interval'],.1)
        self.assertEqual(loaded['threshold'],.91)
        self.assertEqual(loaded['region'],cfg['region'])
        self.assertEqual(loaded['hits'],2)
        self.assertEqual(loaded['cooldown'],45)
