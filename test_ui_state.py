import ast
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


# Exercise state logic without requiring a local graphical display.
module = ast.parse((Path(__file__).parent / 'app.py').read_text(encoding='utf-8'))
function = next(n for n in module.body if isinstance(n, ast.FunctionDef) and n.name == 'ui_state')
namespace = {}
exec(compile(ast.Module(body=[function], type_ignores=[]), 'ui_state', 'exec'), namespace)
ui_state = namespace['ui_state']


class StateTest(unittest.TestCase):
    def test_first_open_requires_region(self):
        s=ui_state(False)
        self.assertEqual(s['status'],'尚未选择监控区域')
        self.assertTrue(s['region']);self.assertFalse(s['start']);self.assertFalse(s['stop'])

    def test_existing_configuration_can_start(self):
        s=ui_state(True)
        self.assertEqual(s['status'],'已就绪')
        self.assertTrue(s['start']);self.assertTrue(s['region'])

    def test_running_disables_reselection(self):
        s=ui_state(True,running=True)
        self.assertEqual(s['status'],'监控中')
        self.assertTrue(s['stop']);self.assertFalse(s['start']);self.assertFalse(s['region'])

    def test_stopping_blocks_new_operations(self):
        s=ui_state(True,running=True,stopping=True)
        self.assertEqual(s['status'],'正在停止')
        self.assertFalse(any(s[k] for k in ['start','stop','region']))

    def test_detected_has_readable_status_not_only_color(self):
        s=ui_state(True,running=True,detected=True)
        self.assertIn('发现目标',s['status']);self.assertTrue(s['stop'])


