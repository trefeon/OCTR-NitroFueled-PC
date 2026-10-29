#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Import CUSA13795 PKGs: classify, extract, merge, validate, install.

Flow (see docs/superpowers/specs/2026-10-30-ctr-pkg-import-design.md): the
caller picks a folder with exactly one base PKG, one update PKG and optional
matching DLC PKGs, plus a game-library destination. Each PKG is classified
through the extractor's ``--check-type`` exit codes (101 base, 102 update,
103 DLC -- never zero on success), base and update are extracted into separate
subdirectories of a unique staging directory on the destination volume, their
SFO metadata is validated (both CUSA13795; base a game package; update
01.21), they are merged with ``tools/merge_update.py``, gated on
``scripts/game_check.py``, atomically promoted to a new unused install
directory (never overwriting one), and matching DLC PKGs are copied raw
(never extracted, never overwriting).

Source PKGs are read-only inputs. Only this import's staging directory is
ever removed; existing installs and the source folder are never touched.

Usage:
    pkg_import.py PKG_DIR DEST_DIR [--extractor PATH|--extractor CMD ...]
                                   [--dlc-dir PATH]
                                   [--merge-script PATH] [--check-script PATH]

Prints ``STAGING=<dir>`` first (so a caller that kills this process can clean
it) and ``MERGED_DIR=<dir>`` last on success.
"""
import argparse
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# The extractor's --check-type exit codes: base, update and DLC are reported
# as process exit codes, not conventional zero-on-success.
TYPE_CODES = {101: "base", 102: "update", 103: "dlc"}

SUPPORTED_TITLE = "CUSA13795"
SUPPORTED_VERSION = "01.21"
INSTALL_DIR_NAME = SUPPORTED_TITLE + "-1.21"

BASE_CATEGORY = "gd"  # a base game package, as the launcher's Analyze reads it
DLC_CATEGORY = "ac"


class Cancelled(Exception):
    """The import was cancelled (cancel event set or child terminated)."""


def _last_line(output):
    lines = output.strip().splitlines()
    return lines[-1] if lines else "no output"


def _log(message, log):
    if log is not None:
        log(message)
    else:
        print(message, flush=True)


def _extractor_cmd(extractor):
    """The extractor command prefix: a binary path, or a [cmd, args...] list."""
    if isinstance(extractor, (list, tuple)):
        return [str(a) for a in extractor]
    return [str(extractor)]


def _count_word(n):
    return {0: "no", 1: "one", 2: "two", 3: "three"}.get(n, str(n))


def default_extractor():
    """The bundled extractor next to the repo (out/pkg_extractor[.exe])."""
    for name in ("pkg_extractor.exe", "pkg_extractor"):
        candidate = REPO_ROOT / "out" / name
        if candidate.is_file():
            return str(candidate)
    exe = shutil.which("pkg_extractor")
    if exe:
        return exe
    raise RuntimeError("pkg_extractor not found in out/ nor on PATH")


def classify(pkg_dir, extractor):
    """{base, update, dlc} PKG lists via the extractor's --check-type codes.

    Every *.pkg is run through the real extractor subprocess; exit codes
    101/102/103 map to base/update/dlc and anything else is an error.
    Exactly one base and one update package are required.
    """
    pkg_dir = Path(pkg_dir)
    roles = {"base": [], "update": [], "dlc": []}
    cmd_prefix = _extractor_cmd(extractor)
    pkgs = sorted(pkg_dir.glob("*.pkg")) + sorted(pkg_dir.glob("*.PKG"))
    seen, unique = set(), []
    for pkg in pkgs:
        key = os.path.normcase(os.path.abspath(pkg))
        if key not in seen:
            seen.add(key)
            unique.append(pkg)
    pkgs = unique
    for pkg in pkgs:
        if not pkg.is_file():
            continue
        try:
            proc = subprocess.run(cmd_prefix + [str(pkg), "--check-type"],
                                  capture_output=True, text=True,
                                  stdin=subprocess.DEVNULL)
        except OSError as e:
            raise ValueError(f"could not run the extractor on {pkg.name}: {e}")
        role = TYPE_CODES.get(proc.returncode)
        if role is None:
            detail = (proc.stdout + proc.stderr).strip().splitlines()
            hint = f": {detail[-1]}" if detail else ""
            raise ValueError(f"unsupported package type for {pkg.name} "
                             f"(extractor exit {proc.returncode}{hint})")
        roles[role].append(pkg)
    n_base, n_update = len(roles["base"]), len(roles["update"])
    if n_base != 1 or n_update != 1:
        raise ValueError(
            f"need exactly one base and one update package "
            f"(found {_count_word(n_base)} base and "
            f"{_count_word(n_update)} update in {pkg_dir})")
    return roles


def make_stage(dest_dir):
    """A unique staging directory on the destination volume (for hard links)."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="stage-", dir=str(dest_dir)))


