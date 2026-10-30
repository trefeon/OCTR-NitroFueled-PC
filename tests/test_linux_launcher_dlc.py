import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from paths import ROOT


class LinuxLauncherDlcDirectoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not sys.platform.startswith("linux") or importlib.util.find_spec("gi") is None:
            raise unittest.SkipTest("Linux GTK launcher dependencies are unavailable")
        import gi
        try:
            gi.require_version("Gtk", "4.0")
            gi.require_version("Adw", "1")
            from gi.repository import Adw, Gtk  # noqa: F401
        except (ImportError, ValueError):
            raise unittest.SkipTest("Linux GTK launcher dependencies are unavailable")
        sys.path.insert(0, str(ROOT / "launcher"))
        import sacredfire_launcher
        cls.launcher = sacredfire_launcher

    def test_default_path_and_launch_environment(self):
        launcher = self.launcher
        with patch.object(launcher, "DATA_DIR", Path("/user/data")), \
                patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BB_DLC_DIR", None)
            settings = dict(launcher.DEFAULTS)
            self.assertEqual(launcher.resolve_dlc_dir(settings), Path("/user/data/dlc"))
            self.assertEqual(launcher.game_environment(settings)["BB_DLC_DIR"], "/user/data/dlc")

    def test_saved_custom_path_is_used_for_launch(self):
        launcher = self.launcher
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(launcher, "CONFIG_DIR", Path(temporary)), \
                patch.object(launcher, "CONFIG_FILE", Path(temporary) / "settings.json"), \
                patch.dict(os.environ, {"BB_DLC_DIR": "/inherited/dlc"}, clear=False):
            settings = dict(launcher.DEFAULTS, dlc_dir="/custom/packages")
            launcher.save_settings(settings)
            loaded = launcher.load_settings()
            self.assertEqual(launcher.resolve_dlc_dir(loaded), Path("/custom/packages"))
            self.assertEqual(launcher.game_environment(loaded)["BB_DLC_DIR"], "/custom/packages")

    def test_relative_saved_path_resolves_from_port_directory(self):
        launcher = self.launcher
        with patch.object(launcher, "PORT_DIR", Path("/opt/sacredfire")):
            settings = dict(launcher.DEFAULTS, dlc_dir="external/dlc")
            expected = Path("/opt/sacredfire/external/dlc")
            self.assertEqual(launcher.resolve_dlc_dir(settings), expected)
            self.assertEqual(launcher.game_environment(settings)["BB_DLC_DIR"], str(expected))

    def test_inherited_environment_falls_back_after_empty_setting(self):
        launcher = self.launcher
        with patch.object(launcher, "DATA_DIR", Path("/user/data")), \
                patch.object(launcher, "PORT_DIR", Path("/opt/sacredfire")), \
                patch.dict(os.environ, {"BB_DLC_DIR": "external/dlc"}, clear=False):
            expected = Path("/opt/sacredfire/external/dlc")
            self.assertEqual(launcher.resolve_dlc_dir({}), expected)
            settings = dict(launcher.DEFAULTS, dlc_dir="")
            self.assertEqual(launcher.resolve_dlc_dir(settings), expected)
            self.assertEqual(launcher.game_environment(settings)["BB_DLC_DIR"], str(expected))
            settings["dlc_dir"] = "saved/dlc"
            self.assertEqual(launcher.resolve_dlc_dir(settings), Path("/opt/sacredfire/saved/dlc"))
            settings["dlc_dir"] = "/saved/dlc"
            self.assertEqual(launcher.resolve_dlc_dir(settings), Path("/saved/dlc"))


if __name__ == "__main__":
    unittest.main()
