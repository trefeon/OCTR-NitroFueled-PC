"""Compat coverage for the pinned pkg_extractor dependency (Task 1).

Self-contained: the 0x403 tail-split contract is inline (no new production
Python module), and the extractor CLI checks use synthetic invalid-PKG
fixtures in the style of the upstream
``extractor/tests/cli_invalid_pkg.cmake`` (missing file, empty file,
truncated 4-byte magic). The CLI tests skip when no extractor binary is
available (``PKG_EXTRACTOR`` env, ``out/pkg_extractor[.exe]``); they never
need real game PKGs, which are never committed to this repo.

Contract (CUSA13795 ``npbind.dat``, entry ``0x403``, observed size ``0x214``):
the owned patch ``patches/pkg-extractor-0x403-tail.patch`` decrypts the
largest AES-block-aligned prefix (``0x210``) with the existing CBC routine
and carries the short tail (4 bytes) through verbatim. Total length is
unchanged and no read/write ever crosses a span boundary::

    full = size & ~(16 - 1); tail = size - full   # 0 <= tail < 16

Run: ``python -m unittest discover -s tests -p 'test_pkg_extractor_compat.py' -q``
"""
import os
import shutil
import subprocess
import unittest
from pathlib import Path

from paths import ROOT

BASELINE_SHA = "9d2e76127ce32d13192f9a6ab84a96404677394a"
AES_BLOCK = 16
NP_403_ENTRY_ID = 0x403
NP_403_OBSERVED_SIZE = 0x214  # 532 bytes: 33 full AES blocks + 4 tail bytes
NP_403_FULL_PREFIX = 0x210
NP_403_TAIL = 4
CHECK_TYPE_CODES = {101: "base", 102: "update", 103: "dlc"}

PATCH = ROOT / "patches" / "pkg-extractor-0x403-tail.patch"
SUBMODULE = ROOT / "third_party" / "shadps4plus-pkg"
README = ROOT / "third_party" / "shadps4plus-pkg.README.CTR.md"
README_MIRROR = SUBMODULE / "README.CTR.md"


def split_tail(size, block=AES_BLOCK):
    """Inline tail-split contract: aligned prefix plus a short tail."""
    full = size & ~(block - 1)
    return full, size - full


def find_extractor():
    """Locate a built pkg_extractor binary, or None (CLI tests skip)."""
    env = os.environ.get("PKG_EXTRACTOR")
    if env and Path(env).is_file():
        return env
    for candidate in (ROOT / "out" / "pkg_extractor.exe",
                      ROOT / "out" / "pkg_extractor"):
        if candidate.is_file():
            return str(candidate)
    return shutil.which("pkg_extractor")


class TailSplitContractTests(unittest.TestCase):
    def test_403_observed_size_splits_to_0x210_plus_4(self):
        full, tail = split_tail(NP_403_OBSERVED_SIZE)
        self.assertEqual(full, NP_403_FULL_PREFIX)
        self.assertEqual(tail, NP_403_TAIL)

    def test_full_prefix_is_block_aligned_and_bounded(self):
        for size in (NP_403_OBSERVED_SIZE, 0x210, 0x220, 17, 16, 1):
            full, tail = split_tail(size)
            self.assertEqual(full % AES_BLOCK, 0)
            self.assertEqual(full + tail, size)
            self.assertGreaterEqual(tail, 0)
            self.assertLess(tail, AES_BLOCK)

    def test_aligned_sizes_have_empty_tail(self):
        for size in (0x200, 0x210, 16, 0):
            self.assertEqual(split_tail(size), (size, 0))

    def test_tail_passthrough_preserves_exact_bytes(self):
        # Reference model of the owned patch semantics: the aligned prefix
        # is replaced by the block decrypt output while the short tail
        # passes through verbatim, so the output length never changes and
        # the tail bytes are exactly preserved.
        data = bytes(range(256)) * 2 + bytes(range(0x10)) + b"ABCD"
        self.assertEqual(len(data), NP_403_OBSERVED_SIZE)
        full, tail = split_tail(len(data))
        self.assertEqual((full, tail), (NP_403_FULL_PREFIX, NP_403_TAIL))
        out = bytearray(len(data))
        out[:full] = data[:full]  # placeholder for the block-decrypt output
        out[full:] = data[full:]  # short tail passes through verbatim
        self.assertEqual(len(out), len(data))
        self.assertEqual(bytes(out[:full]), data[:full])
        self.assertEqual(bytes(out[full:]), b"ABCD")

    def test_check_type_codes_cover_base_update_dlc(self):
        self.assertEqual(CHECK_TYPE_CODES, {101: "base", 102: "update", 103: "dlc"})


