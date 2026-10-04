{
  pkgs,
  lib,
  stdenv,
  fetchurl,
  autoPatchelfHook,
  zstd,
}:
let
  runtimeLibraries = with pkgs; [
    stdenv.cc.cc.lib
    alsa-lib
    cups
    dbus
    ffmpeg_6
    fontconfig
    freetype
    glib
    gnutls
    libGL
    libgphoto2
    libkrb5
    libpcap
    libpulseaudio
    libusb1
    libxkbcommon
    ocl-icd
    pcsclite
    pipewire
    sane-backends
    udev
    unixodbc
    vulkan-loader
    wayland
    libx11
    libxcomposite
    libxcursor
    libxext
    libxfixes
    libxi
    libxinerama
    libxrandr
    libxrender
    libxxf86vm
    gst_all_1.gstreamer
    gst_all_1.gst-plugins-base
  ];
in
stdenv.mkDerivation {
  pname = "wine4office-runtime";
  version = "0.2.2-beta.2";
  src = fetchurl {
    url = "https://github.com/ttv20/wine4office/releases/download/0.2.2-beta.2/wine4office-0.2.2-beta.2-x86_64.tar.zst";
    sha256 = "16d90f45ca0ecf6ab0b28c78f3e893a915aa829ecc050038a71783acc19f2360";
  };
  nativeBuildInputs = [
    autoPatchelfHook
    zstd
  ];
  buildInputs = runtimeLibraries;
  runtimeDependencies = map lib.getLib runtimeLibraries;
  # Wine loads optional libraries from its Unix DLLs with dlopen. RUNPATH on
  # bin/wine is not inherited by those DLLs; give every ELF the complete paths.
  appendRunpaths = map (pkg: "${lib.getLib pkg}/lib") runtimeLibraries ++ [
    "/run/opengl-driver/lib"
  ];
  dontBuild = true;
  dontStrip = true;
  installPhase = ''
    runHook preInstall
    mkdir -p "$out"
    cp -a ./. "$out/"
    runHook postInstall
  '';
  preFixup = ''
    addAutoPatchelfSearchPath "$out/lib/wine/x86_64-unix"
    # Debian's libpcap.so.0.8 and nixpkgs' libpcap.so.1 expose the same ABI.
    patchelf --replace-needed libpcap.so.0.8 libpcap.so.1 \
      "$out/lib/wine/x86_64-unix/wpcap.so"
  '';
  passthru = { inherit runtimeLibraries; };
  meta = {
    description = "Pinned upstream Wine4Office binary runner with NixOS runtime dependencies";
    homepage = "https://github.com/ttv20/wine4office";
    license = lib.licenses.lgpl21Plus;
    sourceProvenance = [ lib.sourceTypes.binaryNativeCode ];
    platforms = [ "x86_64-linux" ];
  };
}
