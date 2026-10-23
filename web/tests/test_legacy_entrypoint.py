"""NAS image updaters can retain the previous container entrypoint."""
import runpy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]


class LegacyEntrypointTests(unittest.TestCase):
    def test_preserved_container_command_delegates_to_current_runtime(self):
        with patch.dict(sys.modules), patch('runtime.entrypoint.run', return_value=7) as run:
            sys.path.insert(0, str(ROOT))
            try:
                with self.assertRaises(SystemExit) as result:
                    runpy.run_module('deploy.nas.app_entrypoint', run_name='__main__')
                self.assertEqual(result.exception.code, 7)
                run.assert_called_once_with()
            finally:
                sys.path.remove(str(ROOT))


if __name__ == '__main__':
    unittest.main()
