"""Read-only default Windows audio-output checks; never changes volume."""
from dataclasses import dataclass
import math

@dataclass(frozen=True)
class SoundStatus:
    state: str
    text: str


def describe(muted, level):
    if not math.isfinite(level) or not 0 <= level <= 1:
        raise ValueError('invalid audio volume')
    if muted:
        return SoundStatus('warning','声音：系统已静音，请取消静音')
    if level <= 0:
        return SoundStatus('warning','声音：音量为 0，请调高音量')
    percent=max(1,round(level*100))
    return SoundStatus('ready',f'声音：系统音量 {percent}% · 请测试确认可听见')


def read_output():
    from pycaw.pycaw import AudioUtilities
    device=AudioUtilities.GetSpeakers()
    if device is None:
        return SoundStatus('warning','声音：没有可用播放设备，请检查连接')
    volume=device.EndpointVolume
    return describe(bool(volume.GetMute()),float(volume.GetMasterVolumeLevelScalar()))


def check_output(reader=None):
    """Each background check owns its COM apartment and releases it on exit."""
    if reader is not None:
        try:return reader()
        except Exception:return SoundStatus('unknown','声音：检查失败，请测试声音并检查播放设备')
    import sys
    if sys.platform != 'win32':
        return SoundStatus('unknown','声音：仅支持 Windows 检查')
    initialized=False
    try:
        import comtypes
        comtypes.CoInitialize()
        initialized=True
        return read_output()
    except Exception:
        return SoundStatus('unknown','声音：无法确认，请测试声音并检查播放设备')
    finally:
        if initialized:comtypes.CoUninitialize()
