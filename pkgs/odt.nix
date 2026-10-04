{
  lib,
  stdenvNoCC,
  fetchurl,
  p7zip,
}:
stdenvNoCC.mkDerivation {
  pname = "office-deployment-tool";
  version = "16.0.20326.20112";
  src = fetchurl {
    url = "https://download.microsoft.com/download/6c1eeb25-cf8b-41d9-8d0d-cc1dbc032140/officedeploymenttool_20326-20112.exe";
    sha256 = "fbb64358fd4168acd52ee4efe47ffd032b6231dfb415ae2dce61b0e58ba67f86";
  };
  nativeBuildInputs = [ p7zip ];
  dontUnpack = true;
  dontBuild = true;
  dontFixup = true;
  installPhase = ''
    runHook preInstall
    mkdir -p "$out/share/odt"
    7z x "$src" -o"$out/share/odt" -y >/dev/null
    test -f "$out/share/odt/setup.exe"
    runHook postInstall
  '';
  meta = {
    description = "Microsoft Office Deployment Tool (installation executable only)";
    license = lib.licenses.unfree;
    sourceProvenance = [ lib.sourceTypes.binaryNativeCode ];
    platforms = [ "x86_64-linux" ];
  };
}