@unittest.skipUnless(sys.platform=='win32','Real Tk layout is verified on the Windows build runner')
class WindowsWidgetTest(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        import app,alert
        self.app_module,self.alert=app,alert
        self.temp=tempfile.TemporaryDirectory()
        self.config_path=Path(self.temp.name)/'config.json'
        self.patch_config=patch.object(alert,'CONFIG',self.config_path);self.patch_config.start()
        self.patch_root=patch.object(alert,'ROOT',Path(self.temp.name));self.patch_root.start()
        def refresh(instance):
            instance.monitors=[dict(left=0,top=0,width=1920,height=1080)]
            instance.update_monitor_choices()
        self.patch_refresh=patch.object(app.App,'refresh',refresh);self.patch_refresh.start()
        self.root=tk.Tk();self.root.withdraw();self.app=None

    def make_app(self,cfg=None,scale=1):
        if cfg:
            self.config_path.write_text(json.dumps(cfg),encoding='utf-8')
        scaling=float(self.root.tk.call('tk','scaling'))
        self.root.tk.call('tk','scaling',scaling*scale)
        self.app=self.app_module.App(self.root)
        self.root.deiconify();self.root.update()
        return self.app

    def tearDown(self):
        if self.app:
            self.app.worker=None;self.app.busy=False;self.app.close()
        else:
            self.root.destroy()
        self.patch_refresh.stop();self.patch_root.stop();self.patch_config.stop();self.temp.cleanup()

    def test_no_configuration_disables_start(self):
        a=self.make_app()
        self.assertEqual(str(a.start_button['state']),'disabled')
        self.assertEqual(a.setup_button['text'],'1  选择监控区域')
        self.assertEqual(self.root.title(),'冒险岛提醒V1.0（永久版）')

    def test_settings_preserve_custom_values_and_reopen(self):
        cfg=dict(threshold=.91,region=dict(left=10,top=20,width=640,height=400))
        a=self.make_app(cfg)
        a.open_settings();self.root.update()
        self.assertEqual(a.threshold.get(),'0.91')
        a.settings_window.withdraw();a.open_settings();self.root.update()
        self.assertNotEqual(a.settings_window.state(),'withdrawn')
        a.save_settings()
        actual=json.loads(self.config_path.read_text())
        self.assertEqual(actual['region'],cfg['region']);self.assertEqual(actual['threshold'],.91)
        a.settings_window.destroy();a.open_settings();self.root.update()
        self.assertEqual(a.threshold.get(),'0.91')

    def test_running_controls_and_stop_then_ready(self):
        a=self.make_app(dict(threshold=.8,region=dict(left=0,top=0,width=640,height=400)))
        a.worker=object();a.render_state()
        self.assertEqual(a.start_button['text'],'2  停止监控')
        self.assertEqual(str(a.setup_button['state']),'disabled')
        a.stop();self.assertTrue(a.stop_event.is_set());self.assertEqual(a.status.get(),'正在停止')
        a.worker=None;a.stopping=False;a.render_state()
        self.assertEqual(str(a.start_button['state']),'normal')
        self.assertEqual(a.status.get(),'已就绪')

    def test_cancel_selection_preserves_region_and_threshold(self):
        cfg=dict(threshold=.93,region=dict(left=30,top=50,width=640,height=400))
        a=self.make_app(cfg);a.open_settings();self.root.update();a.hide_for_capture();a.busy=True
        with patch.object(self.alert,'mss',create=True) as mss, patch.object(self.alert,'capture',return_value=None), patch.object(self.app_module,'select_rectangle',return_value=None):
            a.finish_setup(dict(left=0,top=0,width=1920,height=1080))
        self.assertEqual(json.loads(self.config_path.read_text()),cfg)
        self.assertEqual(a.threshold.get(),'0.93');self.assertFalse(a.busy)
        self.assertNotEqual(a.settings_window.state(),'withdrawn')

    def test_recording_status_displays_measured_fps(self):
        a=self.make_app()
        a.recording_event('active',(4,None,0))
        self.assertIn('—',a.recording_status.get())
        a.recording_event('active',(65,23.6,8))
        self.assertIn('01:05',a.recording_status.get())
        self.assertIn('实际 24 FPS',a.recording_status.get())

    def test_sound_test_does_not_start_recording(self):
        a=self.make_app()
        with patch.object(self.alert,'notify',return_value=[]), patch.object(a.recorder,'start') as start:
            a.test()
        start.assert_not_called()

    def test_alarm_starts_recording_and_failure_does_not_block_sound(self):
        a=self.make_app()
        a.monitor_region=dict(left=0,top=0,width=640,height=400)
        a.events.put(('alert',None))
        with patch.object(a.recorder,'start',side_effect=OSError('disk full')) as start, patch.object(self.alert,'notify',return_value=[]) as notify:
            a.poll()
        start.assert_called_once_with(a.monitor_region)
        notify.assert_called_once()
        self.assertIn('录屏失败',a.recording_status.get())
        self.assertIn('disk full',a.feedback.get())
        self.assertTrue(a.detected)

    def test_close_waits_for_recording_finalization(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        a=self.make_app()
        fake=SimpleNamespace(active=True,stop=Mock())
        a.recorder=fake
        a.close()
        fake.stop.assert_called_once()
        self.assertTrue(a.closing)
        self.assertTrue(self.root.winfo_exists())
        fake.active=False

    def test_save_failure_during_close_restores_window_and_details(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        a=self.make_app()
        fake=SimpleNamespace(active=True,stop=Mock())
        a.recorder=fake
        a.close();fake.active=False
        a.events.put(('recording',('error','rename failed; partial.mp4')))
        a.events.put(('recording',('idle',None)))
        with patch.object(self.app_module.messagebox,'showerror') as dialog:
            a.poll()
        dialog.assert_called_once()
        self.assertFalse(a.closing)
        self.assertTrue(self.root.winfo_exists())
        self.assertNotEqual(self.root.state(),'withdrawn')
        self.assertIn('partial.mp4',a.feedback.get())

    def test_close_drains_already_queued_recording_failure(self):
        a=self.make_app()
        a.events.put(('recording',('error','pending save failure')))
        with patch.object(self.app_module.messagebox,'showerror') as dialog:
            a.close()
        dialog.assert_called_once()
        self.assertFalse(a.closing)
        self.assertTrue(self.root.winfo_exists())

    def test_error_arriving_at_empty_queue_boundary_is_not_lost(self):
        import queue
        from types import SimpleNamespace
        from unittest.mock import Mock
        a=self.make_app()
        fake=SimpleNamespace(active=True,stop=Mock())
        a.recorder=fake;a.close()
        class BoundaryQueue(queue.Queue):
            injected=False
            def get_nowait(self):
                if not self.injected:
                    self.injected=True
                    self.put(('recording',('error','late save failure')))
                    fake.active=False
                    raise queue.Empty
                return super().get_nowait()
        a.events=BoundaryQueue()
        with patch.object(self.app_module.messagebox,'showerror') as dialog:
            a.poll()
            dialog.assert_not_called()
            self.assertTrue(self.root.winfo_exists())
            a.poll()
        dialog.assert_called_once()
        self.assertFalse(a.closing)

    def test_recording_preference_survives_save(self):
        cfg=dict(threshold=.91,region=dict(left=0,top=0,width=640,height=400),record_on_alarm=False)
        a=self.make_app(cfg)
        self.assertFalse(a.record_on_alarm.get())
        a.save_settings()
        self.assertFalse(json.loads(self.config_path.read_text())['record_on_alarm'])

    def test_main_controls_fit_at_150_percent(self):
        a=self.make_app(scale=1.5)
        self.root.update()
        for w in [a.setup_button,a.start_button,a.settings_button,a.score_label,a.threshold_label,a.reminder_label,a.recording_label,a.stop_recording_button,a.open_recordings_button]:
            self.assertGreater(w.winfo_width(),20)
            self.assertLessEqual(w.winfo_rootx()+w.winfo_width(),self.root.winfo_rootx()+self.root.winfo_width())
            self.assertLessEqual(w.winfo_rooty()+w.winfo_height(),self.root.winfo_rooty()+self.root.winfo_height())

    def test_main_controls_fit_at_125_percent(self):
        a=self.make_app(scale=1.25)
        self.root.update()
        for w in [a.setup_button,a.start_button,a.score_label,a.threshold_label,a.reminder_label,a.recording_label,a.stop_recording_button,a.open_recordings_button]:
            self.assertLessEqual(w.winfo_rooty()+w.winfo_height(),self.root.winfo_rooty()+self.root.winfo_height())


if __name__=='__main__':
    unittest.main()
