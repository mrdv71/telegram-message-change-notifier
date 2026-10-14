"""Offline deployment-state checks; never use a real account."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import notifier


class DeploymentStateTests(unittest.TestCase):
    def test_nested_private_state_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / 'nested' / 'private state'
            previous = os.umask(0o077)
            try:
                with patch.object(notifier, 'STATE', target):
                    notifier.state_dir()
                self.assertTrue(target.is_dir())
                if os.name == 'posix':
                    self.assertEqual(target.stat().st_mode & 0o777, 0o700)
            finally:
                os.umask(previous)

    def test_setup_uses_custom_state_without_overwriting(self):
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / 'config.json'
            config.write_text('{"synthetic": true}')
            result = subprocess.run(
                [sys.executable, str(Path(notifier.__file__)),
                 '--state-dir', temp, 'setup'],
                input='', capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 1)
            self.assertIn('RuntimeError', result.stdout)
            self.assertEqual(config.read_text(), '{"synthetic": true}')
            self.assertEqual(sorted(p.name for p in Path(temp).iterdir()), ['config.json'])
