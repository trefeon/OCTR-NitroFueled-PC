# SacredFire on Windows

SacredFire builds and runs natively on 64-bit Windows 10 (1903+) and 11 with the MSYS2 CLANG64
toolchain, from the same sources as the Linux build. The Windows layer (loader, memory, TLS,
exceptions, files, timers) comes from Supermedo's Windows port of bbport
([Supermedo/bloodborne_pc](https://github.com/Supermedo/bloodborne_pc), branch `windows`),
merged into SacredFire. Tested on Windows 11 24H2 with an NVIDIA RTX 3070.

## Build

Install [MSYS2](https://www.msys2.org) into `C:\msys64`, open the **MSYS2 CLANG64** shell, run
`pacman -Syu` (twice if it closes the window) and install the toolchain and libraries:

```bash
pacman -S --needed git patch \
  mingw-w64-clang-x86_64-{toolchain,cmake,ninja,pkgconf,python} \
  mingw-w64-clang-x86_64-{vulkan-headers,vulkan-loader,vulkan-memory-allocator,glslang} \
  mingw-w64-clang-x86_64-{sdl3,fmt,boost,robin-map,xxhash,ffmpeg,zydis} \
  mingw-w64-clang-x86_64-{spirv-tools,spirv-headers,spirv-cross}
```

magic_enum, miniz and xbyak (not packaged by MSYS2) are submodules under `gpu/third_party/`.
Then, in the repository (CLANG64 shell):

```bash
git submodule update --init --recursive
bash build.sh                      # portable defaults; out/sacredfire.exe, out/bb-gpu-capabilities.exe
```

For a local build optimized for an Intel Core i7-11800H, opt into Tiger Lake CPU tuning:

```bash
BB_TARGET_CPU=tigerlake BB_LTO=ON bash build.sh
```

This target-specific build is for this CPU, not for portable release assets; leave `BB_TARGET_CPU`
unset for release builds that must run across many CPUs.

## Game files

As on Linux: your own dump of CUSA13795 with the 1.21 update merged in. With the base game and
the update dumped to two folders, `tools/merge_update.py` makes the merged folder out of hard
links (no copy, the dumps stay untouched; same drive):

```bash
python tools/merge_update.py <path>/CUSA13795 <path>/CUSA13795-patch <path>/CUSA13795-1.21
```

## Launcher

`CTR-NitroFueled-PC.exe` (`launcher/windows/launcher.cpp`, target `ctr-launcher`; Dear ImGui on
Win32 + Direct3D 11, linked statically, no DLLs) is what players open:

- **Play**: base game and 1.21 update folders (told apart by `param.sfo`), merged with hard links
  into `CUSA13795-1.21`, checked with `scripts/game_check.py`; starts `run.py` with the bundled
  Python in a job object and shows its output. **Import PKG folder…** takes a folder with one
  CUSA13795 base PKG, one 1.21 update PKG and optional matching DLC PKGs instead: pick that one
  source folder (file names do not matter) and the launcher first shows a pre-flight review —
  one row per package with its role in green or red — plus a *How to prepare…* guide popup.
  *Import* classifies each package with the bundled `out\pkg_extractor.exe` (`--check-type`),
  extracts base and update into a unique `stage-*` directory on the library volume (determinate
  file progress, streamed log, cancellable), validates both `param.sfo` trees (CUSA13795; update
  `01.21`), merges them into the staging tree, gates on `scripts/game_check.py`, then atomically
  renames the validated tree to a new unused `CUSA13795-1.21` install, selects it and reports
  *Game ready*. The library defaults to `<launcher root>\games` (`Change...` picks another
  folder); existing installs are never overwritten, and failure or cancel removes only that
  import's staging and leaves the current game folder unchanged. Source PKGs are read-only inputs.
  A first run in a fresh folder shows a one-time setup wizard that creates `games/` and `dlc/`
  next to the launcher.
- **Settings**: FPS limit, background shader compiles, fullscreen, FPS counter (`bbport.ini`, the
  in-game menu's keys); monitor, present mode, controller, game language, PS4 Pro mode, and the DLC
  folder (`launcher.ini`, passed as `BB_*` variables). The Gamepad tab can bind up to four buttons
  to one action; Change adds a distinct button, while Clear removes the full custom binding.
- **Online**: Speed Mode (Experimental), stored in `bbport.ini`. Every player in the private
  room needs the same build with Speed enabled. Adventure and Arcade keep their normal item
  and Wumpa boxes. The in-game menu has the same switch under Room; it applies to the next
  private race without restarting because both archive views are prepared at launch.
- **Patches**: every patch for CUSA13795 01.21 in `patches\*.xml`; the three community patches
  keep their `bbport.ini` keys, others go through `patches.json`.
- **Saves**: backups to `backups\`, restore, export, import of another PC's saves or a decrypted
  PS4 save (`sce_sys\param.sfo`, converted to the runtime's `<dir>` + `<dir>.sce_sys\param.bin`).
- **DLC** (optional): the player's own add-on packages (`.pkg`) copied into the selected folder;
  configure it in the DLC tab and `launcher.ini` as `dlc_dir`. Missing or empty values keep the
  default `<launcher root>\dlc`; relative values resolve against the launcher root, and the launcher
  passes the resolved path to `run.py` as `BB_DLC_DIR`. Matching CUSA13795 DLC from an Import PKG
  folder is copied raw (never extracted, never overwritten): existing files stay untouched and
  copies report duplicates separately. `scripts/content_profile.py` reads the
  entitlement labels (package header and `param.sfo`; nothing is extracted because CTR Nitro-Fueled's
  DLC only carry the license) into `content.bin` (`BBCONT02`), and the runtime reports them installed
  from `sceAppContentGetAddcontInfoList`. `BB_DLC=0` / `BB_DLC_DISABLED=label,...` turn them off.

A test build with `-DLAUNCHER_SELFTEST` runs the file operations from the command line
(`--import`, `--restore`, `--patches`, `--dlc`).

## Run

`run.py` is the Windows `run.sh` (same variables; MSYS2's Python or a python.org Python):

```bat
set BB_GAME_DIR=C:\Games\CUSA13795-1.21
C:\msys64\clang64\bin\python.exe run.py
```

On CPUs without MOVBE, BMI1 or LZCNT (Sandy Bridge, Ivy Bridge) the Python that runs `run.py`
also needs `iced-x86` for the start-up rewrite (15-isa-rewrite.md): install it in a python.org
Python 3 (`pip install iced-x86`) and run `run.py` with that one. MSYS2's Python has no compatible
wheel (its source build needs Rust); the portable ZIP bundles iced-x86 through
`make_portable.py`.

From a source tree it puts `C:\msys64\clang64\bin` (or `%MSYS2_ROOT%\clang64\bin`) on `PATH`
for the DLLs. The in-game menu (Insert, or L3+R3) works as on Linux; "Apply and restart" ends
the game with exit code 75 (`BB_RESTART_EXIT_CODE`) and `run.py` starts it again with the new
patches, in the same console. Every launch is logged to `last_run.log`.
Saves, the shader cache and `bbport.ini` go to `user/` and the repository folder (`BB_DATA_DIR`
moves them).

## Performance defaults

The portable defaults ship with background shader compiles and async submits on
(`packaging/windows/portable/bbport.default.ini`):
`async_pipelines=1` and `async_submit=1`. Unzipping over an existing install keeps
that install's saved `bbport.ini` keys, so existing players stay on their current
values until they toggle the setting or delete `bbport.ini` to regenerate defaults.

- `async_submit`: queue submissions go out from the recording threads once their
  segments are recorded, instead of the GPU thread waiting for them (~65 µs
  each). The renderer default is on too (`BB_ASYNC_SUBMIT=0` turns it off);
  `run.py` forwards the ini key to `BB_ASYNC_SUBMIT`. Expected effect: higher
  steady-state FPS with no visual change. `docs/CHANGES_2026-10-06.md` measured
  the stack that includes it (RX 7800 XT / RADV, Mesa 26, 1080p output, no FPS
  limit): 138.7 → 143.6 FPS on the old memory model, 101 → 152–155 FPS standing
  in Yahar'gul plus 95.5 → 115–124 FPS at a heavy ~2000-draw spot on the new one.
- `async_pipelines`: graphics pipelines of passes drawn every frame compile on
  worker threads while their draws go without them for a frame or two, instead
  of the game stalling on the driver (about 18 ms a pipeline on a GTX 1060, up
  to 1–4 s hitches in a first session). Expected effect: first-seen shader
  hitches go away; an effect may show up a frame late instead. No steady-state
  cost. `BB_ASYNC_PIPELINES=0` turns it off.

How to measure: set `show_fps=1` in `bbport.ini` (or the in-game menu) for the
corner counter, and capture a PresentMon session for the game process over a
repeatable run (same spot, same duration) to compare frame-time percentiles
with both keys at 0 versus 1.

## What is different from Linux

| Linux | Windows |
|---|---|
| `fs:` loads rewritten to `gs:[0]`, GS base set with `arch_prctl` | GS is the TEB: the loader rewrites them to `gs:[0x1480 + 8*slot]` (a TEB TLS slot, claimed first thing in `main`). |
| One memfd, `mmap(MAP_FIXED)` views | A pagefile-backed section; the PS4 range is reserved as a placeholder that views replace (`VirtualAlloc2`/`MapViewOfFile3`, as in shadPS4). |
| SIGSEGV handler, `sigsetjmp` recovery | Vectored exception handler; `bb_setjmp`/`bb_longjmp` (`src/compat_win.c`). |
| glibc sleeps, `CLOCK_MONOTONIC` condition variables | High-resolution waitable timers; semaphore and event flag deadlines on `CLOCK_REALTIME` (winpthreads rejects monotonic condition variables). |
| `libbbgpu.so` | A static `libbbgpu.a` linked into `sacredfire.exe`, with a manifest for UTF-8 and long paths. |
| `run.sh`, GTK launcher | `run.py` and the Windows launcher (`CTR-NitroFueled-PC.exe`, above). |
| Save files replaced with `rename` | `MoveFileEx(MOVEFILE_REPLACE_EXISTING)`; all files opened in binary mode. |

Not available on Windows: the PC memory model (`BB_PC_MODEL`/`BB_GUEST_IN_PLACE`, dma-buf guest
memory), userfaultfd tracking, the watchdog/`--timeout` thread dumps, `BB_SAMPLE_THREAD`,
`BB_FREE_CHECK`, `BB_WAIT_TRACE`, `BB_COPY_PROTECT`, MangoHud, PGO, the AppImage and the
`tests/` programs. These are Linux diagnostics or Linux-only mechanisms; the game itself does not
need them.

## Portable build

`packaging/windows/make_portable.py` makes the portable ZIP (the file in Releases) from a finished
build: `sacredfire.exe`, the DLLs it needs (found with `llvm-objdump`), the scripts
(`scripts\pkg_import.py` included), default `bbport.default.ini` (windowed, 60 FPS limit), launchers with
CRLF line endings, `LICENSE` and a `THIRD-PARTY.txt` that names the MSYS2 package and license of
every DLL. It also bundles the PKG import extractor: `out\pkg_extractor.exe` (where the launcher
expects it) with `out\zlib1.dll` and `out\libc++.dll`, plus the shadPS4Plus GPL notice and the
versioned pin record (`licenses\shadps4plus-pkg-LICENSE.txt`,
`licenses\shadps4plus-pkg.README.CTR.md`; the extractor sources plus
`patches\pkg-extractor-0x403-tail.patch` stay in this repository). The ZIP carries no PKGs, game
files, saves or `user/` data: the player's source packages remain read-only inputs for Import PKG
folder, and the game-library destination plus per-import `stage-*` directories live outside the
package. The default library ships inside the ZIP as an empty `games/` entry; the first import
(or the setup wizard) fills it in.

The [manual build guide](../../docs/MANUAL_BUILDS.md) covers the full packaging sequence and checksum check. Run the packager in the MSYS2 CLANG64 shell:

```bash
python3 packaging/windows/make_portable.py
```
