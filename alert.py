#!/usr/bin/env python3
"""Local screen-only reminder core, compatible with the existing desktop app."""
import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / 'config.json'
_toaster = None
VERSION = '1.0'
DEFAULT_THRESHOLD = 0.80
DEFAULTS_REVISION = 'sound-threshold-080-v1'


def make_alarm_wav(path):
    """Three rising two-tone bursts, with short fades to prevent clicks."""
    import math
    import struct
    import wave
    rate = 22050
    samples = bytearray()
    for _ in range(3):
        for frequency, duration in [(950, 0.18), (1450, 0.22), (0, 0.16)]:
            count = round(rate * duration)
            fade = round(rate * 0.012)
            for i in range(count):
                envelope = min(1.0, i / fade, (count - 1 - i) / fade)
                value = int(24000 * envelope * math.sin(2 * math.pi * frequency * i / rate)) if frequency else 0
                samples.extend(struct.pack('<h', value))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp.wav')
    with wave.open(str(temp), 'wb') as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(samples)
    temp.replace(path)
    return path


def alarm_sound_file():
    path = ROOT / 'alarm-three-pairs-v1.wav'
    if not path.exists():
        make_alarm_wav(path)
    return path


def migrate_default_threshold():
    """Migrate the previous 0.88 default once; preserve user custom values."""
    if not CONFIG.exists():
        return
    cfg = json.loads(CONFIG.read_text(encoding='utf-8'))
    if cfg.get('defaults_revision') == DEFAULTS_REVISION:
        return
    if cfg.get('threshold', 0.88) == 0.88:
        cfg['threshold'] = DEFAULT_THRESHOLD
    cfg['defaults_revision'] = DEFAULTS_REVISION
    temp = CONFIG.with_suffix('.defaults.tmp')
    temp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(CONFIG)


def notify():
    global _toaster
    errors = []
    try:
        import winsound
        winsound.PlaySound(str(alarm_sound_file()), winsound.SND_FILENAME | winsound.SND_ASYNC)
    except Exception as exc:
        errors.append('声音失败：' + str(exc))
    try:
        from windows_toasts import Toast, WindowsToaster
        if _toaster is None:
            _toaster = WindowsToaster('Maple Alert')
        toast = Toast()
        toast.text_fields = ['冒险岛提醒', '可能出现测谎仪，请手动查看游戏。']
        _toaster.show_toast(toast)
    except Exception as exc:
        errors.append('通知失败：' + str(exc))
        # A failed notification object must not poison subsequent attempts.
        _toaster = None
    if sys.stdout is not None:
        text = '[Maple Alert] Please check the game. ' + '; '.join(errors)
        encoding = getattr(sys.stdout, 'encoding', None) or 'utf-8'
        try:
            print(text.encode(encoding, errors='backslashreplace').decode(encoding), flush=True)
        except (UnicodeError, OSError, ValueError):
            pass
    return errors


def enable_dpi_awareness():
    import ctypes
    try:
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        ctypes.windll.user32.SetProcessDPIAware()


class Gate:
    """Alert on a new appearance; repeat persistent targets after cooldown.

    Compatible with Gate(hits, misses, cooldown) used by the existing UI.
    Confirmed absence starts a fresh episode, independently of cooldown.
    Rearm by elapsed absence time (0.5 s) or the configured miss count.
    A brief false negative does not restart the persistent reminder timer.
    """
    def __init__(self, hits=3, misses=5, cooldown=30, rearm_seconds=0.5):
        self.hits, self.misses = max(1, int(hits)), max(1, int(misses))
        self.cooldown = max(0.0, float(cooldown))
        self.rearm_seconds = max(0.0, float(rearm_seconds))
        self.yes = self.no = 0
        self.armed = True
        self.last = float('-inf')
        self.absent_since = None

    def _absence_confirmed(self, now):
        return self.absent_since is not None and (
            self.no >= self.misses or now - self.absent_since >= self.rearm_seconds)

    def update(self, matched, now):
        if not matched:
            self.yes = 0
            self.no += 1
            if self.absent_since is None:
                self.absent_since = now
            if self._absence_confirmed(now):
                self.armed = True
            return False
        # Also evaluate elapsed absence on the next matching sample.
        if self._absence_confirmed(now):
            self.armed = True
        self.absent_since = None
        self.no = 0
        self.yes += 1
        if self.yes < self.hits:
            return False
        # The interval only limits repeated alerts within a single appearance.
        # Keep at least one second between reminders if cooldown is set to zero.
        if self.armed or now - self.last >= max(1.0, self.cooldown):
            self.armed = False
            self.last = now
            return True
        return False


def libraries():
    migrate_default_threshold()
    global cv2, np, mss
    import cv2
    import numpy as np
    import mss


def capture(sct, region):
    frame = np.asarray(sct.grab(region))[:, :, :3].copy()
    if frame.shape[:2] != (region['height'], region['width']):
        frame = cv2.resize(frame, (region['width'], region['height']), interpolation=cv2.INTER_AREA)
    return frame


def save_image(path, frame):
    ok, data = cv2.imencode('.png', frame)
    if ok:
        data.tofile(str(path))
    return ok


def select(frame, title):
    x, y, w, h = map(int, cv2.selectROI(title, frame, showCrosshair=True))
    cv2.destroyAllWindows()
    if w < 12 or h < 12:
        raise ValueError('选择已取消或区域太小（至少 12×12）。')
    return x, y, w, h


