"""Local, silent recording: bounded fresh-frame capture and timestamped MP4."""
from datetime import datetime
from fractions import Fraction
from pathlib import Path
import os
import queue
import sys
import threading
import time
from uuid import uuid4


def recording_folder():
    if sys.platform == 'win32':
        import ctypes
        buffer = ctypes.create_unicode_buffer(32768)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 0x000E, None, 0, buffer) == 0:
            return Path(buffer.value) / 'MapleAlert'
    return Path.home() / 'Videos' / 'MapleAlert'


def output_size(width, height):
    scale = min(1.0, 1280/width, 720/height)
    return max(2, int(width*scale)//2*2), max(2, int(height*scale)//2*2)


class AvEncoder:
    """Real capture timestamps; no repeated-frame catch-up or fixed-rate mux."""
    time_base = Fraction(1, 1_000_000)

    def __init__(self, path, width, height, fps=30, codec_names=None):
        import av
        self.av = av
        self.path = Path(path)
        self.width, self.height, self.fps = width, height, fps
        names = codec_names or (['h264_mf','h264_nvenc','libx264'] if sys.platform == 'win32' else ['libx264'])
        self.candidates = iter(names)
        self.container = self.stream = None
        self.pending = None
        self.frames = 0
        self.first_timestamp = None
        self.last_pts = -1
        self.codec = ''
        self.diagnostics = []
        self.closed = False
        self._open_next()

    def _open_next(self):
        for name in self.candidates:
            container = None
            try:
                self.av.Codec(name, 'w')
                container = self.av.open(str(self.path), mode='w', format='mp4', options={'video_track_timescale':'1000000'})
                stream = container.add_stream(name, rate=self.fps)
                stream.width, stream.height = self.width, self.height
                stream.pix_fmt = 'nv12' if name == 'h264_mf' else 'yuv420p'
                stream.time_base = self.time_base
                stream.codec_context.time_base = self.time_base
                stream.codec_context.max_b_frames = 0
                stream.codec_context.gop_size = self.fps*2
                stream.bit_rate = 4_000_000
                if name == 'libx264':
                    stream.options = {'preset':'ultrafast','tune':'zerolatency','crf':'23'}
                    stream.codec_context.thread_count = min(4, max(1,(os.cpu_count() or 2)//2))
                elif name == 'h264_mf':
                    stream.options = {'hw_encoding':'1'}
                else:
                    stream.options = {'preset':'p1','tune':'ull','zerolatency':'1','delay':'0'}
                stream.codec_context.open()
                self.container, self.stream, self.codec = container, stream, name
                return
            except Exception as exc:
                self.diagnostics.append(name+': '+str(exc))
                if container is not None:
                    try:container.close()
                    except Exception:pass
        raise RuntimeError('视频编码器不可用：'+'；'.join(self.diagnostics))

    def _packet(self, packet):
        if packet.pts is None:
            raise RuntimeError('编码器未返回视频时间戳。')
        if self.pending is not None:
            previous = self.pending
            previous.duration = max(1, round((packet.pts*packet.time_base-previous.pts*previous.time_base)/previous.time_base))
            self.container.mux(previous)
        self.pending = packet

    def write(self, array, timestamp):
        if self.closed:
            raise RuntimeError('编码器已关闭。')
        if self.first_timestamp is None:
            self.first_timestamp = timestamp
        pts = max(self.last_pts+1, round((timestamp-self.first_timestamp)*1_000_000))
        while True:
            try:
                frame = self.av.VideoFrame.from_ndarray(array, format='bgr24')
                frame.pts, frame.time_base = pts, self.time_base
                for packet in self.stream.encode(frame):
                    self._packet(packet)
                self.frames += 1
                self.last_pts = pts
                return
            except Exception as exc:
                # Some GPU encoders only reveal unsupported hardware on the
                # first frame. Fall back before committing any frame/packet.
                if self.frames or self.pending is not None:
                    raise
                self.diagnostics.append(self.codec+': '+str(exc))
                try:self.container.close()
                except Exception:pass
                self.container = self.stream = None
                self._open_next()

    def close(self, end_timestamp=None):
        if self.closed:
            return
        self.closed = True
        if self.container is None:
            return
        try:
            for packet in self.stream.encode(None):
                self._packet(packet)
            if self.pending is not None:
                packet = self.pending
                if end_timestamp is None:
                    duration = Fraction(1,self.fps)
                else:
                    duration = Fraction(max(1,round((end_timestamp-self.first_timestamp)*1_000_000)),1_000_000)-packet.pts*packet.time_base
                packet.duration = max(1,round(duration/packet.time_base))
                self.container.mux(packet)
                self.pending = None
        finally:
            self.container.close()


def validate_video(path, expected_frames):
    import av
    with av.open(str(path)) as video:
        stream = video.streams.video[0]
        if stream.frames != expected_frames:
            raise RuntimeError('视频帧数校验失败，文件已保留供检查。')
        if next(video.decode(stream), None) is None:
            raise RuntimeError('视频无法回放，文件已保留供检查。')


class ScreenRecorder:
    def __init__(self, emit, folder=None, fps=30, capture_factory=None, encoder_factory=None):
        if not 1 <= fps <= 60:
            raise ValueError('录屏目标帧率无效。')
        self.emit = emit
        self.folder = Path(folder) if folder is not None else recording_folder()
        self.fps = fps
        self.capture_factory = capture_factory
        self.encoder_factory = encoder_factory or AvEncoder
        self._lock = threading.Lock()
        self._active = False
        self._stop = threading.Event()
        self.thread = None
        self.metrics = {}
        self._stop_at = None

    @property
    def active(self):
        with self._lock:return self._active

    def start(self, region):
        region = dict(region)
        if min(region['width'],region['height']) < 1:
            raise ValueError('录屏区域无效。')
        with self._lock:
            if self._active:return False
            self._active = True
            self._stop.clear()
            self._stop_at = None
            self.metrics = dict(captured=0,encoded=0,dropped=0,max_queue=0,capture_seconds=0.0,encode_seconds=0.0,max_frame_age=0.0,codec='',width=0,height=0,elapsed=0.0)
            self.thread = threading.Thread(target=self._run,args=(region,),daemon=False)
            try:self.thread.start()
            except Exception:
                self._active=False
                raise
        return True

    def stop(self):
        if not self._stop.is_set():
            self._stop_at = time.monotonic()
        self._stop.set()

    def _capture(self, region, frames, done, errors, epoch):
        try:
            import numpy as np
            if self.capture_factory is None:
                import mss
                factory=mss.mss
            else:factory=self.capture_factory
            next_due=epoch
            count=0
            with factory() as source:
                while count == 0 or not self._stop.is_set():
                    if count and self._stop.wait(max(0,next_due-time.monotonic())):
                        break
                    began=time.monotonic()
                    array=np.asarray(source.grab(region))[:,:,:3].copy()
                    timestamp=time.monotonic()
                    if array.shape[:2] != (region['height'],region['width']):
                        raise RuntimeError('监控区域尺寸变化，请停止后重新选择区域。')
                    self.metrics['capture_seconds']+=timestamp-began
                    count+=1;self.metrics['captured']=count
                    item=(count,timestamp,array)
                    while True:
                        try:
                            frames.put_nowait(item)
                            break
                        except queue.Full:
                            try:
                                frames.get_nowait();self.metrics['dropped']+=1
                            except queue.Empty:pass
                    self.metrics['max_queue']=max(self.metrics['max_queue'],frames.qsize())
                    # Do not capture catch-up bursts after a slow screenshot.
                    next_due=max(next_due+1/self.fps,time.monotonic())
        except Exception as exc:
            errors.append(exc)
        finally:
            done.set()

    def _run(self, region):
        encoder=None;producer=None;partial=None
        frames=queue.Queue(maxsize=2)
        done=threading.Event();errors=[]
        epoch=time.monotonic();last_capture=None
        try:
            import cv2
            self.emit('recording',('starting',None))
            self.folder.mkdir(parents=True,exist_ok=True)
            name=datetime.now().strftime('MapleAlert-%Y%m%d-%H%M%S-')+uuid4().hex[:8]
            final=self.folder/(name+'.mp4');partial=self.folder/(name+'.recording.mp4')
            width,height=output_size(region['width'],region['height'])
            self.metrics.update(width=width,height=height)
            producer=threading.Thread(target=self._capture,args=(region,frames,done,errors,epoch),daemon=False)
            producer.start()
            last_report=epoch;reported_frames=0;first=True
            while not done.is_set() or not frames.empty():
                try:sequence,timestamp,array=frames.get(timeout=.05)
                except queue.Empty:continue
                if encoder is None:
                    encoder=self.encoder_factory(partial,width,height,self.fps)
                began=time.monotonic()
                self.metrics['max_frame_age']=max(self.metrics['max_frame_age'],began-timestamp)
                if array.shape[:2] != (height,width):
                    array=cv2.resize(array,(width,height),interpolation=cv2.INTER_AREA)
                encoder.write(array,timestamp)
                last_capture=timestamp
                self.metrics['encode_seconds']+=time.monotonic()-began
                self.metrics['encoded']+=1
                self.metrics['codec']=getattr(encoder,'codec','unknown')
                now=time.monotonic()
                self.metrics['elapsed']=now-epoch
                if first or now-last_report>=1:
                    actual=None if first else (self.metrics['encoded']-reported_frames)/(now-last_report)
                    self.emit('recording',('active',(int(now-epoch),actual,self.metrics['dropped'])))
                    first=False;last_report=now;reported_frames=self.metrics['encoded']
            if errors:raise errors[0]
            if encoder is None:raise RuntimeError('没有捕获到视频画面。')
            self.emit('recording',('saving',None))
            # Capture timestamps define playback, including real gaps. Only
            # packet duration is extended to stop time; no duplicate frames.
            end=max(self._stop_at or time.monotonic(),last_capture+1/self.fps)
            encoder.close(end)
            encoder=None
            if not partial.exists() or partial.stat().st_size<64:
                raise RuntimeError('视频未能写入，请检查磁盘空间。')
            validate_video(partial,self.metrics['encoded'])
            partial.replace(final)
            self.emit('recording',('saved',str(final)))
        except Exception as exc:
            self._stop.set()
            if producer is not None and producer.ident is not None:producer.join()
            if encoder is not None:
                try:encoder.close(time.monotonic())
                except Exception:pass
                encoder=None
            suffix=('；部分文件：'+str(partial)) if self.metrics.get('encoded') and partial is not None else ''
            self.emit('recording',('error',type(exc).__name__+': '+str(exc)+suffix))
        finally:
            self._stop.set()
            try:
                if producer is not None and producer.ident is not None:producer.join()
            finally:
                if not self.metrics.get('encoded') and partial is not None:
                    try:partial.unlink(missing_ok=True)
                    except OSError:pass
                self.metrics['elapsed']=time.monotonic()-epoch
                with self._lock:self._active=False
                self.emit('recording',('idle',None))


def codec_self_test():
    """Real timestamped encode/decode without any screen capture."""
    import tempfile
    import av
    import numpy as np
    with tempfile.TemporaryDirectory() as directory:
        path=Path(directory)/'录屏编码测试.mp4'
        encoder=AvEncoder(path,64,48,30)
        for i in range(30):encoder.write(np.full((48,64,3),i*7,np.uint8),i/30)
        encoder.close(1.0)
        with av.open(str(path)) as video:
            decoded=list(video.decode(video=0))
            assert len(decoded)==30
            assert abs(float(video.duration/av.time_base)-1.0)<.06
            assert all(b.pts*b.time_base>a.pts*a.time_base for a,b in zip(decoded,decoded[1:]))
