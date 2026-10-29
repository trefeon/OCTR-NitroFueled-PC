"""Import orchestration for CTR PKG folders (Task 2).

Covers ``scripts/pkg_import.py`` without real game PKGs: a fake extractor
speaks the real ``--check-type`` exit-code protocol (101 base / 102 update /
103 dlc) through :func:`pkg_import.classify`, extracts synthetic PKGs that
carry real SFO bytes, and stub merge/check scripts stand in for
``tools/merge_update.py`` / ``scripts/game_check.py`` (injectable paths, so
the production defaults stay real).

Run: ``python -m unittest discover -s tests -p 'test_pkg_import.py' -q``
"""
import os
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from paths import ROOT

import pkg_import

FAKE_EXTRACTOR = """\
import struct
import sys
from pathlib import Path


def pkg_sfo(path):
    data = Path(path).read_bytes()
    if data[:4] != b"\\x7fCNT":
        return None
    (count,) = struct.unpack_from(">I", data, 0x10)
    (table,) = struct.unpack_from(">I", data, 0x18)
    for i in range(count):
        e = table + i * 32
        (eid,) = struct.unpack_from(">I", data, e)
        if eid != 0x1000:
            continue
        (flags,) = struct.unpack_from(">I", data, e + 8)
        if flags & 0x80000000:
            return None
        (off,) = struct.unpack_from(">I", data, e + 16)
        (size,) = struct.unpack_from(">I", data, e + 20)
        return data[off:off + size]
    return None


def sfo_value(sfo, key):
    magic, _version, keys, values, count = struct.unpack_from("<4sIIII", sfo, 0)
    assert magic == b"\\0PSF"
    for i in range(count):
        ko, fmt, size, _cap, off = struct.unpack_from("<HHIII", sfo, 20 + i * 16)
        end = sfo.index(0, keys + ko)
        if sfo[keys + ko:end].decode() == key:
            raw = sfo[values + off:values + off + size]
            return raw.rstrip(b"\\0").decode()
    return ""


name = Path(sys.argv[1]).name.lower()
if len(sys.argv) == 3 and sys.argv[2] == "--check-type":
    if "broken" in name:
        print("Cannot open PKG file")
        sys.exit(1)
    if "dlc" in name:
        print("pkg is a dlc")
        sys.exit(103)
    if "update" in name:
        print("pkg is a game update")
        sys.exit(102)
    if "base" in name:
        print("pkg is a base game")
        sys.exit(101)
    print("unknown package")
    sys.exit(99)

sfo = pkg_sfo(sys.argv[1])
if sfo is None:
    print("Could not read SFO.")
    sys.exit(1)
title = sfo_value(sfo, "TITLE_ID") or "CUSA13795"
out = Path(sys.argv[2]) / title
(out / "sce_sys").mkdir(parents=True, exist_ok=True)
(out / "sce_sys" / "param.sfo").write_bytes(sfo)
(out / "eboot.bin").write_bytes(b"FAKEEBOOT:" + name.encode())
if "update" in name:
    (out / "update_marker.txt").write_text("patched")
"""

STUB_MERGE = """\
import os
import shutil
import sys
from pathlib import Path
if os.environ.get("PKG_IMPORT_STUB_MERGE") == "fail":
    print("stub merge failed")
    sys.exit(1)
base, update, out = (Path(a) for a in sys.argv[1:4])
shutil.copytree(base, out, dirs_exist_ok=True)
for p in update.rglob("*"):
    if p.is_file():
        t = out / p.relative_to(update)
        t.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, t)
"""

STUB_CHECK = """\
import os
import sys
from pathlib import Path
if os.environ.get("PKG_IMPORT_STUB_CHECK") == "fail":
    print("stub game check failed")
    sys.exit(1)
if not (Path(sys.argv[1]) / "eboot.bin").is_file():
    print("no eboot.bin")
    sys.exit(1)
print("ctr")
"""


