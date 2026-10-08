#!/usr/bin/env python3
"""Reconstruct pinned upstream sources from a patch-only repository."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
SOURCE_REL = Path(".build/Stronghold-Protocol")
# Bump when reconstruction changes generated files outside their Git contents (e.g. permissions).
SOURCE_FORMAT_VERSION = 2


class ProjectError(RuntimeError):
    pass


def run(args, *, cwd=None, optional=False, env=None):
    try:
        result = subprocess.run([str(arg) for arg in args], cwd=cwd, env=env,
                                capture_output=True, text=True, check=False)
    except FileNotFoundError:
        if optional:
            return None
        raise ProjectError(f"Required command is unavailable: {args[0]}") from None
    if result.returncode:
        if optional:
            return None
        raise ProjectError(f"{args[0]} failed (exit {result.returncode}): "
                           f"{result.stderr.strip()[:1600]}")
    return result.stdout


def git(source, *args, **kwargs):
    return run(["git", "-C", source, *args], **kwargs)


def write_json(path, data):
    """Atomically write public project data readable by the container's runtime user."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            # mkstemp starts at 0600; Docker COPY retains that mode but changes the owner to root.
            os.fchmod(stream.fileno(), 0o644)
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_pin(root=ROOT):
    try:
        pin = json.loads((Path(root) / "upstream.lock.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ProjectError("upstream.lock.json is missing or invalid") from None
    return validate_pin(pin)


def validate_pin(pin):
    if pin.get("schemaVersion") != 1:
        raise ProjectError("Unsupported upstream lock schema")
    if not re.fullmatch(r"https://github\.com/[\w.-]+/[\w.-]+(?:\.git)?",
                        str(pin.get("repository", ""))):
        raise ProjectError("Upstream repository must be an HTTPS GitHub repository")
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+(?:[-+][\w.-]+)?", str(pin.get("ref", ""))):
        raise ProjectError("Upstream ref must be a versioned release tag")
    if not re.fullmatch(r"[0-9a-f]{40}", str(pin.get("commit", ""))):
        raise ProjectError("Upstream commit must be a full 40-character SHA")
    if pin.get("version") != pin["ref"][1:]:
        raise ProjectError("Upstream version and release tag disagree")
    release = pin.get("release", {})
    base = pin["repository"].removesuffix(".git")
    expected = f"{base}/releases/download/{pin['ref']}/Stronghold-Protocol-{pin['ref']}.zip"
    if release.get("url") != expected:
        raise ProjectError("Full-release URL does not match the upstream repository and tag")
    if not re.fullmatch(r"[0-9a-f]{64}", str(release.get("sha256", ""))):
        raise ProjectError("Full release must have a pinned SHA256")
    return pin


def safe_relative(value):
    if not isinstance(value, str) or "\\" in value or "\0" in value:
        raise ProjectError("Unsafe relative source path")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(p in {".", "..", ".git"} for p in path.parts):
        raise ProjectError("Unsafe relative source path")
    return path


def apply_ui_patch(document, patch):
    """Validate all expected prior values before changing a copy of the document."""
    if patch.get("schemaVersion") != 1 or patch.get("language") != "ko":
        raise ProjectError("Unsupported Korean UI patch")
    result, conflicts = copy.deepcopy(document), []
    for section, target in [("messages", result), ("meta", result.get("_meta", {}))]:
        changes = patch.get(section, {})
        if not isinstance(changes, dict):
            raise ProjectError(f"Invalid UI patch section: {section}")
        for key, change in changes.items():
            if not isinstance(change, dict) or set(change) != {"base", "value"}:
                raise ProjectError(f"Invalid UI patch entry: {key}")
            if key not in target or target[key] not in (change["base"], change["value"]):
                conflicts.append(f"{section}.{key}")
            else:
                target[key] = change["value"]
    # New feature labels belong to this patch layer, not to the upstream translation.
    additions = patch.get("additions", {})
    if not isinstance(additions, dict):
        raise ProjectError("Invalid UI patch section: additions")
    for key, value in additions.items():
        if not isinstance(key, str) or not key or key.startswith("_") or not isinstance(value, str):
            raise ProjectError(f"Invalid UI patch addition: {key}")
        if key in patch.get("messages", {}):
            raise ProjectError(f"UI patch addition overlaps a guarded message: {key}")
        if key in result and result[key] != value:
            conflicts.append(f"additions.{key}")
        else:
            result[key] = value
    if conflicts:
        raise ProjectError("Upstream Korean text changed; review patch guards: " +
                           ", ".join(conflicts[:20]))
    return result


def patch_fingerprint(root, pin):
    digest = hashlib.sha256(json.dumps({"sourceFormat": SOURCE_FORMAT_VERSION,
                                      "upstream": pin}, sort_keys=True).encode())
    for folder in ("patches", "overlays"):
        base = Path(root) / folder
        if not base.is_dir():
            raise ProjectError(f"Missing patch directory: {folder}")
        for path in sorted(base.rglob("*")):
            if path.is_symlink():
                raise ProjectError(f"Symlinks are not allowed in {folder}")
            if path.is_file():
                digest.update(str(path.relative_to(root)).encode())
                digest.update(b"\0")
                digest.update(path.read_bytes())
    return digest.hexdigest()


def ensure_upstream(root, pin):
    root = Path(root).resolve()
    parent = root / ".cache/upstream"
    parent.mkdir(parents=True, exist_ok=True)
    cache = parent / "source"
    if cache.is_symlink() or not cache.resolve().is_relative_to(root):
        raise ProjectError("Upstream cache must remain inside the project")
    if not (cache / ".git").is_dir():
        temporary = Path(tempfile.mkdtemp(prefix=".clone-", dir=parent))
        try:
            inherited = git(root, "cat-file", "-e", pin["commit"] + "^{commit}", optional=True)
            if inherited is not None:
                run(["git", "clone", "--no-hardlinks", "--no-checkout", "--", root, temporary])
                git(temporary, "remote", "set-url", "origin", pin["repository"])
            else:
                run(["git", "clone", "--depth", "1", "--branch", pin["ref"],
                     "--no-checkout", "--", pin["repository"], temporary])
            git(temporary, "remote", "set-url", "--push", "origin", "no-push://upstream-cache")
            git(temporary, "checkout", "--detach", pin["commit"])
            os.replace(temporary, cache)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
    if git(cache, "remote", "get-url", "origin").strip() != pin["repository"]:
        raise ProjectError("Cached upstream repository differs from the lock")
    if git(cache, "cat-file", "-e", pin["commit"] + "^{commit}", optional=True) is None:
        git(cache, "fetch", "--depth", "1", "origin", pin["ref"])
        if git(cache, "rev-parse", "FETCH_HEAD^{commit}").strip() != pin["commit"]:
            raise ProjectError("Fetched release commit does not match the lock")
    if git(cache, "status", "--porcelain").strip():
        raise ProjectError("Pristine upstream cache has local edits; preserve them before preparing")
    git(cache, "checkout", "--detach", pin["commit"])
    if git(cache, "rev-parse", "HEAD").strip() != pin["commit"]:
        raise ProjectError("Upstream checkout differs from the pinned commit")
    return cache


def reconstruct(root, pin, cache, destination):
    """Build into a disposable checkout; failed patches cannot modify the usable source."""
    root, cache, destination = Path(root), Path(cache), Path(destination)
    run(["git", "clone", "--no-hardlinks", "--no-checkout", "--", cache, destination])
    git(destination, "config", "core.autocrlf", "false")
    git(destination, "checkout", "--detach", pin["commit"])
    git(destination, "remote", "set-url", "--push", "origin", "no-push://generated-source")
    version = json.loads((destination / "package.json").read_text())["version"]
    if version != pin["version"]:
        raise ProjectError("Upstream package version differs from the lock")
    ui_patch = json.loads((root / "patches/ko-ui.json").read_text(encoding="utf-8"))
    relative = safe_relative(ui_patch["file"])
    target = destination.joinpath(*relative.parts)
    document = json.loads(target.read_text(encoding="utf-8"))
    write_json(target, apply_ui_patch(document, ui_patch))
    for source in sorted((root / "overlays").rglob("*")):
        if source.is_symlink():
            raise ProjectError("Overlay symlinks are not supported")
        if not source.is_file():
            continue
        relative = safe_relative(source.relative_to(root / "overlays").as_posix())
        target = destination.joinpath(*relative.parts)
        if target.exists():
            raise ProjectError(f"New overlay collides with upstream: {relative}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    for patch in sorted((root / "patches").glob("*.patch")):
        git(destination, "apply", "--check", str(patch.resolve()))
        git(destination, "apply", str(patch.resolve()))
    exclude = destination / ".git/info/exclude"
    with exclude.open("a", encoding="utf-8") as stream:
        stream.write("\n/.stronghold-build.json\n")
    git(destination, "add", "--all")
    date = git(destination, "show", "-s", "--format=%cI", pin["commit"]).strip()
    environment = dict(os.environ, GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
    git(destination, "-c", "user.name=Stronghold patch build",
        "-c", "user.email=build@localhost", "commit", "-m",
        f"Apply Korean patch layer to {pin['ref']}", env=environment)
    return {"schemaVersion": 1, "upstream": pin,
            "fingerprint": patch_fingerprint(root, pin),
            "tree": git(destination, "rev-parse", "HEAD^{tree}").strip()}


def prepare(root=ROOT, *, pin=None, force=False, _keep_previous=False):
    root = Path(root).resolve()
    pin = validate_pin(pin) if pin is not None else load_pin(root)
    build = root / ".build"
    if build.is_symlink() or not build.resolve().is_relative_to(root):
        raise ProjectError("Build directory must remain inside the project")
    build.mkdir(exist_ok=True)
    target = root / SOURCE_REL
    if target.is_symlink():
        raise ProjectError("Generated source must not be a symlink")
    stamp = target / ".stronghold-build.json"
    fingerprint = patch_fingerprint(root, pin)
    if stamp.is_file() and not force:
        metadata = json.loads(stamp.read_text())
        if metadata.get("fingerprint") == fingerprint:
            if git(target, "status", "--porcelain").strip():
                raise ProjectError("Generated source has local edits; capture them before prepare")
            if metadata.get("tree") != git(target, "rev-parse", "HEAD^{tree}").strip():
                raise ProjectError("Generated source provenance changed; review before replacing")
            return target
    if target.is_dir() and git(target, "status", "--porcelain").strip() and not force:
        raise ProjectError("Generated source has local edits; capture them before prepare")
    cache = ensure_upstream(root, pin)
    temporary = Path(tempfile.mkdtemp(prefix=".prepare-", dir=build))
    previous = build / ".previous-source"
    try:
        metadata = reconstruct(root, pin, cache, temporary)
        write_json(temporary / ".stronghold-build.json", metadata)
        if previous.exists():
            raise ProjectError("A previous source recovery directory exists; recover it before preparing")
        if target.exists():
            os.replace(target, previous)
        try:
            os.replace(temporary, target)
        except BaseException:
            if previous.exists():
                os.replace(previous, target)
            raise
        if previous.exists() and not _keep_previous:
            shutil.rmtree(previous)
        return target
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def update(root, ref, release_sha256=None):
    """Only advance the lock after the candidate reconstructs with every guard satisfied."""
    root = Path(root)
    old = load_pin(root)
    candidate = dict(old, ref=ref, version=ref.removeprefix("v"))
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+(?:[-+][\w.-]+)?", ref):
        raise ProjectError("Use an existing upstream release tag, for example v0.2.1")
    cache = ensure_upstream(root, old)
    git(cache, "fetch", "--depth", "1", "origin", "refs/tags/" + ref)
    candidate["commit"] = git(cache, "rev-parse", "FETCH_HEAD^{commit}").strip()
    repository = old["repository"].removesuffix(".git")
    slug = repository.removeprefix("https://github.com/")
    request = urllib.request.Request(f"https://api.github.com/repos/{slug}/releases/tags/{ref}",
                                     headers={"User-Agent": "Stronghold-Korean-patches",
                                              "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            release = json.load(response)
    except (OSError, ValueError):
        raise ProjectError("Cannot read the upstream GitHub release metadata") from None
    asset_name = f"Stronghold-Protocol-{ref}.zip"
    asset = next((item for item in release.get("assets", [])
                  if item.get("name") == asset_name), None)
    if asset is None:
        raise ProjectError("Selected release has no matching Full Release asset")
    published = str(asset.get("digest") or "").removeprefix("sha256:")
    digest = release_sha256 or published
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ProjectError("Release has no SHA256 digest; supply --release-sha256 after verification")
    if published and published != digest:
        raise ProjectError("Supplied digest differs from GitHub's release digest")
    candidate["release"] = {"url": f"{repository}/releases/download/{ref}/{asset_name}",
                            "sha256": digest}
    validate_pin(candidate)
    target, previous = root / SOURCE_REL, root / ".build/.previous-source"
    had_source = target.exists()
    source = prepare(root, pin=candidate, _keep_previous=True)
    try:
        write_json(root / "upstream.lock.json", candidate)
    except BaseException:
        if previous.exists():
            shutil.rmtree(target)
            os.replace(previous, target)
        elif not had_source and target.exists():
            shutil.rmtree(target)
        raise
    if previous.exists():
        shutil.rmtree(previous)
    return source


def capture(root, paths, name):
    root, source = Path(root), Path(root) / SOURCE_REL
    if not re.fullmatch(r"[0-9]{4}-[\w.-]+\.patch", name):
        raise ProjectError("Use a patch name like 0002-my-change.patch")
    output = root / "patches" / name
    if output.exists():
        raise ProjectError("Choose a new patch name; existing patches are preserved")
    numbers = [int(p.name[:4]) for p in (root / "patches").glob("*.patch")
               if re.match(r"^[0-9]{4}-", p.name)]
    if numbers and int(name[:4]) <= max(numbers):
        raise ProjectError("Use the next patch sequence number so the new patch applies last")
    selected, additions = [], []
    for value in paths:
        relative = safe_relative(value)
        if relative.as_posix() in {"data/assets.json", "data/local-assets.json",
                                  ".stronghold-build.json", ".update-applied.json",
                                  "MANIFEST.json", "UPDATE.json"}:
            raise ProjectError("Generated manifests and runtime metadata cannot become patches")
        if relative.parts[0] in {".cache", "node_modules"} or any(p.startswith(".env") for p in relative.parts):
            raise ProjectError("Private settings and dependency files cannot become patches")
        if relative.parts[:2] in {("public", "assets"), ("public", "fonts"), ("public", "vendor")}:
            raise ProjectError("Downloaded game assets and vendor builds do not belong in this repository")
        target = source.joinpath(*relative.parts)
        if target.is_dir():
            raise ProjectError("Capture individual source files, not entire directories")
        if target.is_symlink() or (target.exists() and not target.resolve().is_relative_to(source.resolve())):
            raise ProjectError("Capture source paths must remain inside the generated checkout")
        if git(source, "ls-files", "--error-unmatch", "--", relative.as_posix(), optional=True) is None:
            if not target.is_file():
                raise ProjectError("New overlays must be regular files")
            additions.append((target, root / "overlays" / Path(*relative.parts)))
        else:
            selected.append(relative.as_posix())
    body = git(source, "diff", "--binary", "--full-index", "HEAD", "--", *selected) if selected else ""
    if not body and not additions:
        raise ProjectError("No changes found in the selected source files")
    for original, overlay in additions:
        if overlay.exists():
            raise ProjectError("Overlay already exists; review its edits explicitly")
    (root / ".cache").mkdir(exist_ok=True)
    pending = Path(tempfile.mkdtemp(prefix=".capture-", dir=root / ".cache"))
    published = []
    index = source / ".git/index"
    previous_index = index.read_bytes()
    try:
        files = []
        if body:
            staged_patch = pending / "change.patch"
            staged_patch.write_text(body, encoding="utf-8")
            files.append((staged_patch, output))
        for offset, (original, overlay) in enumerate(additions):
            staged_file = pending / f"overlay-{offset}"
            shutil.copy2(original, staged_file)
            files.append((staged_file, overlay))
        for staged_file, final in files:
            final.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged_file, final)
            published.append(final)
        # Commit exactly captured paths in the private generated checkout.
        # Other staged/unstaged changes remain visible and prevent a destructive prepare.
        captured = selected + [p.relative_to(root / "overlays").as_posix() for _, p in additions]
        git(source, "add", "--", *captured)
        git(source, "-c", "user.name=Stronghold patch build",
            "-c", "user.email=build@localhost", "commit", "--only",
            "-m", "Capture local patch changes", "--", *captured)
    except BaseException:
        index.write_bytes(previous_index)
        for final in published:
            final.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(pending)
    return {"patch": str(output.relative_to(root)) if body else None,
            "overlays": [str(p.relative_to(root)) for _, p in additions]}


def check(root, source, pin, *, full=False):
    """Run checks through Docker, including Git for upstream packaging tests."""
    validation = "stronghold-protocol:ko-checks"
    run(["docker", "build", "-f", Path(root) / "deploy/Dockerfile.checks",
         "--build-arg", "BASE_IMAGE=node:22-alpine", "-t", validation, Path(root) / "deploy"])
    mount = "/workspace/Stronghold-Protocol"
    prefix = ["docker", "run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}",
              "-e", "NODE_ENV=development", "-e", "npm_config_cache=/tmp/sp-npm-cache",
              "-v", f"{source}:{mount}", "-w", mount, validation]
    commands = [["npm", "ci", "--include=dev", "--no-audit", "--no-fund"],
                ["node", "tools/i18n.mjs", "check", "ko", "--strict"],
                ["node", "--test", "test/voices.test.js"],
                ["npm", "run", "lint"], ["npm", "run", "check:imports"],
                ["npm", "run", "typecheck"]]
    if full:
        commands.insert(3, ["node", "--test", "--test-concurrency=2"])
    for command in commands:
        print("Checking:", " ".join(command), flush=True)
        output = run([*prefix, *command])
        if command[0] == "node" or command[-1] != "lint":
            print(output[-2500:].strip(), flush=True)
    return {"checks": len(commands), "full": full}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    prep = sub.add_parser("prepare", help="Reconstruct pinned source with Korean patches")
    prep.add_argument("--force", action="store_true", help="Replace local generated-source edits")
    upd = sub.add_parser("update", help="Validate a release and advance the source lock")
    upd.add_argument("--ref", required=True)
    upd.add_argument("--release-sha256")
    cap = sub.add_parser("capture", help="Export selected generated-source edits as a patch/overlay")
    cap.add_argument("--path", action="append", required=True, dest="paths")
    cap.add_argument("--name", required=True)
    checks = sub.add_parser("check", help="Docker-based Node checks for the generated source")
    checks.add_argument("--full", action="store_true")
    for action in ("assets", "configure", "build", "setup", "up", "down", "status", "verify", "rollback"):
        help_text = ("Prepare upstream and patches, assets, final image and service Compose; "
                     "then manage service/ with docker compose") if action == "setup" else None
        command = sub.add_parser(action, help=help_text, description=help_text)
        if action in {"assets", "setup"}:
            command.add_argument("--archive", type=Path)
        if action in {"configure", "build", "setup"}:
            command.add_argument("--image")
        if action == "build":
            command.add_argument("--fetch-assets", choices=["0", "1"], default="1")
        if action == "setup":
            command.add_argument("--start", action="store_true", help="Also start and verify after setup")
        if action == "rollback":
            command.add_argument("--snapshot", help="Private rollback snapshot ID")
        if action in {"up", "down", "status", "verify"}:
            command.add_argument("--dev", action="store_true")
    args = parser.parse_args(argv)
    try:
        if __package__:
            from . import deploy
        else:
            import deploy
        pin = load_pin(ROOT)
        if args.action == "prepare":
            result = {"source": str(prepare(ROOT, force=args.force)), "upstream": pin["ref"]}
        elif args.action == "update":
            result = {"source": str(update(ROOT, args.ref, args.release_sha256)),
                      "upstream": load_pin(ROOT)["ref"]}
        elif args.action == "capture":
            result = capture(ROOT, args.paths, args.name)
        elif args.action == "check":
            result = check(ROOT, prepare(ROOT), pin, full=args.full)
        else:
            options = vars(args).copy()
            options.pop("action")
            if args.action == "rollback":
                options["backup"] = options.pop("snapshot")
            source = prepare(ROOT) if args.action in {"configure", "build", "setup"} else ROOT / SOURCE_REL
            result = deploy.dispatch(args.action, ROOT, pin, source=source, **options)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ProjectError, deploy.DeploymentError, OSError, ValueError, KeyError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
