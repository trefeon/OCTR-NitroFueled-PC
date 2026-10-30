// SPDX-License-Identifier: GPL-2.0-or-later
#include <cstdlib>
#include <chrono>
#include <cstdint>
#include <cstddef>
#include <sys/types.h>
#include <setjmp.h>
#if defined(_WIN32) && !defined(sigjmp_buf)
// Windows (gpu/shim/bbport_toggles.h, src/compat_win.h): no signals; the loader
// resumes faults in bb_longjmp. Tests never longjmp, they just need the type.
typedef unsigned long long sigjmp_buf[32];
#endif
// Renderer tests have no guest process. Clock/host-thread services work; guest accesses abort.
extern "C" {
thread_local sigjmp_buf* runtime_fault_recover = nullptr;
uint32_t runtime_disabled_optimizations = 0;
uint64_t runtime_experiment_bits = 0;
uint64_t runtime_tsc_frequency() { return 1000000000; }
int runtime_file_translate(const char*, char*, size_t) { std::abort(); }
uint64_t runtime_memory_clamp(uintptr_t, uint64_t) { std::abort(); }
uint64_t runtime_process_time_us() { return std::chrono::duration_cast<std::chrono::microseconds>(std::chrono::steady_clock::now().time_since_epoch()).count(); }
int runtime_memory_region(uintptr_t, uintptr_t*, uintptr_t*, int*) { std::abort(); }
void runtime_memory_set_gpu_hooks(void (*)(uintptr_t, uint64_t),
    void (*)(uintptr_t, uint64_t), void (*)(uintptr_t, uint64_t)) { std::abort(); }
void runtime_thread_attach_host(const char*) {}
void runtime_memory_gpu_protect(uintptr_t, uint64_t, int, int) { std::abort(); }
void runtime_restart() { std::abort(); }
int runtime_cinematic_mode() { return 0; }
uint64_t runtime_process_time_counter() { return runtime_process_time_us() * 1000; }
int32_t* runtime_errno() { std::abort(); }
int runtime_memory_write_backing(uintptr_t, const void*, uint64_t) { std::abort(); }
void runtime_memory_read_backing(uintptr_t, void*, uint64_t) { std::abort(); }
const uint64_t* runtime_memory_generation() { static const uint64_t generation = 0; return &generation; }
void runtime_memory_note_write(uintptr_t, uint64_t) { std::abort(); }
void runtime_memory_set_note_write_hook(void (*)(uintptr_t, uint64_t)) {}
void runtime_memory_set_cpu_write_hook(void (*)(uintptr_t, uint64_t)) {}
void runtime_memory_set_write_watch(uintptr_t, uint64_t, int) { std::abort(); }
// Detile/tile paths link against the overlay and heap-site helpers; tests never
// upload guest images, so mapped queries say no and friend actions abort.
int runtime_memory_is_mapped(uintptr_t, uint64_t) { return 0; }
struct RfView;
void runtime_friends_start() {}
unsigned runtime_friends_serial() { return 0; }
void runtime_friends_view(RfView*) { std::abort(); }
void runtime_friends_action(const char*, const char*) { std::abort(); }
void runtime_friends_search(const char*) { std::abort(); }
void runtime_friends_report(const char*, const char*, const char*) { std::abort(); }
void runtime_friends_set_hidden(int) {}
void runtime_friends_join(const char*) { std::abort(); }
int runtime_friends_take_event(char*) { return 0; }
int runtime_friends_take_message(char*) { return 0; }
const char* runtime_friends_names(unsigned* serial) {
    if (serial) {
        *serial = 0;
    }
    return "";
}
int runtime_friends_invite_online() { return 0; }
// TAA/scene/camera tests pull the rasterizer and page manager: guest-memory and
// fault-recovery helpers abort (no guest process), plain state is zeroed.
int bb_setjmp(unsigned long long[32]) { return 0; }
ssize_t compat_read_own_memory(void*, const void*, size_t) { std::abort(); }
void (*runtime_thread_started_hook)(const char*) = nullptr;
int runtime_graphics_tab_ready = 0;
struct PingPeer;
int runtime_net_ping_view(PingPeer*, int) { return 0; }
void runtime_memory_set_guest_chunk_allocator(int (*)(uint64_t, uint64_t)) {}
void runtime_memory_set_guest_chunk_whole(int) {}
int runtime_memory_vma_info(uintptr_t, int*, int*, uintptr_t*) { std::abort(); }
int runtime_memory_direct_phys(uintptr_t, uint64_t*, uintptr_t*) { std::abort(); }
void runtime_wait_report(double) {}
void runtime_sleep_stats(uint64_t* calls, uint64_t* ns) { *calls = 0; *ns = 0; }
uint64_t runtime_heap_growths() { return 0; }
}

