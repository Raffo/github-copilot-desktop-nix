#!/usr/bin/env python3
"""Update sources.json using verified downloads from github/app releases."""

import argparse
import base64
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import urllib.request


API_ROOT = "https://api.github.com/repos/github/app/releases"
DOWNLOAD_ROOT = "https://github.com/github/app/releases/download"
ASSETS = {
    "x86_64-linux": "GitHub-Copilot-linux-x64.AppImage",
    "aarch64-linux": "GitHub-Copilot-linux-arm64.AppImage",
    "x86_64-darwin": "GitHub-Copilot-darwin-x64.tar.gz",
    "aarch64-darwin": "GitHub-Copilot-darwin-arm64.tar.gz",
}
TAG_PATTERN = re.compile(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
CHUNK_SIZE = 1024 * 1024
TIMEOUT = 120


class UpdateError(Exception):
    """An invalid release or a failed verification."""


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """API credentials must never follow redirects to asset/CDN hosts."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None:
            redirected.remove_header("Authorization")
        return redirected


OPENER = urllib.request.build_opener(SafeRedirectHandler())


def validate_tag(tag):
    if not isinstance(tag, str) or TAG_PATTERN.fullmatch(tag) is None:
        raise UpdateError("expected a stable release tag such as v1.2.0")
    return tag[1:]


def fetch_release(tag=None):
    if tag is not None:
        validate_tag(tag)
    endpoint = "/latest" if tag is None else "/tags/" + tag
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "github-copilot-desktop-nix-updater",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(API_ROOT + endpoint, headers=headers)
    with OPENER.open(request, timeout=TIMEOUT) as response:
        release = json.load(response)
    if not isinstance(release, dict):
        raise UpdateError("release metadata must be a JSON object")
    release_tag = release.get("tag_name")
    validate_tag(release_tag)
    if tag is not None and release_tag != tag:
        raise UpdateError("release tag does not match requested tag " + tag)
    if release.get("draft") is not False or release.get("prerelease") is not False:
        raise UpdateError("refusing a draft or prerelease")
    return release


def release_assets(release):
    """Validate every required asset before downloading any large files."""
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise UpdateError("release metadata must contain an assets array")
    selected = {}
    for asset in assets:
        if not isinstance(asset, dict) or not isinstance(asset.get("name"), str):
            raise UpdateError("invalid asset metadata")
        name = asset["name"]
        if name not in ASSETS.values():
            continue
        if name in selected:
            raise UpdateError("duplicate release asset: " + name)
        digest = asset.get("digest")
        if digest is not None and (
            not isinstance(digest, str)
            or re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest) is None
        ):
            raise UpdateError("invalid SHA-256 digest for " + name)
        size = asset.get("size")
        if size is not None and (type(size) is not int or size < 0):
            raise UpdateError("invalid asset size for " + name)
        selected[name] = asset
    for name in ASSETS.values():
        if name not in selected:
            raise UpdateError("missing release asset: " + name)
    return selected


def hash_asset(tag, asset):
    name = asset["name"]
    # Never trust browser_download_url, or attach API credentials to downloads.
    url = DOWNLOAD_ROOT + "/" + tag + "/" + name
    request = urllib.request.Request(
        url, headers={"User-Agent": "github-copilot-desktop-nix-updater"}
    )
    digest = hashlib.sha256()
    size = 0
    with OPENER.open(request, timeout=TIMEOUT) as response:
        while True:
            chunk = response.read(CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
    if asset.get("size") is not None and size != asset["size"]:
        raise UpdateError("download size mismatch for " + name)
    expected = asset.get("digest")
    if expected is not None and digest.hexdigest() != expected[7:].lower():
        raise UpdateError("SHA-256 digest mismatch for " + name)
    return "sha256-" + base64.b64encode(digest.digest()).decode("ascii")


def current_version(path):
    try:
        with path.open(encoding="utf-8") as source:
            current = json.load(source)
    except FileNotFoundError:
        return None
    if not isinstance(current, dict):
        raise UpdateError("existing sources file must be a JSON object")
    return current.get("version")


def atomic_write(path, sources):
    """Replace only after all downloads have been verified; clean up on error."""
    temporary = None
    try:
        try:
            mode = stat.S_IMODE(path.stat().st_mode)
        except FileNotFoundError:
            mode = 0o644
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix="." + path.name + ".",
            suffix=".tmp",
            delete=False,
        ) as output:
            temporary = Path(output.name)
            json.dump(sources, output, indent=2)
            output.write("\n")
            output.flush()
            os.chmod(temporary, mode)
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def update(sources_path, tag=None, force=False):
    release = fetch_release(tag)
    release_tag = release["tag_name"]
    version = validate_tag(release_tag)
    assets = release_assets(release)
    if not force and current_version(sources_path) == version:
        print("Already at " + release_tag + "; use --force to rehash.")
        return False
    sources = {"version": version, "tag": release_tag}
    for system, name in ASSETS.items():
        print("Hashing " + name + "...", flush=True)
        sources[system] = {"name": name, "hash": hash_asset(release_tag, assets[name])}
    atomic_write(sources_path, sources)
    print("Updated " + str(sources_path) + " to " + release_tag + ".")
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", help="stable tag to fetch instead of the latest release")
    parser.add_argument("--force", action="store_true", help="rehash even if the version is unchanged")
    parser.add_argument(
        "--sources",
        type=Path,
        default=Path(__file__).resolve().with_name("sources.json"),
        help="sources file (default: sources.json beside this script)",
    )
    args = parser.parse_args(argv)
    try:
        update(args.sources, tag=args.tag, force=args.force)
    except (UpdateError, OSError, ValueError, http.client.HTTPException) as error:
        print("Update failed: " + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
