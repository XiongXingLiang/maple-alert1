import unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
from sound_check import describe,check_output,SoundStatus

class SoundCheckTest(unittest.TestCase):
    def test_muted_even_with_positive_volume(self):
        self.assertEqual(describe(True,.7).state,'warning')
        self.assertIn('静音',describe(True,.7).text)
    def test_zero(self):self.assertIn('音量为 0',describe(False,0).text)
    def test_positive_and_low_volume(self):
        self.assertEqual(describe(False,.0001).state,'ready')
        self.assertIn('1%',describe(False,.0001).text)
        self.assertIn('测试',describe(False,.8).text)
    def test_invalid_values(self):
        for level in [-1,2,float('nan')]:
            with self.assertRaises(ValueError):describe(False,level)
    def test_query_failure_is_unknown_not_ready(self):
        def fail():raise OSError('device removed')
        self.assertEqual(check_output(fail).state,'unknown')
    @unittest.skipUnless(__import__('sys').platform=='win32','Windows Core Audio')
    def test_real_api_returns_valid_status_without_changing_volume(self):
        self.assertIn(check_output().state,['ready','warning','unknown'])
    def test_endpoint_and_missing_device(self):
        import sys
        utilities=SimpleNamespace(GetSpeakers=lambda:None)
        with patch.dict(sys.modules,{'pycaw':SimpleNamespace(),'pycaw.pycaw':SimpleNamespace(AudioUtilities=utilities)}):
            from sound_check import read_output
            self.assertIn('没有可用',read_output().text)
            endpoint=SimpleNamespace(GetMute=Mock(return_value=1),GetMasterVolumeLevelScalar=Mock(return_value=.7))
            utilities.GetSpeakers=lambda:SimpleNamespace(EndpointVolume=endpoint)
            self.assertEqual(read_output().state,'warning')
    def test_com_uninitializes_on_query_error(self):
        import sys
        fake=SimpleNamespace(CoInitialize=Mock(),CoUninitialize=Mock())
        with patch.object(sys,'platform','win32'),patch('sound_check._comtypes',fake),patch('sound_check.read_output',side_effect=OSError()):
            self.assertEqual(check_output().state,'unknown')
        fake.CoUninitialize.assert_called_once()

if __name__=='__main__':unittest.main()