def _game_root(out_parent, before):
    """The extracted game tree: the extractor appends <TitleID>[/...]."""
    after = {p.name: p for p in Path(out_parent).iterdir()}
    new = [p for name, p in after.items() if name not in before]
    if len(new) == 1 and new[0].is_dir():
        return new[0]
    if not new and _has_sfo(Path(out_parent)):
        return Path(out_parent)
    for path in sorted(after.values()):
        if path.is_dir() and _has_sfo(path):
            return path
    raise ValueError(f"extraction into {out_parent} produced no game folder")


def _has_sfo(root):
    return any((root / c).is_file() for c in _SFO_CANDIDATES)


_SFO_CANDIDATES = ("sce_sys/param.sfo", "Sc0/param.sfo",
                   "Image0/sce_sys/param.sfo")


def extract_stage(extractor, pkg, out_dir, log=None, cancel=None):
    """Extract one PKG below out_dir; return the extracted game directory.

    Streams the extractor output to log; a set cancel event terminates the
    child and raises Cancelled. A nonzero extractor exit raises ValueError.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    before = {p.name for p in out_dir.iterdir()}
    cmd = _extractor_cmd(extractor) + [str(pkg), str(out_dir)]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                            text=True)
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            _log(line.rstrip("\n"), log)
            if cancel is not None and cancel.is_set():
                proc.terminate()
                raise Cancelled(f"extraction of {Path(pkg).name} cancelled")
        rc = proc.wait()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    if rc != 0:
        raise ValueError(f"extractor failed on {Path(pkg).name} (exit {rc})")
    return _game_root(out_dir, before)


def parse_sfo(data):
    """param.sfo bytes -> {key: str}; bounds-checked, never executes."""
    if len(data) < 20:
        raise ValueError("param.sfo too short")
    magic, _version, keys, values, count = struct.unpack_from("<4sIIII", data, 0)
    if magic != b"\0PSF":
        raise ValueError("bad SFO signature")
    if count > 4096 or 20 + count * 16 > len(data):
        raise ValueError("damaged SFO header")
    result = {}
    for i in range(count):
        key_off, fmt, size, _cap, data_off = struct.unpack_from("<HHIII", data, 20 + i * 16)
        key_at, val_at = keys + key_off, values + data_off
        if key_at >= len(data) or val_at + size > len(data):
            raise ValueError("damaged SFO entry")
        if fmt == 0x404 and size < 4:
            raise ValueError("damaged SFO integer field")
        end = data.index(0, key_at) if 0 in data[key_at:] else len(data)
        key = data[key_at:end].decode("ascii", "replace")
        raw = data[val_at:val_at + size]
        result[key] = struct.unpack("<I", raw[:4])[0] if fmt == 0x404 else raw.rstrip(b"\0").decode("utf8", "replace")
    return result


def _find_sfo(game_dir):
    for candidate in _SFO_CANDIDATES:
        path = Path(game_dir) / candidate
        if path.is_file():
            return path
    return None


def validate_sfo(game_dir, role):
    """The extracted SFO metadata for a base/update tree; else ValueError.

    Both packages must target CUSA13795; the base must be a game package
    (CATEGORY gd) and the update must be version 01.21.
    """
    sfo_path = _find_sfo(game_dir)
    if sfo_path is None:
        raise ValueError(f"{role}: no sce_sys/param.sfo in {game_dir}")
    try:
        info = parse_sfo(sfo_path.read_bytes())
    except (OSError, ValueError, IndexError, struct.error) as e:
        raise ValueError(f"{role}: unreadable param.sfo in {game_dir}: {e}")
    title = info.get("TITLE_ID", "?")
    if title != SUPPORTED_TITLE:
        raise ValueError(f"{role}: unsupported title {title} (need {SUPPORTED_TITLE})")
    if role == "base":
        if info.get("CATEGORY") != BASE_CATEGORY:
            raise ValueError(f"{role}: not a game package (CATEGORY {info.get('CATEGORY')!r})")
    elif role == "update":
        if info.get("APP_VER") != SUPPORTED_VERSION:
            raise ValueError(f"{role}: unsupported version {info.get('APP_VER', '?')} "
                             f"(need {SUPPORTED_VERSION})")
    else:
        raise ValueError(f"unknown role {role!r}")
    return info


def merge_and_check(base_dir, update_dir, merged_dir, repo_root=None,
                    merge_script=None, check_script=None, log=None, env=None):
    """Merge base+update into merged_dir, then gate on game_check.py."""
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    merge = str(merge_script) if merge_script is not None else str(root / "tools" / "merge_update.py")
    check = str(check_script) if check_script is not None else str(root / "scripts" / "game_check.py")
    proc = subprocess.run([sys.executable, merge, str(base_dir), str(update_dir), str(merged_dir)],
                          capture_output=True, text=True, stdin=subprocess.DEVNULL,
                          cwd=str(root), env=env)
    _log(proc.stdout.strip(), log)
    if proc.returncode != 0:
        raise ValueError(f"merge failed: {_last_line(proc.stdout + proc.stderr)}")
    proc = subprocess.run([sys.executable, check, str(merged_dir)],
                          capture_output=True, text=True, stdin=subprocess.DEVNULL,
                          cwd=str(root), env=env)
    _log(proc.stdout.strip(), log)
    if proc.returncode != 0:
        raise ValueError(f"game check failed: {_last_line(proc.stdout + proc.stderr)}")


def promote(merged_dir, dest_dir, name=INSTALL_DIR_NAME):
    """Atomically rename the validated tree to a new install dir.

    Never overwrites: an existing directory raises FileExistsError so the
    caller picks a different destination.
    """
    target = Path(dest_dir) / name
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"{target} already exists; choose a different destination")
    try:
        os.rename(merged_dir, target)
    except FileExistsError:
        raise FileExistsError(f"{target} already exists; choose a different destination")
    except OSError as e:
        raise ValueError(f"could not promote {merged_dir} to {target}: {e}")
    return target


def read_pkg_sfo(pkg):
    """The cleartext param.sfo (entry 0x1000) of a PS4 PKG, or (None, reason)."""
    try:
        with open(pkg, "rb") as f:
            header = f.read(0x20)
    except OSError as e:
        return None, f"cannot read: {e}"
    if len(header) < 0x20 or header[:4] != b"\x7fCNT":
        return None, "not a PS4 package"
    (count,) = struct.unpack_from(">I", header, 0x10)
    (table,) = struct.unpack_from(">I", header, 0x18)
    if count == 0 or count > 4096:
        return None, "damaged package"
    try:
        with open(pkg, "rb") as f:
            f.seek(table)
            entries = f.read(count * 32)
            if len(entries) < count * 32:
                return None, "damaged package: truncated entry table"
            for i in range(count):
                entry = entries[i * 32:(i + 1) * 32]
                (eid,) = struct.unpack_from(">I", entry, 0)
                if eid != 0x1000:
                    continue
                (flags,) = struct.unpack_from(">I", entry, 8)
                if flags & 0x80000000:
                    return None, "param.sfo is encrypted"
                (offset,) = struct.unpack_from(">I", entry, 16)
                (size,) = struct.unpack_from(">I", entry, 20)
                if size > (1 << 20):
                    return None, "param.sfo too large"
                f.seek(offset)
                return parse_sfo(f.read(size)), ""
    except (OSError, ValueError, struct.error) as e:
        return None, f"damaged package: {e}"
    return None, "no param.sfo"


def is_matching_dlc(pkg):
    """A raw PKG the launcher would accept as this game's DLC, else reason."""
    sfo, reason = read_pkg_sfo(pkg)
    if sfo is None:
        return False, reason
    content = sfo.get("CONTENT_ID", "")
    label = content.rsplit("-", 1)[-1] if "-" in content else ""
    if sfo.get("TITLE_ID") != SUPPORTED_TITLE or sfo.get("CATEGORY") != DLC_CATEGORY or len(label) != 16:
        return False, f"not a {SUPPORTED_TITLE} DLC"
    return True, ""