def setup(monitor):
    with mss.mss() as sct:
        if not 1 <= monitor < len(sct.monitors):
            raise ValueError('显示器编号无效。使用 monitors 查看编号。')
        screen = sct.monitors[monitor]
        x, y, w, h = select(capture(sct, screen), '1 Select game area; Enter confirm; Esc cancel')
        region = dict(left=screen['left'] + x, top=screen['top'] + y, width=w, height=h)
        frame = capture(sct, region)
    x, y, w, h = select(frame, '2 Select FIXED dialog title/border; Enter confirm')
    template = frame[y:y+h, x:x+w]
    if cv2.cvtColor(template, cv2.COLOR_BGR2GRAY).std() < 5:
        raise ValueError('模板缺少纹理，请选择带固定文字和边框的区域。')
    target = ROOT / 'templates' / 'dialog.png'
    target.parent.mkdir(exist_ok=True)
    if not save_image(target, template):
        raise ValueError('模板保存失败。')
    cfg = dict(region=region, templates=['templates/dialog.png'], threshold=DEFAULT_THRESHOLD,
               scales=[0.9, 1.0, 1.1], interval=0.5, hits=3, misses=5, cooldown=30)
    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding='utf-8')
    print('已保存配置和模板。请先使用 check 检查截图，再运行 run。')


def load():
    migrate_default_threshold()
    cfg = json.loads(CONFIG.read_text(encoding='utf-8'))
    r = cfg['region']
    if set(r) != {'left', 'top', 'width', 'height'} or not all(type(v) is int for v in r.values()):
        raise ValueError('region 必须包含四个整数坐标。')
    if min(r['width'], r['height']) < 12:
        raise ValueError('监控区域太小。')
    if not 0 < cfg['threshold'] <= 1 or cfg['interval'] < 0.1:
        raise ValueError('threshold 应为 (0,1]；interval 至少为 0.1 秒。')
    if any(type(cfg[k]) is not int or cfg[k] < 1 for k in ['hits', 'misses']) or cfg['cooldown'] < 0:
        raise ValueError('hits/misses 必须是正整数，cooldown 不能为负。')
    if not cfg['scales'] or any(not 0.25 <= s <= 4 for s in cfg['scales']):
        raise ValueError('scales 必须为 0.25 到 4 之间的非空列表。')
    if cfg.get('profile') == 'three-green-targets-v1':
        from green_targets import load_variants
        cfg['interval'] = min(cfg['interval'], 0.1)
        return cfg, load_variants(r, cfg['scales'])
    if cfg.get('profile') == 'tw-prelude-v1':
        from detector_profile import load_variants
        return cfg, load_variants(r, cfg['scales'])
    if cfg.get('profile'):
        raise ValueError('未知内置模板配置，请重新设置游戏区域。')
    templates = []
    for name in cfg['templates']:
        im = cv2.imdecode(np.fromfile(str(ROOT / name), dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
        if im is None or im.std() < 5:
            raise ValueError('模板无效或缺少纹理：' + name)
        for scale in cfg['scales']:
            t = cv2.resize(im, None, fx=scale, fy=scale)
            if t.shape[0] <= r['height'] and t.shape[1] <= r['width'] and t.std() >= 5:
                templates.append(t)
    if not templates:
        raise ValueError('没有能放入监控区域的有效模板。')
    return cfg, templates


def score(frame, templates, threshold=None):
    if isinstance(templates[0], dict) and templates[0].get('kind') == 'green-targets':
        from green_targets import green_score
        return green_score(frame, templates, threshold)
    if isinstance(templates[0], dict):
        from detector_profile import paired_score
        return paired_score(frame, templates)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return max(float(cv2.minMaxLoc(cv2.matchTemplate(gray, t, cv2.TM_CCOEFF_NORMED))[1]) for t in templates)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['monitors', 'setup', 'check', 'run', 'test-alert'])
    parser.add_argument('--monitor', type=int, default=1)
    args = parser.parse_args()
    if sys.platform != 'win32':
        raise ValueError('此版本请在 Windows 10/11 上运行。')
    if args.command == 'test-alert':
        notify()
        time.sleep(3)
        return
    enable_dpi_awareness()
    libraries()
    if args.command == 'monitors':
        with mss.mss() as sct:
            for i, monitor in enumerate(sct.monitors[1:], 1):
                print(i, monitor)
        return
    if args.command == 'setup':
        setup(args.monitor)
        return
    cfg, templates = load()
    gate = Gate(cfg['hits'], cfg['misses'], cfg['cooldown'])
    with mss.mss() as sct:
        if args.command == 'check':
            frame = capture(sct, cfg['region'])
            out = ROOT / 'check.png'
            if not save_image(out, frame):
                raise ValueError('截图保存失败。')
            print('当前分数 %.3f，阈值 %.3f；截图：%s' % (score(frame, templates), cfg['threshold'], out))
            return
        print('监控中；Ctrl+C 停止。截图只在内存中处理。', flush=True)
        while True:
            started = time.monotonic()
            value = score(capture(sct, cfg['region']), templates, cfg['threshold'])
            print('\r匹配分数 %.3f / %.3f' % (value, cfg['threshold']), end='', flush=True)
            if gate.update(value >= cfg['threshold'], time.monotonic()):
                notify()
            time.sleep(max(0, cfg['interval'] - (time.monotonic() - started)))


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\n已停止。')
    except Exception as exc:
        raise SystemExit('错误：%s\n若截图失败/黑屏，请使用窗口化或无边框窗口，并检查 check.png。' % exc)

