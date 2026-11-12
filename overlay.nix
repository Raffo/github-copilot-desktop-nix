final: _prev: {
  github-copilot-desktop = final.callPackage ./package.nix { };
  # Accept the repository name as an alias for the application package.
  github-copilot-desktop-nix = final.github-copilot-desktop;
}
