"""Windows desktop UI. Runtime data stays in LOCALAPPDATA/MapleAlert."""
import json
import os
from pathlib import Path
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

import alert


def select_rectangle(parent, frame, title):
    """Choose on a scaled screenshot, return original pixel coordinates."""
    from PIL import Image, ImageTk
    image = Image.fromarray(alert.cv2.cvtColor(frame, alert.cv2.COLOR_BGR2RGB))
    scale = min(1, (parent.winfo_screenwidth() - 100) / image.width,
                (parent.winfo_screenheight() - 180) / image.height)
    size = (max(1, int(image.width * scale)), max(1, int(image.height * scale)))
    top = tk.Toplevel(parent)
    top.title(title)
    ttk.Label(top, text='拖动框选区域；松开鼠标后按 Enter 确认，Esc 取消。', padding=12).pack()
    photo = ImageTk.PhotoImage(image.resize(size))
    canvas = tk.Canvas(top, width=size[0], height=size[1], cursor='crosshair', highlightthickness=0)
    canvas.pack()
    canvas.create_image(0, 0, image=photo, anchor='nw')
    state = {'start': None, 'rect': None, 'box': None, 'result': None}

    def point(event):
        return max(0, min(size[0], event.x)), max(0, min(size[1], event.y))

    def down(event):
        state['start'] = point(event)
        state['box'] = None
        if state['rect'] is not None:
            canvas.delete(state['rect'])
        x, y = state['start']
        state['rect'] = canvas.create_rectangle(x, y, x, y, outline='#ff533e', width=3)

    def move(event):
        if state['start'] is None:
            return
        x, y = state['start']
        xx, yy = point(event)
        canvas.coords(state['rect'], x, y, xx, yy)
        state['box'] = (min(x, xx), min(y, yy), max(x, xx), max(y, yy))

    def accept(event=None):
        if state['box']:
            x1, y1, x2, y2 = state['box']
            x, y = round(x1 * image.width / size[0]), round(y1 * image.height / size[1])
            right, bottom = round(x2 * image.width / size[0]), round(y2 * image.height / size[1])
            if right - x >= 12 and bottom - y >= 12:
                state['result'] = (x, y, right - x, bottom - y)
                top.destroy()
                return
        messagebox.showinfo('区域太小', '请拖动选择至少 12 × 12 像素的区域。', parent=top)

    canvas.bind('<ButtonPress-1>', down)
    canvas.bind('<B1-Motion>', move)
    canvas.bind('<ButtonRelease-1>', move)
    top.bind('<Return>', accept)
    top.bind('<Escape>', lambda event: top.destroy())
    top.protocol('WM_DELETE_WINDOW', top.destroy)
    top.grab_set()
    top.focus_force()
    parent.wait_window(top)
    return state['result']


