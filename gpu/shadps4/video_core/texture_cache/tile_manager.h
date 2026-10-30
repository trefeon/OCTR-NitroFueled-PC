// SPDX-FileCopyrightText: Copyright 2024 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <algorithm>
#include <cstdint>
#include <unordered_map>
#include <utility>
#include <vector>

#include <boost/container_hash/hash.hpp>

#include "common/types.h"
#include "video_core/amdgpu/tiling.h"
#include "video_core/buffer_cache/buffer.h"

namespace Vulkan {
struct Runtime;
}

namespace VideoCore {

struct ImageInfo;
struct Image;

// Threads per tiling compute group (tiling.comp local_size_x). One group processes
// 64 texels, so a dispatch of N groups covers N*64 texels starting at its base.
inline constexpr u32 kTilingThreadsPerGroup = 64;
// Upper bound for a single tiling vkCmdDispatch groupCountX. Vulkan only guarantees
// maxComputeWorkGroupCount[0] >= 65535, so larger dispatches are split into several
// legal dispatches with a base-texel push constant (never clamped/dropped).
// Always use through min(device_max, kMaxTilingGroupsX) at the dispatch site;
// never use this constant raw as a dispatch size.
inline constexpr u32 kMaxTilingGroupsX = 65536;

struct TilingDispatch {
    u32 base_texel = 0;
    u32 group_count = 0;
};

// Split dim_x groups into legal dispatches covering every texel exactly once.
// Fast path: dim_x <= max_groups returns a single dispatch.
// Header-inline so unit tests exercise the exact production logic without linking
// the GPU backend.
inline std::vector<TilingDispatch> SplitTilingDispatch(u32 dim_x,
                                                       u32 max_groups = kMaxTilingGroupsX) {
    if (max_groups == 0) {
        max_groups = kMaxTilingGroupsX;
    }
    std::vector<TilingDispatch> out;
    if (dim_x <= max_groups) {
        out.push_back({.base_texel = 0, .group_count = dim_x});
        return out;
    }
    u32 groups_done = 0;
    while (groups_done < dim_x) {
        const u32 count = std::min(dim_x - groups_done, max_groups);
        out.push_back({.base_texel = groups_done * kTilingThreadsPerGroup,
                       .group_count = count});
        groups_done += count;
    }
    return out;
}

class TileManager {
    struct TilingKey {
        AmdGpu::TileMode tile_mode;
        u32 num_bits;
        u32 num_samples;
        bool is_tiler;

        bool operator==(const TilingKey&) const = default;

        struct Hash {
            size_t operator()(const TilingKey& key) const {
                size_t hash = 0;
                boost::hash_combine(hash, key.tile_mode);
                boost::hash_combine(hash, key.num_bits);
                boost::hash_combine(hash, key.num_samples);
                boost::hash_combine(hash, key.is_tiler);
                return hash;
            }
        };
    };

public:
    explicit TileManager(const Vulkan::Instance& instance, Vulkan::Scheduler& scheduler,
                         Vulkan::Runtime& runtime, StreamBuffer& stream_buffer);
    ~TileManager();

    void TileImage(Image& in_image, std::span<vk::BufferImageCopy> buffer_copies,
                   const VideoCore::Buffer* out_buffer, u64 out_offset);

    std::pair<const Buffer*, u64> DetileImage(const VideoCore::Buffer* in_buffer, u64 in_offset,
                                              const ImageInfo& info);

private:
    void RecordTilingDispatch(vk::Pipeline pipeline, const vk::DescriptorBufferInfo& tiled,
                              const vk::DescriptorBufferInfo& linear,
                              const vk::DescriptorBufferInfo& params, u32 dim_x);

    vk::Pipeline GetTilingPipeline(const ImageInfo& info, bool is_tiler);

private:
    const Vulkan::Instance& instance;
    Vulkan::Scheduler& scheduler;
    Vulkan::Runtime& runtime;
    StreamBuffer& stream_buffer;
    u32 max_tiling_groups_x = kMaxTilingGroupsX;
    vk::UniqueDescriptorSetLayout desc_layout;
    vk::UniquePipelineLayout pl_layout;
    std::unordered_map<TilingKey, vk::UniquePipeline, TilingKey::Hash> tiling_pipelines;
};

} // namespace VideoCore
