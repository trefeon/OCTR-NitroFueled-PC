#pragma once

#include <algorithm>
#include <cstddef>
#include <string>
#include <vector>

namespace LauncherBindings {

inline constexpr std::size_t kMaxBindings = 4;

inline std::vector<std::string> Split(const std::string& value) {
    std::vector<std::string> bindings;
    std::size_t start = 0;
    while (start <= value.size()) {
        const std::size_t end = value.find(',', start);
        std::string binding = value.substr(start, end == std::string::npos ? end : end - start);
        const std::size_t first = binding.find_first_not_of(" \t\r\n");
        if (first != std::string::npos) {
            const std::size_t last = binding.find_last_not_of(" \t\r\n");
            bindings.push_back(binding.substr(first, last - first + 1));
        }
        if (end == std::string::npos) break;
        start = end + 1;
    }
    return bindings;
}

inline std::string Serialize(const std::vector<std::string>& bindings) {
    std::string value;
    for (const auto& binding : bindings) {
        if (!value.empty()) value += ',';
        value += binding;
    }
    return value;
}

// Pad bindings add distinct buttons until runtime_pad.c's four-binding limit is reached.
inline std::string AppendPadBinding(const std::string& existing, const std::string& button) {
    std::vector<std::string> bindings;
    for (const auto& binding : Split(existing)) {
        if (std::find(bindings.begin(), bindings.end(), binding) == bindings.end()) {
            if (bindings.size() == kMaxBindings) break;
            bindings.push_back(binding);
        }
    }
    if (!button.empty() && bindings.size() < kMaxBindings &&
        std::find(bindings.begin(), bindings.end(), button) == bindings.end()) {
        bindings.push_back(button);
    }
    return Serialize(bindings);
}

// Keyboard capture keeps its existing behavior: replacing the default on first customization,
// adding to custom bindings, and dropping the oldest binding when a fifth distinct key is captured.
inline std::string CaptureKeyboardBinding(const std::string& existing, const std::string& key,
                                          bool replace_default) {
    std::vector<std::string> bindings = replace_default ? std::vector<std::string>{} : Split(existing);
    if (std::find(bindings.begin(), bindings.end(), key) == bindings.end()) {
        if (bindings.size() >= kMaxBindings) bindings.erase(bindings.begin());
        bindings.push_back(key);
    }
    return Serialize(bindings);
}

}  // namespace LauncherBindings
