{
  lib,
  stdenv,
  windows,
}:
stdenv.mkDerivation {
  pname = "office365-download-probe";
  version = "1.0.0";
  dontUnpack = true;
  buildInputs = [ windows.mcfgthreads ];
  buildPhase = ''
    $CC -O2 -static -municode ${./download-probe.c} -o download-probe.exe -lole32 -luuid
  '';
  installPhase = ''
    install -Dm644 download-probe.exe "$out/bin/download-probe.exe"
  '';
  meta = {
    description = "Explicit local-server check for Office background download service survival";
    license = lib.licenses.gpl3Plus;
    platforms = [ "x86_64-windows" ];
  };
}
