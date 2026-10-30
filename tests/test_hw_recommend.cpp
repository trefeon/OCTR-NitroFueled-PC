// SPDX-License-Identifier: GPL-2.0-or-later
// hw_recommend: the tier mapping behind the launcher's "Recommended for your hardware"
// section (launcher/windows/hw_recommend.h, launcher/windows/launcher.cpp).
#include <cassert>
#include <cstdio>
#include "hw_recommend.h"

using namespace HwRecommend;

int main() {
    // fps cap = min(panel Hz, 60); unknown panels read as 60; never Unlimited (0).
    assert(RecommendedFpsLimit(60) == 60);
    assert(RecommendedFpsLimit(144) == 60);
    assert(RecommendedFpsLimit(165) == 60);
    assert(RecommendedFpsLimit(240) == 60);
    assert(RecommendedFpsLimit(50) == 50);
    assert(RecommendedFpsLimit(30) == 30);
    assert(RecommendedFpsLimit(0) == 60);
    assert(RecommendedFpsLimit(-1) == 60);

    // VRAM tiers: <4 GiB -> 720p, 4-8 GiB -> 1080p, >8 GiB -> 1440p.
    assert(std::string(RecommendedRenderRes(0)) == "720");
    assert(std::string(RecommendedRenderRes(3 * kGiB)) == "720");
    assert(std::string(RecommendedRenderRes(4 * kGiB - 1)) == "720");
    assert(std::string(RecommendedRenderRes(4 * kGiB)) == "1080");
    assert(std::string(RecommendedRenderRes(6 * kGiB)) == "1080");
    assert(std::string(RecommendedRenderRes(8 * kGiB)) == "1080");
    assert(std::string(RecommendedRenderRes(8 * kGiB + 1)) == "1440");
    assert(std::string(RecommendedRenderRes(16 * kGiB)) == "1440");

    // RTX name check and DLSS bridge gating.
    assert(IsRtxName("NVIDIA GeForce RTX 3070"));
    assert(IsRtxName("NVIDIA GeForce RTX 3060 Laptop GPU"));
    assert(!IsRtxName("NVIDIA GeForce GTX 1060"));
    assert(!IsRtxName("AMD Radeon RX 7800 XT"));
    assert(!IsRtxName("Intel(R) UHD Graphics"));
    assert(!IsRtxName(""));
    assert(std::string(RecommendedTemporal(true, true)) == "dlss");
    assert(std::string(RecommendedTemporal(true, false)) == "fsr1");
    assert(std::string(RecommendedTemporal(false, true)) == "fsr1");
    assert(std::string(RecommendedTemporal(false, false)) == "fsr1");

    // The combined recommendation wires the three together.
    {
        const Recommendation r = RecommendForHardware(8 * kGiB, 165, true, true);
        assert(r.fps_limit == 60 && std::string(r.render_res) == "1080" &&
               std::string(r.temporal) == "dlss");
    }
    {
        // Unknown GPU/VRAM/panel: the safe end (720p, 60 FPS, FSR 1).
        const Recommendation r = RecommendForHardware(0, 0, false, false);
        assert(r.fps_limit == 60 && std::string(r.render_res) == "720" &&
               std::string(r.temporal) == "fsr1");
    }
    {
        const Recommendation r = RecommendForHardware(12 * kGiB, 60, false, true);
        assert(r.fps_limit == 60 && std::string(r.render_res) == "1440" &&
               std::string(r.temporal) == "fsr1");
    }

    std::puts("PASS: hardware recommendation tiers");
    return 0;
}
