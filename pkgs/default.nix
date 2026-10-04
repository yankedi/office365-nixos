{
  pkgs,
  lib,
  stdenvNoCC,
  python3,
  bash,
  glibcLocales,
  libnotify,
  nix,
  samba4,
}:
let
  applications = import ./applications.nix;
  runtime = pkgs.callPackage ./runtime.nix { };
  odt = pkgs.callPackage ./odt.nix { };
  shadows = pkgs.callPackage ./shadows.nix { };
  resources = pkgs.writeText "office365-resources.json" (
    builtins.toJSON {
      inherit applications;
      wine = "${runtime}/bin/wine";
      wineserver = "${runtime}/bin/wineserver";
      broker = "${runtime}/lib/wine/x86_64-windows/wine4office-voip-broker.exe";
      odt = "${odt}/share/odt/setup.exe";
      shadows = "${shadows}/bin/office-shadows.exe";
      localeArchive = "${glibcLocales}/lib/locale/locale-archive";
      libraryPath = lib.makeLibraryPath runtime.runtimeLibraries;
      executablePath = lib.makeBinPath [ samba4 ];
      notify = "${libnotify}/bin/notify-send";
      nix = "${nix}/bin/nix";
      # This URL is inert data, not a flake input or a package dependency.
      # Only `officectl install chinese-fonts` resolves/builds the font packages.
      fontFlake = "github:kugland/nix-ttf-ms-win11-auto/9e298e2aab68c1bfc22a039012b29595f04b4ac9";
      runnerVersion = runtime.version;
    }
  );
  desktopItems = lib.mapAttrsToList (
    _: app:
    pkgs.makeDesktopItem {
      name = app.command;
      desktopName = "Microsoft ${app.name} 365";
      genericName = "Microsoft ${app.name}";
      comment = "Microsoft 365 ${app.name}";
      exec = "${app.command} %F";
      icon = app.command;
      categories = lib.unique [
        "Office"
        app.category
      ];
      mimeTypes = app.mimeTypes;
      startupWMClass = app.wmClass;
      startupNotify = true;
    }
  ) applications;
in
stdenvNoCC.mkDerivation {
  pname = "office365-nixos";
  version = "0.1.0";
  dontUnpack = true;
  dontBuild = true;
  installPhase = ''
    runHook preInstall
    mkdir -p "$out/bin" "$out/libexec" "$out/share/applications" \
      "$out/share/icons/hicolor/scalable/apps" "$out/share/mime/packages"
    cp ${./mime-types.xml} "$out/share/mime/packages/office365.xml"
    cp ${../src/officectl.py} "$out/libexec/officectl.py"
    cp ${resources} "$out/libexec/resources.json"
    cat > "$out/bin/officectl" <<EOF
    #!${bash}/bin/bash
    exec ${python3}/bin/python3 "$out/libexec/officectl.py" "\$@"
    EOF
    chmod +x "$out/bin/officectl"
    ${lib.concatStringsSep "\n" (
      lib.mapAttrsToList (key: app: ''
        cat > "$out/bin/${app.command}" <<EOF
        #!${bash}/bin/bash
        exec "$out/bin/officectl" _launch ${key} "\$@"
        EOF
        chmod +x "$out/bin/${app.command}"
        cat > "$out/share/icons/hicolor/scalable/apps/${app.command}.svg" <<'SVG'
        <svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" viewBox="0 0 128 128">
          <rect x="4" y="4" width="120" height="120" rx="18" fill="${app.color}"/>
          <text x="64" y="87" text-anchor="middle" font-family="sans-serif" font-weight="bold" font-size="72" fill="white">${app.letter}</text>
        </svg>
        SVG
      '') applications
    )}
    for item in ${lib.concatStringsSep " " desktopItems}; do
      cp "$item/share/applications/"*.desktop "$out/share/applications/"
    done
    runHook postInstall
  '';
  passthru = {
    inherit
      runtime
      odt
      shadows
      applications
      ;
  };
  meta = {
    description = "Microsoft 365 controller and application launchers for NixOS";
    homepage = "https://github.com/yankedi/office365-nixos";
    license = lib.licenses.gpl3Plus;
    platforms = [ "x86_64-linux" ];
    mainProgram = "officectl";
  };
}
