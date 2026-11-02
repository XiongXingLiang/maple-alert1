"""Silent, local MSS capture; one asynchronous MP4 recording at a time."""
from datetime import datetime
from pathlib import Path
import threading
import time
from uuid import uuid4


def recording_folder():
    """Respect the Windows user's redirected Videos folder when available."""
    import sys
    if sys.platform == 'win32':
        import ctypes
        buffer = ctypes.create_unicode_buffer(32768)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 0x000E, None, 0, buffer) == 0:
            return Path(buffer.value) / 'MapleAlert'
    return Path.home() / 'Videos' / 'MapleAlert'


class ScreenRecorder:
    def __init__(self, emit, folder=None, fps=15, capture_factory=None, writer_factory=None):
        self.emit = emit
        self.folder = Path(folder) if folder is not None else recording_folder()
        self.fps = fps
        self.capture_factory = capture_factory
        self.writer_factory = writer_factory
        self._lock = threading.Lock()
        self._active = False
        self._stop = threading.Event()
        self.thread = None

    @property
    def active(self):
        with self._lock:
            return self._active

    def start(self, region):
        region = dict(region)
        if min(region['width'], region['height']) < 1:
            raise ValueError('录屏区域无效。')
        with self._lock:
            if self._active:
                return False
            self._active = True
            self._stop.clear()
            self.thread = threading.Thread(target=self._run, args=(region,), daemon=False)
            try:
                self.thread.start()
            except Exception:
                self._active = False
                raise
        return True

    def stop(self):
        self._stop.set()

    def _run(self, region):
        writer = None
        partial = None
        frames = 0
        started = None
        try:
            import cv2
            import numpy as np
            self.emit('recording', ('starting', None))
            self.folder.mkdir(parents=True, exist_ok=True)
            name = datetime.now().strftime('MapleAlert-%Y%m%d-%H%M%S-') + uuid4().hex[:8]
            final = self.folder / (name + '.mp4')
            partial = self.folder / (name + '.recording.mp4')
            width = (region['width'] + 1) // 2 * 2
            height = (region['height'] + 1) // 2 * 2
            factory = self.writer_factory or cv2.VideoWriter
            writer = factory(str(partial), cv2.VideoWriter_fourcc(*'mp4v'), self.fps, (width, height))
            if not writer.isOpened():
                raise RuntimeError('无法创建 MP4 文件，请检查保存目录、剩余空间和视频编码支持。')
            if self.capture_factory is None:
                import mss
                factory = mss.mss
            else:
                factory = self.capture_factory
            with factory() as sct:
                started = time.monotonic()
                progress = -1
                # Capture at least one frame even if stopped during startup.
                while frames == 0 or not self._stop.is_set():
                    frame = np.asarray(sct.grab(region))[:, :, :3].copy()
                    if frame.shape[:2] != (region['height'], region['width']):
                        raise RuntimeError('监控区域画面尺寸发生变化，请停止后重新选择区域。')
                    if width != region['width'] or height != region['height']:
                        frame = cv2.copyMakeBorder(frame, 0, height-region['height'], 0, width-region['width'], cv2.BORDER_CONSTANT)
                    elapsed = time.monotonic() - started
                    due = max(1, int(elapsed * self.fps) + 1)
                    # Repeat the latest frame to maintain wall-clock playback
                    # under normal delays; never enqueue unbounded capture work.
                    for _ in range(min(max(1, due-frames), self.fps*2)):
                        writer.write(frame)
                        frames += 1
                    seconds = int(elapsed)
                    if seconds != progress:
                        self.emit('recording', ('active', seconds))
                        progress = seconds
                    self._stop.wait(max(0, started+frames/self.fps-time.monotonic()))
            self.emit('recording', ('saving', None))
            writer.release()
            writer = None
            if not partial.exists() or partial.stat().st_size < 64:
                raise RuntimeError('视频未能写入文件，请检查磁盘空间。')
            # Do not present a corrupt file as a successfully saved recording.
            video = cv2.VideoCapture(str(partial))
            try:
                ok, decoded = video.read()
                if not video.isOpened() or not ok or int(video.get(cv2.CAP_PROP_FRAME_COUNT)) != frames:
                    raise RuntimeError('视频保存后校验失败，录制文件已保留供检查。')
            finally:
                video.release()
            partial.replace(final)
            self.emit('recording', ('saved', str(final)))
        except Exception as exc:
            suffix = ('；部分文件：' + str(partial)) if frames and partial is not None else ''
            self.emit('recording', ('error', str(exc) + suffix))
        finally:
            if writer is not None:
                try:
                    writer.release()
                except Exception:
                    pass
            if frames == 0 and partial is not None:
                try:
                    partial.unlink(missing_ok=True)
                except OSError:
                    pass
            with self._lock:
                self._active = False
            self.emit('recording', ('idle', None))


def codec_self_test():
    """Check actual bundled MP4 encoder/decoder without screen capture."""
    import tempfile
    import cv2
    import numpy as np
    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory) / '录屏测试'
        folder.mkdir()
        path = folder / 'codec.mp4'
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'mp4v'), 15, (64, 48))
        try:
            if not writer.isOpened():
                raise RuntimeError('MP4 encoder unavailable')
            for i in range(15):
                writer.write(np.full((48, 64, 3), i*10, dtype=np.uint8))
        finally:
            writer.release()
        video = cv2.VideoCapture(str(path))
        try:
            count = 0
            while video.read()[0]:
                count += 1
            if count != 15:
                raise RuntimeError('MP4 decode self-test failed')
        finally:
            video.release()
