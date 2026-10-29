# shadPS4Plus PKG extractor pin (CTR import)

> Canonical versioned copy: `third_party/shadps4plus-pkg.README.CTR.md`
> (committed in the superproject). A byte-identical mirror is placed at
> `third_party/shadps4plus-pkg/README.CTR.md` in checked-out trees for
> convenience; the mirror is **not** versioned because git cannot commit a
> file under a submodule path from the superproject. The reproducible pin is
> the superproject gitlink plus the `.gitmodules` entry (see below).

## Baseline

- Upstream: <https://github.com/trefeon/shadPS4Plus.git>, branch `ShadPs4Plus`
  (the brief's `-b main` does not exist upstream; `ShadPs4Plus` is the
  repository's default branch).
- Pinned commit: `9d2e76127ce32d13192f9a6ab84a96404677394a`
  (`make batch_install handle spaces`).
- Superproject records this as:
  `[submodule "third_party/shadps4plus-pkg"]` in `.gitmodules` with the
  gitlink `160000 9d2e7612... third_party/shadps4plus-pkg`.
- Initialise with: `git submodule update --init --recursive`
  (`--recursive` is required for the nested `externals/cryptopp` checkout
  the extractor links against).

## Owned patch

- `patches/pkg-extractor-0x403-tail.patch` (superproject, committed) is
  applied to this checkout at configure time by `gpu/CMakeLists.txt`
  (idempotent check-then-apply; same pattern `build.sh` uses for FSR-Vulkan).
- Purpose: bounds-safe handling of the non-AES-block-aligned `0x403`
  (`npbind.dat`, observed `0x214` bytes = `0x210` full blocks + 4 tail bytes)
  seen in the CUSA13795 base PKG. The baseline rounded the cipher span **up**
  past the end of the data buffer; the patch decrypts the largest aligned
  prefix with the existing CBC routine and carries the short tail through
  verbatim. The alignment guard is kept for every other encrypted NP entry.
- Touches `src/core/file_format/pkg.cpp` (header/table/entry bounds checks
  plus the 0x403 tail-safe decrypt), `src/core/crypto/crypto.{h,cpp}`
  (tail-safe decrypt helper), `extractor/main.cpp` (exit 1 on every
  invalid/unreadable path; check-type 101/102/103 and success 0 preserved),
  and `src/core/loader.cpp` (unreadable magic is Unknown, never UB).
  No other upstream files are carried.

## License

shadPS4Plus sources are GPL-2.0-or-later; all upstream
`SPDX-FileCopyrightText` / `SPDX-License-Identifier` notices are retained.
The Windows portable package bundles `pkg_extractor.exe` with its required
`zlib1.dll`, `libc++.dll`, and the upstream license notices.

## Untouched worktree

The separate `D:/github_repo/shadPS4Plus` checkout is read-only input for
review. It is never modified or committed by this repository; this pin plus
the owned patch above are the only dependency carried here.
