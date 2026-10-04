{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.programs.office365;
  applications = import ../pkgs/applications.nix;
  associations = builtins.listToAttrs (
    lib.concatMap (
      app:
      map (mime: {
        name = mime;
        value = "${app.command}.desktop";
      }) app.mimeTypes
    ) (builtins.attrValues applications)
  );
in
{
  options.programs.office365 = {
    enable = lib.mkEnableOption "Microsoft 365 resources, officectl and application launchers";
    package = lib.mkOption {
      type = lib.types.package;
      default = pkgs.callPackage ../pkgs { };
      description = "Office controller and immutable runtime resources.";
    };
    defaultApplications = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "Associate Office-specific document formats with the Office launchers.";
    };
  };
  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = pkgs.stdenv.hostPlatform.system == "x86_64-linux";
        message = "programs.office365 currently supports x86_64-linux only.";
      }
    ];
    environment.systemPackages = [ cfg.package ];
    xdg.mime = lib.mkIf cfg.defaultApplications {
      enable = true;
      defaultApplications = lib.mapAttrs (_: desktop: lib.mkDefault desktop) associations;
    };
  };
}
