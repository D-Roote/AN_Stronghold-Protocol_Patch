"""Opt-in nginx/acme.sh container tests, with local test certificates only.

SP_TLS_E2E=1 python3 -m unittest tests.test_tls_integration -v
Uses unused localhost ports and temporary Compose projects; never contacts a CA.
Requires the prepared game image (or SP_TLS_TEST_APP_IMAGE for a Node/ws image).
"""
import http.client
import json
import os
import socket
import ssl
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from scripts import deploy, project, tls


@unittest.skipUnless(os.environ.get("SP_TLS_E2E") == "1", "Set SP_TLS_E2E=1 for local container TLS tests")
class TLSContainerTests(unittest.TestCase):
    def command(self, *args, check=True):
        result = subprocess.run([str(arg) for arg in args], text=True, capture_output=True, timeout=90)
        if check and result.returncode:
            self.fail(f"Container test command failed: {result.stderr[-1800:]}")
        return result

    def available_port(self):
        with socket.socket() as stream:
            stream.bind(("127.0.0.1", 0))
            return stream.getsockname()[1]

    def request(self, port, path, *, secure=False):
        connection = (http.client.HTTPSConnection("127.0.0.1", port, timeout=3,
                      context=ssl._create_unverified_context()) if secure else
                      http.client.HTTPConnection("127.0.0.1", port, timeout=3))
        try:
            connection.request("GET", path)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def presented_certificate(self, port):
        with socket.create_connection(("127.0.0.1", port), timeout=3) as stream:
            with ssl._create_unverified_context().wrap_socket(stream, server_hostname="game.example.org") as secure:
                return secure.getpeercert(binary_form=True)

    def certificate_fixture(self, directory, serial):
        pending = directory / "tls/production/game.example.org/certs/pending"
        pending.mkdir(exist_ok=True)
        self.command("openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
                     "-nodes", "-keyout", pending / "privkey.pem", "-out", pending / "fullchain.pem",
                     "-days", "2", "-set_serial", serial, "-subj", "/CN=game.example.org",
                     "-addext", "subjectAltName=DNS:game.example.org")
        return pending

    def exercise(self, target):
        with tempfile.TemporaryDirectory(prefix="sp-tls-e2e-") as temporary:
            directory = Path(temporary)
            for folder in ("source", "local", "bundle/assets", "bundle/healthz"):
                (directory / folder).mkdir(parents=True)
            (directory / "manifest.json").write_text("{}")
            (directory / "bundle/assets/test.txt").write_text("asset fixture")
            (directory / "bundle/healthz/assets").write_text('{"ok":true}')
            image = os.environ.get("SP_TLS_TEST_APP_IMAGE", deploy.image_name(project.load_pin()))
            private = directory / ".env"
            private.write_text(f"STRONGHOLD_IMAGE={image}\nSTRONGHOLD_SOURCE_DIR=./source\n"
                               "LOCAL_ASSETS_DIR=./local\nLOCAL_ASSETS_MANIFEST=./manifest.json\n"
                               "ASSET_BUNDLE_DIR=./bundle\nASSET_PUBLIC_PATH=/sp\n")
            tls.configure(directory, target=target, domain="game.example.org", email="admin@example.org")
            http_port, https_port = self.available_port(), self.available_port()
            private.write_text(deploy._rewrite_env(private.read_text(), {
                "NGINX_BIND_IP": "127.0.0.1", "NGINX_HTTP_PORT": http_port, "NGINX_HTTPS_PORT": https_port,
                "ASSET_BIND_IP": "127.0.0.1", "ASSET_HTTP_PORT": http_port, "ASSET_HTTPS_PORT": https_port}))
            name = "sp-tls-e2e-" + target + "-" + str(os.getpid())
            args = [*tls.compose(directory, target), "-p", name]
            if target == "game":
                backend = ("const http=require('http'),{WebSocketServer}=require('ws');"
                           "const s=http.createServer((req,res)=>{res.setHeader('Content-Type','application/json');"
                           "res.end(JSON.stringify({ok:true,app:'0.2.2',headers:req.headers}));});"
                           "new WebSocketServer({server:s}).on('connection',w=>w.on('message',m=>w.send(m)));"
                           "s.listen(3000,'0.0.0.0');")
                fixture = directory / "backend.yaml"
                fixture.write_text("services:\n  stronghold:\n    ports: !reset []\n"
                                   f"    entrypoint: {json.dumps(['node', '-e', backend])}\n"
                                   "    command: []\n    healthcheck:\n"
                                   "      test: [CMD, node, -e, \"fetch('http://127.0.0.1:3000/healthz').then(r=>process.exit(r.ok?0:1))\"]\n"
                                   "      interval: 1s\n      timeout: 2s\n      retries: 20\n")
                args.extend(["-f", fixture])
            frontend = "nginx" if target == "game" else "assets"
            websocket = None
            try:
                self.command(*args, "up", "-d", "--no-build", "--wait", "--wait-timeout", "45")
                challenge = directory / "tls/production/game.example.org/webroot/.well-known/acme-challenge/probe"
                challenge.parent.mkdir(parents=True)
                challenge.write_text("http-01 works")
                challenge.chmod(0o644)
                self.assertEqual(self.request(http_port, "/.well-known/acme-challenge/probe")[2], b"http-01 works")
                self.assertEqual(self.request(http_port, "/")[0], 503)
                health_path = "/healthz" if target == "game" else "/sp/healthz/assets"
                self.assertEqual(self.request(http_port, health_path)[0], 200)

                pending = self.certificate_fixture(directory, 1)
                # Real acme.sh --install-cert persists the reload hook for later cron
                # renewal. Its input is a local fixture, so no CA is contacted.
                account = directory / "tls/production/game.example.org/acme/game.example.org_ecc"
                account.mkdir()
                for source, destination in (("fullchain.pem", "fullchain.cer"),
                                            ("fullchain.pem", "game.example.org.cer"),
                                            ("privkey.pem", "game.example.org.key")):
                    (account / destination).write_bytes((pending / source).read_bytes())
                (account / "game.example.org.conf").write_text("Le_Domain='game.example.org'\nLe_Keylength='ec-256'\n")
                self.command(*args, "exec", "-T", "acme", "acme.sh", "--install-cert", "--domain", "game.example.org", "--ecc",
                             "--key-file", "/certs/pending/privkey.pem", "--fullchain-file", "/certs/pending/fullchain.pem",
                             "--reloadcmd", "/bin/sh /opt/tls/acme-deploy.sh")
                for attempt in range(20):
                    try:
                        if self.request(https_port, health_path, secure=True)[0] == 200:
                            break
                    except (OSError, ssl.SSLError):
                        pass
                    time.sleep(0.5)
                else:
                    self.fail("nginx didn't enable HTTPS after first certificate installation")
                first = self.presented_certificate(https_port)
                redirect = self.request(http_port, "/hello")
                self.assertEqual(redirect[0], 308)
                self.assertEqual(redirect[1]["Location"], "https://game.example.org/hello")
                self.assertEqual(self.request(http_port, "/.well-known/acme-challenge/probe")[2], b"http-01 works")
                self.command(*args, "exec", "-T", frontend, "nginx", "-t")

                if target == "game":
                    headers = json.loads(self.request(https_port, "/headers", secure=True)[2])["headers"]
                    self.assertEqual(headers["x-forwarded-proto"], "https")
                    # Keep WSS connected throughout automatic nginx reload.
                    client = ("const W=require('ws'),w=new W('wss://nginx/room',{rejectUnauthorized:false});"
                              "let count=0;const t=setTimeout(()=>{w.close();process.exit(count>=8?0:1)},12000);"
                              "w.on('open',()=>setInterval(()=>w.send('ping'),500));"
                              "w.on('message',()=>count++);w.on('error',()=>process.exit(2));"
                              "w.on('close',()=>{if(count<8)process.exit(3)});")
                    websocket = subprocess.Popen([str(v) for v in (*args, "exec", "-T", "stronghold", "node", "-e", client)],
                                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    time.sleep(1)
                else:
                    self.assertEqual(self.request(https_port, "/sp/assets/test.txt", secure=True)[2], b"asset fixture")

                self.certificate_fixture(directory, 2)
                self.command(*args, "exec", "-T", "acme", "/bin/sh", "/opt/tls/acme-deploy.sh")
                for attempt in range(20):
                    if self.presented_certificate(https_port) != first:
                        break
                    time.sleep(0.5)
                else:
                    self.fail("nginx kept the old certificate after renewal")
                renewed = self.presented_certificate(https_port)
                if websocket:
                    _, errors = websocket.communicate(timeout=20)
                    self.assertEqual(websocket.returncode, 0, errors)

                # Mismatched keys cannot replace the active certificate or marker.
                marker = (directory / "tls/production/game.example.org/certs/.reload").read_bytes()
                self.command("openssl", "genpkey", "-algorithm", "EC", "-pkeyopt", "ec_paramgen_curve:prime256v1",
                             "-out", pending / "privkey.pem")
                rejected = self.command(*args, "exec", "-T", "acme", "/bin/sh", "/opt/tls/acme-deploy.sh", check=False)
                self.assertNotEqual(rejected.returncode, 0)
                self.assertEqual((directory / "tls/production/game.example.org/certs/.reload").read_bytes(), marker)
                self.assertEqual(self.presented_certificate(https_port), renewed)
                self.command(*args, "restart", frontend)
                for attempt in range(20):
                    try:
                        if self.request(https_port, health_path, secure=True)[0] == 200:
                            break
                    except OSError:
                        pass
                    time.sleep(0.5)
                else:
                    self.fail("HTTPS didn't survive a container restart")
            finally:
                if websocket and websocket.poll() is None:
                    websocket.terminate()
                    websocket.communicate(timeout=5)
                self.command(*args, "down", check=False)

    def test_game_https_bootstrap_renewal_and_websocket(self):
        self.exercise("game")

    def test_assets_https_bootstrap_renewal_and_prefix(self):
        self.exercise("assets")
