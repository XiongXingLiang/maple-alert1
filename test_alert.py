import io
import sys
import unittest
from unittest.mock import Mock, patch
import alert
from alert import Gate


class GateTest(unittest.TestCase):
    def test_requires_consecutive_hits(self):
        g = Gate(hits=3)
        self.assertFalse(g.update(True, 0))
        self.assertFalse(g.update(False, .1))
        self.assertFalse(g.update(True, .2))
        self.assertFalse(g.update(True, .3))
        self.assertTrue(g.update(True, .4))

    def test_second_appearance_does_not_wait_for_cooldown(self):
        g = Gate(hits=1, misses=8, cooldown=30)
        self.assertTrue(g.update(True, 0))
        g.update(False, 1)
        g.update(False, 1.25)
        g.update(False, 1.5)
        self.assertTrue(g.update(True, 1.75))
        self.assertFalse(g.update(True, 2))

    def test_rearm_checked_at_next_matching_sample(self):
        g = Gate(hits=1, misses=8, cooldown=30)
        g.update(True, 0)
        g.update(False, 1)
        g.update(False, 1.25)
        self.assertTrue(g.update(True, 1.5))

    def test_persistent_target_repeats_every_thirty_seconds(self):
        g = Gate(hits=1, misses=8, cooldown=30)
        fired = [t for t in range(100) if g.update(True, t)]
        self.assertEqual(fired, [0, 30, 60, 90])

    def test_brief_miss_does_not_repeat_early(self):
        g = Gate(hits=1, misses=8, cooldown=30)
        self.assertTrue(g.update(True, 0))
        g.update(False, 1)
        self.assertFalse(g.update(True, 1.25))
        self.assertFalse(g.update(True, 29.9))
        self.assertTrue(g.update(True, 30))

    def test_many_appearances_keep_triggering(self):
        g = Gate(hits=1, misses=8, cooldown=30)
        for t in range(0, 100, 2):
            self.assertTrue(g.update(True, t))
            g.update(False, t+.25)
            g.update(False, t+.5)
            g.update(False, t+.75)

    def test_no_match_never_alerts(self):
        g = Gate(hits=1)
        self.assertFalse(any(g.update(False, t) for t in range(100)))

    def test_miss_count_can_also_rearm(self):
        g = Gate(hits=1, misses=2, cooldown=30)
        g.update(True, 0)
        g.update(False, .1)
        g.update(False, .2)
        self.assertTrue(g.update(True, .3))

    def test_hits_confirmation_still_applies_after_rearm(self):
        g = Gate(hits=2, misses=8, cooldown=30)
        g.update(True, 0)
        self.assertTrue(g.update(True, .25))
        g.update(False, 1)
        self.assertFalse(g.update(True, 2))
        self.assertTrue(g.update(True, 2.25))

    def test_zero_cooldown_does_not_spam_each_frame(self):
        g = Gate(hits=1, cooldown=0)
        self.assertTrue(g.update(True, 0))
        self.assertFalse(g.update(True, .25))
        self.assertFalse(g.update(True, .75))
        self.assertTrue(g.update(True, 1))


