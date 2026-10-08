"""Private runtime management for the patch repository (Python standard library only).

The CLI in project.py calls dispatch(). No command prints dotenv contents or Docker
inspection records; Compose errors deliberately omit potentially secret-bearing output.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
import urllib.request
from urllib.error import HTTPError
from urllib.parse import unquote, urlsplit
import zipfile
from pathlib import Path, PurePosixPath


class DeploymentError(RuntimeError):
    """An actionable error that is safe to print without leaking private settings."""


_ENV_KEY = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z_0-9]*)\s*=")
_MANAGED = {"STRONGHOLD_SOURCE_DIR", "STRONGHOLD_IMAGE", "VOICE_LANG",
            "FETCH_ASSETS", "DEV_PORT", "LOCAL_ASSETS_DIR", "LOCAL_ASSETS_MANIFEST"}
_SNAPSHOT_NAME = re.compile(r"\d{8}T\d{6}\.\d{6}Z")


def _run(args, *, root=None):
    env = os.environ.copy()
    # Compose must use the selected private env file rather than a stale shell value.
    for key in _MANAGED | {"TUNNEL_TOKEN", "COMPOSE_FILE", "COMPOSE_PROJECT_NAME"}:
        env.pop(key, None)
    try:
        result = subprocess.run([str(v) for v in args], cwd=root, env=env,
                                text=True, capture_output=True, check=False)
    except FileNotFoundError:
        raise DeploymentError(f"Required command is unavailable: {args[0]}") from None
    if result.returncode:
        # docker compose config/errors can contain an interpolated Tunnel token.
        command = " ".join(str(v) for v in args[:2])
        raise DeploymentError(f"{command} failed (exit {result.returncode}); private output withheld")
    return result.stdout


def _atomic(path: Path, content: str, *, private=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600 if private else 0o644)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _json(path: Path, value, *, private=False):
    _atomic(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n", private=private)


def _sha(path: Path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _quote(value):
    value = str(value)
    if "\n" in value or "\r" in value or "\0" in value:
        raise DeploymentError("A runtime setting contains a forbidden control character")
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _rewrite_env(text, updates):
    """Preserve all unknown settings/comments without parsing or logging their values."""
    output, remaining = [], dict(updates)
    lines, index = text.splitlines(), 0
    while index < len(lines):
        line = lines[index]
        index += 1
        match = _ENV_KEY.match(line)
        key = match.group(1) if match else None
        block = [line]
        value = line[match.end():].lstrip() if match else ""
        if value.startswith(("'", '"')):
            quote, segment = value[0], value[1:]
            while True:
                escaped, closed = False, False
                for character in segment:
                    if escaped:
                        escaped = False
                    elif character == "\\":
                        escaped = True
                    elif character == quote:
                        closed = True
                        break
                if closed:
                    break
                if index >= len(lines):
                    raise DeploymentError("Private environment contains an unterminated quoted value")
                segment = lines[index]
                block.append(segment)
                index += 1
        if key in updates:
            if key in remaining:
                output.append(f"{key}={_quote(remaining.pop(key))}")
            continue
        output.extend(block)
    if remaining:
        output.append("\n# Generated local settings; private values above are preserved.")
        output.extend(f"{key}={_quote(value)}" for key, value in remaining.items())
    return "\n".join(output).rstrip() + "\n"


def _paths(root):
    root = Path(root).resolve()
    return root, root / "service", root / "deploy"


def image_name(pin, image=None):
    if image is None:
        version = str(pin["version"]).removeprefix("v")
        image = f"stronghold-protocol:ko-{version}-{str(pin['commit'])[:12]}"
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/:@-]*", image):
        raise DeploymentError("Invalid Docker image name")
    return image


def _release(pin):
    release = pin.get("release") or {}
    url, digest = release.get("url", ""), release.get("sha256", "")
    if not url.startswith("https://github.com/") or not re.fullmatch(r"[a-fA-F0-9]{64}", digest):
        raise DeploymentError("The pin must contain an HTTPS GitHub full-release URL and SHA256")
    return url, digest.lower()


def _asset_dir(root, pin):
    ref = str(pin.get("ref", pin["version"]))
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", ref):
        raise DeploymentError("The release ref must be a simple tag for local assets")
    return root / "service" / "assets" / "releases" / ref


def validate_assets(local, manifest):
    local, manifest = Path(local), Path(manifest)
    try:
        if manifest.stat().st_size > 16 * 1024**2:
            raise DeploymentError("Local assets manifest exceeds the size limit")
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise DeploymentError("Local assets manifest is missing or invalid") from None
    if not isinstance(data, dict):
        raise DeploymentError("Local assets manifest must be a JSON object")
    urls = []

    def walk(value):
        if isinstance(value, dict):
            if "path" in value:
                urls.append(value["path"])
            for item in value.values():
                if isinstance(item, (dict, list)):
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(data.get("groups", {}))
    if (not urls or any(not isinstance(url, str) for url in urls)
            or len(urls) != data.get("count") or len(set(urls)) != len(urls)):
        raise DeploymentError("Local assets manifest count is inconsistent")
    resolved = local.resolve()
    for url in urls:
        if not isinstance(url, str) or not url.startswith("/assets/local/") or "\\" in url:
            raise DeploymentError("Local assets manifest contains an unexpected path")
        relative = PurePosixPath(url.removeprefix("/assets/local/"))
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise DeploymentError("Local assets manifest contains an unsafe path")
        target = local.joinpath(*relative.parts)
        if not target.is_file() or not target.resolve().is_relative_to(resolved):
            raise DeploymentError("A file referenced by the local assets manifest is missing or unsafe")
    return {"entries": len(urls), "manifest_sha256": _sha(manifest)}


def _select_assets(root, pin):
    destination = _asset_dir(root, pin)
    metadata = destination / "release.json"
    _, expected = _release(pin)
    if metadata.is_file():
        info = json.loads(metadata.read_text(encoding="utf-8"))
        if info.get("release_sha256") != expected:
            raise DeploymentError("Prepared assets do not match the pinned release")
        local, manifest = destination / "assets/local", destination / "data/local-assets.json"
        validated = validate_assets(local, manifest)
        if validated["manifest_sha256"] != info.get("manifest_sha256"):
            raise DeploymentError("Prepared local assets manifest changed after release extraction")
        return local, manifest
    # Reuse the already verified installation without moving the running volume.
    previous = root / "service/assets-release.json"
    if previous.is_file():
        info = json.loads(previous.read_text(encoding="utf-8"))
        local, manifest = root / "service/assets/local", root / "service/data/local-assets.json"
        if info.get("release_sha256") == expected and manifest.is_file():
            validated = validate_assets(local, manifest)
            if validated["manifest_sha256"] == info.get("manifest_sha256"):
                return local, manifest
    raise DeploymentError("Prepare matching local assets first: project.py assets")


def assets(root, pin, *, archive=None):
    """Verify a full release, extract only local assets, and publish a versioned directory."""
    root, _, _ = _paths(root)
    url, expected = _release(pin)
    destination = _asset_dir(root, pin)
    if destination.exists():
        if not (destination / "release.json").is_file():
            raise DeploymentError("Versioned assets directory already exists without release provenance")
        local, manifest = _select_assets(root, pin)
        return {"assets": str(local), "manifest": str(manifest), **validate_assets(local, manifest)}
    filename = unquote(urlsplit(url).path.rsplit("/", 1)[-1])
    if filename in {"", ".", ".."} or "/" in filename or "\\" in filename:
        raise DeploymentError("The full-release URL contains an unsafe filename")
    archive = Path(archive).resolve() if archive else root / ".cache/releases" / filename
    archive.parent.mkdir(parents=True, exist_ok=True)
    if not archive.exists():
        fd, temporary = tempfile.mkstemp(prefix=".release-", dir=archive.parent)
        try:
            with os.fdopen(fd, "wb") as output, urllib.request.urlopen(
                    urllib.request.Request(url, headers={"User-Agent": "Stronghold-Ko-Patch"}), timeout=60) as response:
                total = 0
                for block in iter(lambda: response.read(1024 * 1024), b""):
                    total += len(block)
                    if total > 4 * 1024**3:
                        raise DeploymentError("Full release exceeds the download size limit")
                    output.write(block)
            if _sha(Path(temporary)) != expected:
                raise DeploymentError("Downloaded full release SHA256 differs from upstream.lock.json")
            os.chmod(temporary, 0o644)
            os.replace(temporary, archive)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    if _sha(archive) != expected:
        raise DeploymentError("Cached full release SHA256 differs from upstream.lock.json")
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".assets-", dir=destination.parent))
    try:
        with zipfile.ZipFile(archive) as bundle:
            manifests = [item for item in bundle.infolist()
                         if PurePosixPath(item.filename).parts[-2:] == ("data", "local-assets.json")]
            if len(manifests) != 1:
                raise DeploymentError("Full release must contain exactly one local-assets manifest")
            manifest_info = manifests[0]
            if manifest_info.file_size > 16 * 1024**2:
                raise DeploymentError("Full release local assets manifest exceeds the size limit")
            base = PurePosixPath(manifest_info.filename).parent.parent.as_posix()
            prefix = ("" if base == "." else base + "/") + "public/assets/local/"
            selected = [item for item in bundle.infolist() if item.filename.startswith(prefix)]
            if not selected or sum(item.file_size for item in selected) > 4 * 1024**3:
                raise DeploymentError("Full release local assets are empty or exceed the extraction limit")
            seen = set()
            for info in [manifest_info, *selected]:
                raw = info.filename
                relative = PurePosixPath(raw)
                if "\\" in raw or relative.is_absolute() or ".." in relative.parts:
                    raise DeploymentError("Full release contains an unsafe local-assets path")
                if stat.S_ISLNK(info.external_attr >> 16):
                    raise DeploymentError("Full release local assets contain a symbolic link")
                if info.is_dir():
                    continue
                target = (stage / "data/local-assets.json" if info is manifest_info
                          else stage / "assets/local" / raw[len(prefix):])
                if str(target) in seen or not target.resolve().is_relative_to(stage.resolve()):
                    raise DeploymentError("Full release contains duplicate or unsafe local-assets files")
                seen.add(str(target))
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(info) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output)
                target.chmod(0o644)
        validated = validate_assets(stage / "assets/local", stage / "data/local-assets.json")
        _json(stage / "release.json", {"ref": pin["ref"], "release_sha256": expected,
                                       "source": url, **validated})
        stage.chmod(0o755)
        os.replace(stage, destination)
        return {"assets": str(destination / "assets/local"),
                "manifest": str(destination / "data/local-assets.json"), **validated}
    except (zipfile.BadZipFile, RuntimeError, OSError) as error:
        if isinstance(error, DeploymentError):
            raise
        raise DeploymentError("Full release extraction failed; existing runtime assets were preserved") from None
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def _compose(service, *, dev=False, env_file=None, compose_file=None):
    config = compose_file or service / ("compose.dev.yaml" if dev else "compose.yaml")
    return ["docker", "compose", "--env-file", env_file or service / ".env", "-f", config]


def configure(root, pin, source, *, image=None):
    """Generate service files while retaining unknown private values."""
    root, service, templates = _paths(root)
    source = Path(source).resolve()
    if not (source / "Dockerfile").is_file() or not (source / "package.json").is_file():
        raise DeploymentError("Prepare the patched application source before configuring runtime files")
    local, manifest = _select_assets(root, pin)
    service.mkdir(parents=True, exist_ok=True)
    root_env = root / ".env"
    if root_env.is_symlink() and root_env.resolve() != (service / ".env").resolve():
        raise DeploymentError("Root .env points to a different private environment; it was preserved")
    name = image_name(pin, image)
    env_file = service / ".env"
    original = env_file.read_text(encoding="utf-8") if env_file.is_file() else (templates / "env.example").read_text(encoding="utf-8")
    text = _rewrite_env(original, {"STRONGHOLD_SOURCE_DIR": source, "STRONGHOLD_IMAGE": name,
                                 "VOICE_LANG": "kr", "FETCH_ASSETS": "1", "DEV_PORT": "3100",
                                 "LOCAL_ASSETS_DIR": local, "LOCAL_ASSETS_MANIFEST": manifest})
    generated = {filename: (templates / filename).read_text(encoding="utf-8")
                 for filename in ("compose.yaml", "compose.dev.yaml")}
    changed = text != original or any(not (service / filename).is_file() or
                    (service / filename).read_text(encoding="utf-8") != body
                    for filename, body in generated.items())
    if changed:
        _atomic(env_file, text, private=True)
        for filename, body in generated.items():
            _atomic(service / filename, body)
    env_file.chmod(0o600)
    if not root_env.exists() and not root_env.is_symlink():
        root_env.symlink_to("service/.env")
    return {"image": name, "source": str(source), "assets": str(local), "manifest": str(manifest),
            "configured": changed}


def build(root, pin, source, *, image=None, fetch_assets="1"):
    root, _, _ = _paths(root)
    source = Path(source).resolve()
    if not (source / "Dockerfile").is_file():
        raise DeploymentError("Prepare the patched application source before building")
    if str(fetch_assets) not in {"0", "1"}:
        raise DeploymentError("fetch_assets must be 0 or 1")
    name = image_name(pin, image)
    _run(["docker", "build", "--build-arg", f"FETCH_ASSETS={fetch_assets}", "--build-arg", "VOICE_LANG=kr",
          "--tag", name, source], root=root)
    return {"image": name, "built": True}


def _health(port, *, attempts=30, expected_version=None):
    for _ in range(attempts):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2) as response:
                payload = json.load(response)
            if isinstance(payload, dict) and payload.get("ok") is True:
                if (expected_version is not None
                        and str(payload.get("app", "")).removeprefix("v")
                        != str(expected_version).removeprefix("v")):
                    raise DeploymentError("Running service version differs from the pinned source version")
                return payload
        except (OSError, ValueError):
            pass
        time.sleep(1)
    raise DeploymentError(f"Service health check did not pass on loopback port {port}")


def _korean_pack(port):
    """Check the files through HTTP, as the browser and unprivileged server see them."""
    def fetch(endpoint):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{endpoint}", timeout=3) as response:
                if getattr(response, "status", 200) != 200:
                    raise DeploymentError(f"Korean language HTTP request failed: {endpoint}")
                raw = response.read(16 * 1024**2 + 1)
            if len(raw) > 16 * 1024**2:
                raise DeploymentError(f"Korean language JSON exceeds the size limit: {endpoint}")
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise DeploymentError(f"Korean language endpoint must return a JSON object: {endpoint}")
            return value
        except HTTPError as error:
            error.close()
            raise DeploymentError(f"Korean language JSON is unavailable or unreadable: {endpoint}") from None
        except (OSError, ValueError):
            raise DeploymentError(f"Korean language JSON is unavailable or unreadable: {endpoint}") from None

    index = fetch("/packs/index.json")
    packs = index.get("packs")
    if not isinstance(packs, list):
        raise DeploymentError("Language pack index does not contain a packs list")
    korean = [entry for entry in packs if isinstance(entry, dict)
              and entry.get("type") == "lang" and entry.get("lang") == "ko"]
    if len(korean) != 1:
        raise DeploymentError("Korean language pack is missing or duplicated in the browser language index")
    files = korean[0].get("files")
    if (not isinstance(files, dict) or files.get("ui") != "/i18n/ko.json"
            or files.get("data") != "/data/i18n/ko.json"):
        raise DeploymentError("Korean language pack index is missing its UI or game data file")
    ui = fetch("/i18n/ko.json")
    metadata = ui.get("_meta")
    strings = sum(isinstance(value, str) and bool(value.strip())
                  for key, value in ui.items() if not key.startswith("_"))
    if not isinstance(metadata, dict) or metadata.get("lang") != "ko" or not strings:
        raise DeploymentError("Korean interface translation JSON has invalid metadata or no translated strings")
    game = fetch("/data/i18n/ko.json")
    tables = game.get("files")
    if game.get("lang") != "ko" or not isinstance(tables, dict) or not tables:
        raise DeploymentError("Korean game data overlay has invalid language metadata or no tables")
    return {"lang": "ko", "listed": True, "ui_strings": strings, "game_tables": len(tables)}


def up(root, pin=None, source=None, *, dev=False):
    _, service, _ = _paths(root)
    _run([*_compose(service, dev=dev), "config", "--quiet"])
    _run([*_compose(service, dev=dev), "up", "-d", "--no-build", "--wait", "--wait-timeout", "90"])
    health = _health(3100 if dev else 3000, expected_version=pin["version"] if pin else None)
    return {"running": True, "dev": dev, "version": health.get("app")}


def down(root, pin=None, source=None, *, dev=False):
    _, service, _ = _paths(root)
    _run([*_compose(service, dev=dev), "down"])
    return {"running": False, "dev": dev}


def status(root, pin=None, source=None, *, dev=False):
    _, service, _ = _paths(root)
    raw = _run([*_compose(service, dev=dev), "ps", "--format", "json"])
    try:
        entries = json.loads(raw) if raw.lstrip().startswith("[") else [json.loads(line) for line in raw.splitlines() if line.strip()]
    except ValueError:
        raise DeploymentError("Docker Compose status returned an unsupported format") from None
    return {"services": [{key: item.get(key) for key in ("Service", "State", "Health", "Image", "Publishers")}
                         for item in entries]}


def verify(root, pin=None, source=None, *, dev=False):
    root, service, _ = _paths(root)
    source = Path(source or root / ".build/Stronghold-Protocol").resolve()
    ids = _run([*_compose(service, dev=dev), "ps", "-q", "stronghold"]).strip().splitlines()
    if len(ids) != 1:
        raise DeploymentError("Exactly one running application container is required for verification")
    language_validation = _korean_pack(3100 if dev else 3000)
    asset_validation = None
    if pin:
        local, manifest = _select_assets(root, pin)
        expected = validate_assets(local, manifest)
        raw = _run(["docker", "inspect", "--format", "{{json .Mounts}}", ids[0]])
        try:
            mounts = json.loads(raw)
        except ValueError:
            raise DeploymentError("Application asset mounts could not be inspected") from None
        targets = {"/app/public/assets/local": local.resolve(),
                   "/app/data/local-assets.json": manifest.resolve()}
        for destination, expected_source in targets.items():
            found = [entry for entry in mounts if entry.get("Destination") == destination]
            if (len(found) != 1 or found[0].get("Type") != "bind" or found[0].get("RW") is not False
                    or Path(found[0].get("Source", "")).resolve() != expected_source):
                raise DeploymentError("Running local asset mounts differ from the pinned release configuration")
        # Hash inside the running container: a file bind can retain an old inode after
        # its host path is replaced, even when Docker's Source pathname is identical.
        script = """const fs=require('node:fs'),crypto=require('node:crypto'),path=require('node:path');