_COPY_CHUNK = 1 << 20


def _copy_file_exclusive(src, dst):
    """Copy src to a newly created dst; never follows or replaces anything.

    Raises FileExistsError when dst already exists (nothing touched), OSError
    otherwise. A partially written dst is removed; only this call's own file
    is ever deleted, since O_EXCL guarantees it created the entry.
    """
    flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL
             | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
    dst_fd = os.open(dst, flags, 0o666)
    try:
        with open(src, "rb") as f:
            while True:
                chunk = f.read(_COPY_CHUNK)
                if not chunk:
                    break
                view = memoryview(chunk)
                while view:
                    view = view[os.write(dst_fd, view):]
        shutil.copystat(src, dst)
    except BaseException:
        os.close(dst_fd)
        try:
            os.unlink(dst)
        except OSError:
            pass
        raise
    os.close(dst_fd)


def copy_dlc_raw(dlc_pkgs, dlc_dir, log=None):
    """Copy matching DLC PKGs unchanged; never overwrite, never extract.

    Returns {"copied", "duplicates", "failed", "skipped"} name lists; copy
    problems are reported, never raised, so DLC status stays separate from
    the base/update install status.
    """
    dlc_dir = Path(dlc_dir)
    dlc_dir.mkdir(parents=True, exist_ok=True)
    report = {"copied": [], "duplicates": [], "failed": [], "skipped": []}
    for pkg in dlc_pkgs:
        pkg = Path(pkg)
        try:
            ok, reason = is_matching_dlc(pkg)
            if not ok:
                _log(f"DLC {pkg.name} skipped: {reason}", log)
                report["skipped"].append(pkg.name)
                continue
            target = dlc_dir / pkg.name
            if os.path.lexists(target):
                _log(f"DLC {pkg.name} already present, kept the existing file", log)
                report["duplicates"].append(pkg.name)
                continue
            try:
                _copy_file_exclusive(pkg, target)
            except FileExistsError:
                _log(f"DLC {pkg.name} already present, kept the existing file", log)
                report["duplicates"].append(pkg.name)
                continue
            report["copied"].append(pkg.name)
        except Cancelled:
            raise
        except Exception as e:  # defensive: DLC faults never abort the install
            _log(f"DLC {pkg.name} copy failed: {e}", log)
            report["failed"].append(pkg.name)
            continue
    return report


