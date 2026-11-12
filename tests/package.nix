{
  pkgs ? import ../nixpkgs.nix {
    config.allowUnfreePredicate = pkg: (pkg.pname or "") == "github-copilot-desktop";
    overlays = [ (import ../overlay.nix) ];
  },
}:

let
  package = pkgs.github-copilot-desktop;
in
pkgs.runCommand "github-copilot-desktop-layout-check"
  {
    nativeBuildInputs = pkgs.lib.optionals pkgs.stdenv.hostPlatform.isLinux [
      pkgs.desktop-file-utils
    ];
  }
  (
    ''
      test -x "${package}/bin/github-copilot-desktop"
    ''
    + pkgs.lib.optionalString pkgs.stdenv.hostPlatform.isLinux ''
      desktop-file-validate "${package}/share/applications/github-copilot-desktop.desktop"
      test -s "${package}/share/icons/hicolor/256x256/apps/github-copilot-desktop.png"
      grep -F "Exec=${package}/bin/github-copilot-desktop %u" \
        "${package}/share/applications/github-copilot-desktop.desktop"
      grep -F 'x-scheme-handler/github-app' \
        "${package}/share/applications/github-copilot-desktop.desktop"
    ''
    + pkgs.lib.optionalString pkgs.stdenv.hostPlatform.isDarwin ''
      test -x "${package}/Applications/GitHub Copilot.app/Contents/MacOS/github"
      test -s "${package}/Applications/GitHub Copilot.app/Contents/Info.plist"
    ''
    + ''
      touch "$out"
    ''
  )
