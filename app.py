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
from recorder import ScreenRecorder

TITLE = "冒险岛提醒V1.0（永久版）"
ASSETS = Path(__file__).resolve().parent / "assets"


def ui_state(configured, running=False, stopping=False, detected=False):
    if stopping:
        return dict(status="正在停止", color="#8A691F", region=False, start=False, stop=False)
    if running:
        return dict(status="发现目标，请查看游戏" if detected else "监控中", color="#B34040" if detected else "#1D655D", region=False, start=False, stop=True)
    return dict(status="已就绪" if configured else "尚未选择监控区域", color="#1E3036" if configured else "#75818A", region=True, start=bool(configured), stop=False)



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
        self.recorder = ScreenRecorder(lambda kind, data: self.events.put((kind, data)))
        self.record_on_alarm = tk.BooleanVar(value=True)
        self.recording_status = tk.StringVar(value="报警自动录屏 · 手动停止")
        self.monitor_region = None
        self.busy = False
        self.closing = False
        self.settings_window = None
        self.restore_settings_after_capture = False
        self.about_window = None
        self.monitors = []
        self.monitor_index = 0
        self.configured = False
        self.stopping = False
        self.detected = False
        self.last_alert_time = None
        self.threshold = tk.StringVar(value='0.80')
        self.score = tk.StringVar(value='匹配分数：—')
        self.last_reminder = tk.StringVar(value='最近提醒：—')
        self.feedback = tk.StringVar(value='')
        self.status = tk.StringVar()
        self.poll_job = None
        self.threshold_entry = self.monitor = self.check_button = None
        root.title(TITLE)
        root.configure(bg='#F3F5F6')
        dpi = max(1.0, root.winfo_fpixels('1i') / 96)
        self.px = lambda n: round(n * dpi)
        style = ttk.Style(root)
        style.theme_use('clam')
        style.configure('.', font=('Microsoft YaHei UI', 10), background='#FFFFFF', foreground='#1E3036')
        style.configure('TFrame', background='#FFFFFF')
        style.configure('TLabel', background='#FFFFFF', foreground='#1E3036')
        style.configure('Title.TLabel', font=('Microsoft YaHei UI', 16, 'bold'))
        style.configure('Status.TLabel', font=('Microsoft YaHei UI', 18, 'bold'))
        style.configure('Muted.TLabel', foreground='#75818A', font=('Microsoft YaHei UI', 9))
        style.configure('TButton', padding=(self.px(14), self.px(9)), background='#FFFFFF', bordercolor='#CBD3D7')
        style.map('TButton', foreground=[('disabled', '#9BA5AC')], background=[('disabled', '#EEF1F3'), ('active', '#F1F6F5')])
        style.configure('Primary.TButton', background='#1D655D', foreground='#FFFFFF', bordercolor='#1D655D', font=('Microsoft YaHei UI', 12, 'bold'))
        style.map('Primary.TButton', background=[('disabled', '#E7ECEE'), ('active', '#154F49')], foreground=[('disabled', '#9BA5AC'), ('!disabled', '#FFFFFF')])
        style.configure('Quiet.TButton', padding=(self.px(7), self.px(4)), borderwidth=0)
        style.configure('Footer.TFrame', background='#F3F5F6')
        style.configure('Footer.TLabel', background='#F3F5F6', foreground='#75818A', font=('Microsoft YaHei UI', 9))
        try:
            self.window_icon = tk.PhotoImage(file=str(ASSETS / 'app-icon.png'))
            root.iconphoto(True, self.window_icon)
            self.header_icon = tk.PhotoImage(file=str(ASSETS / 'app-icon-48.png'))
            if sys.platform == 'win32':
                root.iconbitmap(str(ASSETS / 'app-icon.ico'))
        except (tk.TclError, OSError):
            self.window_icon = self.header_icon = None
        body = ttk.Frame(root, padding=self.px(26))
        body.pack(fill='both', expand=True)
        header = ttk.Frame(body)
        header.pack(fill='x')
        if self.header_icon:
            ttk.Label(header, image=self.header_icon).pack(side='left', padx=(0, self.px(12)))
        ttk.Label(header, text=TITLE, style='Title.TLabel').pack(side='left')
        self.settings_button = ttk.Button(header, text='设置', style='Quiet.TButton', command=self.open_settings)
        self.settings_button.pack(side='right', padx=(self.px(12), 0))
        status_row = ttk.Frame(body)
        status_row.pack(fill='x', pady=(self.px(30), self.px(22)))
        self.dot = tk.Canvas(status_row, width=self.px(15), height=self.px(15), bg='#FFFFFF', highlightthickness=0)
        self.dot.create_oval(self.px(2), self.px(2), self.px(13), self.px(13), fill='#75818A', outline='', tags='dot')
        self.dot.pack(side='left', padx=(0, self.px(12)))
        ttk.Label(status_row, textvariable=self.status, style='Status.TLabel').pack(side='left')
        metrics = ttk.Frame(body)
        metrics.pack(fill='x', pady=(0, self.px(20)))
        self.score_label = ttk.Label(metrics, textvariable=self.score)
        self.score_label.grid(row=0, column=0, sticky='w')
        threshold_row = ttk.Frame(metrics)
        threshold_row.grid(row=0, column=1, sticky='e')
        ttk.Label(threshold_row, text='匹配阈值：').pack(side='left')
        self.threshold_label = ttk.Label(threshold_row, textvariable=self.threshold)
        self.threshold_label.pack(side='left')
        self.reminder_label = ttk.Label(metrics, textvariable=self.last_reminder, style='Muted.TLabel')
        self.reminder_label.grid(row=1, column=0, columnspan=2, sticky='w', pady=(self.px(8),0))
        metrics.columnconfigure(0, weight=1)
        self.setup_button = ttk.Button(body, text='1  选择监控区域', command=self.setup)
        self.setup_button.pack(fill='x', pady=(0, self.px(12)))
        self.start_button = ttk.Button(body, text='2  开始监控', style='Primary.TButton', command=self.toggle)
        self.start_button.pack(fill='x')
        recording_row = ttk.Frame(body)
        recording_row.pack(fill='x', pady=(self.px(14),0))
        self.recording_label = ttk.Label(recording_row, textvariable=self.recording_status, style='Muted.TLabel')
        self.recording_label.pack(side='left')
        self.stop_recording_button = ttk.Button(recording_row, text='停止录制', style='Quiet.TButton', command=self.stop_recording, state='disabled')
        self.stop_recording_button.pack(side='right')
        self.open_recordings_button = ttk.Button(body, text='打开录屏文件夹', style='Quiet.TButton', command=self.open_recordings)
        self.open_recordings_button.pack(anchor='w', pady=(self.px(6),0))
        self.first_hint = ttk.Label(body, text='请先选择监控区域', style='Muted.TLabel')
        self.first_hint.pack(anchor='w', pady=(self.px(10), 0))
        footer = ttk.Frame(root, style='Footer.TFrame', padding=(self.px(26), self.px(12)))
        footer.pack(fill='x', side='bottom')
        ttk.Label(footer, text='仅本地识别 · 不操作游戏', style='Footer.TLabel').pack(side='left')
        ttk.Button(footer, text='关于', style='Quiet.TButton', command=self.open_about).pack(side='right')
        if alert.CONFIG.exists():
            try:
                cfg = json.loads(alert.CONFIG.read_text(encoding='utf-8'))
                self.threshold.set(str(cfg.get('threshold', .80)))
                self.configured = 'region' in cfg
                self.record_on_alarm.set(bool(cfg.get('record_on_alarm', True)))
            except (ValueError, OSError):
                self.feedback.set('配置无法读取，请重新选择监控区域。')
        if not self.record_on_alarm.get():
            self.recording_status.set('报警自动录屏已关闭')
        self.refresh()
        self.render_state()
        root.update_idletasks()
        width = max(self.px(570), root.winfo_reqwidth())
        height = max(self.px(390), root.winfo_reqheight())
        root.geometry('%dx%d' % (width, height))
        root.minsize(width, height)
        root.protocol('WM_DELETE_WINDOW', self.close)
        self.poll_job = root.after(50, self.poll)

    def render_state(self):
        running = self.worker is not None
        state = ui_state(self.configured, running, self.stopping, self.detected)
        self.status.set(state['status'])
        self.dot.itemconfigure('dot', fill=state['color'])
        self.setup_button.configure(text='1  重新选择监控区域' if self.configured else '1  选择监控区域', state='normal' if state['region'] and not self.busy and not self.recorder.active else 'disabled')
        self.start_button.configure(text='2  停止监控' if running else '2  开始监控', state='normal' if (state['stop'] or state['start']) and not self.busy else 'disabled')
        if self.configured or running:
            self.first_hint.pack_forget()
        elif not self.first_hint.winfo_manager():
            self.first_hint.pack(anchor='w', pady=(self.px(10), 0))
        self.stop_recording_button.configure(state='normal' if self.recorder.active and not self.closing else 'disabled')
        for widget in [self.threshold_entry, self.check_button]:
            if widget is not None and widget.winfo_exists():
                widget.configure(state='disabled' if running or self.busy else 'normal')
        if self.monitor is not None and self.monitor.winfo_exists():
            self.monitor.configure(state='disabled' if running or self.busy else 'readonly')

    def refresh(self):
        try:
            with alert.mss.mss() as sct:
                self.monitors = [dict(m) for m in sct.monitors[1:]]
            self.monitor_index = min(self.monitor_index, max(0, len(self.monitors)-1))
        except Exception as exc:
            self.feedback.set('无法读取显示器：' + str(exc))
        self.update_monitor_choices()

    def update_monitor_choices(self):
        if self.monitor is not None and self.monitor.winfo_exists():
            self.monitor['values'] = ['屏幕 %d · %d × %d' % (i, m['width'], m['height']) for i,m in enumerate(self.monitors,1)]
            if self.monitors:
                self.monitor.current(self.monitor_index)

    def error(self, exc):
        messagebox.showerror('操作未完成', str(exc), parent=self.root)

    def open_settings(self):
        if self.busy:
            return
        if self.settings_window is not None and self.settings_window.winfo_exists():
            self.settings_window.deiconify()
            self.settings_window.lift()
            return
        top = self.settings_window = tk.Toplevel(self.root)
        top.title('设置 · ' + TITLE)
        top.configure(bg='#FFFFFF')
        top.transient(self.root)
        frame = ttk.Frame(top, padding=self.px(22))
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='监控设置', font=('Microsoft YaHei UI', 14, 'bold')).grid(row=0,column=0,columnspan=3,sticky='w',pady=(0,self.px(18)))
        ttk.Label(frame,text='监控屏幕').grid(row=1,column=0,sticky='w',padx=(0,self.px(16)))
        self.monitor = ttk.Combobox(frame,state='readonly',width=25)
        self.monitor.grid(row=1,column=1,sticky='ew')
        self.monitor.bind('<<ComboboxSelected>>',lambda event: setattr(self,'monitor_index',self.monitor.current()))
        ttk.Button(frame,text='刷新',command=self.refresh).grid(row=1,column=2,padx=(self.px(8),0))
        ttk.Label(frame,text='匹配阈值').grid(row=2,column=0,sticky='w',pady=self.px(14))
        self.threshold_entry = ttk.Spinbox(frame,from_=0.5,to=0.99,increment=0.01,textvariable=self.threshold,width=7)
        self.threshold_entry.grid(row=2,column=1,sticky='w')
        ttk.Label(frame,text='数值越低越敏感，也更容易误报。',style='Muted.TLabel').grid(row=3,column=0,columnspan=3,sticky='w')
        actions=ttk.Frame(frame)
        actions.grid(row=4,column=0,columnspan=3,sticky='ew',pady=self.px(16))
        self.check_button = ttk.Button(actions,text='检查监控画面',command=self.check)
        self.check_button.pack(side='left',padx=(0,self.px(8)))
        ttk.Button(actions,text='测试声音与通知',command=self.test).pack(side='left')
        ttk.Label(frame,textvariable=self.feedback,style='Muted.TLabel',wraplength=self.px(440)).grid(row=7,column=0,columnspan=3,sticky='w')
        ttk.Button(frame,text='打开本地配置文件夹',command=lambda: os.startfile(str(alert.ROOT))).grid(row=8,column=0,columnspan=3,sticky='w',pady=(self.px(16),0))
        ttk.Checkbutton(frame,text='报警时自动录制监控区域（无声音）',variable=self.record_on_alarm).grid(row=9,column=0,columnspan=3,sticky='w',pady=(self.px(16),0))
        ttk.Label(frame,text='手动停止后保存 MP4；重复报警不会重复开录。',style='Muted.TLabel').grid(row=10,column=0,columnspan=3,sticky='w',pady=(self.px(6),0))
        bottom=ttk.Frame(frame)
        bottom.grid(row=11,column=0,columnspan=3,sticky='ew',pady=(self.px(18),0))
        ttk.Button(bottom,text='关于',command=self.open_about).pack(side='left')
        ttk.Button(bottom,text='保存设置',command=self.save_settings).pack(side='right')
        frame.columnconfigure(1,weight=1)
        self.update_monitor_choices()
        self.render_state()

    def save_settings(self):
        if self.worker is not None or self.busy:
            self.feedback.set('请先停止监控，再修改设置。')
            return
        try:
            value=self.threshold_value()
            if alert.CONFIG.exists():
                cfg=json.loads(alert.CONFIG.read_text(encoding='utf-8'))
                cfg['threshold']=value
                cfg['record_on_alarm']=self.record_on_alarm.get()
                self.write_config(cfg)
            if not self.recorder.active:
                self.recording_status.set('报警自动录屏 · 手动停止' if self.record_on_alarm.get() else '报警自动录屏已关闭')
            self.feedback.set('设置已保存。' if alert.CONFIG.exists() else '阈值已选定，请选择监控区域。')
        except Exception as exc:
            self.error(exc)

    def open_about(self):
        if self.about_window is not None and self.about_window.winfo_exists():
            self.about_window.lift()
            return
        top=self.about_window=tk.Toplevel(self.root)
        top.title('关于 · '+TITLE)
        top.transient(self.root)
        frame=ttk.Frame(top,padding=self.px(24));frame.pack(fill='both',expand=True)
        ttk.Label(frame,text=TITLE,style='Title.TLabel').pack(anchor='w')
        ttk.Label(frame,text='© 2026 老熊头',padding=(0,self.px(12))).pack(anchor='w')
        try:
            notice=(Path(__file__).resolve().parent/'COPYRIGHT.txt').read_text(encoding='utf-8')
        except OSError:
            notice='本软件仅供授权用户本人使用。未经书面许可，不得转售、出租、转授权或向第三方分发。第三方组件遵循各自许可证。'
        ttk.Label(frame,text=notice,wraplength=self.px(430),justify='left').pack(anchor='w')
        ttk.Label(frame,text='仅本地画面提醒，不自动操作游戏。',style='Muted.TLabel',padding=(0,self.px(14))).pack(anchor='w')
        ttk.Button(frame,text='关闭',command=top.destroy).pack(anchor='e')

    def threshold_value(self):
        value=float(self.threshold.get())
        if not 0 < value <= 1:
            raise ValueError('匹配阈值必须大于 0 且不超过 1。')
        return value

    def write_config(self,cfg):
        temp=alert.CONFIG.with_suffix('.tmp')
        temp.write_text(json.dumps(cfg,ensure_ascii=False,indent=2),encoding='utf-8')
        temp.replace(alert.CONFIG)

    def config(self):
        if not alert.CONFIG.exists():
            raise ValueError('请先选择监控区域。')
        cfg=json.loads(alert.CONFIG.read_text(encoding='utf-8'))
        cfg['threshold']=self.threshold_value()
        cfg['record_on_alarm']=self.record_on_alarm.get()
        self.write_config(cfg)
        return alert.load()

    def hide_for_capture(self):
        self.restore_settings_after_capture = bool(self.settings_window is not None and self.settings_window.winfo_exists() and self.settings_window.state() != "withdrawn")
        if self.restore_settings_after_capture:
            self.settings_window.withdraw()
        self.root.withdraw()

    def restore_after_capture(self):
        self.root.deiconify()
        if self.restore_settings_after_capture and self.settings_window is not None and self.settings_window.winfo_exists():
            self.settings_window.deiconify()
            self.settings_window.lift()
        self.restore_settings_after_capture = False

    def setup(self):
        if self.busy or self.worker is not None or self.recorder.active:
            return
        if not self.monitors:
            return self.error('没有可用屏幕，请在设置中刷新。')
        screen=self.monitors[self.monitor_index]
        try:
            self.threshold_value()
        except Exception as exc:
            return self.error(exc)
        self.busy=True
        self.render_state()
        self.hide_for_capture()
        self.root.after(350,lambda:self.finish_setup(screen))

    def finish_setup(self,screen):
        try:
            with alert.mss.mss() as sct:
                frame=alert.capture(sct,screen)
            box=select_rectangle(self.root,frame,'框选监控区域；Enter 确认，Esc 取消')
            if box is None:
                return
            x,y,w,h=box
            region=dict(left=screen['left']+x,top=screen['top']+y,width=w,height=h)
            from green_targets import config_for_region,load_variants
            cfg=config_for_region(region)
            cfg['threshold']=self.threshold_value()
            cfg['record_on_alarm']=self.record_on_alarm.get()
            load_variants(region,cfg['scales'])
            self.write_config(cfg)
            self.configured=True
            self.feedback.set('监控区域已保存。')
        except Exception as exc:
            self.error(exc)
        finally:
            self.busy=False
            self.restore_after_capture()
            self.render_state()

    def check(self):
        if self.busy or self.worker is not None:
            return
        try:
            cfg,templates=self.config()
        except Exception as exc:
            return self.error(exc)
        self.busy=True
        self.render_state()
        self.hide_for_capture()
        self.root.after(350,lambda:self.finish_check(cfg,templates))

    def finish_check(self,cfg,templates):
        try:
            from PIL import Image,ImageTk
            with alert.mss.mss() as sct:
                frame=alert.capture(sct,cfg['region'])
            value=alert.score(frame,templates)
            self.score.set('匹配分数：%.3f'%value)
            self.restore_after_capture()
            top=tk.Toplevel(self.root);top.title('检查监控画面')
            ttk.Label(top,text='确认监控范围正确；没有目标时低分正常。',padding=12).pack()
            im=Image.fromarray(alert.cv2.cvtColor(frame,alert.cv2.COLOR_BGR2RGB))
            im.thumbnail((self.root.winfo_screenwidth()-100,self.root.winfo_screenheight()-200))
            photo=ImageTk.PhotoImage(im)
            label=ttk.Label(top,image=photo);label.image=photo;label.pack()
            ttk.Button(top,text='关闭预览',command=top.destroy).pack(pady=12)
            top.transient(self.root);top.grab_set()
        except Exception as exc:
            self.error(exc)
        finally:
            self.busy=False;self.restore_after_capture();self.render_state()

    def toggle(self):
        self.stop() if self.worker is not None else self.start()

    def start(self):
        if self.worker is not None or self.busy:
            return
        try:
            cfg,templates=self.config()
        except Exception as exc:
            return self.error(exc)
        self.monitor_region=dict(cfg['region'])
        self.stop_event.clear();self.stopping=False;self.detected=False
        self.worker=threading.Thread(target=self.monitor_loop,args=(cfg,templates),daemon=True)
        self.render_state()
        self.worker.start()

    def monitor_loop(self,cfg,templates):
        try:
            gate=alert.Gate(cfg['hits'],cfg['misses'],cfg['cooldown'])
            with alert.mss.mss() as sct:
                self.events.put(('running',None))
                while not self.stop_event.is_set():
                    started=time.monotonic()
                    value=alert.score(alert.capture(sct,cfg['region']),templates,cfg['threshold'])
                    self.events.put(('score',(value,cfg['threshold'])))
                    if gate.update(value>=cfg['threshold'],time.monotonic()):
                        self.events.put(('alert',None))
                    self.events.put(('target',not gate.armed))
                    self.stop_event.wait(max(0,cfg['interval']-(time.monotonic()-started)))
        except Exception as exc:
            self.events.put(('error',str(exc)))
        finally:
            self.events.put(('done',None))

    def stop(self):
        self.stop_event.set();self.stopping=True;self.render_state()

    def test(self):
        errors=alert.notify()
        self.feedback.set('测试提醒已发送，请检查声音与通知。' if not errors else '；'.join(errors))

    def open_recordings(self):
        try:
            self.recorder.folder.mkdir(parents=True, exist_ok=True)
            os.startfile(str(self.recorder.folder))
        except Exception as exc:
            self.error(exc)

    def stop_recording(self):
        if self.recorder.active:
            self.recorder.stop()
            self.recording_status.set('正在结束录制并保存…')
            self.stop_recording_button.configure(state='disabled')

    def record_alarm(self):
        if not self.record_on_alarm.get() or self.monitor_region is None:
            return
        try:
            if self.recorder.start(self.monitor_region):
                self.recording_status.set('正在启动录屏…')
                self.render_state()
        except Exception as exc:
            self.recording_status.set('录屏失败，请查看设置')
            self.feedback.set('录屏失败：'+str(exc))

    def recording_event(self, state, value):
        if state=='starting':
            self.recording_status.set('正在启动录屏…')
        elif state=='active':
            self.recording_status.set('正在录制 · %02d:%02d'%(value//60,value%60))
        elif state=='saving':
            self.recording_status.set('正在保存录屏…')
        elif state=='saved':
            self.recording_status.set('录屏已保存')
            self.feedback.set('录屏已保存：'+value)
        elif state=='error':
            self.recording_status.set('录屏失败，请查看设置')
            self.feedback.set('录屏失败：'+value)
        self.render_state()

    def poll(self):
        try:
            while True:
                kind,data=self.events.get_nowait()
                if kind=='score':
                    self.score.set('匹配分数：%.3f'%data[0])
                elif kind=='target':
                    if self.detected != data:
                        self.detected=data;self.render_state()
                elif kind=='alert' and not self.stop_event.is_set():
                    self.record_alarm()
                    self.test();self.detected=True
                    self.last_reminder.set('最近提醒：'+time.strftime('%H:%M:%S'))
                    self.render_state()
                elif kind=='recording':
                    self.recording_event(*data)
                elif kind=='error' and not self.closing:
                    self.error(data)
                elif kind=='done':
                    self.worker=None;self.stopping=False;self.detected=False;self.render_state()
        except queue.Empty:
            pass
        if self.closing and self.worker is None and not self.recorder.active:
            self.root.destroy();return
        self.poll_job=self.root.after(50,self.poll)

    def close(self):
        if self.busy:
            return
        self.closing=True;self.stop_event.set();self.recorder.stop();self.root.withdraw()
        if self.worker is None and not self.recorder.active:
            if self.poll_job is not None:
                self.root.after_cancel(self.poll_job)
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
        from recorder import codec_self_test
        codec_self_test()
        root = tk.Tk()
        root.withdraw()
        for name in ['app-icon.png', 'app-icon-48.png', 'app-icon.ico']:
            assert (ASSETS / name).exists(), name
        app = App(root)
        assert app.window_icon is not None
        assert app.status.get() in ['尚未选择监控区域', '已就绪']
        root.update_idletasks()
        if app.poll_job is not None:
            root.after_cancel(app.poll_job)
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

