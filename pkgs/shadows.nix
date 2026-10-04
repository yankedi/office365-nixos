{ lib, pkgsCross }:
pkgsCross.mingwW64.stdenv.mkDerivation {
  pname = "office365-shadows";
  version = "1.0.0";
  dontUnpack = true;
  buildPhase = ''
    $CC -O2 -o office-shadows.exe ${../src/office-shadows.c} -luser32
  '';
  installPhase = ''
    mkdir -p "$out/bin"
    install -m 0644 office-shadows.exe "$out/bin/office-shadows.exe"
  '';
  meta = {
    description = "Hide Microsoft Office's four MSO_BORDEREFFECT shadow windows";
    license = lib.licenses.gpl3Plus;
  };
}
