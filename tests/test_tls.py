"""TLS configuration, private-state preservation and certificate lifecycle checks."""
import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import deploy, tls


class TLSTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.private = self.directory / ".env"
        self.private.write_text("TUNNEL_TOKEN=private-token\nCUSTOM='keep this'\n"
                                "STRONGHOLD_IMAGE=prepared:latest\nSTRONGHOLD_SOURCE_DIR=./source\n"
                                "LOCAL_ASSETS_DIR=./local\nLOCAL_ASSETS_MANIFEST=./manifest.json\n"
                                "ASSET_BUNDLE_DIR=./bundle\nASSET_HTTP_PORT=8081\nASSET_PUBLIC_PATH=/sp\n")

    def configure(self, **options):
        with patch.object(deploy, "_run", return_value=""):
            return tls.configure(self.directory, domain="game.example.org", email="admin@example.org", **options)

    def test_configuration_preserves_secrets_and_generates_state_without_issuance(self):
        with patch.object(deploy, "_run", return_value="") as run:
            result = tls.configure(self.directory, domain="GAME.EXAMPLE.ORG.", email="admin@example.org")
        self.assertEqual(result["domain"], "game.example.org")
        self.assertFalse(result["certificate_issued"])
        self.assertNotIn("private-token", json.dumps(result))
        text = self.private.read_text()
        self.assertIn("TUNNEL_TOKEN=private-token", text)
        self.assertIn("CUSTOM='keep this'", text)
        self.assertEqual(stat.S_IMODE(self.private.stat().st_mode), 0o600)
        self.assertTrue((self.directory / "tls/production/game.example.org/acme").is_dir())
        self.assertEqual(stat.S_IMODE((self.directory / "tls/production/game.example.org/webroot").stat().st_mode), 0o755)
        self.assertFalse((self.directory / "tls/production/game.example.org/certs/fullchain.pem").exists())
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0][-2:], ["config", "--quiet"])

    def test_staging_state_never_overwrites_production_keys(self):
        self.configure()
        key = self.directory / "tls/production/game.example.org/certs/privkey.pem"
        key.write_text("saved private key")
        self.configure(staging=True)
        self.assertIn("TLS_CA='letsencrypt_test'", self.private.read_text())
        self.assertEqual(key.read_text(), "saved private key")
        self.assertTrue((self.directory / "tls/staging/game.example.org/acme").is_dir())
        self.configure()
        self.assertEqual(key.read_text(), "saved private key")
        self.assertIn("TLS_CA='letsencrypt'", self.private.read_text())

    def test_reconfiguration_reuses_certificate_state(self):
        self.configure()
        certificate = self.directory / "tls/production/game.example.org/certs/fullchain.pem"
        certificate.write_text("saved certificate")
        self.configure()
        self.assertEqual(certificate.read_text(), "saved certificate")

    def test_invalid_inputs_cannot_modify_existing_environment(self):
        saved = self.private.read_bytes()
        for domain in ("localhost", "*.example.org", "https://example.org", "example.org:443",
                       "127.0.0.1", "-bad.example.org", "a;return 200;.org", "a..org"):
            with self.subTest(domain=domain), self.assertRaises(deploy.DeploymentError):
                tls.configure(self.directory, domain=domain, email="admin@example.org")
        with self.assertRaises(deploy.DeploymentError):
            tls.configure(self.directory, domain="example.org", email="broken\naddress")
        self.assertEqual(saved, self.private.read_bytes())
        self.assertFalse((self.directory / "tls").exists())

    def test_unprepared_directory_is_rejected_and_changed_domain_gets_isolated_state(self):
        self.private.write_text("ASSET_BUNDLE_DIR=./bundle\n")
        with self.assertRaises(deploy.DeploymentError):
            self.configure()
        self.configure(target="assets")
        certificate = self.directory / "tls/production/game.example.org/certs/fullchain.pem"
        certificate.write_text("old domain certificate")
        with patch.object(deploy, "_run", return_value=""):
            tls.configure(self.directory, target="assets", domain="other.example.org", email="admin@example.org")
        self.assertEqual(certificate.read_text(), "old domain certificate")
        self.assertTrue((self.directory / "tls/production/other.example.org/acme").is_dir())

    def test_project_nginx_management_keeps_the_renewal_service(self):
        self.configure()
        selected = deploy._compose(self.directory, gateway="nginx")
        self.assertIn(self.directory / "stack.nginx-acme.yaml", selected)
        self.assertNotIn(self.directory / "stack.nginx-acme.yaml", deploy._compose(self.directory))

    def test_asset_runtime_receives_only_asset_and_shared_templates(self):
        self.configure(target="assets")
        self.assertTrue((self.directory / "stack.assets-acme.yaml").is_file())
        self.assertTrue((self.directory / "acme-deploy.sh").is_file())
        self.assertFalse((self.directory / "stack.nginx-acme.yaml").exists())

    @unittest.skipUnless(shutil.which("docker"), "Docker CLI is required to parse TLS Compose")
    def test_compose_ports_permissions_and_resources_for_both_targets(self):
        # Parsing doesn't need a Docker daemon or public domain.
        for target in ("game", "assets"):
            with self.subTest(target=target):
                self.private.write_text(self.private.read_text().replace("TLS_TARGET=", "OLD_TLS_TARGET=")
                                        .replace("TLS_DOMAIN=", "OLD_TLS_DOMAIN="))
                self.configure(target=target)
                output = deploy._run([*tls.compose(self.directory, target), "config", "--format", "json"])
                parsed = json.loads(output)
                frontend = parsed["services"]["nginx" if target == "game" else "assets"]
                self.assertEqual({int(port["published"]) for port in frontend["ports"]}, {80, 443})
                volumes = {item["target"]: item for item in frontend["volumes"]}
                self.assertTrue(volumes["/etc/nginx/tls"]["read_only"])
                self.assertFalse(volumes["/etc/nginx/tls"]["bind"]["create_host_path"])
                acme = parsed["services"]["acme"]
                self.assertEqual(acme["user"], f"{os.getuid()}:{os.getgid()}")
                self.assertEqual(acme["command"], ["daemon"])
                self.assertEqual(int(acme["mem_limit"]), 128 * 1024 * 1024)
                self.assertEqual(acme["restart"], "unless-stopped")
                self.assertFalse(any("docker.sock" in item["target"] for item in acme["volumes"]))

    def test_failed_issuance_resumes_daemon_without_reporting_success(self):
        self.configure()
        calls = []

        def command(args, **options):
            calls.append(args)
            if "/opt/tls/acme-issue.sh" in args:
                raise deploy.DeploymentError("issuance failed")
            return ""

        with patch.object(deploy, "_run", side_effect=command):
            with self.assertRaisesRegex(deploy.DeploymentError, "public port 80"):
                tls.issue(self.directory)
        self.assertEqual(calls[-1][-4:], ["up", "-d", "--no-build", "acme"])
        self.assertFalse(any("reload" in args for args in calls))

    def test_manual_renewal_does_not_force_reissuance(self):
        self.configure()
        with patch.object(deploy, "_run", return_value="") as run:
            result = tls.renew(self.directory)
        self.assertFalse(result["forced"])
        self.assertTrue(any("--cron" in call.args[0] for call in run.call_args_list))
        self.assertFalse(any("--force" in call.args[0] for call in run.call_args_list))

    def test_shell_scripts_have_valid_posix_syntax(self):
        for name in ("nginx-acme.sh", "acme-deploy.sh", "acme-issue.sh"):
            result = subprocess.run(["sh", "-n", tls.ROOT / "deploy" / name], capture_output=True)
            self.assertEqual(result.returncode, 0, name)


if __name__ == "__main__":
    unittest.main()
