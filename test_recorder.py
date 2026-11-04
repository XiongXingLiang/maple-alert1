import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import av
import cv2  # Actual app warms OpenCV on the main thread before recording.
import numpy as np
from recorder import ScreenRecorder, AvEncoder, codec_self_test, output_size


class Capture:
    def __init__(self, ready=None, fail=False, delay=0):
        self.ready=ready;self.fail=fail;self.delay=delay;self.n=0
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def grab(self,region):
        if self.fail:raise OSError('capture unavailable')
        if self.delay:time.sleep(self.delay)
        self.n+=1
        if self.ready:self.ready.set()
        return np.full((region['height'],region['width'],4),self.n%200,np.uint8)


class FakeEncoder:
    codec='test'
    def __init__(self,path,width,height,fps,delay=0,fail_close=False):
        self.path=Path(path);self.path.write_bytes(b'fake-video'*20)
        self.delay=delay;self.fail_close=fail_close;self.samples=[];self.closed=False
    def write(self,frame,timestamp):
        if self.delay:time.sleep(self.delay)
        self.samples.append((int(frame[0,0,0]),timestamp))
    def close(self,end=None):
        self.closed=True
        if self.fail_close:raise OSError('flush failed')


class RecorderTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.folder=Path(self.temp.name)/'录屏'
        self.events=[];self.ready=threading.Event();self.capture=Capture(self.ready)
        self.rec=ScreenRecorder(lambda *e:self.events.append(e),folder=self.folder,capture_factory=lambda:self.capture)
        self.region=dict(left=-100,top=20,width=65,height=49)
    def tearDown(self):
        self.rec.stop()
        if self.rec.thread:self.rec.thread.join(10)
        self.temp.cleanup()
    def finish(self):
        self.rec.stop();self.rec.thread.join(10)
        self.assertFalse(self.rec.thread.is_alive());self.assertFalse(self.rec.active)
    def saved(self):return [Path(data[1]) for kind,data in self.events if data[0]=='saved']
    def test_duplicate_start_manual_stop_and_odd_dimensions(self):
        self.assertTrue(self.rec.start(self.region));self.assertTrue(self.ready.wait(10))
        self.assertFalse(self.rec.start(self.region));time.sleep(.15);self.finish()
        paths=self.saved();self.assertEqual(len(paths),1)
        with av.open(str(paths[0])) as v:
            f=next(v.decode(video=0));self.assertEqual((f.height,f.width),(48,64))
        self.assertFalse(list(self.folder.glob('*.recording.mp4')))
    def test_immediate_stop_finalizes_and_can_start_again(self):
        for _ in range(2):
            self.assertTrue(self.rec.start(self.region));self.finish()
        self.assertEqual(len(set(self.saved())),2)
    def test_capture_failure_never_reports_saved(self):
        self.capture.fail=True
        self.rec.start(self.region);self.rec.thread.join(10)
        self.assertFalse(self.rec.active);self.assertFalse(self.saved())
        self.assertTrue(any(data[0]=='error' and 'capture unavailable' in data[1] for kind,data in self.events))
        self.assertFalse(list(self.folder.glob('*.mp4')))
    def test_codec_start_failure_stops_capture_and_resets_state(self):
        def fail(*args):raise OSError('codec unavailable')
        self.rec.encoder_factory=fail
        self.rec.start(self.region);self.rec.thread.join(10)
        self.assertFalse(self.rec.active);self.assertFalse(self.saved())
        self.assertTrue(any(data[0]=='error' for kind,data in self.events))
    def test_slow_encoding_keeps_capture_independent_and_queue_fresh(self):
        encoders=[]
        def factory(*args):
            encoder=FakeEncoder(*args,delay=.08);encoders.append(encoder);return encoder
        self.rec.encoder_factory=factory
        with patch('recorder.validate_video'):
            self.rec.start(self.region);self.assertTrue(self.ready.wait(10))
            time.sleep(.8);before=time.monotonic();self.finish()
        self.assertLess(time.monotonic()-before,1)
        m=self.rec.metrics
        self.assertGreater(m['captured'],m['encoded']+3)
        self.assertGreater(m['dropped'],0);self.assertLessEqual(m['max_queue'],2)
        tags=[n for n,t in encoders[0].samples]
        self.assertEqual(len(tags),len(set(tags)))
        self.assertTrue(all(b>a for a,b in zip(tags,tags[1:])))
        self.assertLess(m['max_frame_age'],.5)
    def test_slow_capture_does_not_shorten_video_or_duplicate_frames(self):
        self.capture.delay=.12
        self.rec.start(self.region);self.assertTrue(self.ready.wait(10))
        time.sleep(.3);self.finish()
        with av.open(str(self.saved()[0])) as v:
            frames=list(v.decode(video=0))
            duration=v.duration/av.time_base
        self.assertEqual(len(frames),self.rec.metrics['encoded'])
        timestamps=[float(f.pts*f.time_base) for f in frames]
        self.assertTrue(all(b>a for a,b in zip(timestamps,timestamps[1:])))
        self.assertGreater(duration,.30)
        self.assertLess(len(frames),duration*20)
    def test_release_error_resets_active_and_keeps_partial(self):
        self.rec.encoder_factory=lambda *args:FakeEncoder(*args,fail_close=True)
        self.rec.start(self.region);self.assertTrue(self.ready.wait(10));time.sleep(.04);self.finish()
        self.assertFalse(self.saved())
        self.assertTrue(any(data[0]=='error' and 'flush failed' in data[1] for kind,data in self.events))
        self.assertTrue(list(self.folder.glob('*.recording.mp4')))
    def test_unwritable_directory_reports_error(self):
        self.folder.write_text('not a directory')
        self.rec.start(self.region);self.rec.thread.join(10)
        self.assertFalse(self.rec.active);self.assertFalse(self.saved())
        self.assertTrue(any(data[0]=='error' for kind,data in self.events))
    def test_capture_thread_start_failure_resets_state(self):
        original=threading.Thread.start
        def fail_capture(thread):
            if getattr(thread,'_target',None)==self.rec._capture:
                raise RuntimeError('cannot start capture thread')
            return original(thread)
        with patch.object(threading.Thread,'start',fail_capture):
            self.rec.start(self.region);self.rec.thread.join(10)
        self.assertFalse(self.rec.active)
        self.assertTrue(any(data[0]=='error' and 'cannot start capture' in data[1] for kind,data in self.events))
        self.assertTrue(any(data[0]=='idle' for kind,data in self.events))

    def test_actual_mp4_codec_roundtrip(self):codec_self_test()
    def test_real_vfr_timestamps_no_repeat_and_frame_content(self):
        path=Path(self.temp.name)/'可变帧率.mp4'
        writer=AvEncoder(path,64,48,30,codec_names=['libx264'])
        samples=[0,.04,.20,.70,.74]
        for i,t in enumerate(samples):writer.write(np.full((48,64,3),20+i*30,np.uint8),10+t)
        writer.close(10.8)
        with av.open(str(path)) as video:
            frames=list(video.decode(video=0));duration=video.duration/av.time_base
        self.assertEqual(len(frames),5);self.assertAlmostEqual(duration,.8,delta=.01)
        self.assertTrue(all(abs(float(f.pts*f.time_base)-t)<.001 for f,t in zip(frames,samples)))
        self.assertAlmostEqual(float(frames[0].to_ndarray(format='bgr24').mean()),20,delta=3)
        self.assertAlmostEqual(float(frames[-1].to_ndarray(format='bgr24').mean()),140,delta=3)
    def test_hardware_unavailable_uses_software(self):
        writer=AvEncoder(Path(self.temp.name)/'fallback.mp4',64,48,30,codec_names=['not-a-codec','libx264'])
        writer.write(np.zeros((48,64,3),np.uint8),0);writer.close(.1)
        self.assertEqual(writer.codec,'libx264')
    def test_first_encode_failure_falls_back_before_committing(self):
        path=Path(self.temp.name)/'first-frame-fallback.mp4'
        writer=AvEncoder(path,64,48,30,codec_names=['libx264','libx264'])
        class BrokenStream:
            def encode(self,frame):raise OSError('GPU rejected first frame')
        writer.stream=BrokenStream()
        writer.write(np.full((48,64,3),99,np.uint8),10)
        writer.close(10.1)
        self.assertEqual(writer.frames,1)
        self.assertTrue(any('GPU rejected first frame' in d for d in writer.diagnostics))
        with av.open(str(path)) as video:
            frames=list(video.decode(video=0))
        self.assertEqual(len(frames),1)
        self.assertAlmostEqual(float(frames[0].to_ndarray(format="bgr24").mean()),99,delta=3)

    def test_resolution_caps(self):
        self.assertEqual(output_size(1920,1080),(1280,720))
        self.assertEqual(output_size(3840,2160),(1280,720))
        self.assertEqual(output_size(65,49),(64,48))
        self.assertLessEqual(output_size(1080,1920)[1],720)


if __name__=='__main__':unittest.main()
