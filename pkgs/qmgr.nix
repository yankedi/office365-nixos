{
  pkgs,
  lib,
  stdenv,
  fetchurl,
  runner,
}:
let
  version = runner.version;
  sourceHashes = {
    "qmgr.h" = "2fb36c0b416034791aebe4977dc732b699756ed7d0dd0eb31fb66fa35dc943c7";
    "qmgr.spec" = "c07a8af8498d6eb67056f5e547bb40b2a6166bb9bea5c823f00eed31ffca4b85";
    "enum_files.c" = "965aff4e73c6c38fb5063364eab7deb2ad2cdd1966db924fd5b78f1219ad6de8";
    "enum_jobs.c" = "101e1cc39eaf638ace558bda96ebc9c83ffb6895bcf95f80e7105b9247d06b8f";
    "factory.c" = "0c2e93e8f191072b489f8c85def8b55a4faed04bd1192a4f559c5cdd53bd10bf";
    "file.c" = "b8d7a85d7a32438bd566f0c163b2eae48469516d4562a156be447f1c12cd6d0c";
    "job.c" = "5adfc2763ad6805abbac7e9a6c09c47da26c23a5c69e5b4b8f28418a26ed73ce";
    "qmgr.c" = "48eafb31f2d026a8dafd898f532e4ed6fd47e696418a409b68c70ad23a01395d";
    "service.c" = "6b6f5a2996fbaab9a2d4e00f06f44c65372ebf48e65cd7828c11e5db4453f7c8";
    "qmgr_local.idl" = "2792a285ed066c5628b5239ff10d63ff2b722b7787af9e328af841bdb49c9c28";
    "deliveryoptimization_proxy.idl" =
      "e533b94ea72b1d037e61e25e055a9fc9cb96e9b8eb02a7d6b8c25de197fd19f5";
    "qmgr.rgs" = "33584996247dbb5b7517cbd9e76c86e641d33bfe77e05eda9668d60d2ca58295";
    "wine/list.h" = "0a26ad30970b147c06f2068e6e419f88b638db03414aed653fc4df594ef73ae7";
  };
  sources = lib.mapAttrs (
    name: sha256:
    fetchurl {
      name = "wine4office-${version}-${lib.replaceStrings [ "/" ] [ "-" ] name}";
      url = "https://raw.githubusercontent.com/ttv20/wine4office/${version}/${
        if name == "wine/list.h" then "include/${name}" else "dlls/qmgr/${name}"
      }";
      inherit sha256;
    }
  ) sourceHashes;
in
stdenv.mkDerivation {
  pname = "wine4office-qmgr";
  inherit version;
  nativeBuildInputs = [
    pkgs.pkgsCross.mingwW64.stdenv.cc
    pkgs.pkgsCross.mingw32.stdenv.cc
  ];
  unpackPhase = ''
    runHook preUnpack
    mkdir -p dlls/qmgr/wine
    ${lib.concatStringsSep "\n" (
      lib.mapAttrsToList (name: source: "cp ${source} dlls/qmgr/${name}") sources
    )}
    chmod -R u+w dlls
    runHook postUnpack
  '';
  patches = [ ./patches/qmgr-buffer-lifetime.patch ];
  buildPhase = ''
    runHook preBuild
    build_qmgr() {
      local target="$1" bits="$2" arch="$3" threads="$4"
      mkdir "$arch"
      cp -r dlls/qmgr/. "$arch/"
      pushd "$arch"
      ${runner}/bin/widl --win"$bits" -I${runner}/include/wine/windows -u qmgr_local.idl
      ${runner}/bin/widl --win"$bits" -I${runner}/include/wine/windows \
        -p -u -h -r deliveryoptimization_proxy.idl
      ${runner}/bin/widl --dlldata-only deliveryoptimization_proxy
      printf '%s\n' '1 WINE_REGISTRY "qmgr.rgs"' \
        '2 WINE_REGISTRY "deliveryoptimization_proxy_r.rgs"' > registration.rc
      ${runner}/bin/wrc -I${runner}/include/wine/windows -o registration.res registration.rc
      # The builtin marker lets wineboot discover and register this module.
      ${runner}/bin/winegcc -b "$target" -m"$bits" -shared -O2 -g -Wl,--wine-builtin \
        -D__WINESRC__ -DWINE_REGISTER_DLL -DPROXY_CLSID=CLSID_PSFactoryBuffer \
        -I${runner}/include/wine/windows -I${runner}/include -L"$threads/lib" \
        -o qmgr.dll qmgr.spec registration.res \
        enum_files.c enum_jobs.c factory.c file.c job.c qmgr.c service.c \
        qmgr_local_i.c deliveryoptimization_proxy_i.c deliveryoptimization_proxy_p.c dlldata.c \
        -luuid -lwinhttp -loleaut32 -lole32 -lrpcrt4 -ladvapi32
      popd
    }
    build_qmgr x86_64-w64-mingw32 64 x86_64-windows ${pkgs.pkgsCross.mingwW64.windows.mcfgthreads}
    build_qmgr i686-w64-mingw32 32 i386-windows ${pkgs.pkgsCross.mingw32.windows.mcfgthreads}
    runHook postBuild
  '';
  installPhase = ''
    runHook preInstall
    for arch in x86_64-windows i386-windows; do
      install -Dm644 "$arch/qmgr.dll" "$out/lib/wine/$arch/qmgr.dll"
    done
    runHook postInstall
  '';
  dontStrip = true;
  passthru.downloadProbe = pkgs.pkgsCross.mingwW64.callPackage ../tests/download-probe.nix { };
  meta = {
    description = "Wine4Office download service with safe asynchronous buffer lifetimes";
    license = lib.licenses.lgpl21Plus;
    platforms = [ "x86_64-linux" ];
  };
}