def install(pkg_dir, dest_dir, extractor=None, dlc_dir=None, log=None,
            cancel=None, repo_root=None, merge_script=None, check_script=None,
            env=None):
    """Full import: classify, extract, validate, merge, check, promote, DLC.

    Returns the new install directory. On any failure (or cancellation) this
    import's staging directory is removed and nothing else is touched: the
    source PKGs, existing installs and the previous game selection are
    unchanged.
    """
    pkg_dir, dest_dir = Path(pkg_dir), Path(dest_dir)
    if extractor is None:
        extractor = default_extractor()
    if isinstance(extractor, (str, Path)) and not Path(extractor).is_file():
        raise RuntimeError(f"pkg_extractor not found: {extractor}")
    stage = make_stage(dest_dir)
    _log(f"STAGING={stage}", log)

    def fail():
        shutil.rmtree(stage, ignore_errors=True)

    try:
        roles = classify(pkg_dir, extractor)
        if cancel is not None and cancel.is_set():
            raise Cancelled("import cancelled")
        base_out, update_out = stage / "base", stage / "update"
        base_root = extract_stage(extractor, roles["base"][0], base_out, log=log, cancel=cancel)
        update_root = extract_stage(extractor, roles["update"][0], update_out, log=log, cancel=cancel)
        validate_sfo(base_root, "base")
        validate_sfo(update_root, "update")
        merged = stage / "merged"
        merge_and_check(base_root, update_root, merged, repo_root=repo_root,
                        merge_script=merge_script, check_script=check_script,
                        log=log, env=env)
        target = promote(merged, dest_dir)
        report = copy_dlc_raw(roles["dlc"],
                              dlc_dir if dlc_dir is not None else dest_dir / "dlc",
                              log=log)
        _log(f"DLC: {len(report['copied'])} copied, "
             f"{len(report['duplicates'])} already present, "
             f"{len(report['failed'])} failed, "
             f"{len(report['skipped'])} skipped", log)
    except BaseException:
        fail()
        raise
    shutil.rmtree(stage, ignore_errors=True)
    _log(f"MERGED_DIR={target}", log)
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pkg_dir", type=Path)
    parser.add_argument("dest_dir", type=Path)
    parser.add_argument("--extractor", nargs="+",
                        help="extractor binary, optionally with fixed leading args")
    parser.add_argument("--dlc-dir", type=Path, default=None)
    parser.add_argument("--merge-script", type=Path, default=None)
    parser.add_argument("--check-script", type=Path, default=None)
    args = parser.parse_args(argv)
    extractor = None
    if args.extractor:
        extractor = args.extractor[0] if len(args.extractor) == 1 else args.extractor
    try:
        install(args.pkg_dir, args.dest_dir, extractor=extractor,
                dlc_dir=args.dlc_dir, merge_script=args.merge_script,
                check_script=args.check_script)
    except (ValueError, FileExistsError, RuntimeError, OSError, Cancelled) as e:
        print(f"ERROR: {e}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
