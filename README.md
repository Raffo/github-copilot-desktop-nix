# GitHub Copilot desktop app for Nix

An unofficial binary package for [`github/app`](https://github.com/github/app),
with scheduled GitHub Actions updates. This is the **desktop app**, not GitHub
Copilot CLI or GitHub Desktop.

| Nix system | Upstream artifact |
| --- | --- |
| `x86_64-linux` | Linux x64 AppImage |
| `aarch64-linux` | Linux ARM64 AppImage |
| `x86_64-darwin` | Intel macOS `.app` archive |
| `aarch64-darwin` | Apple Silicon macOS `.app` archive |

## Build and run

Classic, non-flake Nix packaging, following
[`Raffo/pi-coding-agent-nix`](https://github.com/Raffo/pi-coding-agent-nix).
No experimental Nix features are required.

```sh
nix-build
./result/bin/github-copilot-desktop
```

Optional profile installation:

```sh
nix-env -if .
```

The default build uses the exact Nixpkgs 26.05 revision and unpacked hash in
`nixpkgs.json`, rather than your current channel. It permits only this unfree
application. To use your own Nixpkgs and unfree policy:

```sh
nix-build --arg pkgs 'import <nixpkgs> { config.allowUnfreePredicate = pkg: (pkg.pname or "") == "github-copilot-desktop"; }'
```

## Overlay (NixOS / Home Manager)

Clone this repository to a persistent local path:

```sh
git clone https://github.com/Raffo/github-copilot-desktop-nix.git
```

Use the same overlay pattern as `pi-coding-agent-nix` in your NixOS configuration
(replace the path with your checkout):

```nix
{ pkgs, ... }:
{
  nixpkgs.overlays = [
    (import /path/to/github-copilot-desktop-nix/overlay.nix)
  ];
  nixpkgs.config.allowUnfreePredicate = pkg:
    (pkg.pname or "") == "github-copilot-desktop";

  environment.systemPackages = [ pkgs.github-copilot-desktop ];
}
```

Then run `sudo nixos-rebuild switch`. No flake inputs or `specialArgs` are needed.
If you already allow unfree packages, keep your existing policy; if you use a
predicate, add this package to it rather than replacing your existing allowlist.

For standalone Home Manager, use the same overlay and unfree configuration with
`home.packages = [ pkgs.github-copilot-desktop ];` instead. When Home Manager uses
NixOS's package set (`home-manager.useGlobalPkgs = true`), configure the overlay
and unfree policy in NixOS only.

The overlay uses **your** Nixpkgs, not this repository's pin. The package is also
callable directly with `pkgs.callPackage /path/to/package.nix { }`.
Neither entry point overrides your unfree policy.

## Desktop integration and limitations

- **Linux:** uses `appimageTools.wrapType2`, preserving upstream's bundled
  WebKit libraries and launch hooks in a bubblewrap FHS environment. No FUSE is
  needed, but unprivileged user namespaces must work. Git, GitHub CLI, and SSH
  are included in that environment. This is a compatibility wrapper, **not a
  security sandbox**; the coding agent can access your user files.
- Installing the Linux package exposes its launcher, icon, and upstream URL
  schemes (`github-app`, `ghapp`, `gh`). A desktop session is required to use
  the GUI. Running the launcher from `./result` alone does not register URL
  handlers in your session.
  If your desktop does not pick up the installed handler automatically:

  ```sh
  xdg-mime default github-copilot-desktop.desktop x-scheme-handler/github-app
  xdg-mime default github-copilot-desktop.desktop x-scheme-handler/ghapp
  ```

- Linux rendering also needs a working graphics-driver setup. On non-NixOS
  hosts, you may need a Nix-compatible OpenGL/EGL setup such as nixGL. This
  package does not configure your host GPU drivers; headless Xvfb testing also
  requires an explicitly configured software renderer.
- **macOS:** installs the upstream bundle under
  `$out/Applications/GitHub Copilot.app`, plus a `github-copilot-desktop` command.
  It avoids stripping and Mach-O fixups to preserve the signed app contents.
  Use your normal nix-darwin/Home Manager application-linking mechanism for
  Finder, Launch Services, and authentication URL integration. For a local
  build you can open the bundle directly:

  ```sh
  open "./result/Applications/GitHub Copilot.app"
  ```

- App files live in the read-only Nix store. Use this repository's updater and
  rebuild/upgrade your Nix profile rather than relying on in-app self-updates.
  User settings and login state remain in the app's normal user directories.
- The upstream app is proprietary, requires an appropriate account/provider
  setup, and is not built from source here. Authentication, GPU/Wayland support,
  and agent workflows need testing in your actual desktop session.

## Automatic updates

After pushing to GitHub:

1. Enable Actions if your repository or organization has disabled them.
2. Ensure repository/organization policy allows Actions to write repository
   contents. The update workflow requests `contents: write` only for its final
   commit job.
3. The **Update sources** workflow runs every six hours, or manually
   from the Actions tab. Scheduled runs use the default branch.

The updater checks the latest stable GitHub release, verifies that all four
artifacts exist, downloads and hashes each one, and compares any SHA-256 digest
and size published in the API. It atomically replaces `sources.json` only once
all downloads succeed. No release change means no downloads or commit.

Before pushing an update, Actions runs the offline updater tests and native
package/layout checks on all four platforms. It commits **only `sources.json`**
using `github-actions[bot]`. `GITHUB_TOKEN` is sufficient; no PAT or third-party
update service is needed. Bot pushes do not trigger ordinary push workflows,
so the update workflow explicitly waits for its own builds before committing.
If the branch moves while validation runs, the update fails safely rather than
pushing an update validated against stale code; rerun the workflow.

This deliberately uses direct commits, like the CLI packaging reference.
Branch protection/rulesets must permit those bot pushes, or you will need to
adapt the workflow to open pull requests instead. Organization policy and
runner availability can also restrict the native ARM64/macOS jobs.

GitHub may delay scheduled workflows and disables schedules in inactive public
repositories after 60 days. Check the Actions tab if updates stop.

### Update by hand

Only Python 3 (3.8+) is required:

```sh
./update.sh                      # latest stable release
./update.sh --tag v1.2.0         # choose a particular stable release
./update.sh --force             # download and verify even at the same version
```

Optionally set `GH_TOKEN` or `GITHUB_TOKEN` for GitHub API rate limits.
Credentials are sent only to the API, never to release asset downloads.
Downloads are streamed rather than kept on disk by the updater.

Review and build before committing:

```sh
python3 -m unittest discover -s tests -v
nix-build --option experimental-features ''
nix-build tests/package.nix --no-out-link --option experimental-features ''
git diff -- sources.json
```

Nixpkgs is **not** updated by the release job. Refresh `nixpkgs.json` deliberately:

```sh
rev=$(curl -fsSL https://channels.nixos.org/nixos-26.05/git-revision)
hash=$(nix-prefetch-url --unpack "https://github.com/NixOS/nixpkgs/archive/$rev.tar.gz")
python3 - "$rev" "$hash" <<'PY'
import json, sys
with open("nixpkgs.json", "w") as f:
    json.dump(dict(rev=sys.argv[1], sha256=sys.argv[2]), f, indent=2)
    f.write("\n")
PY
nix-build
```

For development, `nix-shell` provides Python, nixfmt, shellcheck, and update tools.
`tests/package.nix` builds through the overlay and checks installed desktop assets.
CI builds both entry points with experimental features disabled.

CI also runs on pushes and pull requests. Actions dependencies are pinned to
commit SHAs; Dependabot checks those pins weekly.

## License

The packaging and automation in this repository are MIT-licensed (see
`LICENSE`). The GitHub Copilot application is proprietary and remains subject
to GitHub's terms. This is not an official GitHub package.
