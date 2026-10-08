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

    def test_configure_preserves_private_values_and_creates_private_backup(self):
        self.existing_assets()
        private = "# comment\nTUNNEL_TOKEN='private-$value'\nCUSTOM_SETTING=keep\nSTRONGHOLD_IMAGE=original:tag\n"
        (self.service / ".env").write_text(private)
        original = "name: stronghold\nservices: {}\n"
        (self.service / "compose.yaml").write_text(original)
        with patch.object(deploy, "_image_id", return_value="sha256:" + "f" * 64), patch.object(deploy, "_run", return_value="") as run:
            result = deploy.configure(self.root, self.pin, self.source)
        changed = (self.service / ".env").read_text()
        self.assertIn("TUNNEL_TOKEN='private-$value'", changed)
        self.assertIn("CUSTOM_SETTING=keep", changed)
        self.assertEqual(stat.S_IMODE((self.service / ".env").stat().st_mode), 0o600)
        backup = self.service / "rollback" / result["rollback"]
        self.assertEqual((backup / "compose.yaml").read_text(), original)
        self.assertIn("TUNNEL_TOKEN='private-$value'", (backup / ".env").read_text())
        self.assertIn("stronghold-protocol:rollback-", (backup / ".env").read_text())
        self.assertEqual(stat.S_IMODE((backup / ".env").stat().st_mode), 0o600)
        self.assertIn("tag", run.call_args.args[0])
        self.assertEqual((self.root / ".env").resolve(), (self.service / ".env").resolve())
        self.assertNotIn("private-$value", json.dumps(result))

    def test_unknown_multiline_value_survives_managed_setting_rewrite(self):
        text = "PRIVATE='first\nVOICE_LANG=inside-secret\nlast'\nVOICE_LANG=cn\n"
        changed = deploy._rewrite_env(text, {"VOICE_LANG": "kr"})
        self.assertIn("PRIVATE='first\nVOICE_LANG=inside-secret\nlast'", changed)
        self.assertTrue(changed.endswith("VOICE_LANG='kr'\n"))

    def test_unterminated_private_quote_fails_without_echoing_value(self):
        with self.assertRaises(deploy.DeploymentError) as raised:
            deploy._rewrite_env("SECRET='private-value\n", {"VOICE_LANG": "kr"})
        self.assertNotIn("private-value", str(raised.exception))

    def test_configure_is_idempotent_and_keeps_first_pending_backup(self):
        self.existing_assets()
        (self.service / ".env").write_text("TUNNEL_TOKEN=private\n")
        (self.service / "compose.yaml").write_text("name: stronghold\n")
        with patch.object(deploy, "_image_id", return_value=None):
            first = deploy.configure(self.root, self.pin, self.source)
            second = deploy.configure(self.root, self.pin, self.source)
        self.assertTrue(first["configured"])
        self.assertFalse(second["configured"])
        self.assertEqual((self.service / ".pending-rollback").read_text().strip(), first["rollback"])
        self.assertEqual(len(list((self.service / "rollback").iterdir())), 1)

    def existing_runtime(self):
        (self.service / ".env").write_text("TUNNEL_TOKEN=private-token\nSTRONGHOLD_IMAGE=original:tag\n")
        (self.service / "compose.yaml").write_text("name: stronghold\nservices: {}\n")

    def test_build_freezes_same_production_tag_before_docker_overwrites_it(self):
        self.existing_runtime()
        target = deploy.image_name(self.pin)
        with patch.object(deploy, "_image_id", return_value="old-image"), patch.object(deploy, "_desired_image_name", return_value=target), patch.object(deploy, "_desired_image_id", return_value="new-image"), patch.object(deploy, "_run", return_value="") as run:
            result = deploy.build(self.root, self.pin, self.source)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(commands[0][:3], ["docker", "image", "tag"])
        self.assertEqual(commands[1][:2], ["docker", "build"])
        backup = self.service / "rollback" / result["rollback"]
        metadata = json.loads((backup / "backup.json").read_text())
        self.assertEqual(metadata["image"], "old-image")
        self.assertEqual(metadata["reason"], "build")
        self.assertEqual((self.service / ".pending-rollback").read_text().strip(), result["rollback"])
        self.assertNotIn("private-token", json.dumps(result))

    def test_build_different_development_tag_does_not_create_production_snapshot(self):
        self.existing_runtime()
        with patch.object(deploy, "_image_id", return_value="old-image"), patch.object(deploy, "_desired_image_name", return_value="production:tag"), patch.object(deploy, "_snapshot") as snapshot, patch.object(deploy, "_run", return_value="") as run:
            result = deploy.build(self.root, self.pin, self.source, image="development:tag")
        snapshot.assert_not_called()
        self.assertEqual(run.call_args.args[0][:2], ["docker", "build"])
        self.assertIsNone(result["rollback"])
        self.assertFalse((self.service / ".pending-rollback").exists())

    def test_cached_same_image_build_discards_only_new_pending_marker(self):
        self.existing_runtime()
        (self.service / ".latest-rollback").write_text("20261007T120000.000001Z\n")
        target = deploy.image_name(self.pin)
        with patch.object(deploy, "_image_id", return_value="same-image"), patch.object(deploy, "_desired_image_name", return_value=target), patch.object(deploy, "_desired_image_id", return_value="same-image"), patch.object(deploy, "_run", return_value=""):
            result = deploy.build(self.root, self.pin, self.source)
        self.assertIsNone(result["rollback"])
        self.assertFalse((self.service / ".pending-rollback").exists())
        self.assertEqual((self.service / ".latest-rollback").read_text(), "20261007T120000.000001Z\n")
        self.assertEqual(len(list((self.service / "rollback").iterdir())), 1)

    def test_failed_build_retains_complete_previous_image_backup(self):
        self.existing_runtime()
        target = deploy.image_name(self.pin)

        def command(args, **unused):
            if args[:2] == ["docker", "build"]:
                raise deploy.DeploymentError("Build failed")
            return ""

        with patch.object(deploy, "_image_id", return_value="old-image"), patch.object(deploy, "_desired_image_name", return_value=target), patch.object(deploy, "_run", side_effect=command):
            with self.assertRaises(deploy.DeploymentError):
                deploy.build(self.root, self.pin, self.source)
        name = (self.service / ".pending-rollback").read_text().strip()
        backup = self.service / "rollback" / name
        self.assertTrue(all((backup / file).is_file() for file in ("compose.yaml", ".env", "backup.json")))
        self.assertIn("STRONGHOLD_IMAGE=original:tag", (self.service / ".env").read_text())

    def test_missing_old_image_reference_commits_only_verified_running_app(self):
        self.existing_runtime()

        def command(args, **unused):
            if args[:3] == ["docker", "image", "tag"]:
                return None
            if args[:2] == ["docker", "inspect"]:
                return "old-image true\n"
            return "sha256:committed-image\n"

        with patch.object(deploy, "_image_id", return_value="old-image"), patch.object(deploy, "_application_container", return_value="app-container"), patch.object(deploy, "_run", side_effect=command) as run:
            name = deploy._snapshot(self.service)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(commands[2][:4], ["docker", "commit", "--pause=true", "app-container"])
        metadata = json.loads((self.service / "rollback" / name / "backup.json").read_text())
        self.assertEqual(metadata["image_preservation"], "container-commit")
        self.assertEqual(commands[2][4], metadata["backup_image"])
        self.assertIn(metadata["backup_image"], (self.service / "rollback" / name / ".env").read_text())

    def test_snapshot_refuses_to_commit_stopped_or_replaced_application(self):
        self.existing_runtime()
        for state in ("old-image false", "different-image true"):
            with self.subTest(state=state):
                def command(args, **unused):
                    if args[:3] == ["docker", "image", "tag"]:
                        return None
                    if args[:2] == ["docker", "inspect"]:
                        return state
                    self.fail("Snapshot committed an unverified application container")

                with patch.object(deploy, "_image_id", return_value="old-image"), patch.object(deploy, "_application_container", return_value="app-container"), patch.object(deploy, "_run", side_effect=command):
                    with self.assertRaises(deploy.DeploymentError):
                        deploy._snapshot(self.service)
                self.assertFalse((self.service / ".pending-rollback").exists())
                self.assertFalse(list((self.service / "rollback").iterdir()))

    def test_failed_container_commit_leaves_runtime_and_pending_state_unchanged(self):
        self.existing_runtime()
        original_env = (self.service / ".env").read_bytes()
        original_compose = (self.service / "compose.yaml").read_bytes()

        def command(args, **unused):
            if args[:3] == ["docker", "image", "tag"]:
                return None
            if args[:2] == ["docker", "inspect"]:
                return "old-image true\n"
            raise deploy.DeploymentError("Docker commit failed; output withheld")

        with patch.object(deploy, "_image_id", return_value="old-image"), patch.object(deploy, "_application_container", return_value="app-container"), patch.object(deploy, "_run", side_effect=command):
            with self.assertRaises(deploy.DeploymentError):
                deploy._snapshot(self.service)
        self.assertFalse((self.service / ".pending-rollback").exists())
        self.assertFalse(list((self.service / "rollback").iterdir()))
        self.assertEqual((self.service / ".env").read_bytes(), original_env)
        self.assertEqual((self.service / "compose.yaml").read_bytes(), original_compose)

    def test_incomplete_pending_snapshot_cannot_be_reused(self):
        self.existing_runtime()
        name = "20261007T120000.000001Z"
        folder = self.service / "rollback" / name
        folder.mkdir(parents=True)
        (folder / "compose.yaml").write_text("name: stronghold\n")
        (self.service / ".pending-rollback").write_text(name + "\n")
        with patch.object(deploy, "_run") as run:
            with self.assertRaises(deploy.DeploymentError):
                deploy._snapshot(self.service)
        run.assert_not_called()

    def test_up_of_unchanged_image_after_failed_build_preserves_previous_latest(self):
        self.existing_runtime()
        (self.service / ".latest-rollback").write_text("20261007T120000.000001Z\n")
        with patch.object(deploy, "_image_id", return_value="same-image"), patch.object(deploy, "_run", return_value=""):
            deploy._snapshot(self.service, reason="build")
        with patch.object(deploy, "_image_id", return_value="same-image"), patch.object(deploy, "_desired_image_id", return_value="same-image"), patch.object(deploy, "_run", return_value=""), patch.object(deploy, "_health", return_value={"ok": True, "app": "0.2.1"}):
            result = deploy.up(self.root, self.pin, self.source)
        self.assertIsNone(result["rollback"])
        self.assertFalse((self.service / ".pending-rollback").exists())
        self.assertEqual((self.service / ".latest-rollback").read_text(), "20261007T120000.000001Z\n")

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

    def test_successful_up_consumes_pending_rollback_without_changing_snapshot(self):
        self.existing_assets()
        (self.service / ".env").write_text("TUNNEL_TOKEN=private\n")
        (self.service / "compose.yaml").write_text("name: stronghold\n")
        with patch.object(deploy, "_image_id", return_value=None):
            configured = deploy.configure(self.root, self.pin, self.source)
        with patch.object(deploy, "_run", return_value=""), patch.object(deploy, "_health", return_value={"ok": True, "app": "0.2.1", "version": 1}):
            result = deploy.up(self.root, self.pin, self.source)
        self.assertEqual(result["version"], "0.2.1")
        self.assertEqual(result["rollback"], configured["rollback"])
        self.assertFalse((self.service / ".pending-rollback").exists())
        self.assertEqual((self.service / ".latest-rollback").read_text().strip(), configured["rollback"])

    def test_failed_up_preserves_pending_rollback(self):
        self.existing_assets()
        (self.service / ".env").write_text("TUNNEL_TOKEN=private\n")
        (self.service / "compose.yaml").write_text("name: stronghold\n")
        with patch.object(deploy, "_image_id", return_value=None):
            configured = deploy.configure(self.root, self.pin, self.source)
        with patch.object(deploy, "_run", side_effect=["", deploy.DeploymentError("failed")]):
            with self.assertRaises(deploy.DeploymentError):
                deploy.up(self.root, self.pin, self.source)
        self.assertEqual((self.service / ".pending-rollback").read_text().strip(), configured["rollback"])

    def test_wrong_app_version_is_rejected_by_health(self):
        response = BytesIO(json.dumps({"ok": True, "app": "0.1.4", "version": 1}).encode())
        with patch.object(deploy.urllib.request, "urlopen", return_value=response):
            with self.assertRaises(deploy.DeploymentError):
                deploy._health(3000, attempts=1, expected_version="0.2.1")

    def test_wrong_deployed_version_preserves_pending_rollback(self):
        self.existing_assets()
        (self.service / ".env").write_text("TUNNEL_TOKEN=private\n")
        (self.service / "compose.yaml").write_text("name: stronghold\n")
        with patch.object(deploy, "_image_id", return_value=None):
            configured = deploy.configure(self.root, self.pin, self.source)
        response = BytesIO(json.dumps({"ok": True, "app": "0.1.4", "version": 1}).encode())
        with patch.object(deploy, "_run", return_value=""), patch.object(deploy.urllib.request, "urlopen", return_value=response):
            with self.assertRaises(deploy.DeploymentError):
                deploy.up(self.root, self.pin, self.source)
        self.assertEqual((self.service / ".pending-rollback").read_text().strip(), configured["rollback"])
        self.assertFalse((self.service / ".latest-rollback").exists())

    def test_restarting_same_image_preserves_previous_rollback_marker(self):
        (self.service / ".latest-rollback").write_text("20261007T120000.000001Z\n")
        with patch.object(deploy, "_run", return_value=""), patch.object(deploy, "_image_id", return_value="same-image"), patch.object(deploy, "_desired_image_id", return_value="same-image"), patch.object(deploy, "_snapshot") as snapshot, patch.object(deploy, "_health", return_value={"ok": True, "app": "0.2.1"}):
            deploy.up(self.root, self.pin, self.source)
        snapshot.assert_not_called()
        self.assertEqual((self.service / ".latest-rollback").read_text(), "20261007T120000.000001Z\n")

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
        (self.service / ".pending-rollback").write_text("..\n")
        with self.assertRaises(deploy.DeploymentError):
            deploy._snapshot(self.service)

    def test_rollback_restores_private_env_and_original_compose(self):
        self.existing_assets()
        original_env = "TUNNEL_TOKEN=private\nSTRONGHOLD_IMAGE=original:tag\n"
        original_config = "name: stronghold\nservices: {}\n"
        (self.service / ".env").write_text(original_env)
        (self.service / "compose.yaml").write_text(original_config)
        with patch.object(deploy, "_image_id", return_value=None):
            configured = deploy.configure(self.root, self.pin, self.source)
        with patch.object(deploy, "_image_id", return_value=None), patch.object(deploy, "_run", return_value=""), patch.object(deploy, "_health", return_value={"ok": True, "app": "0.2.1"}):
            result = deploy.rollback(self.root, backup=configured["rollback"])
        self.assertEqual((self.service / ".env").read_text(), original_env)
        self.assertEqual((self.service / "compose.yaml").read_text(), original_config)
        self.assertTrue(result["healthy"])
        self.assertNotIn("private", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
