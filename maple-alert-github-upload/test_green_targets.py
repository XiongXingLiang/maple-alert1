import unittest
import cv2
import numpy as np
import green_targets as detector
import alert


class GreenTargetsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        alert.libraries()
        cls.region = dict(left=0, top=0, width=640, height=400)
        cls.variants = detector.load_variants(cls.region, detector.SCALES)
        cls.mask = cv2.imdecode(np.fromfile(str(detector.ASSET.parent / 'green-target-1.png'), dtype=np.uint8), cv2.IMREAD_GRAYSCALE)

    def frame(self, points, color=(0,255,0), scale=1):
        mask = cv2.resize(self.mask, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
        frame = np.zeros((400,640,3), dtype=np.uint8)
        h, w = mask.shape
        for x,y in points:
            roi = frame[y:y+h,x:x+w]
            roi[mask > 0] = color
        return frame

    def test_three_aligned_targets(self):
        self.assertGreater(alert.score(self.frame([(80,200),(170,200),(260,200)]),self.variants),0.88)

    def test_one_or_two_not_enough(self):
        for n in [1,2]:
            self.assertLess(alert.score(self.frame([(80,200),(170,200)][:n]),self.variants),0.88)

    def test_vertical_or_uneven_not_enough(self):
        for points in [[(80,100),(80,190),(80,280)],[(40,200),(100,200),(300,200)],[(80,100),(170,180),(260,260)]]:
            self.assertLess(alert.score(self.frame(points),self.variants),0.88)

    def test_wrong_color_rejected(self):
        self.assertLess(alert.score(self.frame([(80,200),(170,200),(260,200)],(0,0,255)),self.variants),0.88)

    def test_green_circles_rejected(self):
        frame=np.zeros((400,640,3),dtype=np.uint8)
        for x in [80,170,260]:
            cv2.circle(frame,(x,200),13,(0,255,0),-1)
        self.assertLess(alert.score(frame,self.variants),0.88)

    def test_scaled_targets(self):
        self.assertGreater(alert.score(self.frame([(70,200),(205,200),(340,200)],scale=1.5),self.variants),0.88)

    def test_profile_is_default_ready_without_template_selection(self):
        cfg=detector.config_for_region(self.region)
        self.assertEqual(cfg['profile'],detector.PROFILE)
        self.assertNotIn('templates',cfg)
        self.assertEqual(cfg['hits'],1)


if __name__ == '__main__':
    unittest.main()
