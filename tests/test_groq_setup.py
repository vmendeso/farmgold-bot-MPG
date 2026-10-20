import contextlib
import io
import os
import unittest
from unittest.mock import Mock, patch

from scripts import configure_groq as setup


class GroqSetupTests(unittest.TestCase):
    def run_main(self, argv=()):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = setup.main(list(argv))
        return result, output.getvalue()

    def test_setup_uses_hidden_input_and_never_prints_key(self):
        secret='gsk_test_private_value'
        with patch.object(setup.os,'name','nt'), patch.object(setup.sys.stdin,'isatty',return_value=True), \
                patch.object(setup.getpass,'getpass',return_value=secret), patch.object(setup,'save_key') as save:
            code, output=self.run_main()
        self.assertEqual(code,0)
        save.assert_called_once_with(secret)
        self.assertNotIn(secret,output)

    def test_blank_or_accidentally_pasted_commands_are_never_saved(self):
        for value in ('', '  ', '$env:GROQ_API_KEY = something', '"gsk_value"', 'gsk_\nvalue'):
            with self.subTest(value=value), patch.object(setup.os,'name','nt'), \
                    patch.object(setup.sys.stdin,'isatty',return_value=True), \
                    patch.object(setup.getpass,'getpass',return_value=value), patch.object(setup,'save_key') as save:
                code, _=self.run_main()
                self.assertEqual(code,1)
                save.assert_not_called()

    def test_redirected_input_does_not_fall_back_to_echoing_secret(self):
        with patch.object(setup.os,'name','nt'), patch.object(setup.sys.stdin,'isatty',return_value=False), \
                patch.object(setup.getpass,'getpass') as prompt, patch.object(setup,'save_key') as save:
            code,_=self.run_main()
        self.assertEqual(code,1)
        prompt.assert_not_called();save.assert_not_called()

    def test_registry_write_is_verified_and_mismatch_is_error(self):
        import sys
        registry=Mock()
        registry.CreateKeyEx.return_value.__enter__=Mock(return_value='handle')
        registry.CreateKeyEx.return_value.__exit__=Mock(return_value=False)
        with patch.dict(sys.modules,{'winreg':registry}), patch.object(setup,'stored_key',return_value='secret'):
            setup.save_key('secret')
            registry.SetValueEx.assert_called_once_with('handle','GROQ_API_KEY',0,registry.REG_SZ,'secret')
        with patch.dict(sys.modules,{'winreg':registry}), patch.object(setup,'stored_key',return_value=''), self.assertRaises(OSError):
            setup.save_key('secret')

    def test_check_detects_stale_process_key_without_printing_either_secret(self):
        with patch.object(setup.os,'name','nt'), patch.dict(os.environ,{'GROQ_API_KEY':'old-secret'},clear=True), \
                patch.object(setup,'stored_key',return_value='new-secret'), patch.object(setup,'api_key',return_value='old-secret'), \
                patch.object(setup,'save_key') as save:
            code,output=self.run_main(['--check'])
        self.assertEqual(code,1)
        self.assertIn('difere',output)
        self.assertNotIn('old-secret',output);self.assertNotIn('new-secret',output)
        save.assert_not_called()

    def test_check_explains_explicit_remote_disable(self):
        with patch.object(setup.os,'name','nt'), patch.dict(os.environ,{'FARMBOT_DISABLE_REMOTE_OCR':'1'},clear=True), \
                patch.object(setup,'stored_key',return_value='saved-secret'), patch.object(setup,'api_key',return_value=''):
            code,output=self.run_main(['--check'])
        self.assertEqual(code,1)
        self.assertIn('Remove-Item Env:FARMBOT_DISABLE_REMOTE_OCR',output)
        self.assertNotIn('saved-secret',output)

    def test_permission_failure_does_not_print_exception_that_could_contain_key(self):
        with patch.object(setup.os,'name','nt'), patch.object(setup.sys.stdin,'isatty',return_value=True), \
                patch.object(setup.getpass,'getpass',return_value='private-secret'), \
                patch.object(setup,'save_key',side_effect=PermissionError('private-secret')):
            code,output=self.run_main()
        self.assertEqual(code,1)
        self.assertNotIn('private-secret',output)
