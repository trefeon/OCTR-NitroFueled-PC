// SPDX-License-Identifier: GPL-2.0-or-later
// Tiling compute dispatches must stay within the Vulkan groupCountX limit while
// processing every texel exactly once (no clamping/dropping). Exercises the
// production splitter in tile_manager.h; needs no Vulkan device or game image.
#include <cstdio>
#include <vector>

#include "video_core/texture_cache/tile_manager.h"

namespace {

int failures = 0;

#define CHECK(cond)                                                                          \
    do {                                                                                     \
        if (!(cond)) {                                                                       \
            std::printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);                       \
            ++failures;                                                                      \
        }                                                                                    \
    } while (0)

// Every chunk is legal and the chunks tile [0, dim_x*64) contiguously, exactly once.
void CheckCoverage(u32 dim_x, const std::vector<VideoCore::TilingDispatch>& chunks, u32 max) {
    u64 covered = 0;
    for (size_t i = 0; i < chunks.size(); ++i) {
        const auto& c = chunks[i];
        CHECK(c.group_count <= max);
        CHECK(c.group_count > 0);
        CHECK(c.base_texel == covered);
        covered += u64(c.group_count) * VideoCore::kTilingThreadsPerGroup;
    }
    CHECK(covered == u64(dim_x) * VideoCore::kTilingThreadsPerGroup);
}

void CheckSingle(u32 dim_x) {
    const auto chunks = VideoCore::SplitTilingDispatch(dim_x);
    CHECK(chunks.size() == 1);
    CHECK(chunks[0].base_texel == 0);
    CHECK(chunks[0].group_count == dim_x);
    CheckCoverage(dim_x, chunks, VideoCore::kMaxTilingGroupsX);
}

} // namespace

int main() {
    using namespace VideoCore;

    CHECK(kMaxTilingGroupsX == 65536);

    // Fast path: fits in one dispatch, base zero.
    CheckSingle(1);
    CheckSingle(65535);
    CheckSingle(65536);

    // Just over the limit: two dispatches, nothing dropped.
    {
        const auto chunks = SplitTilingDispatch(65537);
        CHECK(chunks.size() == 2);
        CHECK(chunks[0].group_count == 65536);
        CHECK(chunks[0].base_texel == 0);
        CHECK(chunks[1].group_count == 1);
        CHECK(chunks[1].base_texel == 65536u * kTilingThreadsPerGroup);
        CheckCoverage(65537, chunks, kMaxTilingGroupsX);
    }

    // Regression case from validation-layer VUID: 69120 > 65536 must split, not clamp.
    {
        const auto chunks = SplitTilingDispatch(69120);
        CHECK(chunks.size() == 2);
        CHECK(chunks[0].group_count == 65536);
        CHECK(chunks[1].group_count == 69120 - 65536);
        for (const auto& c : chunks) {
            CHECK(c.group_count <= 65536);
        }
        CheckCoverage(69120, chunks, kMaxTilingGroupsX);
    }

    // Exact multiples and larger spans stay contiguous.
    {
        const auto chunks = SplitTilingDispatch(2 * 65536);
        CHECK(chunks.size() == 2);
        CheckCoverage(2 * 65536, chunks, kMaxTilingGroupsX);
    }
    CheckCoverage(200000, SplitTilingDispatch(200000), kMaxTilingGroupsX);

    // Custom device limit (e.g. strict 65535 hardware) is honored.
    {
        const auto chunks = SplitTilingDispatch(7, 3);
        CHECK(chunks.size() == 3);
        CHECK(chunks[0].group_count == 3 && chunks[0].base_texel == 0);
        CHECK(chunks[1].group_count == 3 && chunks[1].base_texel == 3 * kTilingThreadsPerGroup);
        CHECK(chunks[2].group_count == 1 && chunks[2].base_texel == 6 * kTilingThreadsPerGroup);
        CheckCoverage(7, chunks, 3);
    }
    CheckCoverage(69120, SplitTilingDispatch(69120, 65535), 65535);

    if (failures == 0) {
        std::printf("tiling-dispatch-test: all checks passed\n");
        return 0;
    }
    std::printf("tiling-dispatch-test: %d check(s) failed\n", failures);
    return 1;
}
