{
  description = "GitHub Copilot desktop app with automated release updates";

  # 26.05 still supports Intel macOS, which upstream ships binaries for.
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";

  outputs =
    { self, nixpkgs }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "x86_64-darwin"
        "aarch64-darwin"
      ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
      pkgsFor =
        system:
        import nixpkgs {
          inherit system;
          # Only this explicitly selected proprietary application is allowed.
          config.allowUnfreePredicate = pkg: nixpkgs.lib.getName pkg == "github-copilot-desktop";
        };
    in
    {
      packages = forAllSystems (
        system:
        let
          package = import ./default.nix { pkgs = pkgsFor system; };
        in
        {
          github-copilot-desktop = package;
          default = package;
        }
      );

      apps = forAllSystems (system: {
        default = {
          type = "app";
          program = "${self.packages.${system}.default}/bin/github-copilot-desktop";
          meta.description = "Launch the GitHub Copilot desktop app";
        };
      });

      overlays.default = final: _prev: {
        github-copilot-desktop = import ./default.nix { pkgs = final; };
      };

      checks = forAllSystems (
        system:
        let
          pkgs = pkgsFor system;
          package = self.packages.${system}.default;
        in
        {
          package = package;
          layout =
            pkgs.runCommand "github-copilot-desktop-layout-check"
              {
                nativeBuildInputs = pkgs.lib.optionals pkgs.stdenv.hostPlatform.isLinux [ pkgs.desktop-file-utils ];
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
              );
        }
      );

      formatter = forAllSystems (system: (pkgsFor system).nixfmt);
    };
}
