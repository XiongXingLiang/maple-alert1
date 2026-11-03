import threading
import unittest
from gamebar import choose_start, GameBarStarter, verified_gamebar_path


class Button:
    def __init__(self,name,enabled=True,visible=True,fail=False):
        self.name=name;self.enabled=enabled;self.visible=visible;self.calls=0;self.fail=fail
        self.iface_invoke=self
    def window_text(self):return self.name
    def is_enabled(self):return self.enabled
    def is_visible(self):return self.visible
    def Invoke(self):
        self.calls+=1
        if self.fail:raise RuntimeError('invoke denied')
        self.name='Stop recording'


class GameBarTest(unittest.TestCase):
    def run_request(self,buttons):
        events=[];rec=GameBarStarter(lambda *e:events.append(e),provider=lambda:buttons)
        self.assertTrue(rec.start());rec.thread.join(5)
        self.assertFalse(rec.active);self.assertFalse(rec.thread.is_alive())
        return rec,events
    def test_supported_start_labels(self):
        for name in ['开始录制 (Win+Alt+R)','開始錄製','Start recording','Record from now']:
            with self.subTest(name=name):
                b=Button(name);rec,events=self.run_request([b]);self.assertEqual(b.calls,1)
                self.assertIn(('recording',('requested',None)),events)
    def test_already_recording_never_invokes_start_or_stop(self):
        start=Button('Start recording');stop=Button('停止录制')
        rec,events=self.run_request([start,stop])
        self.assertEqual(start.calls+stop.calls,0)
        self.assertIn(('recording',('already',None)),events)
    def test_repeated_alarm_does_not_toggle_stop(self):
        b=Button('Start recording');rec,events=self.run_request([b])
        rec.start();rec.thread.join(5);self.assertEqual(b.calls,1)
    def test_disabled_hidden_and_unknown_buttons_fail_without_action(self):
        for b in [Button('Start recording',enabled=False),Button('Start recording',visible=False),Button('Start/stop recording'),Button('Record last 30 seconds')]:
            rec,events=self.run_request([b]);self.assertEqual(b.calls,0)
            self.assertTrue(any(e[1][0]=='error' for e in events))
    def test_ambiguous_start_buttons_never_invoked(self):
        bs=[Button('Start recording'),Button('开始录制')]
        rec,events=self.run_request(bs);self.assertEqual(sum(b.calls for b in bs),0)
        self.assertTrue(any(e[1][0]=='error' for e in events))
    def test_invoke_failure_is_not_reported_as_recording(self):
        rec,events=self.run_request([Button('Start recording',fail=True)])
        self.assertTrue(any(e[1][0]=='error' for e in events))
        self.assertNotIn(('recording',('requested',None)),events)
    def test_cancel_and_duplicate_while_provider_pending(self):
        gate=threading.Event();ready=threading.Event();b=Button('Start recording')
        def provider():ready.set();gate.wait(5);return [b]
        rec=GameBarStarter(lambda *e:None,provider=provider)
        rec.start();self.assertTrue(ready.wait(5));self.assertFalse(rec.start())
        rec.stop();gate.set();rec.thread.join(5);self.assertEqual(b.calls,0)
    def test_cancel_during_live_property_read_does_not_invoke(self):
        ready=threading.Event();gate=threading.Event()
        class SlowButton(Button):
            reads=0
            def window_text(self):
                self.reads+=1
                if self.reads==2:
                    ready.set();gate.wait(5)
                return self.name
        b=SlowButton('Start recording')
        rec=GameBarStarter(lambda *e:None,provider=lambda:[b])
        rec.start();self.assertTrue(ready.wait(5));rec.stop();gate.set();rec.thread.join(5)
        self.assertEqual(b.calls,0);self.assertFalse(rec.active)

    def test_already_cancelled_native_request_does_not_spawn(self):
        from unittest.mock import patch
        from gamebar import native_request
        cancel=threading.Event();cancel.set()
        with patch('gamebar.subprocess.Popen') as popen:
            self.assertEqual(native_request(cancel)['state'],'cancelled')
        popen.assert_not_called()

    def test_native_timeout_terminates_child(self):
        from unittest.mock import Mock, patch
        from gamebar import native_request
        process=Mock();process.poll.return_value=None
        with patch('gamebar.subprocess.Popen',return_value=process):
            with self.assertRaisesRegex(RuntimeError,'超时'):
                native_request(threading.Event(),timeout=0)
        process.terminate.assert_called_once()
        process.wait.assert_called_once()

    def test_native_cancel_terminates_pending_child(self):
        from unittest.mock import Mock, patch
        from gamebar import native_request
        cancel=threading.Event();process=Mock();process.poll.return_value=None
        def spawn(*args,**kwargs):cancel.set();return process
        with patch('gamebar.subprocess.Popen',side_effect=spawn):
            self.assertEqual(native_request(cancel)['state'],'cancelled')
        process.terminate.assert_called_once()

    def test_native_result_roundtrip_and_probe_flag(self):
        import json
        from pathlib import Path
        from unittest.mock import Mock, patch
        from gamebar import native_request
        process=Mock();process.poll.return_value=0
        def spawn(command,**kwargs):
            self.assertIn('--gamebar-probe',command)
            output=Path(command[command.index('--gamebar-request')+1])
            output.write_text(json.dumps(dict(state='probe-ok')))
            return process
        with patch('gamebar.subprocess.Popen',side_effect=spawn):
            self.assertEqual(native_request(threading.Event(),probe=True)['state'],'probe-ok')
        process.terminate.assert_not_called()

    def test_foreground_guard_rejects_reminder_but_allows_other_target(self):
        from unittest.mock import patch
        from gamebar import guard_foreground
        with patch('gamebar.foreground_process',return_value=123):
            with self.assertRaisesRegex(RuntimeError,'前台'):
                guard_foreground(123)
            guard_foreground(456)

    def test_process_scope(self):
        self.assertTrue(verified_gamebar_path(r'C:\Program Files\WindowsApps\Microsoft.XboxGamingOverlay_1.0_x64__8wekyb3d8bbwe\GameBar.exe'))
        for p in [r'C:\fake\GameBar.exe',r'C:\Temp\Microsoft.XboxGamingOverlay_fake\GameBar.exe',r'C:\Windows\notepad.exe',r'C:\Microsoft.XboxGamingOverlay_1\game.exe']:
            self.assertFalse(verified_gamebar_path(p))


if __name__=='__main__':unittest.main()
