import unittest
from alert import Gate


class GateTest(unittest.TestCase):
    def test_consecutive_hits_and_single_alert(self):
        g = Gate()
        self.assertFalse(g.update(True, 0))
        self.assertFalse(g.update(False, 1))
        self.assertFalse(g.update(True, 2))
        self.assertFalse(g.update(True, 3))
        self.assertTrue(g.update(True, 4))
        self.assertFalse(g.update(True, 60))

    def test_disappearance_rearms_with_cooldown(self):
        g = Gate(hits=2, misses=2, cooldown=30)
        g.update(True, 0)
        self.assertTrue(g.update(True, 1))
        g.update(False, 2)
        g.update(False, 3)
        g.update(True, 4)
        self.assertFalse(g.update(True, 5))
        self.assertTrue(g.update(True, 31))
        self.assertFalse(g.update(True, 32))

    def test_short_miss_does_not_rearm(self):
        g = Gate(hits=1, misses=3, cooldown=0)
        self.assertTrue(g.update(True, 0))
        g.update(False, 1)
        self.assertFalse(g.update(True, 2))




class NotificationTest(unittest.TestCase):
    def test_sound_failure_still_sends_toast(self):
        from unittest.mock import Mock, patch
        import alert
        sound = Mock(SND_ALIAS=1, SND_ASYNC=2)
        sound.PlaySound.side_effect = RuntimeError('sound unavailable')
        toast_module = Mock()
        toaster = toast_module.WindowsToaster.return_value
        with patch.dict('sys.modules', {'winsound': sound, 'windows_toasts': toast_module}):
            with patch.object(alert, '_toaster', None):
                alert.notify()
        toaster.show_toast.assert_called_once()

    def test_toast_failure_still_plays_sound(self):
        from unittest.mock import Mock, patch
        import alert
        sound = Mock(SND_ALIAS=1, SND_ASYNC=2)
        toast_module = Mock()
        toast_module.WindowsToaster.side_effect = RuntimeError('toast unavailable')
        with patch.dict('sys.modules', {'winsound': sound, 'windows_toasts': toast_module}):
            with patch.object(alert, '_toaster', None):
                alert.notify()
        sound.PlaySound.assert_called_once()




class DesktopNotificationTest(unittest.TestCase):
    def test_no_console_returns_actionable_errors(self):
        from unittest.mock import Mock, patch
        import alert
        sound = Mock(SND_ALIAS=1, SND_ASYNC=2)
        sound.PlaySound.side_effect = RuntimeError('audio off')
        toast_module = Mock()
        toast_module.WindowsToaster.side_effect = RuntimeError('notifications off')
        with patch.dict('sys.modules', {'winsound': sound, 'windows_toasts': toast_module}):
            with patch.object(alert, '_toaster', None), patch('sys.stdout', None):
                errors = alert.notify()
        self.assertEqual(len(errors), 2)
        self.assertIn('audio off', errors[0])
        self.assertIn('notifications off', errors[1])


if __name__ == '__main__':
    unittest.main()