const raw=fs.readFileSync('/app/data/local-assets.json'),data=JSON.parse(raw),urls=[];
function walk(v){if(Array.isArray(v)){v.forEach(walk);}else if(v&&typeof v==='object'){
if(Object.hasOwn(v,'path'))urls.push(v.path);Object.values(v).forEach(walk);}}
walk(data.groups);let missing=0;for(const url of urls){
if(typeof url!=='string'||!url.startsWith('/assets/local/')||url.includes('..')||url.includes('\\\\')
||!fs.existsSync(path.join('/app/public',url)))missing++;}
console.log(JSON.stringify({sha256:crypto.createHash('sha256').update(raw).digest('hex'),
entries:data.count,references:urls.length,missing}));"""
        raw = _run(["docker", "exec", "--user", f"{os.getuid()}:{os.getgid()}", ids[0], "node", "-e", script])
        try:
            actual = json.loads(raw)
        except ValueError:
            raise DeploymentError("Running local assets manifest could not be verified") from None
        if (actual.get("sha256") != expected["manifest_sha256"] or actual.get("entries") != expected["entries"]
                or actual.get("references") != expected["entries"] or actual.get("missing") != 0):
            raise DeploymentError("Running local assets manifest or files differ from the pinned release")
        asset_validation = expected
    image = _run(["docker", "inspect", "--format", "{{.Image}}", ids[0]]).strip()
    base = ["docker", "run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}", "--network", "host",
            "--volumes-from", f"{ids[0]}:ro", "--mount", f"type=bind,source={source / 'tools'},target=/app/tools,readonly",
            "--workdir", "/app", image, "node"]
    voices = _run([*base, "tools/check-voices.mjs", "--lang=kr"]).strip()
    _run([*base, "tools/doctor.mjs", "--port", "3100" if dev else "3000"])
    health = _health(3100 if dev else 3000, attempts=1, expected_version=pin["version"] if pin else None)
    if pin and str(health.get("app", "")).removeprefix("v") != str(pin["version"]).removeprefix("v"):
        raise DeploymentError("Running service version differs from the pinned source version")
    return {"healthy": True, "version": health.get("app"), "voices": voices, "doctor": "passed",
            "assets": asset_validation, "korean_pack": language_validation}


def rollback(root, pin=None, source=None, *, backup=None):
    _, service, _ = _paths(root)
    if backup is None:
        marker = service / ".pending-rollback"
        if not marker.is_file():
            marker = service / ".latest-rollback"
        if not marker.is_file():
            raise DeploymentError("No deployment rollback snapshot is available")
        backup = marker.read_text(encoding="utf-8").strip()
    if not _SNAPSHOT_NAME.fullmatch(str(backup)):
        raise DeploymentError("Invalid rollback snapshot name")
    backup = str(backup)
    folder = service / "rollback" / backup
    if not (folder / "compose.yaml").is_file() or not (folder / ".env").is_file():
        raise DeploymentError("Rollback snapshot files are missing")
    # Validate privately before replacing any current files.
    command = _compose(service, env_file=folder / ".env", compose_file=folder / "compose.yaml")
    _run([*command, "config", "--quiet"])
    _atomic(service / ".env", (folder / ".env").read_text(encoding="utf-8"), private=True)
    _atomic(service / "compose.yaml", (folder / "compose.yaml").read_text(encoding="utf-8"))
    _run([*_compose(service), "up", "-d", "--no-build", "--wait", "--wait-timeout", "90"])
    health = _health(3000)
    (service / ".pending-rollback").unlink(missing_ok=True)
    return {"restored": backup, "version": health.get("app"), "healthy": True}


def setup(root, pin, source, *, archive=None, image=None, start=False):
    """Finish initial setup so the generated service can be managed with Docker Compose."""
    prepared = assets(root, pin, archive=archive)
    built = build(root, pin, source, image=image)
    configured = configure(root, pin, source, image=image)
    _, service, _ = _paths(root)
    result = {"assets": prepared, "build": built, "configuration": configured,
              "service_directory": str(service), "compose": str(service / "compose.yaml")}
    if start:
        result["service"] = up(root, pin, source)
        result["verification"] = verify(root, pin, source)
    return result


def dispatch(action, root, pin, source=None, **options):
    actions = {"assets": assets, "build": build, "configure": configure, "setup": setup,
               "up": up, "down": down, "status": status, "verify": verify, "rollback": rollback}
    if action not in actions:
        raise DeploymentError(f"Unknown deployment action: {action}")
    if action == "assets":
        return assets(root, pin, **options)
    return actions[action](root, pin, source, **options)