def build_sfo(entries):
    keys = b""
    offsets = {}
    for key in entries:
        offsets[key] = len(keys)
        keys += key.encode() + b"\x00"
    values = b""
    meta = []
    for key, value in entries.items():
        raw = value.encode() + b"\x00"
        meta.append((offsets[key], 0x204, len(raw), len(raw), len(values)))
        values += raw
    count = len(entries)
    header = struct.pack("<4sIIII", b"\x00PSF", 0x101,
                         20 + count * 16, 20 + count * 16 + len(keys), count)
    return header + b"".join(struct.pack("<HHIII", *m) for m in meta) + keys + values


def build_pkg(path, sfo_entries=None, encrypted_sfo=False):
    sfo = build_sfo(sfo_entries) if sfo_entries is not None else b"not-an-sfo"
    header = bytearray(0x20)
    header[0:4] = b"\x7fCNT"
    struct.pack_into(">I", header, 0x10, 1)
    struct.pack_into(">I", header, 0x18, 0x20)
    entry = bytearray(32)
    struct.pack_into(">I", entry, 0, 0x1000)
    struct.pack_into(">I", entry, 8, 0x80000000 if encrypted_sfo else 0)
    struct.pack_into(">I", entry, 16, 0x40)
    struct.pack_into(">I", entry, 20, len(sfo))
    Path(path).write_bytes(bytes(header) + bytes(entry) + sfo)


BASE_SFO = {"TITLE_ID": "CUSA13795", "CATEGORY": "gd", "APP_VER": "01.00",
            "TITLE": "Crash Team Racing Nitro-Fueled"}
UPDATE_SFO = {"TITLE_ID": "CUSA13795", "CATEGORY": "gp", "APP_VER": "01.21",
              "TITLE": "Crash Team Racing Nitro-Fueled"}
DLC_SFO = {"TITLE_ID": "CUSA13795", "CATEGORY": "ac",
           "CONTENT_ID": "UP9000-CUSA13795_00-0123456789ABCDEF",
           "TITLE": "Nitros Oxide Edition"}


class PkgImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.extractor = [sys.executable, str(self.root / "fake_extractor.py")]
        (self.root / "fake_extractor.py").write_text(FAKE_EXTRACTOR)
        self.merge_stub = self.root / "stub_merge.py"
        self.merge_stub.write_text(STUB_MERGE)
        self.check_stub = self.root / "stub_check.py"
        self.check_stub.write_text(STUB_CHECK)
        self.env = dict(os.environ)
        self.env.pop("PKG_IMPORT_STUB_MERGE", None)
        self.env.pop("PKG_IMPORT_STUB_CHECK", None)

    def tearDown(self):
        self.tmp.cleanup()

    # -- fixtures ---------------------------------------------------------
    def pkg_dir(self, *specs):
        d = self.root / "pkgs"
        d.mkdir(exist_ok=True)
        for name, sfo in specs:
            build_pkg(d / name, sfo)
        return d

    def std_pkgs(self):
        return self.pkg_dir(("CUSA13795-base.pkg", BASE_SFO),
                            ("CUSA13795-update.pkg", UPDATE_SFO),
                            ("CUSA13795-dlc1.pkg", DLC_SFO),
                            ("CUSA13795-dlc2.pkg", DLC_SFO))

    def install_kwargs(self, dest):
        return dict(extractor=self.extractor, dest_dir=dest,
                    merge_script=self.merge_stub, check_script=self.check_stub,
                    env=self.env)

    # -- classify: real --check-type exit codes ---------------------------
    def test_type_codes_are_101_102_103(self):
        self.assertEqual(pkg_import.TYPE_CODES, {101: "base", 102: "update", 103: "dlc"})

    def test_check_type_codes_round_trip_through_classify(self):
        roles = pkg_import.classify(self.std_pkgs(), self.extractor)
        self.assertEqual([p.name for p in roles["base"]], ["CUSA13795-base.pkg"])
        self.assertEqual([p.name for p in roles["update"]], ["CUSA13795-update.pkg"])
        self.assertEqual(sorted(p.name for p in roles["dlc"]),
                         ["CUSA13795-dlc1.pkg", "CUSA13795-dlc2.pkg"])

    def test_unknown_check_type_code_rejected(self):
        d = self.pkg_dir(("mystery.pkg", BASE_SFO))
        with self.assertRaisesRegex(ValueError, "unsupported package type"):
            pkg_import.classify(d, self.extractor)

    def test_broken_pkg_rejected(self):
        d = self.pkg_dir(("broken-base.pkg", BASE_SFO))
        with self.assertRaisesRegex(ValueError, "extractor failed|unsupported"):
            pkg_import.classify(d, self.extractor)

    def test_non_pkg_files_ignored(self):
        d = self.std_pkgs()
        (d / "notes.txt").write_text("not a package")
        roles = pkg_import.classify(d, self.extractor)
        self.assertEqual(len(roles["base"]), 1)
        self.assertEqual(len(roles["update"]), 1)

    # -- duplicate / missing sets -----------------------------------------
    def test_duplicate_base_rejected_and_staging_cleaned(self):
        d = self.pkg_dir(("CUSA13795-base.pkg", BASE_SFO),
                         ("CUSA13795-base2.pkg", BASE_SFO),
                         ("CUSA13795-update.pkg", UPDATE_SFO))
        dest = self.root / "lib"
        dest.mkdir()
        with self.assertRaisesRegex(ValueError, "two base"):
            pkg_import.install(d, **self.install_kwargs(dest))
        self.assertEqual(list(dest.glob("stage-*")), [])
        self.assertFalse((dest / "CUSA13795-1.21").exists())

    def test_duplicate_update_rejected(self):
        d = self.pkg_dir(("CUSA13795-base.pkg", BASE_SFO),
                         ("CUSA13795-update.pkg", UPDATE_SFO),
                         ("CUSA13795-update2.pkg", UPDATE_SFO))
        dest = self.root / "lib"
        dest.mkdir()
        with self.assertRaisesRegex(ValueError, "two update"):
            pkg_import.install(d, **self.install_kwargs(dest))
        self.assertEqual(list(dest.glob("stage-*")), [])

    def test_missing_update_rejected(self):
        d = self.pkg_dir(("CUSA13795-base.pkg", BASE_SFO))
        with self.assertRaisesRegex(ValueError, "exactly one base and one update"):
            pkg_import.classify(d, self.extractor)

    # -- happy path ---------------------------------------------------------
    def test_install_happy_path(self):
        d = self.std_pkgs()
        dest = self.root / "lib"
        dest.mkdir()
        merged = pkg_import.install(d, **self.install_kwargs(dest))
        self.assertEqual(merged, dest / "CUSA13795-1.21")
        self.assertTrue((merged / "eboot.bin").is_file())
        self.assertTrue((merged / "update_marker.txt").is_file())
        self.assertTrue((merged / "sce_sys" / "param.sfo").is_file())
        # Staging is gone, sources are untouched, DLC landed raw.
        self.assertEqual(list(dest.glob("stage-*")), [])
        self.assertTrue((d / "CUSA13795-base.pkg").is_file())
        dlc_dir = dest / "dlc"
        self.assertTrue((dlc_dir / "CUSA13795-dlc1.pkg").is_file())
        self.assertTrue((dlc_dir / "CUSA13795-dlc2.pkg").is_file())

    def test_install_cleans_staging_on_merge_failure(self):
        d = self.std_pkgs()
        dest = self.root / "lib"
        dest.mkdir()
        self.env["PKG_IMPORT_STUB_MERGE"] = "fail"
        with self.assertRaisesRegex(ValueError, "merge failed"):
            pkg_import.install(d, **self.install_kwargs(dest))
        self.assertEqual(list(dest.glob("stage-*")), [])
        self.assertFalse((dest / "CUSA13795-1.21").exists())

    def test_install_cleans_staging_on_check_failure(self):
        d = self.std_pkgs()
        dest = self.root / "lib"
        dest.mkdir()
        self.env["PKG_IMPORT_STUB_CHECK"] = "fail"
        with self.assertRaisesRegex(ValueError, "game check failed"):
            pkg_import.install(d, **self.install_kwargs(dest))
        self.assertEqual(list(dest.glob("stage-*")), [])
        self.assertFalse((dest / "CUSA13795-1.21").exists())

    def test_promote_never_overwrites_existing(self):
        d = self.std_pkgs()
        dest = self.root / "lib"
        dest.mkdir()
        existing = dest / "CUSA13795-1.21"
        existing.mkdir()
        (existing / "sentinel.txt").write_text("precious")
        with self.assertRaises(FileExistsError):
            pkg_import.install(d, **self.install_kwargs(dest))
        self.assertEqual((existing / "sentinel.txt").read_text(), "precious")
        self.assertEqual(list(dest.glob("stage-*")), [])

    # -- SFO validation ------------------------------------------------------
    def test_update_wrong_title_rejected(self):
        other = dict(UPDATE_SFO, TITLE_ID="CUSA14876")
        d = self.pkg_dir(("CUSA13795-base.pkg", BASE_SFO),
                         ("CUSA13795-update.pkg", other))
        dest = self.root / "lib"
        dest.mkdir()
        with self.assertRaisesRegex(ValueError, "CUSA13795"):
            pkg_import.install(d, **self.install_kwargs(dest))
        self.assertEqual(list(dest.glob("stage-*")), [])

    def test_update_wrong_version_rejected(self):
        old = dict(UPDATE_SFO, APP_VER="01.00")
        d = self.pkg_dir(("CUSA13795-base.pkg", BASE_SFO),
                         ("CUSA13795-update.pkg", old))
        dest = self.root / "lib"
        dest.mkdir()
        with self.assertRaisesRegex(ValueError, "01.21"):
            pkg_import.install(d, **self.install_kwargs(dest))

    def test_base_must_be_game_package(self):
        patch_as_base = dict(BASE_SFO, CATEGORY="gp")
        d = self.pkg_dir(("CUSA13795-base.pkg", patch_as_base),
                         ("CUSA13795-update.pkg", UPDATE_SFO))
        dest = self.root / "lib"
        dest.mkdir()
        with self.assertRaisesRegex(ValueError, "game package"):
            pkg_import.install(d, **self.install_kwargs(dest))

    def test_malformed_sfo_rejected(self):
        d = self.root / "pkgs-bad"
        d.mkdir()
        build_pkg(d / "CUSA13795-base.pkg", None)  # no parseable SFO
        build_pkg(d / "CUSA13795-update.pkg", UPDATE_SFO)
        dest = self.root / "lib"
        dest.mkdir()
        with self.assertRaisesRegex(ValueError, "extractor failed|param.sfo|SFO|unreadable"):
            pkg_import.install(d, **self.install_kwargs(dest))
        self.assertEqual(list(dest.glob("stage-*")), [])

    # -- DLC: raw, matching, never overwritten --------------------------------
    def test_non_matching_dlc_skipped(self):
        other_dlc = dict(DLC_SFO, TITLE_ID="CUSA14876",
                         CONTENT_ID="UP9000-CUSA14876_00-0123456789ABCDEF")
        d = self.pkg_dir(("CUSA13795-base.pkg", BASE_SFO),
                         ("CUSA13795-update.pkg", UPDATE_SFO),
                         ("CUSA13795-dlc1.pkg", DLC_SFO),
                         ("CUSA14876-dlc9.pkg", other_dlc))
        dest = self.root / "lib"
        dest.mkdir()
        merged = pkg_import.install(d, **self.install_kwargs(dest))
        self.assertTrue(merged.is_dir())
        dlc_dir = dest / "dlc"
        self.assertTrue((dlc_dir / "CUSA13795-dlc1.pkg").is_file())
        self.assertFalse((dlc_dir / "CUSA14876-dlc9.pkg").exists())

    def test_dlc_never_overwritten(self):
        d = self.std_pkgs()
        dest = self.root / "lib"
        dest.mkdir()
        dlc_dir = dest / "dlc"
        dlc_dir.mkdir()
        keep = dlc_dir / "CUSA13795-dlc1.pkg"
        keep.write_bytes(b"player's own file")
        report = pkg_import.copy_dlc_raw([d / "CUSA13795-dlc1.pkg",
                                          d / "CUSA13795-dlc2.pkg"], dlc_dir)
        self.assertEqual(keep.read_bytes(), b"player's own file")
        self.assertTrue((dlc_dir / "CUSA13795-dlc2.pkg").is_file())
        self.assertEqual(report["duplicates"], ["CUSA13795-dlc1.pkg"])
        self.assertEqual(report["copied"], ["CUSA13795-dlc2.pkg"])

    # -- CLI -------------------------------------------------------------------
    def test_cli_happy_path_reports_merged_dir(self):
        d = self.std_pkgs()
        dest = self.root / "lib"
        dest.mkdir()
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "pkg_import.py"), str(d), str(dest),
             "--extractor", sys.executable, str(self.root / "fake_extractor.py"),
             "--merge-script", str(self.merge_stub),
             "--check-script", str(self.check_stub)],
            capture_output=True, text=True, stdin=subprocess.DEVNULL, env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("MERGED_DIR=" + str(dest / "CUSA13795-1.21"), proc.stdout)
        self.assertIn("STAGING=", proc.stdout)

    def test_cli_failure_is_nonzero(self):
        d = self.pkg_dir(("CUSA13795-base.pkg", BASE_SFO))
        dest = self.root / "lib"
        dest.mkdir()
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "pkg_import.py"), str(d), str(dest),
             "--extractor", sys.executable, str(self.root / "fake_extractor.py")],
            capture_output=True, text=True, stdin=subprocess.DEVNULL, env=self.env)
        self.assertNotEqual(proc.returncode, 0)

    # -- Fix round: DLC faults never abort the install -------------------------
    def test_truncated_dlc_entry_table_is_skipped(self):
        sfo, reason = pkg_import.read_pkg_sfo(self._truncated_pkg())
        self.assertIsNone(sfo)
        self.assertTrue(reason, "a reason is reported")

    def test_short_sfo_int_field_rejected(self):
        with self.assertRaises(ValueError):
            pkg_import.parse_sfo(self._short_int_sfo())

    def test_truncated_dlc_pkg_skipped_install_succeeds(self):
        d = self.std_pkgs()
        build_pkg(d / "CUSA13795-dlc3.pkg", DLC_SFO)
        with open(d / "CUSA13795-dlc3.pkg", "r+b") as f:
            f.truncate(0x30)  # header plus a partial entry: truncated table
        dest = self.root / "lib"
        dest.mkdir()
        log = []
        merged = pkg_import.install(d, log=log.append, **self.install_kwargs(dest))
        self.assertTrue(merged.is_dir())
        self.assertFalse((dest / "dlc" / "CUSA13795-dlc3.pkg").exists())
        self.assertTrue(any("CUSA13795-dlc3.pkg skipped" in line for line in log),
                        log)
        self.assertTrue(any(line.startswith("MERGED_DIR=") for line in log), log)

    def _truncated_pkg(self):
        path = self.root / "cut.pkg"
        build_pkg(path, DLC_SFO)
        with open(path, "r+b") as f:
            f.truncate(0x30)
        return path

    @staticmethod
    def _short_int_sfo():
        keys, values = b"A\x00", b"AB"
        header = struct.pack("<4sIIII", b"\0PSF", 0x101, 36, 38, 1)
        return header + struct.pack("<HHIII", 0, 0x404, 2, 2, 0) + keys + values

    # -- Fix round: exclusive DLC copy -----------------------------------------
    def test_dangling_symlink_dlc_reported_duplicate_and_untouched(self):
        try:
            link = self.root / "dlc-link"
            link.mkdir()
            target = link / "CUSA13795-dlc1.pkg"
            os.symlink(self.root / "does-not-exist.pkg", target)
        except (OSError, NotImplementedError) as e:
            self.skipTest(f"symlinks unavailable: {e}")
        d = self.std_pkgs()
        report = pkg_import.copy_dlc_raw([d / "CUSA13795-dlc1.pkg"], link)
        self.assertEqual(report["duplicates"], ["CUSA13795-dlc1.pkg"])
        self.assertTrue(target.is_symlink(), "the player's link is not replaced")
        self.assertTrue(os.readlink(target).endswith("does-not-exist.pkg"),
                        os.readlink(target))

    def test_partial_copy_cleaned_on_metadata_failure(self):
        import unittest.mock as mock
        d = self.std_pkgs()
        dest = self.root / "lib"
        dest.mkdir()
        dlc_dir = dest / "dlc"
        dlc_dir.mkdir()
        with mock.patch.object(pkg_import.shutil, "copystat",
                               side_effect=OSError("no metadata")):
            report = pkg_import.copy_dlc_raw([d / "CUSA13795-dlc1.pkg"], dlc_dir)
        self.assertEqual(report["failed"], ["CUSA13795-dlc1.pkg"])
        self.assertFalse((dlc_dir / "CUSA13795-dlc1.pkg").exists()
                         or os.path.lexists(dlc_dir / "CUSA13795-dlc1.pkg"))

    # -- Fix round: job-object process-tree kill (Windows-only mechanism check) --
    # Mirrors the launcher's StartPkgImport/StopPkgImport spawn pattern
    # (CREATE_SUSPENDED -> AssignProcessToJobObject -> ResumeThread ->
    # TerminateJobObject) so the extractor grandchild cannot outlive a cancel.
    @unittest.skipUnless(sys.platform == "win32", "Windows job objects only")
    def test_job_object_kills_process_tree(self):
        import ctypes
        import time
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class STARTUPINFO(ctypes.Structure):
            _fields_ = [("cb", ctypes.c_ulong),
                        ("lpReserved", ctypes.c_void_p),
                        ("lpDesktop", ctypes.c_void_p),
                        ("lpTitle", ctypes.c_void_p),
                        ("dwX", ctypes.c_ulong), ("dwY", ctypes.c_ulong),
                        ("dwXSize", ctypes.c_ulong), ("dwYSize", ctypes.c_ulong),
                        ("dwXCountChars", ctypes.c_ulong),
                        ("dwYCountChars", ctypes.c_ulong),
                        ("dwFillAttribute", ctypes.c_ulong),
                        ("dwFlags", ctypes.c_ulong),
                        ("wShowWindow", ctypes.c_ushort),
                        ("cbReserved2", ctypes.c_ushort),
                        ("lpReserved2", ctypes.c_void_p),
                        ("hStdInput", ctypes.c_void_p),
                        ("hStdOutput", ctypes.c_void_p),
                        ("hStdError", ctypes.c_void_p)]

        class PROCESS_INFORMATION(ctypes.Structure):
            _fields_ = [("hProcess", ctypes.c_void_p),
                        ("hThread", ctypes.c_void_p),
                        ("dwProcessId", ctypes.c_ulong),
                        ("dwThreadId", ctypes.c_ulong)]

        class PROCESSENTRY32(ctypes.Structure):
            _fields_ = [("dwSize", ctypes.c_ulong),
                        ("cntUsage", ctypes.c_ulong),
                        ("th32ProcessID", ctypes.c_ulong),
                        ("th32DefaultHeapID", ctypes.c_void_p),
                        ("th32ModuleID", ctypes.c_ulong),
                        ("cntThreads", ctypes.c_ulong),
                        ("th32ParentProcessID", ctypes.c_ulong),
                        ("pcPriClassBase", ctypes.c_long),
                        ("dwFlags", ctypes.c_ulong),
                        ("szExeFile", ctypes.c_wchar * 260)]

        def children_of(pid):
            snap = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
            if snap == ctypes.c_void_p(-1).value:
                return []
            try:
                entry = PROCESSENTRY32()
                entry.dwSize = ctypes.sizeof(entry)
                found = []
                ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
                while ok:
                    if entry.th32ParentProcessID == pid:
                        found.append(entry.th32ProcessID)
                    ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
                return found
            finally:
                kernel32.CloseHandle(snap)

        def assert_dead(pid):
            deadline = time.time() + 10
            STILL_ACTIVE = 0x103
            while time.time() < deadline:
                handle = kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED
                if not handle:
                    return
                try:
                    code = ctypes.c_ulong()
                    if (kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
                            and code.value != STILL_ACTIVE):
                        return
                finally:
                    kernel32.CloseHandle(handle)
                time.sleep(0.1)
            raise AssertionError(f"process {pid} survived the job kill")

        parent_script = (
            "import subprocess, sys, time; "
            "subprocess.Popen([sys.executable, '-c', "
            "'import time; time.sleep(60)']); time.sleep(60)")
        si, pi = STARTUPINFO(), PROCESS_INFORMATION()
        si.cb = ctypes.sizeof(si)
        cmd = ctypes.create_unicode_buffer(
            sys.executable + ' -c "' + parent_script + '"')
        CREATE_SUSPENDED, CREATE_NO_WINDOW = 0x00000004, 0x08000000
        self.assertTrue(
            kernel32.CreateProcessW(None, cmd, None, None, False,
                                    CREATE_SUSPENDED | CREATE_NO_WINDOW,
                                    None, None, ctypes.byref(si),
                                    ctypes.byref(pi)),
            "CreateProcess suspended parent")
        try:
            job = kernel32.CreateJobObjectW(None, None)
            self.assertTrue(job, "CreateJobObject")
            try:
                self.assertTrue(
                    kernel32.AssignProcessToJobObject(job, pi.hProcess),
                    "parent joins the job before it runs")
                kernel32.ResumeThread(pi.hThread)
                grandchild = None
                for _ in range(100):
                    kids = children_of(pi.dwProcessId)
                    if kids:
                        grandchild = kids[0]
                        break
                    time.sleep(0.1)
                self.assertIsNotNone(grandchild, "grandchild started")
                self.assertTrue(kernel32.TerminateJobObject(job, 1),
                                "job kill terminates the tree")
                assert_dead(pi.dwProcessId)
                assert_dead(grandchild)
            finally:
                kernel32.CloseHandle(job)
        finally:
            kernel32.CloseHandle(pi.hThread)
            kernel32.CloseHandle(pi.hProcess)


    # -- Fix round 2: extractor stdout pipe is always closed ----------------------
    # The warning fires when the TextIOWrapper is collected with the fd still
    # open. On failure paths the traceback keeps Popen alive, so gc.collect()
    # must run after assertRaises exits (which drops the traceback) — hence
    # the runner below instead of a plain context manager.
    def _extract_and_check_warnings(self, fn):
        import gc
        import warnings
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", ResourceWarning)
            result = fn()
            gc.collect()
        leaked = [str(w.message) for w in caught
                  if issubclass(w.category, ResourceWarning)
                  and "unclosed file" in str(w.message)]
        self.assertEqual(leaked, [], "extractor stdout pipe left open")
        return result

    def test_extract_closes_stdout_on_success(self):
        d = self.pkg_dir(("CUSA13795-base.pkg", BASE_SFO))
        out = self.root / "out-ok"
        root = self._extract_and_check_warnings(
            lambda: pkg_import.extract_stage(
                self.extractor, d / "CUSA13795-base.pkg", out,
                log=lambda line: None))
        self.assertTrue((root / "eboot.bin").is_file())

    def test_extract_closes_stdout_on_failure(self):
        junk = self.root / "junk.pkg"
        junk.write_bytes(b"not a package")
        out = self.root / "out-fail"

        def run():
            with self.assertRaises(ValueError):
                pkg_import.extract_stage(
                    self.extractor, junk, out, log=lambda line: None)
        self._extract_and_check_warnings(run)

    def test_extract_closes_stdout_on_cancel(self):
        import threading
        # A chatty extractor: the pre-set cancel fires on the first line.
        slow = self.root / "slow_extractor.py"
        slow.write_text(
            "import sys\n"
            "print('starting', flush=True)\n"
            "for i in range(1000000):\n"
            "    print(f'line {i}', flush=True)\n")
        d = self.pkg_dir(("CUSA13795-base.pkg", BASE_SFO))
        out = self.root / "out-cancel"
        cancel = threading.Event()
        cancel.set()

        def run():
            with self.assertRaises(pkg_import.Cancelled):
                pkg_import.extract_stage(
                    [sys.executable, str(slow)], d / "CUSA13795-base.pkg",
                    out, log=lambda line: None, cancel=cancel)
        self._extract_and_check_warnings(run)


if __name__ == "__main__":
    unittest.main()