class App:
    def __init__(self, root):
        self.root = root
        self.events = queue.Queue()
        self.stop_event = threading.Event()
        self.worker = None
        self.busy = False
        self.closing = False
        self.monitors = []
        root.title('冒险岛 · 测谎仪提醒')
        root.geometry('700x680')
        root.minsize(660, 650)
        root.configure(bg='#edf3f7')
        style = ttk.Style(root)
        style.theme_use('clam')
        style.configure('.', font=('Microsoft YaHei UI', 10))
        style.configure('TFrame', background='#edf3f7')
        style.configure('TLabel', background='#edf3f7', foreground='#17314d')
        style.configure('Title.TLabel', font=('Microsoft YaHei UI', 21, 'bold'))
        style.configure('Status.TLabel', font=('Microsoft YaHei UI', 14, 'bold'))
        style.configure('TButton', padding=(14, 9))
        style.configure('Start.TButton', background='#245c96', foreground='white')
        body = ttk.Frame(root, padding=26)
        body.pack(fill='both', expand=True)
        ttk.Label(body, text='测谎仪出现时，提醒你', style='Title.TLabel').pack(anchor='w')
        ttk.Label(body, text='本地画面检测 · 声音与桌面通知', padding=(0, 7)).pack(anchor='w')
        ttk.Separator(body).pack(fill='x', pady=15)
        self.status = tk.StringVar(value='等待选择游戏区域')
        self.detail = tk.StringVar(value='已内置绿色准头模板；三个准头横向排列时提醒。')
        ttk.Label(body, textvariable=self.status, style='Status.TLabel').pack(anchor='w')
        ttk.Label(body, textvariable=self.detail, wraplength=580).pack(anchor='w', pady=(7, 18))
        row = ttk.Frame(body)
        row.pack(fill='x')
        ttk.Label(row, text='游戏所在屏幕').pack(side='left')
        self.monitor = ttk.Combobox(row, state='readonly', width=25)
        self.monitor.pack(side='left', padx=10)
        self.refresh_button = ttk.Button(row, text='刷新', command=self.refresh)
        self.refresh_button.pack(side='left')
        steps = ttk.Frame(body)
        steps.pack(fill='x', pady=14)
        self.setup_button = ttk.Button(steps, text='1  选择游戏区域', command=self.setup)
        self.setup_button.pack(side='left', padx=(0, 10))
        self.check_button = ttk.Button(steps, text='2  检查画面', command=self.check)
        self.check_button.pack(side='left')
        settings = ttk.Frame(body)
        settings.pack(fill='x', pady=(0, 15))
        ttk.Label(settings, text='识别阈值').pack(side='left')
        self.threshold = tk.StringVar(value='0.88')
        self.threshold_entry = ttk.Spinbox(settings, from_=0.5, to=0.99, increment=0.01,
                                          textvariable=self.threshold, width=7)
        self.threshold_entry.pack(side='left', padx=10)
        ttk.Label(settings, text='先用默认值；误报时提高阈值，并检查游戏区域。').pack(side='left')
        actions = ttk.Frame(body)
        actions.pack(fill='x')
        self.start_button = ttk.Button(actions, text='开始监控', style='Start.TButton', command=self.start)
        self.start_button.pack(side='left', padx=(0, 10))
        self.stop_button = ttk.Button(actions, text='停止', command=self.stop, state='disabled')
        self.stop_button.pack(side='left')
        self.test_button = ttk.Button(actions, text='测试声音与通知', command=self.test)
        self.test_button.pack(side='right')
        self.score = tk.StringVar(value='匹配分数：—')
        ttk.Label(body, textvariable=self.score).pack(anchor='w', pady=(20, 7))
        ttk.Label(body, text='只提醒，不解答验证码。游戏需保持可见；移动或缩放窗口后请重新设置。\n关闭本窗口即退出，最小化后仍继续监控。',
                  wraplength=580).pack(anchor='w')
        ttk.Button(body, text='打开本地配置文件夹', command=lambda: os.startfile(str(alert.ROOT))).pack(anchor='w', pady=(16, 0))
        if alert.CONFIG.exists():
            try:
                cfg = json.loads(alert.CONFIG.read_text(encoding='utf-8'))
                self.threshold.set(str(cfg['threshold']))
                self.status.set('已有配置，可以检查画面')
                if cfg.get('profile') != 'three-green-targets-v1':
                    self.detail.set('这是旧版配置。重新选择游戏区域即可启用三个绿色准头检测。')
            except Exception:
                self.status.set('配置无法读取，请重新设置')
        self.refresh()
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.after(100, self.poll)

    def refresh(self):
        try:
            with alert.mss.mss() as sct:
                self.monitors = [dict(m) for m in sct.monitors[1:]]
            self.monitor['values'] = ['屏幕 %d · %d × %d' % (i, m['width'], m['height'])
                                      for i, m in enumerate(self.monitors, 1)]
            if self.monitors:
                self.monitor.current(0)
        except Exception as exc:
            self.error(exc)

    def error(self, exc):
        messagebox.showerror('操作未完成', str(exc) + '\n\n请检查游戏是否可见，以及是否已选择游戏区域。', parent=self.root)

    def controls(self, active):
        for button in [self.setup_button, self.check_button, self.refresh_button, self.start_button]:
            button.configure(state='disabled' if active else 'normal')
        self.threshold_entry.configure(state='disabled' if active else 'normal')
        self.monitor.configure(state='disabled' if active else 'readonly')
        self.stop_button.configure(state='normal' if active else 'disabled')

    def setup(self):
        if self.busy or self.worker is not None:
            return
        if not self.monitors or self.monitor.current() < 0:
            return self.error('没有可用屏幕，请刷新。')
        messagebox.showinfo('设置说明', '保持游戏窗口可见，接下来只需框选整个游戏区域。\n\n已内置三个绿色准头检测，无需测谎仪当前出现，也无需框选测谎仪。', parent=self.root)
        screen = self.monitors[self.monitor.current()]
        self.busy = True
        self.root.withdraw()
        self.root.after(450, lambda: self.finish_setup(screen))

    def finish_setup(self, screen):
        try:
            with alert.mss.mss() as sct:
                frame = alert.capture(sct, screen)
            box = select_rectangle(self.root, frame, '框选整个游戏区域；无需测谎仪出现')
            if box is None:
                return
            x, y, w, h = box
            region = dict(left=screen['left'] + x, top=screen['top'] + y, width=w, height=h)
            from green_targets import config_for_region, load_variants
            cfg = config_for_region(region)
            load_variants(region, cfg['scales'])
            self.write_config(cfg)
            self.threshold.set(str(cfg['threshold']))
            self.status.set('设置完成，请检查画面')
            self.detail.set('已启用三个绿色准头检测；检查游戏区域后即可开始监控。')
        except Exception as exc:
            self.error(exc)
        finally:
            self.busy = False
            self.root.deiconify()

    def write_config(self, cfg):
        temp = alert.CONFIG.with_suffix('.tmp')
        temp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(alert.CONFIG)

    def config(self):
        if not alert.CONFIG.exists():
            raise ValueError('请先点击“选择游戏区域”。')
        value = float(self.threshold.get())
        if not 0 < value <= 1:
            raise ValueError('识别阈值必须大于 0 且不超过 1。')
        cfg = json.loads(alert.CONFIG.read_text(encoding='utf-8'))
        cfg['threshold'] = value
        self.write_config(cfg)
        return alert.load()

    def check(self):
        if self.busy or self.worker is not None:
            return
        try:
            cfg, templates = self.config()
        except Exception as exc:
            return self.error(exc)
        self.busy = True
        self.root.withdraw()
        self.root.after(450, lambda: self.finish_check(cfg, templates))

    def finish_check(self, cfg, templates):
        try:
            from PIL import Image, ImageTk
            with alert.mss.mss() as sct:
                frame = alert.capture(sct, cfg['region'])
            value = alert.score(frame, templates)
            self.score.set('匹配分数：%.3f / 阈值 %.3f' % (value, cfg['threshold']))
            self.root.deiconify()
            top = tk.Toplevel(self.root)
            top.title('检查当前监控画面（不保存截图）')
            ttk.Label(top, text='请核对游戏区域；没有三个准头时低分正常。分数 %.3f / 阈值 %.3f' % (value, cfg['threshold']), padding=12).pack()
            image = Image.fromarray(alert.cv2.cvtColor(frame, alert.cv2.COLOR_BGR2RGB))
            image.thumbnail((self.root.winfo_screenwidth() - 100, self.root.winfo_screenheight() - 200))
            photo = ImageTk.PhotoImage(image)
            label = ttk.Label(top, image=photo)
            label.image = photo
            label.pack()
            ttk.Button(top, text='关闭预览', command=top.destroy).pack(pady=12)
            top.transient(self.root)
            top.grab_set()
        except Exception as exc:
            self.error(exc)
        finally:
            self.busy = False
            self.root.deiconify()

    def start(self):
        if self.worker is not None or self.busy:
            return
        try:
            cfg, templates = self.config()
        except Exception as exc:
            return self.error(exc)
        self.stop_event.clear()
        self.controls(True)
        self.status.set('正在启动监控…')
        self.detail.set('请让本窗口离开游戏监控区域，保持游戏画面可见。')
        self.worker = threading.Thread(target=self.monitor_loop, args=(cfg, templates), daemon=True)
        self.worker.start()

    def monitor_loop(self, cfg, templates):
        try:
            gate = alert.Gate(cfg['hits'], cfg['misses'], cfg['cooldown'])
            with alert.mss.mss() as sct:
                self.events.put(('running', None))
                while not self.stop_event.is_set():
                    started = time.monotonic()
                    value = alert.score(alert.capture(sct, cfg['region']), templates)
                    self.events.put(('score', (value, cfg['threshold'])))
                    if gate.update(value >= cfg['threshold'], time.monotonic()):
                        self.events.put(('alert', None))
                    self.stop_event.wait(max(0, cfg['interval'] - (time.monotonic() - started)))
        except Exception as exc:
            self.events.put(('error', str(exc)))
        finally:
            self.events.put(('done', None))

    def stop(self):
        self.stop_event.set()
        self.status.set('正在停止…')
        self.stop_button.configure(state='disabled')

    def test(self):
        errors = alert.notify()
        self.detail.set('测试提醒已发送，请检查音量和 Windows 通知设置。' if not errors else '；'.join(errors))

    def poll(self):
        try:
            while True:
                kind, data = self.events.get_nowait()
                if kind == 'running' and not self.stop_event.is_set():
                    self.status.set('正在监控')
                elif kind == 'score':
                    self.score.set('匹配分数：%.3f / 阈值 %.3f' % data)
                elif kind == 'alert' and not self.stop_event.is_set():
                    self.test()
                    self.status.set('发现疑似测谎仪，请查看游戏')
                elif kind == 'error':
                    if not self.closing:
                        self.error(data)
                elif kind == 'done':
                    self.worker = None
                    self.controls(False)
                    self.status.set('已停止')
        except queue.Empty:
            pass
        if self.closing and self.worker is None:
            self.root.destroy()
            return
        self.root.after(100, self.poll)

    def close(self):
        if self.busy:
            return
        self.closing = True
        self.stop_event.set()
        self.root.withdraw()
        if self.worker is None:
            self.root.destroy()


