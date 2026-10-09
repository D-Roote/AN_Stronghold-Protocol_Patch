#!/usr/bin/env python3
"""Configure nginx HTTPS and container-only Let's Encrypt issuance/renewal.

Requires Python's standard library and Docker Compose. No host ACME installation,
Cloudflare API token or Docker socket mounted inside containers is needed.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

if __package__:
    from . import deploy
else:
    import deploy

ROOT = Path(__file__).resolve().parents[1]
STACKS = {"game": ("stack.nginx.yaml", "stack.nginx-acme.yaml"),
          "assets": ("stack.assets-direct.yaml", "stack.assets-acme.yaml")}


def validate_domain(value):
    value = str(value).strip().lower().rstrip(".")
    labels = value.split(".")
    if (len(value) > 253 or len(labels) < 2 or value.endswith(".localhost") or
            all(label.isdigit() for label in labels) or
            any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                for label in labels)):
        raise deploy.DeploymentError("TLS domain must be a public DNS hostname, without a URL, port or wildcard")
    return value


def validate_email(value):
    value = str(value).strip()
    if not re.fullmatch(r"[^\s@'\"$\\]+@[^\s@'\"$\\]+\.[^\s@'\"$\\]+", value):
        raise deploy.DeploymentError("Provide a valid certificate contact email")
    return value


def compose(directory, target):
    args = ["docker", "compose", "--env-file", directory / ".env"]
    for filename in STACKS[target]:
        args.extend(["-f", directory / filename])
    return args


def settings(directory):
    private = directory / ".env"
    if not private.is_file():
        raise deploy.DeploymentError("Run tls.py configure before issuing a certificate")
    text = private.read_text(encoding="utf-8")
    target = deploy._runtime_setting(text, "TLS_TARGET")
    if target not in STACKS:
        raise deploy.DeploymentError("Run tls.py configure to select game or assets")
    domain = validate_domain(deploy._runtime_setting(text, "TLS_DOMAIN", ""))
    validate_email(deploy._runtime_setting(text, "TLS_EMAIL", ""))
    if deploy._runtime_setting(text, "TLS_CA") not in {"letsencrypt", "letsencrypt_test"}:
        raise deploy.DeploymentError("TLS_CA must be letsencrypt or letsencrypt_test")
    return target, domain


def configure(directory, *, domain, email, target="game", staging=False, templates=None):
    """Write private settings/templates only. Never contacts a CA or starts servers."""
    directory = Path(directory).expanduser().resolve()
    templates = Path(templates or ROOT / "deploy")
    domain, email = validate_domain(domain), validate_email(email)
    if target not in STACKS:
        raise deploy.DeploymentError("Choose game or assets")
    private = directory / ".env"
    original = private.read_text(encoding="utf-8") if private.is_file() else ""
    # Reuse a prepared runtime; avoid copying an unrelated .env into an asset server.
    required = (("STRONGHOLD_IMAGE", "STRONGHOLD_SOURCE_DIR", "LOCAL_ASSETS_DIR", "LOCAL_ASSETS_MANIFEST")
                if target == "game" else ("ASSET_BUNDLE_DIR",))
    for key in required:
        if not deploy._runtime_setting(original, key):
            raise deploy.DeploymentError(f"Prepare this {target} directory first; missing {key}")
    prefix = deploy._runtime_setting(original, "ASSET_PUBLIC_PATH", "")
    if target == "assets" and prefix and not re.fullmatch(r"(?:/[A-Za-z0-9_-]+)+", prefix):
        raise deploy.DeploymentError("Invalid ASSET_PUBLIC_PATH")
    old_target = deploy._runtime_setting(original, "TLS_TARGET")
    if old_target and old_target != target:
        raise deploy.DeploymentError("Use a separate service directory for game and asset TLS targets")
    files = (*deploy.TLS_SHARED_FILES, *deploy.TLS_GAME_FILES, "stack.nginx.yaml")
    if target == "assets":
        files = tuple(dict.fromkeys((*deploy.TLS_SHARED_FILES, *deploy.TLS_ASSET_FILES, *STACKS[target],
                      *(name for name in deploy.GENERATED_FILES if name.startswith("nginx-assets")))))
    bodies = {name: (templates / name).read_text(encoding="utf-8") for name in files}
    # Production and test account/certificate state must never be mixed.
    state = directory / "tls" / ("staging" if staging else "production") / domain
    for folder, mode in ((state, 0o700), (state / "acme", 0o700),
                         (state / "certs", 0o700), (state / "webroot", 0o755)):
        folder.mkdir(parents=True, exist_ok=True)
        folder.chmod(mode)
    updates = {"TLS_TARGET": target, "TLS_DOMAIN": domain, "TLS_EMAIL": email,
               "TLS_CA": "letsencrypt_test" if staging else "letsencrypt",
               "TLS_STATE_DIR": state, "TLS_UID": os.getuid(), "TLS_GID": os.getgid()}
    # HTTP-01 is reached on public port 80; TLS defaults to public port 443.
    if target == "assets":
        updates.update(ASSET_HTTP_PORT="80", ASSET_HTTPS_PORT="443")
    else:
        updates.update(NGINX_HTTP_PORT="80", NGINX_HTTPS_PORT="443")
    deploy._atomic(private, deploy._rewrite_env(original, updates), private=True)
    for name, body in bodies.items():
        deploy._atomic(directory / name, body)
    deploy._run([*compose(directory, target), "config", "--quiet"], root=directory)
    return {"configured": True, "target": target, "domain": domain,
            "ca": updates["TLS_CA"], "directory": str(directory),
            "compose_files": list(STACKS[target]), "certificate_issued": False}


def issue(directory):
    directory = Path(directory).expanduser().resolve()
    target, domain = settings(directory)
    args = compose(directory, target)
    frontend = "nginx" if target == "game" else "assets"
    # Serialize initial issuance with renewal; a failed issuance leaves HTTP validation
    # available, and an existing certificate continues to serve HTTPS.
    deploy._run([*args, "config", "--quiet"], root=directory)
    deploy._run([*args, "stop", "acme"], root=directory)
    try:
        deploy._run([*args, "up", "-d", "--no-build", "--wait", "--wait-timeout", "90", frontend], root=directory)
        deploy._run([*args, "run", "--rm", "--no-deps", "-T", "acme",
                     "/bin/sh", "/opt/tls/acme-issue.sh"], root=directory)
        deploy._run([*args, "exec", "-T", frontend, "/bin/sh", "/opt/tls/nginx-acme.sh", "reload"], root=directory)
    except deploy.DeploymentError:
        raise deploy.DeploymentError("TLS issuance/startup failed. Check DNS and public port 80, "
                                     "Compose service logs and TLS_STATE_DIR/acme/acme.log; then retry issue") from None
    finally:
        # Resume periodic renewal even if an attempted reissue fails.
        deploy._run([*args, "up", "-d", "--no-build", "acme"], root=directory)
    return {"issued": True, "domain": domain, "https": f"https://{domain}",
            "automatic_renewal": True, "nginx_reload": True}


def renew(directory):
    directory = Path(directory).expanduser().resolve()
    target, domain = settings(directory)
    args = compose(directory, target)
    # A one-off container avoids racing the daemon's scheduled cron job.
    deploy._run([*args, "stop", "acme"], root=directory)
    try:
        deploy._run([*args, "run", "--rm", "--no-deps", "-T", "acme",
                     "acme.sh", "--cron", "--config-home", "/acme.sh"], root=directory)
    finally:
        deploy._run([*args, "up", "-d", "--no-build", "acme"], root=directory)
    return {"renewal_checked": True, "domain": domain, "forced": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=ROOT / "service", help="Prepared runtime directory, e.g. ~/SP_Assets")
    commands = parser.add_subparsers(dest="action", required=True)
    config = commands.add_parser("configure", help="Prepare TLS configuration; no issuance or server startup")
    config.add_argument("--domain", required=True)
    config.add_argument("--email", required=True)
    config.add_argument("--target", choices=STACKS, default="game")
    config.add_argument("--staging", action="store_true", help="Use isolated Let's Encrypt staging (not browser trusted)")
    commands.add_parser("issue", help="Start nginx, issue/install a certificate, enable automatic renewal")
    commands.add_parser("renew", help="Check renewal now, without forcing early reissuance")
    args = vars(parser.parse_args(argv))
    action, directory = args.pop("action"), args.pop("directory")
    try:
        result = {"configure": configure, "issue": issue, "renew": renew}[action](directory, **args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (deploy.DeploymentError, OSError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
