"""Windows game-language selection used by run.py."""
from paths import ROOT
from pathlib import Path
import importlib.util
import tempfile
import unittest


_spec = importlib.util.spec_from_file_location('windows_run', ROOT / 'run.py')
run = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run)


class RunLanguageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / 'bbport.ini'
        self.launcher_ini = self.root / 'launcher.ini'

    def resolve(self, env):
        run.configure_game_language(env, self.config, self.launcher_ini)
        return env.get('BB_LANGUAGE')

    def test_explicit_environment_override_wins(self):
        self.config.write_text('language=2\n', encoding='utf-8')
        self.launcher_ini.write_text('language=20\n', encoding='utf-8')
        self.assertEqual(self.resolve({'BB_LANGUAGE': '17'}), '17')

    def test_existing_game_config_precedes_saved_launcher_language(self):
        self.config.write_text('game_language=2\n', encoding='utf-8')
        self.launcher_ini.write_text('language=20\n', encoding='utf-8')
        self.assertEqual(self.resolve({}), '2')

    def test_saved_launcher_language_is_used_without_gui_launch(self):
        self.launcher_ini.write_text('language=20\n', encoding='utf-8')
        self.assertEqual(self.resolve({}), '20')

    def test_missing_empty_or_invalid_launcher_ini_is_non_fatal(self):
        for contents in (None, '', 'ui_language=es\nmalformed\n'):
            with self.subTest(contents=contents):
                self.launcher_ini.unlink(missing_ok=True)
                if contents is not None:
                    self.launcher_ini.write_text(contents, encoding='utf-8')
                env = {}
                self.assertIsNone(self.resolve(env))
                self.assertNotIn('BB_LANGUAGE', env)


if __name__ == '__main__':
    unittest.main()
