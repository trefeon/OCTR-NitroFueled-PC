#pragma once

#include <filesystem>
#include <system_error>

namespace LauncherPaths {

inline std::filesystem::path ResolveDlcDirectory(const std::filesystem::path& launcher_root,
                                                 const std::filesystem::path& configured) {
    const std::filesystem::path path = configured.empty() ? std::filesystem::path(L"dlc") : configured;
    return (path.is_absolute() ? path : launcher_root / path).lexically_normal();
}

inline std::filesystem::path AbsoluteNormalizedDlcDirectory(const std::filesystem::path& launcher_root,
                                                            const std::filesystem::path& configured) {
    const std::filesystem::path resolved = ResolveDlcDirectory(launcher_root, configured);
    std::error_code ec;
    const std::filesystem::path absolute = std::filesystem::absolute(resolved, ec);
    return (ec ? resolved : absolute).lexically_normal();
}

inline std::filesystem::path PersistedDlcDirectory(const std::filesystem::path& launcher_root,
                                                   const std::filesystem::path& configured) {
    return configured.empty() ? std::filesystem::path{}
                              : AbsoluteNormalizedDlcDirectory(launcher_root, configured);
}

/// Paths for the PKG import flow (scripts/pkg_import.py plus the bundled extractor).
struct PkgImportPaths {
    static std::filesystem::path Extractor(const std::filesystem::path& launcher_root) {
        return (launcher_root / L"out" / L"pkg_extractor.exe").lexically_normal();
    }

    static std::filesystem::path Script(const std::filesystem::path& launcher_root) {
        return (launcher_root / L"scripts" / L"pkg_import.py").lexically_normal();
    }
};

}  // namespace LauncherPaths