class OwnedPatchTests(unittest.TestCase):
    def test_patch_exists(self):
        self.assertTrue(PATCH.is_file(), f"missing owned patch: {PATCH}")

    def test_patch_targets_baseline_extractor_sources(self):
        text = PATCH.read_text(encoding="utf-8")
        self.assertIn("src/core/file_format/pkg.cpp", text)
        self.assertIn("src/core/crypto/crypto.cpp", text)
        self.assertIn("extractor/main.cpp", text)
        self.assertIn("src/core/loader.cpp", text)

    def test_patch_touches_only_owned_files(self):
        files = sorted(line.split(" b/")[1] for line in
                       PATCH.read_text(encoding="utf-8").splitlines()
                       if line.startswith("diff --git"))
        self.assertEqual(files, ["extractor/main.cpp",
                                 "src/core/crypto/crypto.cpp",
                                 "src/core/crypto/crypto.h",
                                 "src/core/file_format/pkg.cpp",
                                 "src/core/loader.cpp"])

    def test_patch_keeps_alignment_guard(self):
        text = PATCH.read_text(encoding="utf-8")
        self.assertIn("not AES block aligned", text)

    def test_patch_handles_403_short_tail(self):
        text = PATCH.read_text(encoding="utf-8")
        self.assertIn("0x403", text)
        self.assertIn("BLOCKSIZE", text)

    def test_patch_removes_round_up_overrun(self):
        # The baseline sized a cipher span past the data buffer
        # (msize rounded UP). No added line may reintroduce that pattern.
        added = [line for line in PATCH.read_text(encoding="utf-8").splitlines()
                 if line.startswith("+") and not line.startswith("+++")]
        self.assertTrue(added, "patch adds no lines")
        for line in added:
            self.assertNotIn("msize", line)

    def test_patch_rejects_invalid_inputs_before_parsing(self):
        # Real-binary acceptance: truncated headers must fail before any
        # table seek, and every invalid/unreadable path must exit nonzero.
        text = PATCH.read_text(encoding="utf-8")
        for marker in ("PKG header is truncated",
                       "Invalid PKG header magic",
                       "PKG table entry is truncated",
                       "PKG entry range is outside the file",
                       "PKG table entry range is outside the file",
                       "Failed to read PKG title ID",
                       "PKG is missing param.sfo",
                       "Failed to restore PKG table offset",
                       "Failed to read PKG entry data",
                       "Failed to write complete PKG output file",
                       "PSF file has no CATEGORY",
                       "invalid DLC entitlement label"):
            self.assertIn(marker, text)
        added = [line for line in text.splitlines()
                 if line.startswith("+") and not line.startswith("+++")]
        self.assertTrue(any(line.lstrip("+").strip() == "return 1;" for line in added))


