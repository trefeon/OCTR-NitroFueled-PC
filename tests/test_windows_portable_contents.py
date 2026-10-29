"""Windows portable package contents, assembled from synthetic files without external tools."""
import sys
import tempfile
import types
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from paths import ROOT

sys.path.insert(0, str(ROOT / "packaging" / "windows"))
import make_portable as portable  # noqa: E402


def _source_file(path, content=b"synthetic"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _stage_source(temp):
    """Synthetic repo tree with extractor, DLLs, notices and decoy game/user data."""
    root = Path(temp) / "source"
    dist = Path(temp) / "dist"
    dxc = Path(temp) / "dxc"
    clang_bin = Path(temp) / "clang-bin"
    portable_dir = root / "packaging" / "windows" / "portable"
    for directory in (
        root / "out", root / "docs" / "native-handbook", root / "scripts" / "widescreen_data",
        root / "scripts" / "button_prompts", root / "tools" / "shaders",
        root / "third_party" / "gcn2hlsl", root / "third_party" / "shadps4plus-pkg",
        root / "patches", root / "art", root / "user", root / "CUSA13795",
        dxc / "bin" / "x64", clang_bin,
    ):
        directory.mkdir(parents=True, exist_ok=True)

    for name in ("sacredfire.exe", "bb-gpu-capabilities.exe", portable.LAUNCHER, "aerolib.inl"):
        _source_file(root / "out" / name)
    _source_file(root / "out" / portable.PKG_EXTRACTOR, b"synthetic-extractor-exe")
    _source_file(root / "out" / "VERSION", b"1.0\n")
    _source_file(root / "run.py", b"# synthetic runner")
    _source_file(root / "docs" / "mod-packaging.html")
    _source_file(root / "docs" / "mod-support.txt")
    _source_file(root / "scripts" / "support.py")
    _source_file(root / "scripts" / "pkg_import.py", b"# synthetic pkg_import")
    _source_file(root / "tools" / "gen_import_names.py")
    _source_file(root / "tools" / "merge_update.py")
    _source_file(root / "tools" / "shaders" / "build_spirv.py")
    for name in ("dxc.exe", "dxcompiler.dll"):
        _source_file(dxc / "bin" / "x64" / name)
    _source_file(root / "LICENSE")
    _source_file(root / "art" / "hero.png", b"keep this asset")
    for name, content in (
        ("bbport.default.ini", b"default settings"),
        ("Jugar CTR Nitro-Fueled.bat", b"Spanish batch"),
        ("LEEME.txt", b"Spanish readme"),
    ):
        _source_file(portable_dir / name, content)
    _source_file(root / "third_party" / "shadps4plus-pkg" / "LICENSE",
                 b"synthetic-upstream-GPL-notice")
    _source_file(root / "third_party" / "shadps4plus-pkg.README.CTR.md",
                 b"synthetic-pin-record 9d2e7612")
    for name, content in (("zlib1.dll", b"synthetic-zlib"), ("libc++.dll", b"synthetic-libcxx")):
        _source_file(clang_bin / name, content)
    # Decoys that must never ship: user saves, a game tree and loose PKGs.
    _source_file(root / "user" / "last_run.log", b"player log")
    _source_file(root / "CUSA13795" / "eboot.bin", b"fake game")
    _source_file(root / "base-game.pkg", b"fake pkg")
    _source_file(root / "update.pkg", b"fake pkg")
    return root, dist, dxc, clang_bin, portable_dir


def _run_packager(root, dist, dxc, clang_bin, portable_dir):
    imports = types.ModuleType("gen_import_names")
    imports.aerolib_names = lambda _path: True
    with patch.object(portable, "ROOT", root), \
         patch.object(portable, "PORTABLE", portable_dir), \
         patch.object(portable, "CLANG_BIN", clang_bin), \
         patch.object(portable, "closure", return_value={}), \
         patch.object(portable, "bundle_python", return_value="synthetic Python"), \
         patch.object(portable, "bundle_iced_x86"), \
         patch.dict(sys.modules, {"gen_import_names": imports}), \
         patch.object(sys, "argv", ["make_portable.py", "--version", "1.0", "--dist", str(dist),
                                      "--dxc-dir", str(dxc)]):
        portable.main()
    return dist / f"{portable.NAME}-windows.zip"


class WindowsPortableContents(unittest.TestCase):
    def test_zip_excludes_spanish_only_files_and_keeps_defaults(self):
        with tempfile.TemporaryDirectory() as temp:
            root, dist, dxc, clang_bin, portable_dir = _stage_source(temp)
            archive = _run_packager(root, dist, dxc, clang_bin, portable_dir)
            with zipfile.ZipFile(archive) as package:
                names = set(package.namelist())
            prefix = f"{portable.NAME}/"
            self.assertNotIn(prefix + "Jugar sin launcher.bat", names)
            self.assertNotIn(prefix + "Jugar CTR Nitro-Fueled.bat", names)
            self.assertNotIn(prefix + "LEEME.txt", names)
            self.assertIn(prefix + "bbport.default.ini", names)
            self.assertIn(prefix + "art/hero.png", names)

    def test_portable_bundles_extractor_dlls_and_notices(self):
        with tempfile.TemporaryDirectory() as temp:
            root, dist, dxc, clang_bin, portable_dir = _stage_source(temp)
            archive = _run_packager(root, dist, dxc, clang_bin, portable_dir)
            with zipfile.ZipFile(archive) as package:
                names = set(package.namelist())
                prefix = f"{portable.NAME}/"
                # Extractor where the launcher expects it, with its runtime DLLs.
                self.assertIn(prefix + "out/pkg_extractor.exe", names)
                self.assertEqual(package.read(prefix + "out/pkg_extractor.exe"),
                                 b"synthetic-extractor-exe")
                self.assertIn(prefix + "out/zlib1.dll", names)
                self.assertEqual(package.read(prefix + "out/zlib1.dll"), b"synthetic-zlib")
                self.assertIn(prefix + "out/libc++.dll", names)
                self.assertEqual(package.read(prefix + "out/libc++.dll"), b"synthetic-libcxx")
                # Import helper ships with the scripts.
                self.assertIn(prefix + "scripts/pkg_import.py", names)
                self.assertEqual(package.read(prefix + "scripts/pkg_import.py"),
                                 b"# synthetic pkg_import")
                # GPL notice plus the versioned pin record land in licenses/.
                self.assertIn(prefix + "licenses/shadps4plus-pkg-LICENSE.txt", names)
                self.assertEqual(package.read(prefix + "licenses/shadps4plus-pkg-LICENSE.txt"),
                                 b"synthetic-upstream-GPL-notice")
                self.assertIn(prefix + "licenses/shadps4plus-pkg.README.CTR.md", names)
                self.assertEqual(package.read(prefix + "licenses/shadps4plus-pkg.README.CTR.md"),
                                 b"synthetic-pin-record 9d2e7612")
                # Dependency record names the extractor and its license.
                third_party = package.read(prefix + "THIRD-PARTY.txt").decode("utf-8")
            self.assertIn("pkg_extractor.exe", third_party)
            self.assertIn("GPL-2.0-or-later", third_party)
            self.assertIn("shadps4plus-pkg", third_party)
            self.assertIn("zlib1.dll", third_party)
            self.assertIn("libc++.dll", third_party)

    def test_portable_excludes_pkgs_game_and_user_data(self):
        with tempfile.TemporaryDirectory() as temp:
            root, dist, dxc, clang_bin, portable_dir = _stage_source(temp)
            archive = _run_packager(root, dist, dxc, clang_bin, portable_dir)
            with zipfile.ZipFile(archive) as package:
                names = set(package.namelist())
            prefix = f"{portable.NAME}/"
            self.assertFalse(any(n.startswith(prefix + "user/") for n in names),
                             "portable ZIP must not bundle user/ saves or logs")
            self.assertFalse(any(n.lower().endswith(".pkg") for n in names),
                             "portable ZIP must not bundle source PKGs")
            self.assertFalse(any("CUSA13795" in n for n in names),
                             "portable ZIP must not bundle game files")
            self.assertFalse(any(n.startswith(prefix + "CUSA") for n in names))


if __name__ == "__main__":
    unittest.main()
