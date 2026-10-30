"""GPU picker: bb-gpu-capabilities --list-devices and the launchers' identity resolution.

Probe smoke: --list-devices exits 0 with >= 1 "index vendorID name type vramMiB driver"
tab-separated line. Resolution: the stored launcher.ini "vendorID:name" identity maps to this
run's BB_GPU_ID index (exact match wins, anything else falls back to Auto).
"""
import os
import subprocess
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from paths import ROOT

TYPES = {"discrete", "integrated", "virtual", "cpu", "other"}

# Five lines as on the developer's dual-GPU laptop (Intel UHD + RTX 3060, Dozen over D3D12,
# Microsoft's Basic Render Driver): the exact mix the picker must sort (CPU skipped, Dozen last).
SAMPLE = (
    "0\t0x8086\tIntel(R) UHD Graphics\tintegrated\t16275\t0x1947cf\n"
    "1\t0x8086\tMicrosoft Direct3D12 (Intel(R) UHD Graphics)\tintegrated\t16275\t0x6802063\n"
    "2\t0x10de\tNVIDIA GeForce RTX 3060 Laptop GPU\tdiscrete\t5994\t0x9b404000\n"
    "3\t0x10de\tMicrosoft Direct3D12 (NVIDIA GeForce RTX 3060 Laptop GPU)\tdiscrete\t5994\t0x6802063\n"
    "4\t0x1414\tMicrosoft Direct3D12 (Microsoft Basic Render Driver)\tcpu\t16275\t0x6802063\n"
)
RTX = "0x10de:NVIDIA GeForce RTX 3060 Laptop GPU"


def probe_path():
    """The built probe (out/bb-gpu-capabilities, .exe on Windows); None when not built."""
    for name in ("out/bb-gpu-capabilities.exe", "out/bb-gpu-capabilities"):
        found = ROOT / name
        if found.is_file():
            return found
    return None


def probe_env():
    """The probe's DLLs live in MSYS2's CLANG64 bin on Windows (run.py extends PATH the same way)."""
    env = dict(os.environ)
    for directory in (r"C:\msys64\clang64\bin", r"C:\msys64\usr\bin"):
        if Path(directory).is_dir() and directory not in env.get("PATH", ""):
            env["PATH"] = directory + os.pathsep + env.get("PATH", "")
    return env


class ListDevicesTests(unittest.TestCase):
    @unittest.skipUnless(probe_path(), "build the probe first (bash build.sh)")
    def test_list_devices_smoke(self):
        run = subprocess.run([str(probe_path()), "--list-devices"], capture_output=True, text=True,
                             timeout=30, env=probe_env())
        self.assertEqual(run.returncode, 0, run.stderr)
        lines = [line for line in run.stdout.splitlines() if line.strip()]
        self.assertGreaterEqual(len(lines), 1, run.stdout)
        for position, line in enumerate(lines):
            parts = line.split("\t")
            self.assertGreaterEqual(len(parts), 6, line)
            self.assertEqual(int(parts[0]), position)  # index: the BB_GPU_ID for that run
            int(parts[1], 16)  # vendorID hex
            self.assertTrue(parts[2])  # name
            self.assertIn(parts[3], TYPES)  # type
            self.assertGreaterEqual(int(parts[4]), 0)  # vramMiB
            int(parts[5], 16)  # driverVersion hex


def import_launcher():
    """sacredfire_launcher without a desktop: stub gi (GTK/Adwaita), keep the real GPU helpers."""
    before = set(sys.modules)
    gi = types.ModuleType("gi")
    gi.require_version = lambda *args: None
    repository = types.ModuleType("gi.repository")

    def make_module(name):
        module = types.ModuleType(f"gi.repository.{name}")

        def __getattr__(attribute, _name=name):
            return type(f"{_name}.{attribute}", (), {})

        module.__getattr__ = __getattr__
        return module

    for name in ("Adw", "Gdk", "Gio", "GLib", "Gtk"):
        module = make_module(name)
        setattr(repository, name, module)
        sys.modules[f"gi.repository.{name}"] = module
    gi.repository = repository
    sys.modules["gi"] = gi
    sys.modules["gi.repository"] = repository
    sys.path.insert(0, str(ROOT / "launcher"))
    try:
        import sacredfire_launcher
        return sacredfire_launcher, before
    except Exception:
        for key in [key for key in sys.modules if key not in before]:
            del sys.modules[key]
        raise


class ResolveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.launcher, cls._modules_before = import_launcher()
        except Exception as error:
            raise unittest.SkipTest(f"launcher imports need more stubbing: {error}")

    @classmethod
    def tearDownClass(cls):
        for key in [key for key in sys.modules if key not in cls._modules_before]:
            del sys.modules[key]
        if str(ROOT / "launcher") in sys.path:
            sys.path.remove(str(ROOT / "launcher"))

    def test_parse_sample(self):
        devices = self.launcher.parse_gpu_devices(SAMPLE)
        self.assertEqual(len(devices), 5)
        self.assertEqual(devices[2]["identity"], RTX)
        self.assertEqual(devices[2]["index"], 2)
        self.assertFalse(devices[2]["dozen"])
        self.assertTrue(devices[3]["dozen"])
        self.assertEqual(devices[2]["label"], "NVIDIA GeForce RTX 3060 Laptop GPU \u2014 5994 MiB")
        self.assertTrue(devices[3]["label"].endswith("(compatibility)"))

    def test_entries_skip_cpu_and_list_dozen_last(self):
        entries = self.launcher.gpu_entries(self.launcher.parse_gpu_devices(SAMPLE))
        self.assertEqual([d["index"] for d in entries], [0, 2, 1, 3])  # no CPU, Dozen last

    def test_exact_match_wins(self):
        devices = self.launcher.parse_gpu_devices(SAMPLE)
        self.assertEqual(self.launcher.resolve_gpu_index(RTX, devices), 2)
        self.assertEqual(self.launcher.resolve_gpu_index(
            "0x8086:Microsoft Direct3D12 (Intel(R) UHD Graphics)", devices), 1)

    def test_auto_and_no_match_fall_back(self):
        devices = self.launcher.parse_gpu_devices(SAMPLE)
        self.assertIsNone(self.launcher.resolve_gpu_index("", devices))
        self.assertIsNone(self.launcher.resolve_gpu_index("0x10de:No Such GPU", devices))
        self.assertIsNone(self.launcher.resolve_gpu_index(
            "0x1414:Microsoft Direct3D12 (Microsoft Basic Render Driver)", devices))  # CPU skipped

    def test_igp_warning(self):
        devices = {device["identity"]: device for device in self.launcher.parse_gpu_devices(SAMPLE)}
        self.assertTrue(self.launcher.gpu_needs_igp_warning(devices["0x8086:Intel(R) UHD Graphics"]))
        self.assertFalse(self.launcher.gpu_needs_igp_warning(devices[RTX]))

    def test_environment_exports_explicit_gpu(self):
        launcher = self.launcher
        settings = dict(launcher.DEFAULTS, gpu=RTX)
        with patch.object(launcher, "connected_gpus",
                          return_value=launcher.parse_gpu_devices(SAMPLE)):
            env = launcher.game_environment(settings)
        self.assertEqual(env.get("BB_GPU_ID"), "2")
        self.assertEqual(launcher._LAST_GPU_NOTE, "")

    def test_environment_falls_back_to_auto(self):
        launcher = self.launcher
        settings = dict(launcher.DEFAULTS, gpu="0x10de:No Such GPU")
        with patch.object(launcher, "connected_gpus",
                          return_value=launcher.parse_gpu_devices(SAMPLE)):
            env = launcher.game_environment(settings)
        self.assertNotIn("BB_GPU_ID", env)
        self.assertIn("Auto", launcher._LAST_GPU_NOTE)

    def test_environment_auto_sets_nothing(self):
        launcher = self.launcher
        settings = dict(launcher.DEFAULTS, gpu="")
        with patch.object(launcher, "connected_gpus") as probed:
            env = launcher.game_environment(settings)
        probed.assert_not_called()  # Auto needs no probe
        self.assertNotIn("BB_GPU_ID", env)


if __name__ == "__main__":
    unittest.main()