def main():
    if sys.platform != 'win32':
        raise SystemExit('此图形界面版本面向 Windows 10/11。')
    alert.enable_dpi_awareness()
    root = tk.Tk()
    root.withdraw()
    try:
        alert.ROOT = Path(os.environ['LOCALAPPDATA']) / 'MapleAlert'
        alert.ROOT.mkdir(parents=True, exist_ok=True)
        alert.CONFIG = alert.ROOT / 'config.json'
        alert.libraries()
        App(root)
        root.deiconify()
        root.mainloop()
    except Exception as exc:
        messagebox.showerror('无法启动', str(exc), parent=root)
        root.destroy()
        raise


def build_self_test():
    """No capture, input, notifications or networking; checks the frozen bundle."""
    try:
        from windows_toasts import Toast, WindowsToaster
        import winsound
        import winrt.windows.data.xml.dom
        import winrt.windows.ui.notifications
        alert.libraries()
        np = alert.np
        template = np.random.default_rng(12).integers(0, 255, (20, 24), dtype=np.uint8)
        frame = np.zeros((80, 100, 3), dtype=np.uint8)
        frame[30:50, 40:64] = template[:, :, None]
        assert alert.score(frame, [template]) > 0.99
        from detector_profile import config_for_region, load_variants
        cfg = config_for_region(dict(left=0, top=0, width=640, height=400))
        variants = load_variants(cfg['region'], [1])
        v = variants[0]
        frame = np.zeros((400, 640, 3), dtype=np.uint8)
        for patch, (x, y) in [(v['title'], (20, 20)), (v['safety'], (121, 87))]:
            h, w = patch.shape
            frame[y:y+h, x:x+w] = patch[:, :, None]
        assert alert.score(frame, variants) > 0.888
        import green_targets
        variants = green_targets.load_variants(cfg['region'], [1])
        mask = alert.cv2.imdecode(np.fromfile(str(green_targets.ASSET.parent / 'green-target-1.png'), dtype=np.uint8), alert.cv2.IMREAD_GRAYSCALE)
        frame = np.zeros((400, 640, 3), dtype=np.uint8)
        for x in [80, 170, 260]:
            h, w = mask.shape
            roi = frame[200:200+h, x:x+w]
            roi[mask > 0] = (0, 255, 0)
        assert alert.score(frame, variants) > 0.88
        root = tk.Tk()
        root.withdraw()
        root.update_idletasks()
        root.destroy()
        return 0
    except Exception:
        import traceback
        path = Path(os.environ.get('TEMP', '.')) / 'MapleAlert-build-error.txt'
        path.write_text(traceback.format_exc(), encoding='utf-8')
        return 1


if __name__ == '__main__':
    if '--self-test' in sys.argv:
        raise SystemExit(build_self_test())
    main()
