"""Deployment safety checks with fake releases, private settings and Docker calls."""
import hashlib
import importlib.util
import json
import os
import shutil
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
        for name in deploy.GENERATED_FILES:
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

    def test_public_runtime_setting_ignores_keys_inside_private_multiline_values(self):
        text = "PRIVATE='first\nASSET_BUNDLE_DIR=/private-location\nlast'\nASSET_BUNDLE_DIR='./assets/my bundle' # public\n"
        self.assertEqual(deploy._runtime_setting(text, "ASSET_BUNDLE_DIR"), "./assets/my bundle")
        self.assertIsNone(deploy._runtime_setting(text, "ASSET_TLS_KEY"))
        value = "/tmp/player's assets"
        text = deploy._rewrite_env(text, {"ASSET_BUNDLE_DIR": value})
        self.assertEqual(deploy._runtime_setting(text, "ASSET_BUNDLE_DIR"), value)
        for setting in ('"${PRIVATE}"', "${HOME}/assets", "'first\nlast'"):
            with self.assertRaises(deploy.DeploymentError):
                deploy._runtime_setting(f"ASSET_BUNDLE_DIR={setting}\n", "ASSET_BUNDLE_DIR")

    def fake_asset_image(self):
        image = self.root / "fake-image"
        files = {"public/assets/char/avatar.png": b"PNG", "public/assets/audio/bgm/track.mp3": b"BGM",
                 "public/assets/audio/voice/kr/player/line.mp3": b"KR",
                 "public/assets/audio/voice/jp/player/line.mp3": b"JP",
                 "public/assets/local/stale.png": b"STALE",
                 "public/fonts/fonts.css": b"font-face", "public/fonts/font.woff2": b"WOFF"}
        for relative, content in files.items():
            file = image / relative
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(content)
        document = {"art": "/assets/char/avatar.png", "bgm": "/assets/audio/bgm/track.mp3",
                    "audio": {"voicePacks": {"kr": {"player": "/assets/audio/voice/kr/player/line.mp3"},
                                             "jp": {"player": "/assets/audio/voice/jp/player/line.mp3"}}},
                    "fonts": {"css": "/fonts/fonts.css", "face": "/fonts/font.woff2"}}
        (image / "data").mkdir()
        (image / "data/assets.json").write_text(json.dumps(document))
        (image / "package.json").write_text('{"version":"0.2.1"}')

        def command(args, **unused):
            args = list(args)
            if args[:3] == ["docker", "image", "inspect"]:
                return "sha256:" + "a" * 64
            if args[:2] == ["docker", "create"]:
                return "c" * 64
            if args[:2] == ["docker", "cp"]:
                relative = args[2].split(":/app/", 1)[1].removesuffix("/.")
                source, target = image / relative, Path(args[3])
                if source.is_dir():
                    shutil.copytree(source, target, dirs_exist_ok=True, symlinks=True)
                else:
                    shutil.copyfile(source, target)
                return ""
            if args[:3] == ["docker", "rm", "-f"]:
                return ""
            raise AssertionError(f"Unexpected Docker command: {args[:3]}")
        return image, command

    def test_asset_export_contains_all_media_fonts_and_verified_local_files(self):
        self.existing_assets()
        self.existing_runtime()
        _, command = self.fake_asset_image()
        with patch.object(deploy, "_run", side_effect=command) as run:
            result = deploy.export_asset_server(self.root, self.pin, self.source)
        destination = Path(result["bundle_directory"])
        self.assertEqual(result["files"], 7)
        self.assertFalse(result["reused"])
        self.assertEqual(result["bytes"], 26)
        self.assertFalse((destination / "assets/local/stale.png").exists())
        self.assertEqual((destination / "assets/local/model/item.png").read_bytes(), b"PNG")
        self.assertEqual((destination / "assets/audio/voice/jp/player/line.mp3").read_bytes(), b"JP")
        health = json.loads((destination / "healthz/assets").read_text())
        self.assertEqual(health["bundle"], result["bundle"])
        self.assertTrue(health["ok"])
        self.assertFalse((destination / "server").exists())
        self.assertTrue(all((self.service / name).exists() for name in deploy.ASSET_COMPOSE_FILES))
        self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE((destination / "healthz/assets").stat().st_mode), 0o644)
        self.assertTrue(any(call.args[0][:3] == ["docker", "rm", "-f"] for call in run.call_args_list))
        self.assertFalse(any(call.args[0][:2] == ["docker", "start"] for call in run.call_args_list))
        self.assertIn("TUNNEL_TOKEN=private-token", (self.service / ".env").read_text())
        self.assertNotIn("private-token", json.dumps(result))

    def test_asset_export_reuses_matching_inventory_and_repairs_changed_files(self):
        self.existing_assets()
        self.existing_runtime()
        _, command = self.fake_asset_image()
        with patch.object(deploy, "_run", side_effect=command):
            first = deploy.export_asset_server(self.root, self.pin, self.source)
        with patch.object(deploy, "_run", side_effect=command) as run:
            second = deploy.export_asset_server(self.root, self.pin, self.source)
        self.assertTrue(second["reused"])
        self.assertEqual(first["bundle"], second["bundle"])
        self.assertEqual(run.call_count, 1, "reuse only inspects the image identity")
        file = Path(second["bundle_directory"]) / "assets/char/avatar.png"
        file.write_bytes(b"BROKEN")
        with patch.object(deploy, "_run", side_effect=command):
            third = deploy.export_asset_server(self.root, self.pin, self.source)
        self.assertFalse(third["reused"])
        self.assertEqual(file.read_bytes(), b"PNG")
        self.assertFalse(list(file.parents[2].glob(".current.retired-*")))

    def test_asset_export_rejects_wrong_image_version_and_missing_or_mixed_voice_banks(self):
        self.existing_assets()
        self.existing_runtime()
        image, command = self.fake_asset_image()
        (image / "package.json").write_text('{"version":"0.1.3"}')
        with patch.object(deploy, "_run", side_effect=command), self.assertRaisesRegex(deploy.DeploymentError, "version"):
            deploy.export_asset_server(self.root, self.pin, self.source)
        (image / "package.json").write_text('{"version":"0.2.1"}')
        path = image / "data/assets.json"
        document = json.loads(path.read_text())
        del document["audio"]["voicePacks"]["jp"]
        path.write_text(json.dumps(document))
        with patch.object(deploy, "_run", side_effect=command), self.assertRaisesRegex(deploy.DeploymentError, "KR and JP"):
            deploy.export_asset_server(self.root, self.pin, self.source)
        document["audio"]["voicePacks"]["jp"] = {"player": "/assets/audio/voice/kr/player/line.mp3"}
        path.write_text(json.dumps(document))
        with patch.object(deploy, "_run", side_effect=command), self.assertRaisesRegex(deploy.DeploymentError, "KR and JP"):
            deploy.export_asset_server(self.root, self.pin, self.source)

    def test_asset_export_uses_configurable_directory_and_public_prefix(self):
        self.existing_assets()
        self.existing_runtime()
        with (self.service / ".env").open("a") as stream:
            stream.write("ASSET_BUNDLE_DIR='./assets/my bundle'\nASSET_PUBLIC_PATH=/stronghold/assets-v2\n")
        _, command = self.fake_asset_image()
        with patch.object(deploy, "_run", side_effect=command):
            result = deploy.export_asset_server(self.root, self.pin, self.source)
        self.assertEqual(Path(result["bundle_directory"]), self.service / "assets/my bundle")
        self.assertEqual(result["health_path"], "/stronghold/assets-v2/healthz/assets")
        self.assertEqual(deploy._runtime_setting((self.service / ".env").read_text(), "ASSET_BUNDLE_DIR"), result["bundle_directory"])
        for prefix in ("/../private", "/trailing/", "https://example.com", "/bad;include", "/$variable"):
            with self.subTest(prefix=prefix):
                (self.service / ".env").write_text(f"ASSET_PUBLIC_PATH='{prefix}'\n")
                with patch.object(deploy, "_run") as run, self.assertRaises(deploy.DeploymentError):
                    deploy.export_asset_server(self.root, self.pin, self.source)
                run.assert_not_called()

    def test_failed_asset_export_preserves_published_bundle_env_and_cleans_container(self):
        self.existing_assets()
        self.existing_runtime()
        image, command = self.fake_asset_image()
        with patch.object(deploy, "_run", side_effect=command):
            first = deploy.export_asset_server(self.root, self.pin, self.source)
        old_env = (self.service / ".env").read_bytes()
        old_inventory = (Path(first["bundle_directory"]) / ".bundle.json").read_bytes()
        (image / "public/assets/char/avatar.png").unlink()
        def changed_image(args, **options):
            return "sha256:" + "b" * 64 if args[:3] == ["docker", "image", "inspect"] else command(args, **options)
        with patch.object(deploy, "_run", side_effect=changed_image) as run, self.assertRaises(deploy.DeploymentError):
            deploy.export_asset_server(self.root, self.pin, self.source)
        self.assertEqual((self.service / ".env").read_bytes(), old_env)
        self.assertEqual((Path(first["bundle_directory"]) / ".bundle.json").read_bytes(), old_inventory)
        self.assertTrue(any(call.args[0][:3] == ["docker", "rm", "-f"] for call in run.call_args_list))
        self.assertFalse(list((self.service / "assets/direct").glob(".current.*")))

    def test_asset_export_rejects_symlinks_empty_files_and_non_generated_destinations(self):
        self.existing_assets()
        self.existing_runtime()
        image, command = self.fake_asset_image()
        target = self.service / "custom"
        target.mkdir()
        (target / "keep").write_text("user data")
        with patch.object(deploy, "_run") as run, self.assertRaises(deploy.DeploymentError):
            deploy.export_asset_server(self.root, self.pin, self.source, output=target)
        run.assert_not_called()
        self.assertEqual((target / "keep").read_text(), "user data")
        for destination in (self.root, self.service, self.source, self.root.parent):
            with self.subTest(destination=destination), self.assertRaises(deploy.DeploymentError):
                deploy.export_asset_server(self.root, self.pin, self.source, output=destination)
        file = image / "public/assets/char/avatar.png"
        file.write_bytes(b"")
        with patch.object(deploy, "_run", side_effect=command), self.assertRaises(deploy.DeploymentError):
            deploy.export_asset_server(self.root, self.pin, self.source)
        file.unlink()
        file.symlink_to(image / "public/fonts/fonts.css")
        with patch.object(deploy, "_run", side_effect=command), self.assertRaises(deploy.DeploymentError):
            deploy.export_asset_server(self.root, self.pin, self.source)

    def test_setup_asset_server_exports_after_build_and_configuration_without_start(self):
        archive = self.release()
        with patch.object(deploy, "_run", return_value=""), patch.object(deploy, "export_asset_server", return_value={"files": 7}) as export:
            result = deploy.setup(self.root, self.pin, self.source, archive=archive, asset_server=True, asset_output=Path("custom"))
        export.assert_called_once_with(self.root, self.pin, self.source, image=result["build"]["image"], output=Path("custom"))
        self.assertEqual(result["asset_server"], {"files": 7})
        self.assertNotIn("service", result)
        with self.assertRaises(deploy.DeploymentError):
            deploy.setup(self.root, self.pin, self.source, asset_output=Path("custom"))

    @unittest.skipUnless(shutil.which("docker"), "Docker CLI is required to parse generated Compose")
    def test_asset_compose_is_nginx_only_and_https_override_replaces_the_http_template(self):
        self.existing_assets()
        self.existing_runtime()
        templates = Path(__file__).resolve().parents[1] / "deploy"
        for name in deploy.GENERATED_FILES:
            (self.root / "deploy" / name).write_text((templates / name).read_text())
        _, command = self.fake_asset_image()
        with patch.object(deploy, "_run", side_effect=command):
            result = deploy.export_asset_server(self.root, self.pin, self.source)
        environment = {key: value for key, value in os.environ.items()
                       if key not in deploy._MANAGED | {"TUNNEL_TOKEN", "COMPOSE_FILE", "COMPOSE_PROJECT_NAME"}}
        with (self.service / ".env").open("a") as stream:
            stream.write("ASSET_TLS_CERT=./tls/fullchain.pem\nASSET_TLS_KEY=./tls/privkey.pem\nASSET_PUBLIC_PATH=/stronghold\n")
        for tls in (False, True):
            args = ["docker", "compose", "-f", "stack.assets-direct.yaml"]
            if tls:
                args.extend(["-f", "stack.assets-https.yaml"])
            parsed = deploy.subprocess.run([*args, "config", "--format", "json"], cwd=self.service,
                                           env=environment, capture_output=True, text=True)
            self.assertEqual(parsed.returncode, 0)
            config = json.loads(parsed.stdout)
            self.assertEqual(config["name"], "stronghold-assets")
            self.assertEqual(set(config["services"]), {"assets"})
            assets = config["services"]["assets"]
            self.assertNotIn("build", assets)
            self.assertEqual(assets["environment"]["ASSET_PUBLIC_PATH"], "/stronghold")
            self.assertEqual(assets["environment"]["NGINX_ENVSUBST_FILTER"].replace("$$", "$"), "^ASSET_PUBLIC_PATH$")
            volumes = {v["target"]: v for v in assets["volumes"]}
            self.assertEqual(volumes["/srv/stronghold-assets"]["source"], result["bundle_directory"])
            chosen = "nginx-assets-https.conf.template" if tls else "nginx-assets-http.conf.template"
            self.assertEqual(Path(volumes["/etc/nginx/templates/default.conf.template"]["source"]).name, chosen)
            self.assertTrue(all(v["read_only"] and not v["bind"]["create_host_path"] for v in volumes.values()))
            self.assertEqual({int(p["published"]) for p in assets["ports"]}, {8081, 443} if tls else {8081})

    def existing_runtime(self):
        (self.service / ".env").write_text("TUNNEL_TOKEN=private-token\nSTRONGHOLD_IMAGE=original:tag\n")
        (self.service / "stack.cf-tunnel.yaml").write_text("name: stronghold\nservices: {}\n")

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

    def test_known_generated_legacy_compose_files_are_retired(self):
        self.existing_assets()
        self.existing_runtime()
        bodies = {"compose.yaml": "original tunnel template", "compose.dev.yaml": "original dev template"}
        hashes = {name: hashlib.sha256(body.encode()).hexdigest() for name, body in bodies.items()}
        for name, body in bodies.items():
            (self.service / name).write_text(body)
        with patch.object(deploy, "LEGACY_COMPOSE_HASHES", hashes):
            deploy.configure(self.root, self.pin, self.source)
        self.assertTrue(all(not (self.service / name).exists() for name in bodies))
        self.assertTrue(all((self.service / name).is_file() for name in deploy.GENERATED_FILES))
        self.assertIn("TUNNEL_TOKEN=private-token", (self.service / ".env").read_text())

    def test_custom_legacy_compose_is_preserved_before_any_configuration_write(self):
        self.existing_assets()
        self.existing_runtime()
        legacy = self.service / "compose.yaml"
        legacy.write_text("custom deployment settings\n")
        original_env = (self.service / ".env").read_bytes()
        with self.assertRaisesRegex(deploy.DeploymentError, "custom changes"):
            deploy.configure(self.root, self.pin, self.source)
        self.assertEqual(legacy.read_text(), "custom deployment settings\n")
        self.assertEqual((self.service / ".env").read_bytes(), original_env)
        self.assertFalse((self.service / "stack.nginx.yaml").exists())

    def test_nginx_selection_and_development_selection_are_explicit(self):
        self.assertIn(self.service / "stack.nginx.yaml", deploy._compose(self.service, gateway="nginx"))
        self.assertIn(self.service / "stack.dev.yaml", deploy._compose(self.service, dev=True))
        for options in ({"gateway": "unknown"}, {"gateway": "nginx", "dev": True}):
            with self.subTest(options=options), self.assertRaises(deploy.DeploymentError):
                deploy._compose(self.service, **options)
        with patch.object(deploy, "_run", return_value="") as run, patch.object(deploy, "_health", return_value={"app": "0.2.1"}):
            deploy.up(self.root, self.pin, self.source, gateway="nginx")
        self.assertTrue(all((self.service / "stack.nginx.yaml") in call.args[0] for call in run.call_args_list))

    def test_build_only_builds_requested_image_without_old_image_lookup(self):
        self.existing_runtime()
        with patch.object(deploy, "_run", return_value="") as run:
            result = deploy.build(self.root, self.pin, self.source, image="stronghold:new", fetch_assets="0")
        run.assert_called_once_with(["docker", "build", "--build-arg", "FETCH_ASSETS=0", "--build-arg",
                                    "VOICE_LANG=kr", "--tag", "stronghold:new", self.source], root=self.root)
        self.assertEqual(result, {"image": "stronghold:new", "built": True})
        self.assertFalse((self.service / "rollback").exists())
        self.assertFalse((self.service / ".pending-rollback").exists())

    def test_setup_finishes_assets_image_and_compose_without_starting_service(self):
        self.existing_runtime()
        archive = self.release()
        with patch.object(deploy, "_run", return_value="") as run:
            result = deploy.setup(self.root, self.pin, self.source, archive=archive)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0][:2], ["docker", "build"])
        self.assertEqual(result["assets"]["entries"], 1)
        self.assertEqual(result["build"]["image"], result["configuration"]["image"])
        self.assertEqual(Path(result["service_directory"]), self.service)
        self.assertTrue(Path(result["compose"]).is_file())
        self.assertTrue((self.service / "stack.dev.yaml").is_file())
        env = (self.service / ".env").read_text()
        self.assertIn("TUNNEL_TOKEN=private-token", env)
        self.assertIn(f"STRONGHOLD_SOURCE_DIR='{self.source}'", env)
        self.assertIn("VOICE_LANG='kr'", env)
        self.assertEqual(stat.S_IMODE((self.service / ".env").stat().st_mode), 0o600)
        self.assertFalse((self.service / "rollback").exists())
        self.assertNotIn("service", result)

    @unittest.skipUnless(shutil.which("docker"), "Docker CLI is required to parse generated Compose")
    def test_generated_service_is_directly_usable_by_docker_compose(self):
        self.existing_runtime()
        actual_templates = Path(__file__).resolve().parents[1] / "deploy"
        for filename in deploy.GENERATED_FILES:
            (self.root / "deploy" / filename).write_text((actual_templates / filename).read_text())
        archive = self.release()
        with patch.object(deploy, "_run", return_value=""):
            result = deploy.setup(self.root, self.pin, self.source, archive=archive)
        environment = {key: value for key, value in os.environ.items()
                       if key not in deploy._MANAGED | {"TUNNEL_TOKEN", "COMPOSE_FILE", "COMPOSE_PROJECT_NAME"}}
        available = deploy.subprocess.run(["docker", "compose", "version"], capture_output=True, env=environment)
        if available.returncode:
            self.skipTest("Docker Compose plugin is unavailable")
        for filename, project, port in (("stack.cf-tunnel.yaml", "stronghold", 3000),
                                         ("stack.dev.yaml", "stronghold-ko-dev", 3100),
                                         ("stack.nginx.yaml", "stronghold", 3000)):
            parsed = deploy.subprocess.run(["docker", "compose", "-f", filename, "config", "--format", "json"],
                                           cwd=self.service, env=environment, capture_output=True, text=True)
            self.assertEqual(parsed.returncode, 0)
            config = json.loads(parsed.stdout)
            app = config["services"]["stronghold"]
            self.assertEqual(config["name"], project)
            self.assertEqual(app["image"], result["build"]["image"])
            self.assertEqual(Path(app["build"]["context"]), self.source)
            self.assertEqual(app["build"]["args"], {"FETCH_ASSETS": "1", "VOICE_LANG": "kr"})
            self.assertEqual(app["ports"][0]["host_ip"], "127.0.0.1")
            self.assertEqual(int(app["ports"][0]["published"]), port)
            self.assertTrue(all(mount["read_only"] for mount in app["volumes"]))
            self.assertEqual(app["environment"]["SP_MAX_BOTS"], "1")
            if filename == "stack.nginx.yaml":
                self.assertNotIn("cloudflared", config["services"])
                self.assertEqual(int(config["services"]["nginx"]["ports"][0]["published"]), 80)
                # The nginx choice must also parse without any Tunnel token.
                env_path = self.service / ".env"
                env_path.write_text(env_path.read_text().replace("TUNNEL_TOKEN=private-token\n", ""))
                again = deploy.subprocess.run(["docker", "compose", "-f", filename, "config", "--quiet"],
                                              cwd=self.service, env=environment, capture_output=True)
                self.assertEqual(again.returncode, 0)

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

    def test_verify_checks_nginx_proxy_in_addition_to_the_app(self):
        health = {"ok": True, "app": "0.2.1"}
        with patch.object(deploy, "_run", side_effect=["container-id", "image-id", "KR voices", "JP voices", "", json.dumps(health)]) as run, \
                patch.object(deploy, "_korean_pack", return_value={"lang": "ko"}), \
                patch.object(deploy, "_health", return_value=health):
            result = deploy.verify(self.root, source=self.source, gateway="nginx")
        self.assertEqual(result["gateway"], {"name": "nginx", "healthy": True})
        self.assertEqual(result["voice_packs"], {"kr": "KR voices", "jp": "JP voices"})
        self.assertTrue(any(call.args[0][-1] == "--lang=jp" for call in run.call_args_list))
        self.assertEqual(run.call_args.args[0], [*deploy._compose(self.service, gateway="nginx"), "exec", "-T", "nginx",
                                               "wget", "-q", "-O", "-", "http://127.0.0.1/healthz"])

    def test_verify_rejects_invalid_or_mismatched_nginx_health(self):
        for response in ("<html>error</html>", "[]", '{"ok":false,"app":"0.2.1"}', '{"ok":true,"app":"0.1.3"}'):
            with self.subTest(response=response), \
                    patch.object(deploy, "_run", side_effect=["container-id", "image-id", "KR voices", "JP voices", "", response]), \
                    patch.object(deploy, "_korean_pack", return_value={"lang": "ko"}), \
                    patch.object(deploy, "_health", return_value={"ok": True, "app": "0.2.1"}):
                with self.assertRaises(deploy.DeploymentError):
                    deploy.verify(self.root, source=self.source, gateway="nginx")

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
        self.assertEqual((self.service / "stack.cf-tunnel.yaml").read_text(), compose)
        self.assertEqual(stat.S_IMODE((self.service / ".env").stat().st_mode), 0o600)
        self.assertEqual(len(list((self.service / "rollback").iterdir())), 1)
        self.assertEqual((backup / ".env").read_text(), private)
        self.assertEqual(result, {"restored": name, "version": "0.1.4", "healthy": True})
        self.assertNotIn("private-token", json.dumps(result))

if __name__ == "__main__":
    unittest.main()
