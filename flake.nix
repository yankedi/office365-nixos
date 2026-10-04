{
  description = "Declarative Microsoft 365 resources and explicit per-user installation on NixOS";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs {
        inherit system;
        config.allowUnfreePredicate = pkg: nixpkgs.lib.getName pkg == "office-deployment-tool";
      };
      office = pkgs.callPackage ./pkgs { };
    in
    {
      packages.${system} = {
        default = office;
        office365-nixos = office;
        wine4office = office.runtime;
        office-deployment-tool = office.odt;
        office-shadows = office.shadows;
      };

      apps.${system}.default = {
        type = "app";
        program = "${office}/bin/officectl";
        meta.description = office.meta.description;
      };

      overlays.default = final: _prev: {
        office365-nixos = final.callPackage ./pkgs { };
      };

      nixosModules.default = import ./modules/office365.nix;

      checks.${system} = {
        runtime-libraries =
          pkgs.runCommand "office365-runtime-library-check"
            {
              nativeBuildInputs = [ pkgs.binutils ];
            }
            ''
              for dll in win32u.so crypt32.so secur32.so; do
                readelf -d "${office.runtime}/lib/wine/x86_64-unix/$dll" > "$TMPDIR/dynamic"
                grep -F '${nixpkgs.lib.getLib pkgs.freetype}/lib' "$TMPDIR/dynamic"
                grep -F '${nixpkgs.lib.getLib pkgs.gnutls}/lib' "$TMPDIR/dynamic"
                grep -F '/run/opengl-driver/lib' "$TMPDIR/dynamic"
              done
              touch "$out"
            '';
        controller =
          pkgs.runCommand "office365-controller-tests" { nativeBuildInputs = [ pkgs.python3 ]; }
            ''
              export HOME="$TMPDIR/home"
              mkdir -p "$HOME"
              cp -r ${./src} src
              cp -r ${./tests} tests
              chmod -R u+w src tests
              python -m unittest discover -s tests -v
              touch "$out"
            '';
        module =
          let
            evaluated = nixpkgs.lib.nixosSystem {
              inherit system;
              modules = [
                self.nixosModules.default
                {
                  programs.office365.enable = true;
                  nixpkgs.config.allowUnfreePredicate = pkg: nixpkgs.lib.getName pkg == "office-deployment-tool";
                  system.stateVersion = "26.05";
                  fileSystems."/" = {
                    device = "none";
                    fsType = "tmpfs";
                  };
                  boot.loader.grub.enable = false;
                }
              ];
            };
            config = evaluated.config;
            withoutDefaults =
              (evaluated.extendModules {
                modules = [ { programs.office365.defaultApplications = false; } ];
              }).config;
          in
          assert !(config.system.activationScripts ? office365);
          assert !(config.systemd.services ? office365);
          assert
            config.xdg.mime.defaultApplications."application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            == "word365.desktop";
          assert
            config.xdg.mime.defaultApplications."application/vnd.openxmlformats-officedocument.presentationml.presentation"
            == "powerpoint365.desktop";
          assert !(config.xdg.mime.defaultApplications ? "text/plain");
          assert !(config.xdg.mime.defaultApplications ? "text/csv");
          assert !(config.xdg.mime.defaultApplications ? "application/pdf");
          assert !(withoutDefaults.xdg.mime.defaultApplications ? "application/vnd.ms-access");
          assert builtins.elem withoutDefaults.programs.office365.package
            withoutDefaults.environment.systemPackages;
          pkgs.runCommand "office365-module-check" { } ''
            echo ${nixpkgs.lib.escapeShellArg (toString config.programs.office365.package)} > "$out"
          '';
      };

      formatter.${system} = pkgs.nixfmt;
      devShells.${system}.default = pkgs.mkShell {
        packages = [
          pkgs.python3
          pkgs.nixfmt
        ];
      };
    };
}
