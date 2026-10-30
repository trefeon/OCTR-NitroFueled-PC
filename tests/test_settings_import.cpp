// Windows launcher settings import plus binding serialization and DLC path resolution. Build:
// ninja -C out/gpu-win settings-import-test; run out/gpu-win/settings-import-test.exe.
#include "settings_import.h"
#include "launcher_bindings.h"
#include "launcher_paths.h"

#include <windows.h>

#include <chrono>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <sstream>
#include <string>

namespace fs = std::filesystem;

static int failures = 0;
#define CHECK(cond)                                                                \
    do {                                                                           \
        if (!(cond)) {                                                             \
            std::fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);   \
            ++failures;                                                            \
        }                                                                          \
    } while (0)

static void Write(const fs::path& p, const std::string& text) {
    fs::create_directories(p.parent_path());
    std::ofstream(p, std::ios::binary) << text;
}
static std::string Read(const fs::path& p) {
    std::ifstream in(p, std::ios::binary);
    std::stringstream ss;
    ss << in.rdbuf();
    return ss.str();
}

int main() {
    wchar_t temp[MAX_PATH];
    GetTempPathW(MAX_PATH, temp);
    const fs::path base = fs::path(temp) / L"ctr-settings-import-test";
    fs::remove_all(base);
    // Downloads\CTR-NitroFueled-PC-portable-v0.5.2-windows\CTR-NitroFueled-PC-portable (played)
    // and the same for v0.6 (just unpacked), plus an older one that the list knows (ñ in the path).
    const fs::path old_install = base / L"CTR-NitroFueled-PC-portable-v0.5.2-windows" / L"CTR-NitroFueled-PC-portable";
    const fs::path listed = base / L"Juegos ñ" / L"octr";
    const fs::path fresh = base / L"CTR-NitroFueled-PC-portable-v0.6-windows" / L"CTR-NitroFueled-PC-portable";
    const fs::path installs = base / L"appdata" / L"installs.txt";
    Write(old_install / L"run.py", "");
    Write(old_install / L"bbport.ini", "key.cross=Mouse Left\nonline_name=ceffe\n");
    Write(old_install / L"launcher.ini", "game_dir=D:\\CTR\\CUSA13795-1.21\n");
    Write(old_install / L"ryunet_online.key", std::string(64, 'a') + "\n");
    Write(old_install / L"user" / L"savedata" / L"1" / L"CUSA13795" / L"SAVE" / L"data", "save");
    Write(old_install / L"user" / L"trophies" / L"CUSA13795" / L"unlocked.txt", "1,2,3");
    Write(old_install / L"user" / L"cache" / L"CUSA13795" / L"shaders.bin", "big");
    Write(old_install / L"dlc" / L"pack" / L"file", "dlc");
    Write(old_install / L"bin" / L"sacredfire.exe", "the old program");
    Write(listed / L"run.py", "");
    Write(listed / L"launcher.ini", "game_dir=E:\\x\n");
    Write(fresh / L"run.py", "");
    Write(fresh / L"bin" / L"sacredfire.exe", "the new program");
    // The played one was used last.
    fs::last_write_time(listed / L"launcher.ini", fs::file_time_type::clock::now() - std::chrono::hours(48));

    using namespace SettingsImport;
    CHECK(IsUsedInstall(old_install) && IsUsedInstall(listed) && !IsUsedInstall(fresh));
    SettingsImport::Remember(installs, listed);
    SettingsImport::Remember(installs, listed);  // once
    SettingsImport::Remember(installs, fresh);
    CHECK(Read(installs).find("Juegos") != std::string::npos);
    std::size_t lines = 0;
    for (char c : Read(installs)) lines += c == '\n';
    CHECK(lines == 2);

    const auto previous = Previous(installs, fresh);
    CHECK(previous.size() == 2);
    CHECK(previous.size() == 2 && fs::equivalent(previous[0], old_install));  // the last played first
    CHECK(previous.size() == 2 && fs::equivalent(previous[1], listed));

    const auto items = Items(old_install);
    auto has = [&](const wchar_t* p) {
        for (const auto& i : items)
            if (i.lexically_normal() == fs::path(p).lexically_normal()) return true;
        return false;
    };
    CHECK(has(L"bbport.ini") && has(L"launcher.ini") && has(L"ryunet_online.key"));
    CHECK(has(L"user/savedata/1/CUSA13795/SAVE/data") && has(L"user/trophies/CUSA13795/unlocked.txt"));
    CHECK(has(L"dlc/pack/file"));
    CHECK(!has(L"user/cache/CUSA13795/shaders.bin") && !has(L"bin/sacredfire.exe") && !has(L"run.py"));

    CHECK(Copy(old_install, fresh).empty());
    CHECK(Read(fresh / L"bbport.ini") == "key.cross=Mouse Left\nonline_name=ceffe\n");
    CHECK(Read(fresh / L"user" / L"savedata" / L"1" / L"CUSA13795" / L"SAVE" / L"data") == "save");
    CHECK(Read(fresh / L"bin" / L"sacredfire.exe") == "the new program");
    CHECK(!fs::exists(fresh / L"user" / L"cache"));
    // The new one is a used installation now, and the old one stays as it was.
    CHECK(IsUsedInstall(fresh) && Read(old_install / L"dlc" / L"pack" / L"file") == "dlc");

    CHECK(LauncherBindings::AppendPadBinding("a", "b") == "a,b");
    CHECK(LauncherBindings::AppendPadBinding("a,b", "a") == "a,b");
    CHECK(LauncherBindings::AppendPadBinding("a, b, a", "c") == "a,b,c");
    CHECK(LauncherBindings::AppendPadBinding("a,b,c,d", "e") == "a,b,c,d");
    CHECK(LauncherBindings::CaptureKeyboardBinding("default", "x", true) == "x");
    CHECK(LauncherBindings::CaptureKeyboardBinding("a,b", "c", false) == "a,b,c");
    CHECK(LauncherBindings::CaptureKeyboardBinding("a,b", "b", false) == "a,b");
    CHECK(LauncherBindings::CaptureKeyboardBinding("a,b,c,d", "e", false) == "b,c,d,e");

    const fs::path launcher_root = (fs::current_path() / L"launcher-root").lexically_normal();
    CHECK(LauncherPaths::ResolveDlcDirectory(launcher_root, fs::path{}) == (launcher_root / L"dlc").lexically_normal());
    CHECK(LauncherPaths::ResolveDlcDirectory(launcher_root, fs::path(L"../shared/dlc")) ==
          (launcher_root.parent_path() / L"shared" / L"dlc").lexically_normal());
    const fs::path absolute_dlc = (launcher_root / L"other" / L".." / L"dlc").lexically_normal();
    CHECK(LauncherPaths::ResolveDlcDirectory(launcher_root, absolute_dlc) == absolute_dlc);
    CHECK(LauncherPaths::AbsoluteNormalizedDlcDirectory(launcher_root, fs::path(L"custom/../dlc")) ==
          (launcher_root / L"dlc").lexically_normal());
    CHECK(LauncherPaths::PersistedDlcDirectory(launcher_root, fs::path{}).empty());
    CHECK(LauncherPaths::PersistedDlcDirectory(launcher_root, fs::path(L"custom/../dlc")) ==
          (launcher_root / L"dlc").lexically_normal());
    CHECK(LauncherPaths::PersistedDlcDirectory(launcher_root, absolute_dlc) == absolute_dlc);

    fs::remove_all(base);
    if (failures) {
        std::fprintf(stderr, "%d failures\n", failures);
        return 1;
    }
    std::puts("PASS: launcher settings import, bindings and DLC path resolution");
    return 0;
}
