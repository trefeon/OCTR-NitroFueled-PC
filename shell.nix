# Build environment: `nix-shell native_probe/shell.nix --run 'bash native_probe/run.sh'`
{ pkgs ? import <nixpkgs> {} }:
let
  icedX86 = import ./packaging/iced-x86.nix {
    pythonPackages = pkgs.python3Packages;
    fetchurl = pkgs.fetchurl;
  };
  pythonWithIcedX86 = pkgs.python3.withPackages (_: [ icedX86 ]);
in pkgs.mkShell {
  packages = (with pkgs; [
    gcc gnumake cmake ninja pkg-config binutils git
    vulkan-headers vulkan-loader sdl3 xz
    directx-shader-compiler  # the native Vulkan engine compiles its shaders with it (scripts/native_shaders.py)
    # GPU library (gpu/): shadPS4 video core dependencies
    ffmpeg-headless boost fmt magic-enum robin-map xxhash vulkan-memory-allocator glslang spirv-cross xbyak zydis spirv-headers miniz libx11 libxcb xorgproto wayland
  ]) ++ [ pythonWithIcedX86 ];
}
