{
  pkgs ? import ./nixpkgs.nix {
    # Permit only this proprietary application for standalone builds.
    config.allowUnfreePredicate = pkg: (pkg.pname or "") == "github-copilot-desktop";
  },
}:

pkgs.callPackage ./package.nix { }
