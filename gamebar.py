"""Request Xbox Game Bar's explicit Start action; never toggle/stop recording."""
from pathlib import Path, PureWindowsPath
import re
import sys
import json
import subprocess
import tempfile
import time
import threading
from recorder import recording_folder



START = re.compile(r'^(?:开始录制|開始錄製|开始录影|開始錄影|start recording|record from now)(?:$|[\s（(：:])', re.I)
STOP = re.compile(r'^(?:停止录制|停止錄製|停止录影|停止錄影|stop recording)(?:$|[\s（(：:])', re.I)
NAMES = {'gamebar.exe', 'gamebarftserver.exe', 'gamebarwidget.exe'}


def verified_gamebar_path(path):
    p = PureWindowsPath(path)
    return (p.is_absolute() and p.name.lower() in NAMES and
            p.parent.name.lower().startswith('microsoft.xboxgamingoverlay_') and
            p.parent.name.lower().endswith('_8wekyb3d8bbwe') and
            p.parent.parent.name.lower() == 'windowsapps')


def process_image(pid):
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
    kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel.GetPackageFamilyName.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR)
    kernel.GetPackageFamilyName.restype = wintypes.LONG
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return ''
    try:
        family_size = wintypes.DWORD(0)
        if kernel.GetPackageFamilyName(handle, ctypes.byref(family_size), None) != 122:
            return ''
        family = ctypes.create_unicode_buffer(family_size.value)
        if kernel.GetPackageFamilyName(handle, ctypes.byref(family_size), family) != 0 or family.value.lower() != 'microsoft.xboxgamingoverlay_8wekyb3d8bbwe':
            return ''
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        return buffer.value if kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)) else ''
    finally:
        kernel.CloseHandle(handle)


def choose_start(controls):
    """Pure selector; controls are already scoped to verified Game Bar PID."""
    starts = []
    seen = []
    recording = False
    for control in controls:
        try:
            if not control.is_visible():
                continue
            name = control.window_text().strip()
            seen.append(name)
            if control.is_enabled():
                if STOP.match(name):
                    recording = True
                elif START.match(name):
                    starts.append(control)
        except Exception:
            continue
    # A visible, enabled Stop is evidence of an existing session. Never
    # invoke anything then, even if an old Start remains in the UI tree.
    if recording:
        return 'already', None
    if len(starts) == 1:
        return 'start', starts[0]
    if len(starts) > 1:
        raise RuntimeError('发现多个开始录制按钮，未执行。请只保留一个游戏栏捕获面板。')
    names = ' / '.join(seen[:16]) or '没有可访问的按钮'
    raise RuntimeError('未找到可用的“开始录制”按钮。请在游戏中按 Win+G 打开“捕获”面板，并保持面板可见。检测结果：'+names)


def gamebar_controls():
    from pywinauto import Desktop
    desktop = Desktop(backend='uia')
    paths = {}
    def trusted(pid):
        if pid not in paths:
            paths[pid] = verified_gamebar_path(process_image(pid))
        return paths[pid]
    controls = []
    seen = set()
    for window in desktop.windows(visible_only=True):
        try:
            # ApplicationFrameHost may own a host window, but every button
            # still must belong to an independently verified Game Bar PID.
            if not trusted(window.process_id()) and not re.search(r'game\s*bar|游戏栏|遊戲列|捕获|擷取|capture', window.window_text(), re.I):
                continue
            for button in window.descendants(control_type='Button'):
                if not trusted(button.process_id()):
                    continue
                identity = tuple(button.element_info.runtime_id)
                if identity not in seen:
                    seen.add(identity)
                    controls.append(button)
        except Exception:
            continue
    return controls


def captures_folder():
    fallback = recording_folder().parent / 'Captures'
    if sys.platform == 'win32':
        try:
            import os, winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders') as key:
                value, kind = winreg.QueryValueEx(key, '{EDC0FE71-98D8-4F4A-B920-C8DC133CB165}')
                if value:
                    return Path(os.path.expandvars(value))
        except OSError:
            pass
    return fallback


class GameBarStarter:
    """active means request-in-progress, never claims to own a video session."""
    external = True
    def __init__(self, emit, provider=None):
        self.emit = emit
        self.provider = provider
        self.folder = captures_folder()
        self._lock = threading.Lock()
        self._active = False
        self._cancel = threading.Event()
        self.thread = None

    @property
    def active(self):
        with self._lock:
            return self._active

    def start(self, region=None):
        with self._lock:
            if self._active:
                return False
            self._active = True
            self._cancel.clear()
            self.thread = threading.Thread(target=self._run, daemon=False)
            try:
                self.thread.start()
            except Exception:
                self._active = False
                raise
        return True

    def stop(self):
        # Cancels only a pending request. It does not stop Game Bar's video.
        self._cancel.set()

    def _run(self):
        try:
            self.emit('recording', ('starting', None))
            if self.provider is None:
                result = native_request(self._cancel)
            else:
                result = invoke_start(self.provider(), self._cancel)
            if result['state'] != 'cancelled':
                self.emit('recording', (result['state'], result.get('message')))
        except Exception as exc:
            self.emit('recording', ('error', str(exc)))
        finally:
            with self._lock:
                self._active = False
            self.emit('recording', ('idle', None))


def invoke_start(controls, cancel):
    if cancel.is_set():
        return dict(state='cancelled')
    action, button = choose_start(controls)
    if action == 'already':
        return dict(state='already')
    if not button.is_visible() or not button.is_enabled() or not START.match(button.window_text().strip()):
        raise RuntimeError('游戏栏按钮状态已变化，未发送请求，请查看捕获面板。')
    # Property reads are remote calls and may block; recheck afterwards.
    if cancel.is_set():
        return dict(state='cancelled')
    button.iface_invoke.Invoke()
    return dict(state='requested')


def _stop_child(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


def native_request(cancel, timeout=12, probe=False):
    """Bound UIA/RPC hangs in a helper process; no encoder or video frames."""
    if cancel.is_set():
        return dict(state='cancelled')
    with tempfile.TemporaryDirectory(prefix='MapleAlert-GameBar-') as directory:
        output = Path(directory) / 'result.json'
        command = [sys.executable]
        if not getattr(sys, 'frozen', False):
            command.append(str(Path(__file__).with_name('app.py')))
        command.extend(['--gamebar-request', str(output)])
        if probe:
            command.append('--gamebar-probe')
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            deadline = time.monotonic()+timeout
            while process.poll() is None:
                if cancel.wait(.05):
                    return dict(state='cancelled')
                if time.monotonic() >= deadline:
                    raise RuntimeError('游戏栏响应超时，未重复尝试；请检查系统录制计时器和捕获面板。')
            if not output.exists():
                raise RuntimeError('游戏栏启动模块未返回结果，请查看捕获面板。')
            result = json.loads(output.read_text(encoding='utf-8'))
            if result.get('state') not in ('requested','already','error','probe-ok'):
                raise RuntimeError('游戏栏返回了未知状态。')
            return result
        finally:
            _stop_child(process)


def backend_self_test():
    """Import frozen UIA dependencies only; no scanning or Invoke."""
    sys.coinit_flags = 0
    from pywinauto import Desktop
    from pywinauto.uia_defines import IUIA
    IUIA()


def run_gamebar_request(path, probe=False):
    try:
        backend_self_test()
        result = dict(state='probe-ok') if probe else invoke_start(gamebar_controls(), threading.Event())
    except Exception as exc:
        result = dict(state='error', message=str(exc)[:4000])
    destination = Path(path)
    temporary = destination.with_suffix('.tmp')
    temporary.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
    temporary.replace(destination)
    return 0
