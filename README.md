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

## Try it locally

With Nix and flakes enabled:

```sh
nix build path:.
nix run path:.
# Alternatively:
./result/bin/github-copilot-desktop
```

`path:.` includes the files before your first Git commit. After committing,
`nix build .` and `nix run .` work too.

Without flakes, using your configured `<nixpkgs>`:

```sh
nix-build
./result/bin/github-copilot-desktop
```

The flake pins Nixpkgs 26.05 in `flake.lock` to retain Intel macOS support.
It permits this specific unfree package; it does not enable all unfree packages.
The legacy entry point uses your own Nixpkgs, which must support your platform.

After pushing this repository, replace `OWNER/REPO` below:

```sh
nix run github:OWNER/REPO
nix profile install github:OWNER/REPO
```

### NixOS / Home Manager

Add a flake input:

```nix
inputs.copilot-desktop.url = "github:OWNER/REPO";
```

Then use its package in your module (pass `inputs` through `specialArgs` or
`extraSpecialArgs`, as appropriate):

```nix
{ pkgs, inputs, ... }: {
  environment.systemPackages = [
    inputs.copilot-desktop.packages.${pkgs.stdenv.hostPlatform.system}.default
  ];
}
```

For Home Manager, use `home.packages` instead of `environment.systemPackages`.
The flake also exports `overlays.default`; when using the overlay, allow
`github-copilot-desktop` in your own Nixpkgs unfree policy.

## Desktop integration and limitations

- **Linux:** uses `appimageTools.wrapType2`, preserving upstream's bundled
  WebKit libraries and launch hooks in a bubblewrap FHS environment. No FUSE is
  needed, but unprivileged user namespaces must work. Git, GitHub CLI, and SSH
  are included in that environment. This is a compatibility wrapper, **not a
  security sandbox**; the coding agent can access your user files.
- Installing the Linux package exposes its launcher, icon, and upstream URL
  schemes (`github-app`, `ghapp`, `gh`). A desktop session is required to use
  the GUI. A plain `nix run` does not register URL handlers in your session.
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
nix flake check path:.
git diff -- sources.json
```

Nixpkgs is **not** updated by the release job. Update `flake.lock` separately:

```sh
nix flake update
nix flake check path:.
```

CI also runs on pushes and pull requests. Actions dependencies are pinned to
commit SHAs; Dependabot checks those pins weekly.

## License

The packaging and automation in this repository are MIT-licensed (see
`LICENSE`). The GitHub Copilot application is proprietary and remains subject
to GitHub's terms. This is not an official GitHub package.