class TableCursorDisciplineTests(unittest.TestCase):
    """Behavioral contract for the Open table walk fixed by the owned patch.

    The walk must visit every table entry in order even when param.sfo sits
    mid-table: seeking to the payload and restoring the saved table cursor
    must not skip or repeat entries. This simulates the fixed discipline
    (Tell, seek payload, read, restore) against a synthetic 3-entry table
    with the sfo entry second; the C++ implementation is pinned by
    PatchedSourceStructureTests below and proven on-device by the
    coordinator's real-package runs.
    """

    TABLE_STRIDE = 32

    def walk_table(self, entry_count, sfo_index):
        visited = []
        cursor = 0
        for index in range(entry_count):
            visited.append(cursor // self.TABLE_STRIDE)
            cursor += self.TABLE_STRIDE  # ReadTableEntry consumes one stride
            if index == sfo_index:
                saved = cursor
                cursor = 0x2000  # seek(entry.offset): payload read elsewhere
                cursor = saved  # restore table cursor
        return visited

    def test_mid_table_sfo_visits_every_entry_once(self):
        self.assertEqual(self.walk_table(3, 1), [0, 1, 2])

    def test_first_and_last_sfo_positions_stay_ordered(self):
        self.assertEqual(self.walk_table(3, 0), [0, 1, 2])
        self.assertEqual(self.walk_table(3, 2), [0, 1, 2])


class PatchedSourceStructureTests(unittest.TestCase):
    """Structural guards on the resolved patched sources (pin + owned patch).

    Materializes the patched files in a temp dir (read-only: git show from
    the pin plus git apply of the owned patch) and asserts the review
    findings stayed fixed in the result. Real behavior is proven by the
    coordinator's rebuild + real-package runs; these guards catch patch
    drift or regressions of the exact defects found in review.
    """

    SOURCES = ("src/core/file_format/pkg.cpp",
                 "src/core/crypto/crypto.cpp",
                 "src/core/crypto/crypto.h",
                 "src/core/loader.cpp",
                 "extractor/main.cpp")

    @classmethod
    def setUpClass(cls):
        import tempfile
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        for rel in cls.SOURCES:
            show = subprocess.run(["git", "-C", str(SUBMODULE), "show", f"HEAD:{rel}"],
                                  capture_output=True, cwd=ROOT)
            if show.returncode != 0:
                raise unittest.SkipTest("pinned submodule not available")
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(show.stdout)
        apply = subprocess.run(["git", "apply", str(PATCH)],
                               capture_output=True, text=True, cwd=root)
        if apply.returncode != 0:
            raise unittest.SkipTest(f"owned patch does not apply: {apply.stderr.strip()}")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    @classmethod
    def pkg(cls):
        return (Path(cls._tmp.name) / "src/core/file_format/pkg.cpp").read_text(encoding="utf-8")

    @classmethod
    def main(cls):
        return (Path(cls._tmp.name) / "extractor/main.cpp").read_text(encoding="utf-8")

    def test_title_id_uses_boolean_read(self):
        # Read() returns 1/0 for arrays, never the byte count: comparing to
        # sizeof rejected every valid PKG.
        self.assertIn("!file.Read(pkgTitleID)", self.pkg())
        self.assertNotIn("!= sizeof(pkgTitleID)", self.pkg())

    def test_sfo_payload_restores_table_cursor(self):
        self.assertIn("const s64 table_pos = file.Tell();", self.pkg())
        self.assertIn("file.Seek(table_pos)", self.pkg())

    def test_entry_payloads_are_byte_count_checked(self):
        pkg = self.pkg()
        self.assertGreaterEqual(pkg.count("!= entry.size"), 3)
        self.assertIn("|| !out.Flush()", pkg)

    def test_np_path_reuses_loaded_buffer(self):
        pkg = self.pkg()
        # One loader per raw-write path (empty-name + main); the NP block
        # must not declare a third shadowing buffer or re-seek.
        self.assertEqual(pkg.count("std::vector<u8> data;"), 2)
        self.assertIn("std::span<const u8> cipherNp(data.data(), data.size());", pkg)

    def test_invalid_paths_exit_nonzero(self):
        main = self.main()
        self.assertGreaterEqual(main.count("return 1;"), 6)
        self.assertIn("return ret;", main)
        for code in ("ret = 101;", "ret = 102;", "ret = 103;"):
            self.assertIn(code, main)


class SubmodulePinTests(unittest.TestCase):
    def test_gitmodules_registers_pin(self):
        gitmodules = (ROOT / ".gitmodules").read_text(encoding="utf-8")
        self.assertIn('third_party/shadps4plus-pkg', gitmodules)
        self.assertIn('trefeon/shadPS4Plus', gitmodules)

    def test_gitlink_pinned_to_baseline(self):
        proc = subprocess.run(["git", "ls-files", "-s", "third_party/shadps4plus-pkg"],
                              capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(proc.returncode, 0)
        self.assertIn(BASELINE_SHA, proc.stdout)
        self.assertIn("160000", proc.stdout)

    def test_readme_records_pin_and_patch(self):
        # Versioned copy outside the submodule path: this is what a fresh
        # clone carries (a file under the submodule path cannot be committed
        # from the superproject).
        self.assertTrue(README.is_file(), f"missing versioned pin record: {README}")
        text = README.read_text(encoding="utf-8")
        self.assertIn(BASELINE_SHA, text)
        self.assertIn("pkg-extractor-0x403-tail.patch", text)
        self.assertIn("GPL-2.0-or-later", text)
        self.assertIn("D:/github_repo/shadPS4Plus", text)

    def test_submodule_mirror_matches_versioned_record(self):
        # Convenience mirror inside the checked-out submodule tree; required
        # to stay byte-identical to the versioned record when present.
        if not SUBMODULE.is_dir():
            self.skipTest("submodule not checked out")
        self.assertTrue(README_MIRROR.is_file(), f"missing submodule mirror: {README_MIRROR}")
        self.assertEqual(README_MIRROR.read_bytes(), README.read_bytes())


class BuildWiringTests(unittest.TestCase):
    def test_cmake_hooks_pkg_extractor(self):
        text = (ROOT / "gpu" / "CMakeLists.txt").read_text(encoding="utf-8")
        self.assertIn("pkg_extractor", text)
        self.assertIn("pkg-extractor-0x403-tail.patch", text)

    def test_build_sh_builds_extractor_on_windows_path(self):
        text = (ROOT / "build.sh").read_text(encoding="utf-8")
        self.assertIn("pkg_extractor", text)


@unittest.skipIf(find_extractor() is None, "no pkg_extractor binary (set PKG_EXTRACTOR)")
class ExtractorCliTests(unittest.TestCase):
    """Synthetic invalid-PKG fixtures, mirroring cli_invalid_pkg.cmake.

    Requires a patched build: the owned patch makes every invalid path
    exit 1 with a deterministic message (missing/empty -> invalid-PKG
    notice, 4-byte magic-only header -> "PKG header is truncated").
    Rebuild out/pkg_extractor.exe from the pinned submodule plus the owned
    patch before running (the Task 3 gate rebuilds and copies the exe).
    """

    EXTRACTOR = find_extractor()
    PKG_MAGIC = bytes((0x7F, 0x43, 0x4E, 0x54))  # 0x7F 'C' 'N' 'T', little-endian

    def run_extractor(self, pkg, workdir):
        return subprocess.run([self.EXTRACTOR, str(pkg), str(workdir)],
                              capture_output=True, text=True, stdin=subprocess.DEVNULL)

    def test_missing_package_reports_invalid_pkg(self):
        with _workdir() as tmp:
            proc = self.run_extractor(tmp / "missing package.pkg", tmp)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("doesn't appear to be a valid PKG file", proc.stdout)

    def test_empty_package_reports_invalid_pkg(self):
        with _workdir() as tmp:
            empty = tmp / "empty package.pkg"
            empty.write_bytes(b"")
            proc = self.run_extractor(empty, tmp)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("doesn't appear to be a valid PKG file", proc.stdout)

    def test_truncated_header_reports_truncated_pkg(self):
        with _workdir() as tmp:
            truncated = tmp / "truncated package.pkg"
            truncated.write_bytes(self.PKG_MAGIC)
            proc = self.run_extractor(truncated, tmp)
        # Patched parser rejects the short header before any table seek,
        # so this must exit 1, never crash (baseline access-violated here).
        self.assertEqual(proc.returncode, 1)
        self.assertIn("PKG header is truncated", proc.stdout)


class _workdir:
    """Minimal TemporaryDirectory context manager (unittest-friendly)."""

    def __enter__(self):
        import tempfile
        self._dir = tempfile.TemporaryDirectory()
        return Path(self._dir.name)

    def __exit__(self, *exc):
        self._dir.cleanup()
        return False


if __name__ == "__main__":
    unittest.main()
