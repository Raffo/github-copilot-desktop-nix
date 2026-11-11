"""Run with python3 -m unittest discover -v; no network or Nix required."""

import base64
import contextlib
import copy
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest import mock
import urllib.error
import urllib.request

import update


class TrackingResponse(io.BytesIO):
    def __init__(self, body):
        super().__init__(body)
        self.read_sizes = []

    def read(self, size=-1):
        self.read_sizes.append(size)
        return super().read(size)


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "sources.json"
        self.original = b'{ "version": "1.1.0", "unchanged": true }\n'
        self.path.write_bytes(self.original)
        self.payloads = {
            name: ("binary payload for " + name).encode() + b"\x00\xff"
            for name in update.ASSETS.values()
        }
        self.release = {
            "tag_name": "v1.2.0",
            "draft": False,
            "prerelease": False,
            "assets": [
                {
                    "name": name,
                    "size": len(body),
                    "digest": "sha256:" + hashlib.sha256(body).hexdigest(),
                    "browser_download_url": "https://untrusted.invalid/" + name,
                }
                for name, body in self.payloads.items()
            ],
        }
        self.requests = []
        self.responses = []
        self.download_failure = None
        patcher = mock.patch.object(update.OPENER, "open", side_effect=self.open)
        self.open_mock = patcher.start()
        self.addCleanup(patcher.stop)
        # Tests never use credentials from the executing user's environment.
        env = mock.patch.dict(os.environ, {}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()
        for redirect in (
            contextlib.redirect_stdout(self.stdout),
            contextlib.redirect_stderr(self.stderr),
        ):
            redirect.__enter__()
            self.addCleanup(redirect.__exit__, None, None, None)

    def open(self, request, timeout):
        self.assertEqual(timeout, update.TIMEOUT)
        self.requests.append(request)
        if request.full_url.startswith(update.API_ROOT):
            body = json.dumps(self.release).encode()
        else:
            name = request.full_url.rsplit("/", 1)[-1]
            self.assertEqual(
                request.full_url,
                update.DOWNLOAD_ROOT + "/" + self.release["tag_name"] + "/" + name,
            )
            if name == self.download_failure:
                raise urllib.error.URLError("interrupted download")
            body = self.payloads[name]
        response = TrackingResponse(body)
        self.responses.append(response)
        return response

    def run_update(self, *args):
        return update.main(["--sources", str(self.path), *args])

    def assert_untouched(self):
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def assert_metadata_failure(self, *args):
        self.assertEqual(self.run_update(*args), 1)
        self.assert_untouched()
        self.assertEqual(len(self.requests), 1)

    def test_updates_all_platforms_with_computed_sri_hashes(self):
        self.assertEqual(self.run_update(), 0)
        sources = json.loads(self.path.read_text())
        self.assertEqual(set(sources), {"version", "tag", *update.ASSETS})
        self.assertEqual(sources["version"], "1.2.0")
        self.assertEqual(sources["tag"], "v1.2.0")
        for system, name in update.ASSETS.items():
            expected = base64.b64encode(hashlib.sha256(self.payloads[name]).digest()).decode()
            self.assertEqual(sources[system], {"name": name, "hash": "sha256-" + expected})
        self.assertEqual(self.requests[0].full_url, update.API_ROOT + "/latest")
        self.assertEqual(len(self.requests), 5)
        self.assertTrue(all(response.closed for response in self.responses))
        self.assertTrue(self.path.read_bytes().endswith(b"\n"))

    def test_same_version_is_noop_and_preserves_exact_bytes(self):
        self.original = b'{ "version": "1.2.0", "custom": true }\n'
        self.path.write_bytes(self.original)
        self.assertEqual(self.run_update(), 0)
        self.assert_untouched()
        self.assertEqual(len(self.requests), 1)

    def test_force_rehashes_same_version(self):
        self.path.write_text('{"version": "1.2.0"}')
        self.assertEqual(self.run_update("--force"), 0)
        self.assertEqual(len(self.requests), 5)
        self.assertIn("x86_64-linux", json.loads(self.path.read_text()))

    def test_explicit_tag_uses_tag_api(self):
        self.assertEqual(self.run_update("--tag", "v1.2.0"), 0)
        self.assertEqual(self.requests[0].full_url, update.API_ROOT + "/tags/v1.2.0")

    def test_invalid_requested_tags_never_make_requests(self):
        for tag in (
            "", "1.2.0", "v1.2", "v01.2.0", "v1.2.0-rc.1", "v1.2.0+build",
            "../latest", "v1.2.0/evil", "v1.2.0\n", "v1.2.0?x=y",
        ):
            with self.subTest(tag=tag):
                self.assertEqual(self.run_update("--tag", tag), 1)
                self.assert_untouched()
        self.assertEqual(self.requests, [])

    def test_invalid_response_tags_are_rejected(self):
        for tag in (None, 12, "v1.2.0-rc.1", "../latest", "v1.2.0\n"):
            with self.subTest(tag=tag):
                self.requests.clear()
                self.release["tag_name"] = tag
                self.assert_metadata_failure()

    def test_requested_tag_must_match_response(self):
        self.assert_metadata_failure("--tag", "v1.1.9")

    def test_drafts_prereleases_and_missing_stability_flags_are_rejected(self):
        original = copy.deepcopy(self.release)
        for field in ("draft", "prerelease"):
            for value in (True, None, 0, "false"):
                with self.subTest(field=field, value=value):
                    self.requests.clear()
                    self.release = copy.deepcopy(original)
                    self.release[field] = value
                    self.assert_metadata_failure()
            self.requests.clear()
            self.release = copy.deepcopy(original)
            del self.release[field]
            self.assert_metadata_failure()

    def test_missing_asset_fails_before_any_downloads(self):
        self.release["assets"].pop()
        self.assert_metadata_failure()
        self.assertIn("missing release asset", self.stderr.getvalue())

    def test_wrong_asset_name_is_not_a_substitute(self):
        self.release["assets"][0]["name"] = "GitHub-Copilot-linux-x64.deb"
        self.assert_metadata_failure()

    def test_duplicate_expected_asset_is_rejected(self):
        self.release["assets"].append(copy.deepcopy(self.release["assets"][0]))
        self.assert_metadata_failure()

    def test_unrelated_release_assets_are_ignored(self):
        self.release["assets"].append({"name": "latest.json", "digest": "unrelated"})
        self.assertEqual(self.run_update(), 0)
        self.assertEqual(len(self.requests), 5)

    def test_invalid_asset_arrays_and_entries_are_rejected(self):
        for assets in (None, {}, "assets", [None], [{}], [{"name": 42}]):
            with self.subTest(assets=assets):
                self.requests.clear()
                self.release["assets"] = assets
                self.assert_metadata_failure()

    def test_invalid_digests_fail_before_downloads(self):
        for digest in ("", "sha256:abc", "sha512:" + "0" * 64, "sha256:" + "z" * 64, 3):
            with self.subTest(digest=digest):
                self.requests.clear()
                self.release["assets"][-1]["digest"] = digest
                self.assert_metadata_failure()

    def test_missing_or_null_digest_still_downloads_and_hashes(self):
        self.release["assets"][0].pop("digest")
        self.release["assets"][1]["digest"] = None
        self.assertEqual(self.run_update(), 0)
        self.assertEqual(len(self.requests), 5)
        sources = json.loads(self.path.read_text())
        for system, name in update.ASSETS.items():
            expected = "sha256-" + base64.b64encode(hashlib.sha256(self.payloads[name]).digest()).decode()
            self.assertEqual(sources[system]["hash"], expected)

    def test_uppercase_digest_hex_is_accepted(self):
        for asset in self.release["assets"]:
            asset["digest"] = "sha256:" + asset["digest"][7:].upper()
        self.assertEqual(self.run_update(), 0)

    def test_api_digest_is_checked_against_download_not_blindly_trusted(self):
        self.release["assets"][-1]["digest"] = "sha256:" + "0" * 64
        self.assertEqual(self.run_update(), 1)
        self.assert_untouched()
        self.assertEqual(len(self.requests), 5)
        self.assertIn("digest mismatch", self.stderr.getvalue())

    def test_missing_asset_size_is_allowed(self):
        for asset in self.release["assets"]:
            asset.pop("size")
        self.assertEqual(self.run_update(), 0)

    def test_invalid_asset_sizes_are_rejected(self):
        for size in (-1, True, "100", 1.5):
            with self.subTest(size=size):
                self.requests.clear()
                self.release["assets"][-1]["size"] = size
                self.assert_metadata_failure()

    def test_truncated_download_without_digest_is_rejected(self):
        asset = self.release["assets"][-1]
        asset.pop("digest")
        self.payloads[asset["name"]] = self.payloads[asset["name"]][:-1]
        self.assertEqual(self.run_update(), 1)
        self.assert_untouched()
        self.assertIn("size mismatch", self.stderr.getvalue())

    def test_midway_network_failure_keeps_old_file(self):
        self.download_failure = self.release["assets"][2]["name"]
        self.assertEqual(self.run_update(), 1)
        self.assert_untouched()
        self.assertTrue(all(response.closed for response in self.responses))

    def test_streaming_read_failure_keeps_old_file_and_closes_response(self):
        original_read = TrackingResponse.read

        def failing_read(response, size=-1):
            if response.read_sizes and size == update.CHUNK_SIZE:
                raise OSError("connection reset")
            return original_read(response, size)

        with mock.patch.object(TrackingResponse, "read", failing_read):
            self.assertEqual(self.run_update(), 1)
        self.assert_untouched()
        self.assertTrue(all(response.closed for response in self.responses))

    def test_api_http_failure_keeps_old_file(self):
        self.open_mock.side_effect = urllib.error.HTTPError(
            update.API_ROOT + "/latest", 403, "rate limited", {}, None
        )
        self.assertEqual(self.run_update(), 1)
        self.assert_untouched()
        self.assertIn("403", self.stderr.getvalue())

    def test_incomplete_http_response_keeps_old_file(self):
        self.open_mock.side_effect = http.client.IncompleteRead(b"partial", 100)
        self.assertEqual(self.run_update(), 1)
        self.assert_untouched()
        self.assertIn("IncompleteRead", self.stderr.getvalue())

    def test_malformed_api_json_keeps_old_file(self):
        self.open_mock.side_effect = None
        self.open_mock.return_value = io.BytesIO(b'{"invalid":')
        self.assertEqual(self.run_update(), 1)
        self.assert_untouched()

    def test_nonobject_api_json_is_rejected(self):
        self.release = []
        self.assert_metadata_failure()

    def test_invalid_existing_json_keeps_old_file(self):
        self.original = b'{"broken":'
        self.path.write_bytes(self.original)
        self.assertEqual(self.run_update(), 1)
        self.assert_untouched()
        self.assertEqual(len(self.requests), 1)

    def test_nonobject_existing_json_is_rejected(self):
        self.original = b"[]\n"
        self.path.write_bytes(self.original)
        self.assertEqual(self.run_update(), 1)
        self.assert_untouched()

    def test_force_can_repair_invalid_existing_sources(self):
        self.path.write_bytes(b"not json")
        self.assertEqual(self.run_update("--force"), 0)
        self.assertEqual(json.loads(self.path.read_text())["version"], "1.2.0")

    def test_missing_sources_file_is_created(self):
        self.path.unlink()
        self.assertEqual(self.run_update(), 0)
        self.assertEqual(json.loads(self.path.read_text())["version"], "1.2.0")
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o644)

    def test_missing_sources_stays_missing_if_verification_fails(self):
        self.path.unlink()
        self.release["assets"][-1]["digest"] = "sha256:" + "0" * 64
        self.assertEqual(self.run_update(), 1)
        self.assertFalse(self.path.exists())
        self.assertEqual(list(self.path.parent.iterdir()), [])

    def test_existing_permissions_are_preserved(self):
        self.path.chmod(0o640)
        self.assertEqual(self.run_update(), 0)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o640)

    def test_replace_error_removes_temporary_file_and_preserves_original(self):
        with mock.patch.object(update.os, "replace", side_effect=OSError("replace failed")):
            self.assertEqual(self.run_update(), 1)
        self.assert_untouched()

    def test_fsync_error_removes_temporary_file_and_preserves_original(self):
        with mock.patch.object(update.os, "fsync", side_effect=OSError("disk full")):
            self.assertEqual(self.run_update(), 1)
        self.assert_untouched()

    def test_json_write_error_removes_temporary_file_and_preserves_original(self):
        with mock.patch.object(update.json, "dump", side_effect=OSError("disk full")):
            self.assertEqual(self.run_update(), 1)
        self.assert_untouched()

    def test_atomic_replace_uses_complete_sibling_file(self):
        real_replace = os.replace

        def replace(source, destination):
            self.assertEqual(source.parent, self.path.parent)
            self.assertEqual(destination, self.path)
            self.assertEqual(self.path.read_bytes(), self.original)
            complete = json.loads(source.read_text())
            self.assertEqual(set(complete), {"version", "tag", *update.ASSETS})
            real_replace(source, destination)

        with mock.patch.object(update.os, "replace", side_effect=replace) as replaced:
            self.assertEqual(self.run_update(), 0)
        replaced.assert_called_once()

    def test_downloads_are_read_in_bounded_chunks(self):
        name = next(iter(self.payloads))
        payload = b"\xff\x00" * (update.CHUNK_SIZE + 19)
        self.payloads[name] = payload
        self.release["assets"][0]["size"] = len(payload)
        self.release["assets"][0]["digest"] = "sha256:" + hashlib.sha256(payload).hexdigest()
        self.assertEqual(self.run_update(), 0)
        download = self.responses[1]
        self.assertGreaterEqual(len(download.read_sizes), 4)
        self.assertEqual(set(download.read_sizes), {update.CHUNK_SIZE})

    def test_credentials_are_sent_only_to_api_and_gh_token_takes_precedence(self):
        os.environ.update(GH_TOKEN="gh-secret", GITHUB_TOKEN="github-secret")
        self.assertEqual(self.run_update(), 0)
        self.assertEqual(self.requests[0].get_header("Authorization"), "Bearer gh-secret")
        for request in self.requests[1:]:
            self.assertIsNone(request.get_header("Authorization"))
            self.assertTrue(request.full_url.startswith(update.DOWNLOAD_ROOT))

    def test_github_token_fallback(self):
        os.environ.update(GH_TOKEN="", GITHUB_TOKEN="github-secret")
        self.assertEqual(self.run_update(), 0)
        self.assertEqual(self.requests[0].get_header("Authorization"), "Bearer github-secret")

    def test_unauthenticated_api_works(self):
        self.assertEqual(self.run_update(), 0)
        self.assertIsNone(self.requests[0].get_header("Authorization"))

    def test_api_credentials_are_stripped_on_redirects(self):
        handler = update.SafeRedirectHandler()
        request = urllib.request.Request(
            update.API_ROOT + "/latest",
            headers={"Authorization": "Bearer secret", "User-Agent": "test"},
        )
        for url in ("https://untrusted.invalid/asset", update.API_ROOT + "/other"):
            with self.subTest(url=url):
                redirected = handler.redirect_request(request, None, 302, "Found", {}, url)
                self.assertIsNone(redirected.get_header("Authorization"))
                self.assertEqual(redirected.get_header("User-agent"), "test")
                self.assertEqual(request.get_header("Authorization"), "Bearer secret")


class WrapperTests(unittest.TestCase):
    def test_wrapper_works_from_another_directory_and_forwards_arguments(self):
        root = Path(update.__file__).resolve().parent
        with tempfile.TemporaryDirectory() as directory:
            # A fake python3 checks exact argv; no Python subprocess can use the network.
            fake_python = Path(directory) / "python3"
            fake_python.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\nexit 17\n')
            fake_python.chmod(0o755)
            result = subprocess.run(
                ["sh", str(root / "update.sh"), "--sources", "a path/sources.json", "--force"],
                cwd=directory,
                env={**os.environ, "PATH": directory + os.pathsep + os.environ["PATH"]},
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(result.returncode, 17)
        self.assertEqual(
            result.stdout.splitlines(),
            [str(root / "update.py"), "--sources", "a path/sources.json", "--force"],
        )

    def test_default_sources_path_is_beside_script_not_working_directory(self):
        with mock.patch.object(update, "update") as updater:
            self.assertEqual(update.main([]), 0)
        updater.assert_called_once_with(
            Path(update.__file__).resolve().with_name("sources.json"), tag=None, force=False
        )


if __name__ == "__main__":
    unittest.main()