class NotificationTest(unittest.TestCase):
    def setUp(self):
        self.sound_patch = patch.object(alert, 'alarm_sound_file', return_value='mock-alarm.wav')
        self.sound_patch.start()
        self.addCleanup(self.sound_patch.stop)

    def mocks(self):
        return Mock(SND_FILENAME=0x20000, SND_ASYNC=1), Mock()

    def test_sound_failure_still_sends_toast(self):
        sound, toast = self.mocks()
        sound.PlaySound.side_effect = RuntimeError('audio off')
        with patch.dict(sys.modules, {'winsound': sound, 'windows_toasts': toast}), patch.object(alert, '_toaster', None):
            errors = alert.notify()
        toast.WindowsToaster.return_value.show_toast.assert_called_once()
        self.assertEqual(len(errors), 1)

    def test_toast_failure_does_not_block_next_attempt(self):
        sound, toast = self.mocks()
        broken, good = Mock(), Mock()
        broken.show_toast.side_effect = RuntimeError('stale toast object')
        toast.WindowsToaster.return_value = good
        with patch.dict(sys.modules, {'winsound': sound, 'windows_toasts': toast}), patch.object(alert, '_toaster', broken):
            self.assertEqual(len(alert.notify()), 1)
            self.assertEqual(alert.notify(), [])
            self.assertEqual(alert.notify(), [])
        self.assertEqual(good.show_toast.call_count, 2)
        self.assertEqual(sound.PlaySound.call_count, 3)

    def test_sound_and_toast_repeated_fifty_times(self):
        sound, toast = self.mocks()
        with patch.dict(sys.modules, {'winsound': sound, 'windows_toasts': toast}), patch.object(alert, '_toaster', None), patch('sys.stdout', None):
            for _ in range(50):
                self.assertEqual(alert.notify(), [])
        self.assertEqual(sound.PlaySound.call_count, 50)
        sound.PlaySound.assert_called_with('mock-alarm.wav', 0x20001)
        self.assertEqual(toast.WindowsToaster.return_value.show_toast.call_count, 50)

    def test_no_console_returns_errors(self):
        sound, toast = self.mocks()
        sound.PlaySound.side_effect = RuntimeError('audio off')
        toast.WindowsToaster.side_effect = RuntimeError('toast off')
        with patch.dict(sys.modules, {'winsound': sound, 'windows_toasts': toast}), patch.object(alert, '_toaster', None), patch('sys.stdout', None):
            self.assertEqual(len(alert.notify()), 2)

    def test_cp1252_console_does_not_break_reminders(self):
        sound, toast = self.mocks()
        toast.WindowsToaster.side_effect = RuntimeError('通知失败')
        buffer = io.BytesIO()
        stream = io.TextIOWrapper(buffer, encoding='cp1252', errors='strict')
        with patch.dict(sys.modules, {'winsound': sound, 'windows_toasts': toast}), patch.object(alert, '_toaster', None), patch('sys.stdout', stream):
            self.assertEqual(len(alert.notify()), 1)
        stream.flush()
        self.assertIn(b'[Maple Alert]', buffer.getvalue())
        stream.detach()


class SoundAndDefaultsTest(unittest.TestCase):
    def test_alarm_wav_is_valid_and_has_three_bursts(self):
        import tempfile
        import wave
        import struct
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            path = alert.make_alarm_wav(Path(folder) / 'alarm.wav')
            with wave.open(str(path), 'rb') as wav:
                self.assertEqual((wav.getnchannels(), wav.getsampwidth(), wav.getframerate()), (1, 2, 22050))
                raw = wav.readframes(wav.getnframes())
                self.assertAlmostEqual(wav.getnframes() / 22050, 1.68, places=2)
            values = struct.unpack('<%dh' % (len(raw) // 2), raw)
            self.assertGreater(max(values), 20000)
            self.assertLessEqual(max(abs(x) for x in values), 24000)
            for start in [0.40, 0.96, 1.52]:
                gap = values[round((start + .01) * 22050):round((start + .14) * 22050)]
                self.assertTrue(all(x == 0 for x in gap))

    def migrate(self, cfg):
        import tempfile
        import json
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'config.json'
            path.write_text(json.dumps(cfg), encoding='utf-8')
            with patch.object(alert, 'CONFIG', path):
                alert.migrate_default_threshold()
            return json.loads(path.read_text(encoding='utf-8'))

    def test_old_default_becomes_point_eight(self):
        cfg = self.migrate({'threshold': .88, 'region': {'width': 640}})
        self.assertEqual(cfg['threshold'], .8)
        self.assertEqual(cfg['region'], {'width': 640})
        self.assertEqual(cfg['defaults_revision'], alert.DEFAULTS_REVISION)

    def test_custom_threshold_is_preserved(self):
        self.assertEqual(self.migrate({'threshold': .91})['threshold'], .91)

    def test_manual_change_after_migration_is_preserved(self):
        cfg = self.migrate({'threshold': .88, 'defaults_revision': alert.DEFAULTS_REVISION})
        self.assertEqual(cfg['threshold'], .88)

    def test_missing_config_does_not_create_incomplete_file(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'config.json'
            with patch.object(alert, 'CONFIG', path):
                alert.migrate_default_threshold()
            self.assertFalse(path.exists())


if __name__ == '__main__':
    unittest.main()
