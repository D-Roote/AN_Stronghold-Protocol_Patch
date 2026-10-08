"""Deployment safety checks with fake releases, private settings and Docker calls."""
import hashlib
import importlib.util
import json
import stat
import tempfile
import unittest
import zipfile
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("stronghold_deploy", Path(__file__).parents[1] / "scripts/deploy.py")
deploy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deploy)

class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / ".build/Stronghold-Protocol"
        self.source.mkdir(parents=True)
        (self.source / "Dockerfile").write_text("FROM node:22-alpine\n")
        (self.source / "package.json").write_text('{"version":"0.2.1"}')
        self.service = self.root / "service"
        self.service.mkdir()
        templates = self.root / "deploy"
        templates.mkdir()
        for name in ("compose.yaml", "compose.dev.yaml"):
            (templates / name).write_text('name: stronghold\nservices:\n  stronghold:\n    image: "${STRONGHOLD_IMAGE}"\n')
        (templates / "env.example").write_text("TUNNEL_TOKEN=\n")
        self.manifest = {"count": 1, "groups": {"model": {"item": {"path": "/assets/local/model/item.png"}}}}
        self.pin = {"repository": "https://github.com/example/Stronghold-Protocol.git", "ref": "v0.2.1",
                    "version": "0.2.1", "commit": "a" * 40,
                    "release": {"url": "https://github.com/example/Stronghold-Protocol/releases/download/v0.2.1/full.zip",
                                "sha256": "b" * 64}}

    def existing_assets(self):
        local = self.service / "assets/local/model"
        local.mkdir(parents=True)
        (local / "item.png").write_bytes(b"PNG")
        manifest = self.service / "data/local-assets.json"
        manifest.parent.mkdir()
        manifest.write_text(json.dumps(self.manifest))
        (self.service / "assets-release.json").write_text(json.dumps({
            "release_sha256": self.pin["release"]["sha256"],
            "manifest_sha256": deploy._sha(manifest)}))
        return local.parent, manifest

    def release(self, *, additions=None, manifest=None, duplicate=False, symlink=False):
        archive = self.root / "full.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("Stronghold-Protocol/data/local-assets.json", json.dumps(manifest or self.manifest))
            bundle.writestr("Stronghold-Protocol/public/assets/local/model/item.png", b"PNG")
            bundle.writestr("Stronghold-Protocol/public/assets/audio/voice/cn/private.mp3", b"CN")
            bundle.writestr("Stronghold-Protocol/server/index.js", b"unpatched runtime")
            for name, content in additions or []:
                bundle.writestr("Stronghold-Protocol/public/assets/local/" + name, content)
            if duplicate:
                bundle.writestr("Stronghold-Protocol/data/local-assets.json", json.dumps(self.manifest))
            if symlink:
                info = zipfile.ZipInfo("Stronghold-Protocol/public/assets/local/linked")
                info.create_system = 3
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                bundle.writestr(info, "../../../private")
        self.pin["release"]["sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
        return archive

    def test_unknown_multiline_value_survives_managed_setting_rewrite(self):
        text = "PRIVATE='first\nVOICE_LANG=inside-secret\nlast'\nVOICE_LANG=cn\n"
        changed = deploy._rewrite_env(text, {"VOICE_LANG": "kr"})
        self.assertIn("PRIVATE='first\nVOICE_LANG=inside-secret\nlast'", changed)
        self.assertTrue(changed.endswith("VOICE_LANG='kr'\n"))

    def test_unterminated_private_quote_fails_without_echoing_value(self):
        with self.assertRaises(deploy.DeploymentError) as raised:
            deploy._rewrite_env("SECRET='private-value\n", {"VOICE_LANG": "kr"})
        self.assertNotIn("private-value", str(raised.exception))

    def existing_runtime(self):
        (self.service / ".env").write_text("TUNNEL_TOKEN=private-token\nSTRONGHOLD_IMAGE=original:tag\n")
        (self.service / "compose.yaml").write_text("name: stronghold\nservices: {}\n")

    def test_configure_preserves_private_values_without_backup_or_docker(self):
        self.existing_assets()
        self.existing_runtime()
        with (self.service / ".env").open("a") as stream:
            stream.write("CUSTOM_SETTING=keep\n")
        with patch.object(deploy, "_run") as run:
            result = deploy.configure(self.root, self.pin, self.source)
        run.assert_not_called()
        env = (self.service / ".env").read_text()
        self.assertIn("TUNNEL_TOKEN=private-token", env)
        self.assertIn("CUSTOM_SETTING=keep", env)
        self.assertEqual(stat.S_IMODE((self.service / ".env").stat().st_mode), 0o600)
        self.assertEqual((self.root / ".env").resolve(), (self.service / ".env").resolve())
        self.assertFalse((self.service / "rollback").exists())
        self.assertFalse((self.service / ".pending-rollback").exists())
        self.assertNotIn("private-token", json.dumps(result))
        self.assertNotIn("rollback", result)

    def test_configure_is_idempotent_without_creating_runtime_backups(self):
        self.existing_assets()
        self.existing_runtime()
        with patch.object(deploy, "_run") as run:
            first = deploy.configure(self.root, self.pin, self.source)
            second = deploy.configure(self.root, self.pin, self.source)
        run.assert_not_called()
        self.assertTrue(first["configured"])
        self.assertFalse(second["configured"])
        self.assertFalse((self.service / "rollback").exists())

    def test_build_only_builds_requested_image_without_old_image_lookup(self):
        self.existing_runtime()
        with patch.object(deploy, "_run", return_value="") as run:
            result = deploy.build(self.root, self.pin, self.source, image="stronghold:new", fetch_assets="0")
        run.assert_called_once_with(["docker", "build", "--build-arg", "FETCH_ASSETS=0", "--build-arg",
                                    "VOICE_LANG=kr", "--tag", "stronghold:new", self.source], root=self.root)
        self.assertEqual(result, {"image": "stronghold:new", "built": True})
        self.assertFalse((self.service / "rollback").exists())
        self.assertFalse((self.service / ".pending-rollback").exists())

    def test_failed_build_preserves_private_configuration_without_creating_backup(self):
        self.existing_runtime()
        original = (self.service / ".env").read_bytes()
        with patch.object(deploy, "_run", side_effect=deploy.DeploymentError("Build failed")) as run:
            with self.assertRaises(deploy.DeploymentError):
                deploy.build(self.root, self.pin, self.source)
        self.assertEqual(run.call_count, 1)
        self.assertEqual((self.service / ".env").read_bytes(), original)
        self.assertFalse((self.service / "rollback").exists())

    def test_up_only_starts_and_checks_health_while_preserving_existing_backup_files(self):
        self.existing_runtime()
        existing = self.service / "rollback/existing"
        existing.mkdir(parents=True)
        (existing / "preserved").write_text("keep")
        (self.service / ".pending-rollback").write_text("legacy-marker\n")
        (self.service / ".latest-rollback").write_text("older-marker\n")
        with patch.object(deploy, "_run", return_value="") as run, patch.object(deploy, "_health", return_value={"ok": True, "app": "0.2.1"}) as health:
            result = deploy.up(self.root, self.pin, self.source)
        self.assertEqual([call.args[0] for call in run.call_args_list], [
            [*deploy._compose(self.service), "config", "--quiet"],
            [*deploy._compose(self.service), "up", "-d", "--no-build", "--wait", "--wait-timeout", "90"]])
        health.assert_called_once_with(3000, expected_version="0.2.1")
        self.assertEqual(result, {"running": True, "dev": False, "version": "0.2.1"})
        self.assertEqual((existing / "preserved").read_text(), "keep")
        self.assertEqual((self.service / ".pending-rollback").read_text(), "legacy-marker\n")
        self.assertEqual((self.service / ".latest-rollback").read_text(), "older-marker\n")
        self.assertEqual(len(list((self.service / "rollback").iterdir())), 1)

    def test_failed_up_does_not_create_backup_or_rewrite_private_configuration(self):
        self.existing_runtime()
        original = (self.service / ".env").read_bytes()
        with patch.object(deploy, "_run", side_effect=["", deploy.DeploymentError("Up failed")]) as run, patch.object(deploy, "_health") as health:
            with self.assertRaises(deploy.DeploymentError):
                deploy.up(self.root, self.pin, self.source)
        self.assertEqual(run.call_count, 2)
        health.assert_not_called()
        self.assertEqual((self.service / ".env").read_bytes(), original)
        self.assertFalse((self.service / "rollback").exists())

    def test_up_rejects_wrong_app_version_without_creating_backup(self):
        self.existing_runtime()
        response = BytesIO(json.dumps({"ok": True, "app": "0.1.4", "version": 1}).encode())
        with patch.object(deploy, "_run", return_value=""), patch.object(deploy.urllib.request, "urlopen", return_value=response):
            with self.assertRaises(deploy.DeploymentError):
                deploy.up(self.root, self.pin, self.source)
        self.assertFalse((self.service / "rollback").exists())
        self.assertFalse((self.service / ".pending-rollback").exists())

    def test_configure_rejects_wrong_root_env_link_before_mutating_runtime(self):
        self.existing_assets()
        (self.service / ".env").write_text("TUNNEL_TOKEN=private\n")
        original = (self.service / ".env").read_bytes()
        (self.root / ".env").symlink_to("somewhere-else.env")
        with self.assertRaises(deploy.DeploymentError):
            deploy.configure(self.root, self.pin, self.source)
        self.assertEqual((self.service / ".env").read_bytes(), original)

    def test_release_extracts_only_paired_local_assets_and_is_idempotent(self):
        archive = self.release()
        result = deploy.assets(self.root, self.pin, archive=archive)
        files = {str(path.relative_to(self.service)) for path in self.service.rglob("*") if path.is_file()}
        self.assertEqual(files, {"assets/releases/v0.2.1/assets/local/model/item.png",
                                "assets/releases/v0.2.1/data/local-assets.json", "assets/releases/v0.2.1/release.json"})
        self.assertEqual(result["entries"], 1)
        self.assertEqual(deploy.assets(self.root, self.pin, archive=archive), result)

    def test_release_sha_mismatch_does_not_publish_assets(self):
        archive = self.release()
        self.pin["release"]["sha256"] = "0" * 64
        with self.assertRaises(deploy.DeploymentError):
            deploy.assets(self.root, self.pin, archive=archive)
        self.assertFalse((self.service / "assets/releases/v0.2.1").exists())

    def test_prepared_manifest_changes_are_detected_on_reuse(self):
        archive = self.release()
        result = deploy.assets(self.root, self.pin, archive=archive)
        manifest = Path(result["manifest"])
        data = json.loads(manifest.read_text())
        data["unexpected"] = "edited"
        manifest.write_text(json.dumps(data))
        with self.assertRaises(deploy.DeploymentError):
            deploy.assets(self.root, self.pin, archive=archive)

    def test_existing_version_directory_without_provenance_is_preserved(self):
        archive = self.release()
        destination = self.service / "assets/releases/v0.2.1"
        destination.mkdir(parents=True)
        marker = destination / "preserve"
        marker.write_text("keep")
        with self.assertRaises(deploy.DeploymentError):
            deploy.assets(self.root, self.pin, archive=archive)
        self.assertEqual(marker.read_text(), "keep")

    def test_release_zip_traversal_cannot_escape_stage(self):
        archive = self.release(additions=[("../../../../escaped", b"unsafe")])
        with self.assertRaises(deploy.DeploymentError):
            deploy.assets(self.root, self.pin, archive=archive)
        self.assertFalse((self.service / "assets/releases/v0.2.1").exists())
        self.assertFalse((self.root / "escaped").exists())

    def test_release_symlink_is_rejected(self):
        archive = self.release(symlink=True)
        with self.assertRaises(deploy.DeploymentError):
            deploy.assets(self.root, self.pin, archive=archive)
        self.assertFalse((self.service / "assets/releases/v0.2.1").exists())

    def test_missing_manifest_file_is_rejected_before_publish(self):
        manifest = {"count": 1, "groups": {"model": {"item": {"path": "/assets/local/model/missing.png"}}}}
        archive = self.release(manifest=manifest)
        with self.assertRaises(deploy.DeploymentError):
            deploy.assets(self.root, self.pin, archive=archive)
        self.assertFalse((self.service / "assets/releases/v0.2.1").exists())

    def test_invalid_manifest_count_is_rejected(self):
        local, manifest = self.existing_assets()
        self.manifest["count"] = 2
        manifest.write_text(json.dumps(self.manifest))
        with self.assertRaises(deploy.DeploymentError):
            deploy.validate_assets(local, manifest)

    def test_subprocess_error_hides_secret_bearing_output(self):
        result = deploy.subprocess.CompletedProcess(["docker", "compose"], 1, "private-token", "private-token")
        with patch.object(deploy.subprocess, "run", return_value=result):
            with self.assertRaises(deploy.DeploymentError) as raised:
                deploy._run(["docker", "compose", "config"])
        self.assertNotIn("private-token", str(raised.exception))

    def test_wrong_app_version_is_rejected_by_health(self):
        response = BytesIO(json.dumps({"ok": True, "app": "0.1.4", "version": 1}).encode())
        with patch.object(deploy.urllib.request, "urlopen", return_value=response):
            with self.assertRaises(deploy.DeploymentError):
                deploy._health(3000, attempts=1, expected_version="0.2.1")

    def test_verify_matches_actual_mounted_manifest(self):
        local, manifest = self.existing_assets()
        expected = deploy.validate_assets(local, manifest)
        mounts = [{"Type": "bind", "Source": str(local), "Destination": "/app/public/assets/local", "RW": False},
                  {"Type": "bind", "Source": str(manifest), "Destination": "/app/data/local-assets.json", "RW": False}]

        def command(args, **unused):
            if "ps" in args:
                return "container-id\n"
            if "{{json .Mounts}}" in args:
                return json.dumps(mounts)
            if args[1] == "exec":
                return json.dumps({"sha256": expected["manifest_sha256"], "entries": 1, "references": 1, "missing": 0})
            if "{{.Image}}" in args:
                return "sha256:image\n"
            return "Voices: KR · 1 operators · 1 files · 0 errors"

        with patch.object(deploy, "_run", side_effect=command) as run, patch.object(deploy, "_health", return_value={"ok": True, "app": "0.2.1"}), patch.object(deploy, "_korean_pack", return_value={"lang": "ko", "listed": True}):
            result = deploy.verify(self.root, self.pin, self.source)
        self.assertEqual(result["assets"], expected)
        self.assertTrue(result["healthy"])
        self.assertTrue(any(call.args[0][1] == "exec" for call in run.call_args_list))

    def test_verify_detects_old_bound_manifest_inode(self):
        local, manifest = self.existing_assets()
        mounts = [{"Type": "bind", "Source": str(local), "Destination": "/app/public/assets/local", "RW": False},
                  {"Type": "bind", "Source": str(manifest), "Destination": "/app/data/local-assets.json", "RW": False}]

        def command(args, **unused):
            if "ps" in args:
                return "container-id\n"
            if "{{json .Mounts}}" in args:
                return json.dumps(mounts)
            if args[1] == "exec":
                return json.dumps({"sha256": "0" * 64, "entries": 1, "references": 1, "missing": 0})
            self.fail("Verification continued after a mismatched mounted manifest")

        with patch.object(deploy, "_run", side_effect=command), patch.object(deploy, "_korean_pack", return_value={"lang": "ko", "listed": True}):
            with self.assertRaises(deploy.DeploymentError):
                deploy.verify(self.root, self.pin, self.source)

    def test_verify_rejects_writable_asset_mount(self):
        local, manifest = self.existing_assets()
        mounts = [{"Type": "bind", "Source": str(local), "Destination": "/app/public/assets/local", "RW": True}]
        with patch.object(deploy, "_run", side_effect=["container-id\n", json.dumps(mounts)]), patch.object(deploy, "_korean_pack", return_value={"lang": "ko", "listed": True}):
            with self.assertRaises(deploy.DeploymentError):
                deploy.verify(self.root, self.pin, self.source)

    def language_responses(self, **overrides):
        responses = {
            "/packs/index.json": {"packs": [{"type": "lang", "lang": "ko", "files": {
                "ui": "/i18n/ko.json", "data": "/data/i18n/ko.json"}}]},
            "/i18n/ko.json": {"_meta": {"lang": "ko"}, "原文": "번역"},
            "/data/i18n/ko.json": {"lang": "ko", "files": {"operators": {"name": "이름"}}},
        }
        responses.update(overrides)

        def fetch(url, **unused):
            value = responses[deploy.urlsplit(url).path]
            if isinstance(value, BaseException):
                raise value
            return BytesIO(value if isinstance(value, bytes) else json.dumps(value).encode())

        return fetch

    def test_korean_pack_requires_browser_index_ui_and_game_data(self):
        with patch.object(deploy.urllib.request, "urlopen", side_effect=self.language_responses()) as fetch:
            result = deploy._korean_pack(3000)
        self.assertEqual(result, {"lang": "ko", "listed": True, "ui_strings": 1, "game_tables": 1})
        self.assertEqual([deploy.urlsplit(call.args[0]).path for call in fetch.call_args_list],
                         ["/packs/index.json", "/i18n/ko.json", "/data/i18n/ko.json"])

    def test_korean_pack_missing_from_index_is_rejected(self):
        for index in ({"packs": []}, {"packs": [{"type": "lang", "lang": "en"}]}, {"packs": "invalid"}, {}):
            with self.subTest(index=index), patch.object(deploy.urllib.request, "urlopen", side_effect=self.language_responses(**{"/packs/index.json": index})):
                with self.assertRaises(deploy.DeploymentError):
                    deploy._korean_pack(3000)

    def test_korean_pack_duplicate_or_missing_file_index_is_rejected(self):
        entry = {"type": "lang", "lang": "ko", "files": {"ui": "/i18n/ko.json", "data": "/data/i18n/ko.json"}}
        indexes = [{"packs": [entry, entry]},
                   {"packs": [{"type": "lang", "lang": "ko", "files": {"ui": "/i18n/ko.json"}}]},
                   {"packs": [{"type": "lang", "lang": "ko", "files": {"ui": "https://external/private", "data": "/data/i18n/ko.json"}}]}]
        for index in indexes:
            with self.subTest(index=index), patch.object(deploy.urllib.request, "urlopen", side_effect=self.language_responses(**{"/packs/index.json": index})) as fetch:
                with self.assertRaises(deploy.DeploymentError):
                    deploy._korean_pack(3000)
                self.assertEqual(fetch.call_count, 1)

    def test_korean_pack_permission_errors_and_http_errors_are_rejected(self):
        for endpoint in ("/packs/index.json", "/i18n/ko.json", "/data/i18n/ko.json"):
            for error in (PermissionError("EACCES private-token"), HTTPError("http://127.0.0.1" + endpoint, 404, "private-token", {}, None)):
                with self.subTest(endpoint=endpoint, error=type(error).__name__), patch.object(deploy.urllib.request, "urlopen", side_effect=self.language_responses(**{endpoint: error})):
                    with self.assertRaises(deploy.DeploymentError) as raised:
                        deploy._korean_pack(3000)
                    self.assertNotIn("private-token", str(raised.exception))

    def test_korean_pack_invalid_ui_metadata_or_no_strings_is_rejected(self):
        for ui in ({"_meta": {"lang": "en"}, "原文": "번역"}, {"_meta": {"lang": "ko"}}, b"not-json", []):
            with self.subTest(ui=ui), patch.object(deploy.urllib.request, "urlopen", side_effect=self.language_responses(**{"/i18n/ko.json": ui})):
                with self.assertRaises(deploy.DeploymentError):
                    deploy._korean_pack(3000)

    def test_korean_pack_invalid_game_data_language_or_no_tables_is_rejected(self):
        for game in ({"lang": "en", "files": {"operators": {}}}, {"lang": "ko", "files": {}}, {"lang": "ko"}, b"not-json"):
            with self.subTest(game=game), patch.object(deploy.urllib.request, "urlopen", side_effect=self.language_responses(**{"/data/i18n/ko.json": game})):
                with self.assertRaises(deploy.DeploymentError):
                    deploy._korean_pack(3000)

    def test_verify_stops_when_runtime_index_has_lost_korean_selection(self):
        with patch.object(deploy, "_run", return_value="container-id\n") as run, patch.object(deploy.urllib.request, "urlopen", side_effect=self.language_responses(**{"/packs/index.json": {"packs": []}})):
            with self.assertRaises(deploy.DeploymentError):
                deploy.verify(self.root, self.pin, self.source)
        self.assertEqual(run.call_count, 1)

    def test_malicious_snapshot_names_and_markers_are_rejected(self):
        (self.service / ".env").write_text("TUNNEL_TOKEN=private\n")
        (self.service / "compose.yaml").write_text("name: stronghold\n")
        for name in ("..", ".", "../private", "20261007T120000.000001Z/.."):
            with self.subTest(name=name), patch.object(deploy, "_run") as run:
                with self.assertRaises(deploy.DeploymentError):
                    deploy.rollback(self.root, backup=name)
                run.assert_not_called()
        for marker in (".pending-rollback", ".latest-rollback"):
            path = self.service / marker
            path.write_text("..\n")
            with patch.object(deploy, "_run") as run:
                with self.assertRaises(deploy.DeploymentError):
                    deploy.rollback(self.root)
                run.assert_not_called()
            path.unlink()

    def test_explicit_rollback_restores_existing_snapshot_without_creating_new_backup(self):
        self.existing_runtime()
        name = "20261007T120000.000001Z"
        backup = self.service / "rollback" / name
        backup.mkdir(parents=True)
        private = "TUNNEL_TOKEN=private-token\nSTRONGHOLD_IMAGE=previous:tag\n"
        compose = "name: stronghold\nservices: {}\n"
        (backup / ".env").write_text(private)
        (backup / "compose.yaml").write_text(compose)
        with patch.object(deploy, "_run", return_value="") as run, patch.object(deploy, "_health", return_value={"ok": True, "app": "0.1.4"}):
            result = deploy.rollback(self.root, backup=name)
        self.assertEqual([call.args[0] for call in run.call_args_list], [
            [*deploy._compose(self.service, env_file=backup / ".env", compose_file=backup / "compose.yaml"), "config", "--quiet"],
            [*deploy._compose(self.service), "up", "-d", "--no-build", "--wait", "--wait-timeout", "90"]])
        self.assertEqual((self.service / ".env").read_text(), private)
        self.assertEqual((self.service / "compose.yaml").read_text(), compose)
        self.assertEqual(stat.S_IMODE((self.service / ".env").stat().st_mode), 0o600)
        self.assertEqual(len(list((self.service / "rollback").iterdir())), 1)
        self.assertEqual((backup / ".env").read_text(), private)
        self.assertEqual(result, {"restored": name, "version": "0.1.4", "healthy": True})
        self.assertNotIn("private-token", json.dumps(result))

if __name__ == "__main__":
    unittest.main()
