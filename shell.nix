{
  pkgs ? import ./nixpkgs.nix { },
}:

pkgs.mkShell {
  packages = with pkgs; [
    git
    curl
    jq
    python3
    nixfmt
    shellcheck
  ];
}
