// SPDX-License-Identifier: GPL-2.0-or-later
// Hardware-matched recommended settings: the pure tier mapping the Windows launcher
// applies (launcher/windows/launcher.cpp). Portable on purpose: the focused test
// (tests/test_hw_recommend.cpp) builds it on any host, no Windows headers.
#pragma once

#include <algorithm>
#include <cctype>
#include <cstdint>
#include <string>

namespace HwRecommend {

// Tier table (documented choice):
// - fps cap = min(panel Hz, 60). The game was built to run at 60 with the community
//   60 FPS patch (patches/CrashTeamRacing.xml, "60 FPS Unlock"); above 60 the game
//   logic itself speeds up, so the cap never exceeds 60 and Unlimited is never picked.
// - render_res follows the renderer's own paths (docs/sacredfire/13-resolution.md,
//   scripts/patches.py render_plan, gpu/shim/bbport_settings.h RenderResHeights):
//   720p/480p drop the PS4 Pro path's output doubling (picture stays 1920x1080),
//   1080p is the base PS4 path, above 1080p the Pro path renders at 4K output.
//   Tiers: <4 GiB VRAM -> 720p (no Pro path, smallest targets), 4-8 GiB -> 1080p
//   (base path, as the game shipped), >8 GiB -> 1440p (the Pro's own scale).
//   Native 4K (2160p) is never picked automatically: it needs the larger GPU heap
//   patch and scales past what mid GPUs hold. 1620p is left for manual choice.
// - temporal: DLSS only on an RTX card with the DLSS bridge present
//   (bin/sacredfire_dlss.dll + bin/nvngx_dlss.dll, dlss_bridge/); otherwise FSR 1,
//   which runs on every GPU including the native renderer (FSR 3/DLSS do not).
// - async_pipelines/async_submit on (bbport defaults, run.py forwards async_submit
//   to BB_ASYNC_SUBMIT); present_mode Mailbox (lowest latency vsync'd mode).
// Unknown hardware falls back to the safe end: vram 0 -> 720p, hz <= 0 -> 60.

constexpr std::uint64_t kGiB = std::uint64_t{1} << 30;

inline bool IsRtxName(const std::string& gpu_name) {
    std::string lower = gpu_name;
    std::transform(lower.begin(), lower.end(), lower.begin(),
                   [](unsigned char c) { return char(std::tolower(c)); });
    return lower.find("rtx") != std::string::npos;
}

// Never 0 (Unlimited): unknown panels (hz <= 0) read as 60.
inline int RecommendedFpsLimit(int panel_hz) {
    const int hz = panel_hz <= 0 ? 60 : panel_hz;
    return hz < 60 ? hz : 60;
}

inline const char* RecommendedRenderRes(std::uint64_t vram_bytes) {
    if (vram_bytes < 4 * kGiB) return "720";
    if (vram_bytes <= 8 * kGiB) return "1080";
    return "1440";
}

inline const char* RecommendedTemporal(bool is_rtx, bool dlss_bridge_present) {
    return (is_rtx && dlss_bridge_present) ? "dlss" : "fsr1";
}

struct Recommendation {
    int fps_limit = 60;
    const char* render_res = "1080";
    const char* temporal = "fsr1";
};

inline Recommendation RecommendForHardware(std::uint64_t vram_bytes, int panel_hz, bool is_rtx,
                                           bool dlss_bridge_present) {
    Recommendation r;
    r.fps_limit = RecommendedFpsLimit(panel_hz);
    r.render_res = RecommendedRenderRes(vram_bytes);
    r.temporal = RecommendedTemporal(is_rtx, dlss_bridge_present);
    return r;
}

}  // namespace HwRecommend
