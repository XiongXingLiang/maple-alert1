import tempfile
import threading
import time
import unittest
from pathlib import Path
import cv2
import numpy as np
from recorder import ScreenRecorder, codec_self_test


class Capture:
    def __init__(self, ready=None, fail=False):
        self.ready=ready;self.fail=fail
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def grab(self,region):
        if self.fail:raise OSError('capture unavailable')
        if self.ready:self.ready.set()
        return np.full((region['height'],region['width'],4),127,np.uint8)


class RecorderTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.folder=Path(self.temp.name)/'录屏'
        self.events=[];self.ready=threading.Event()
        self.rec=ScreenRecorder(lambda *e:self.events.append(e),folder=self.folder,capture_factory=lambda:Capture(self.ready))
        self.region=dict(left=-100,top=20,width=65,height=49)
    def tearDown(self):
        self.rec.stop()
        if self.rec.thread:self.rec.thread.join(10)
        self.temp.cleanup()
    def finish(self):
        self.rec.stop();self.rec.thread.join(10)
        self.assertFalse(self.rec.thread.is_alive());self.assertFalse(self.rec.active)
    def saved(self):
        return [Path(data[1]) for kind,data in self.events if data[0]=='saved']
    def test_duplicate_trigger_manual_stop_and_odd_dimensions(self):
        self.assertTrue(self.rec.start(self.region));self.assertTrue(self.ready.wait(10))
        self.assertFalse(self.rec.start(self.region))
        time.sleep(.15);self.finish()
        paths=self.saved();self.assertEqual(len(paths),1);self.assertTrue(paths[0].exists())
        v=cv2.VideoCapture(str(paths[0]))
        try:
            ok,frame=v.read();self.assertTrue(ok);self.assertEqual(frame.shape[:2],(50,66))
        finally:v.release()
        self.assertFalse(list(self.folder.glob('*.recording.mp4')))
    def test_immediate_stop_still_finalizes_and_can_record_again(self):
        for _ in range(2):
            self.assertTrue(self.rec.start(self.region));self.finish()
        self.assertEqual(len(set(self.saved())),2)
    def test_capture_failure_releases_state_and_reports(self):
        self.rec.capture_factory=lambda:Capture(fail=True)
        self.rec.start(self.region);self.rec.thread.join(10)
        self.assertFalse(self.rec.active);self.assertFalse(self.saved())
        self.assertTrue(any(data[0]=='error' and 'capture unavailable' in data[1] for kind,data in self.events))
        self.assertFalse(list(self.folder.glob('*.mp4')))
    def test_codec_failure_is_not_a_saved_file(self):
        class Writer:
            released=False
            def isOpened(self):return False
            def release(self):self.released=True
        writer=Writer();self.rec.writer_factory=lambda *args:writer
        self.rec.start(self.region);self.rec.thread.join(10)
        self.assertTrue(writer.released);self.assertFalse(self.rec.active);self.assertFalse(self.saved())
        self.assertTrue(any(data[0]=='error' for kind,data in self.events))
    def test_unwritable_directory_is_reported(self):
        self.folder.write_text('file instead of directory')
        self.rec.start(self.region);self.rec.thread.join(10)
        self.assertFalse(self.rec.active);self.assertFalse(self.saved())
        self.assertTrue(any(data[0]=='error' for kind,data in self.events))
    def test_playback_duration_survives_slow_capture(self):
        class SlowCapture(Capture):
            def grab(self,region):
                time.sleep(.12)
                return super().grab(region)
        self.rec.capture_factory=lambda:SlowCapture(self.ready)
        before=time.monotonic()
        self.rec.start(self.region);self.assertTrue(self.ready.wait(10))
        time.sleep(.25);self.finish()
        elapsed=time.monotonic()-before
        paths=self.saved();self.assertEqual(len(paths),1)
        v=cv2.VideoCapture(str(paths[0]))
        try:
            duration=v.get(cv2.CAP_PROP_FRAME_COUNT)/v.get(cv2.CAP_PROP_FPS)
            self.assertLess(abs(duration-elapsed),.20)
        finally:v.release()

    def test_actual_mp4_codec_roundtrip(self):
        codec_self_test()


if __name__=='__main__':unittest.main()
