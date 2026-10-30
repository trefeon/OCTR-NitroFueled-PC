"""Shipped performance defaults agree across ini, code fallback and launcher.

Covers the config-first perf slice: async_pipelines and async_submit default on
in the portable ini, the C++ fallbacks used when the key/env is unset, the
Windows launcher checkbox fallback, and the run.py forwarding of async_submit
to BB_ASYNC_SUBMIT. Unit-level only: no game launch.
"""
import importlib.util
import re
import tempfile
import unittest
from pathlib import Path

from paths import ROOT

_spec = importlib.util.spec_from_file_location('windows_run', ROOT / 'run.py')
run = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run)

DEFAULT_INI = ROOT / 'packaging' / 'windows' / 'portable' / 'bbport.default.ini'
SETTINGS_H = ROOT / 'gpu' / 'shim' / 'bbport_settings.h'
LAUNCHER_CPP = ROOT / 'launcher' / 'windows' / 'launcher.cpp'
SCHEDULER_CPP = ROOT / 'gpu' / 'shadps4' / 'video_core' / 'renderer_vulkan' / 'vk_scheduler.cpp'
RUN_PY = ROOT / 'run.py'


def _ini_defaults(path):
    values = {}
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        name, sep, value = line.partition('=')
        if sep and not line.lstrip().startswith('#'):
            values[name.strip()] = value.strip()
    return values


class PerfDefaultsTests(unittest.TestCase):
    def test_default_ini_enables_async(self):
        values = _ini_defaults(DEFAULT_INI)
        self.assertEqual(values.get('async_pipelines'), '1')
        self.assertEqual(values.get('async_submit'), '1')

    def test_settings_fallback_is_on(self):
        text = SETTINGS_H.read_text(encoding='utf-8')
        self.assertRegex(text, r'async_pipelines\{true\}')

    def test_launcher_fallback_is_on(self):
        text = LAUNCHER_CPP.read_text(encoding='utf-8')
        self.assertRegex(text, r'GameFlag\("async_pipelines",.*?,\s*true,', re.DOTALL)

    def test_scheduler_default_is_on_when_env_unset(self):
        # vk_scheduler.cpp: unset BB_ASYNC_SUBMIT means on (only '0...' disables).
        text = SCHEDULER_CPP.read_text(encoding='utf-8')
        self.assertIn("async_submit = !async || async[0] != '0';", text)

    def test_run_forwards_async_submit_to_env(self):
        source = RUN_PY.read_text(encoding='utf-8')
        self.assertIn("ini_value(config, 'async_submit')", source)
        self.assertIn("env['BB_ASYNC_SUBMIT'] = v", source)
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / 'bbport.ini'
            config.write_text('async_submit=1\n', encoding='utf-8')
            env = {}
            if 'BB_ASYNC_SUBMIT' not in env:
                v = run.ini_value(config, 'async_submit')
                if v is not None:
                    env['BB_ASYNC_SUBMIT'] = v
            self.assertEqual(env, {'BB_ASYNC_SUBMIT': '1'})

    def test_missing_async_submit_means_scheduler_default_on(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / 'bbport.ini'
            config.write_text('fps_limit=60\n', encoding='utf-8')
            self.assertIsNone(run.ini_value(config, 'async_submit'))


if __name__ == '__main__':
    unittest.main()
