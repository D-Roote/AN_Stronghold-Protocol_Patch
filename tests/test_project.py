"""Patch reconstruction checks using local, minimal Git sources and no network."""
import copy
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


REPOSITORY = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("stronghold_project", REPOSITORY / "scripts/project.py")
project = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(project)


class KoreanUITests(unittest.TestCase):
    def setUp(self):
        self.layer = json.loads((REPOSITORY / "patches/ko-ui.json").read_text(encoding="utf-8"))
        self.document = {"_meta": {"lang": "ko", "machineTranslated": True},
                         "unchanged upstream text": "원본 번역"}
        self.document["_meta"].update({key: change["base"] for key, change in self.layer["meta"].items()})
        self.document.update({key: change["base"] for key, change in self.layer["messages"].items()})

    def test_real_layer_applies_all_corrections_without_changing_input(self):
        original = copy.deepcopy(self.document)
        result = project.apply_ui_patch(self.document, self.layer)
        for key, change in self.layer["messages"].items():
            self.assertEqual(result[key], change["value"])
        for key, change in self.layer["meta"].items():
            self.assertEqual(result["_meta"][key], change["value"])
        for key, value in self.layer.get("additions", {}).items():
            self.assertEqual(result[key], value)
        self.assertEqual(result["unchanged upstream text"], "원본 번역")
        for key, value in self.document["_meta"].items():
            if key not in self.layer["meta"]:
                self.assertEqual(result["_meta"][key], value)
        self.assertEqual(self.document, original)

    def test_upstream_new_messages_and_metadata_are_preserved(self):
        self.document["new upstream message"] = "새 번역"
        self.document["_meta"]["new upstream field"] = {"revision": 3}
        result = project.apply_ui_patch(self.document, self.layer)
        self.assertEqual(result["new upstream message"], "새 번역")
        self.assertEqual(result["_meta"]["new upstream field"], {"revision": 3})

    def test_separate_real_feature_translations_preserve_upstream_corrections(self):
        layer = json.loads((REPOSITORY / "patches/ko-features.json").read_text(encoding="utf-8"))
        corrected = project.apply_ui_patch(self.document, self.layer)
        before = copy.deepcopy(corrected)
        result = project.apply_ui_patch(corrected, layer)
        for key, value in layer["additions"].items():
            self.assertEqual(result[key], value)
        for key, value in corrected.items():
            self.assertEqual(result[key], value)
        self.assertEqual(corrected, before)

    def test_changed_or_deleted_guarded_values_reject_without_mutating_input(self):
        first = next(iter(self.layer["messages"]))
        cases = [("messages", first, "changed"), ("messages", first, "deleted"),
                 ("meta", "version", "changed"), ("meta", "credits", "deleted")]
        for section, key, action in cases:
            with self.subTest(section=section, action=action):
                document = copy.deepcopy(self.document)
                target = document if section == "messages" else document["_meta"]
                if action == "changed":
                    target[key] = "unexpected upstream replacement"
                else:
                    del target[key]
                before = copy.deepcopy(document)
                with self.assertRaisesRegex(project.ProjectError, "review patch guards"):
                    project.apply_ui_patch(document, self.layer)
                self.assertEqual(document, before)

    def test_already_corrected_upstream_values_are_accepted(self):
        result = project.apply_ui_patch(self.document, self.layer)
        self.assertEqual(project.apply_ui_patch(result, self.layer), result)

    def test_feature_additions_reject_upstream_collisions_without_mutating_input(self):
        layer = copy.deepcopy(self.layer)
        layer["additions"] = {"new feature": "새 기능"}
        result = project.apply_ui_patch(self.document, layer)
        self.assertEqual(result["new feature"], "새 기능")
        self.assertEqual(project.apply_ui_patch(result, layer), result)
        document = dict(self.document, **{"new feature": "upstream replacement"})
        before = copy.deepcopy(document)
        with self.assertRaisesRegex(project.ProjectError, "additions.new feature"):
            project.apply_ui_patch(document, layer)
        self.assertEqual(document, before)

    def test_feature_additions_reject_metadata_non_strings_and_guarded_keys(self):
        for additions in [{"_meta": "wrong"}, {"label": {}}, [],
                          {next(iter(self.layer["messages"])): "replacement"}]:
            with self.subTest(additions=additions):
                with self.assertRaises(project.ProjectError):
                    project.apply_ui_patch(self.document, dict(self.layer, additions=additions))


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="stronghold-project-test-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        global_config = self.base / "empty-gitconfig"
        global_config.write_text("")
        environment = patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(global_config),
                                             "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0"})
        environment.start()
        self.addCleanup(environment.stop)
        self.root = self.base / "patch-project"
        self.root.mkdir()
        self.upstream = self.base / "pristine"
        self.upstream.mkdir()
        self.git(self.upstream, "init", "--quiet")
        self.git(self.upstream, "config", "user.name", "Local fixture")
        self.git(self.upstream, "config", "user.email", "fixture@localhost")
        project.write_json(self.upstream / "package.json", {"name": "minimal-fixture", "version": "0.2.1"})
        project.write_json(self.upstream / "public/i18n/ko.json", {
            "_meta": {"version": "0.2.0", "credits": "original", "machineTranslated": True},
            "hello": "원본", "untouched": "유지"})
        (self.upstream / "Dockerfile").write_text("FROM scratch\n# upstream marker\n")
        (self.upstream / ".gitignore").write_text(".env\npublic/assets/\nnode_modules/\n")
        self.commit_fixture("Initial upstream fixture")
        self.pin = {"schemaVersion": 1, "repository": "https://github.com/example/Stronghold-Protocol.git",
                    "ref": "v0.2.1", "version": "0.2.1",
                    "commit": self.git(self.upstream, "rev-parse", "HEAD").strip(),
                    "release": {"url": "https://github.com/example/Stronghold-Protocol/releases/download/v0.2.1/Stronghold-Protocol-v0.2.1.zip",
                                "sha256": "a" * 64}}
        project.write_json(self.root / "upstream.lock.json", self.pin)
        self.layer = {"schemaVersion": 1, "language": "ko", "file": "public/i18n/ko.json",
                      "source": {"tag": self.pin["ref"], "commit": self.pin["commit"]},
                      "meta": {"version": {"base": "0.2.0", "value": "0.2.1"}},
                      "messages": {"hello": {"base": "원본", "value": "교정"}}}
        project.write_json(self.root / "patches/ko-ui.json", self.layer)
        (self.root / "patches/01-001-Build-marker.patch").write_text(
            "diff --git a/Dockerfile b/Dockerfile\n"
            "--- a/Dockerfile\n+++ b/Dockerfile\n@@ -1,2 +1,2 @@\n"
            " FROM scratch\n-# upstream marker\n+# Korean build marker\n")
        overlay = self.root / "overlays/tools/custom-check.mjs"
        overlay.parent.mkdir(parents=True)
        overlay.write_text("export const language = 'kr';\n")

    @staticmethod
    def git(source, *args):
        result = subprocess.run(["git", "-C", str(source), *args], check=True,
                                text=True, capture_output=True)
        return result.stdout

    def commit_fixture(self, message):
        self.git(self.upstream, "add", "--all")
        with patch.dict(os.environ, {"GIT_AUTHOR_DATE": "2000-01-01T00:00:00+00:00",
                                     "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+00:00"}):
            self.git(self.upstream, "commit", "--quiet", "-m", message)
        return self.git(self.upstream, "rev-parse", "HEAD").strip()

    def prepare(self, **kwargs):
        with patch.object(project, "ensure_upstream", return_value=self.upstream):
            return project.prepare(self.root, **kwargs)

    def update_with_local_release(self, ref, candidate_commit, *, supplied_digest=None):
        release = {"assets": [{"name": f"Stronghold-Protocol-{ref}.zip", "digest": "sha256:" + "b" * 64}]}
        actual_git = project.git

        def local_fetch(source, *args, **kwargs):
            if Path(source) == self.upstream and args[0] == "fetch":
                return ""
            if Path(source) == self.upstream and args == ("rev-parse", "FETCH_HEAD^{commit}"):
                return candidate_commit + "\n"
            return actual_git(source, *args, **kwargs)

        with patch.object(project, "ensure_upstream", return_value=self.upstream), \
             patch.object(project, "git", side_effect=local_fetch), \
             patch.object(project.urllib.request, "urlopen", return_value=io.StringIO(json.dumps(release))):
            return project.update(self.root, ref, supplied_digest)

    def test_prepare_creates_clean_committed_source_and_leaves_pristine_upstream_unchanged(self):
        upstream_head = self.git(self.upstream, "rev-parse", "HEAD")
        source = self.prepare()
        document = json.loads((source / "public/i18n/ko.json").read_text())
        self.assertEqual(document["hello"], "교정")
        self.assertEqual(document["untouched"], "유지")
        self.assertEqual(document["_meta"]["version"], "0.2.1")
        self.assertEqual((source / "public/i18n/ko.json").stat().st_mode & 0o777, 0o644,
                         "Docker's node user must be able to read root-owned Korean strings")
        self.assertEqual((source / "Dockerfile").read_text(), "FROM scratch\n# Korean build marker\n")
        self.assertTrue((source / "tools/custom-check.mjs").is_file())
        self.assertEqual(self.git(source, "status", "--porcelain"), "")
        self.assertNotEqual(self.git(source, "rev-parse", "HEAD").strip(), self.pin["commit"])
        self.assertEqual(self.git(source, "rev-parse", "HEAD^1").strip(), self.pin["commit"])
        self.assertEqual(self.git(self.upstream, "rev-parse", "HEAD"), upstream_head)
        self.assertEqual(self.git(self.upstream, "status", "--porcelain"), "")
        self.assertEqual(self.git(source, "remote", "get-url", "--push", "origin").strip(),
                         "no-push://generated-source")

    def test_prepare_rebuilds_after_source_generation_format_changes(self):
        source = self.prepare()
        korean = source / "public/i18n/ko.json"
        korean.chmod(0o600)
        with patch.object(project, "SOURCE_FORMAT_VERSION", project.SOURCE_FORMAT_VERSION + 1):
            self.assertEqual(self.prepare(), source)
        self.assertEqual(korean.stat().st_mode & 0o777, 0o644)
        self.assertEqual(json.loads(korean.read_text())["hello"], "교정")

    def test_prepare_applies_separate_feature_translations_and_rebuilds_after_edits(self):
        layer = {"schemaVersion": 1, "language": "ko", "file": "public/i18n/ko.json",
                 "additions": {"new feature": "새 기능"}}
        path = self.root / "patches/ko-features.json"
        project.write_json(path, layer)
        source = self.prepare()
        document = json.loads((source / "public/i18n/ko.json").read_text())
        self.assertEqual(document["hello"], "교정")
        self.assertEqual(document["new feature"], "새 기능")
        layer["additions"]["new feature"] = "기능 수정"
        project.write_json(path, layer)
        self.assertEqual(self.prepare(), source)
        document = json.loads((source / "public/i18n/ko.json").read_text())
        self.assertEqual(document["new feature"], "기능 수정")
        self.assertEqual(self.git(source, "status", "--porcelain"), "")

    def test_feature_translation_collision_preserves_prepared_source(self):
        source = self.prepare()
        before = self.git(source, "rev-parse", "HEAD")
        project.write_json(self.root / "patches/ko-features.json", {
            "schemaVersion": 1, "language": "ko", "file": "public/i18n/ko.json",
            "additions": {"hello": "추가 기능"}})
        with self.assertRaisesRegex(project.ProjectError, "additions.hello"):
            self.prepare()
        self.assertEqual(self.git(source, "rev-parse", "HEAD"), before)
        self.assertEqual(json.loads((source / "public/i18n/ko.json").read_text())["hello"], "교정")

    def test_initial_upstream_cache_bootstrap_checks_out_source_before_cleanliness_check(self):
        # A local bootstrap can reuse available objects, without network or real repository history.
        shutil.copytree(self.upstream / ".git", self.root / ".git")
        cache = project.ensure_upstream(self.root, self.pin)
        self.assertEqual(self.git(cache, "rev-parse", "HEAD").strip(), self.pin["commit"])
        self.assertEqual(self.git(cache, "status", "--porcelain"), "")
        self.assertEqual((cache / "Dockerfile").read_text(), "FROM scratch\n# upstream marker\n")
        self.assertEqual(self.git(cache, "remote", "get-url", "origin").strip(), self.pin["repository"])
        self.assertEqual(self.git(cache, "remote", "get-url", "--push", "origin").strip(),
                         "no-push://upstream-cache")

    def test_prepare_is_reproducible_and_noop_skips_upstream_access(self):
        source = self.prepare()
        original = self.git(source, "rev-parse", "HEAD")
        stamp = (source / ".stronghold-build.json").read_bytes()
        with patch.object(project, "ensure_upstream", side_effect=AssertionError("No network or fetch expected")):
            self.assertEqual(project.prepare(self.root), source)
        self.prepare(force=True)
        self.assertEqual(self.git(source, "rev-parse", "HEAD"), original)
        self.assertEqual((source / ".stronghold-build.json").read_bytes(), stamp)

    def test_setup_cli_prepares_upstream_and_patches_without_prior_prepare_command(self):
        deployment = types.ModuleType("deploy")
        deployment.DeploymentError = RuntimeError
        deployment.dispatch = Mock(return_value={"configured": True})
        self.assertFalse((self.root / project.SOURCE_REL).exists())
        with patch.object(project, "ROOT", self.root), patch.object(project, "ensure_upstream", return_value=self.upstream), patch.dict(sys.modules, {"deploy": deployment}), patch("sys.stdout", new=io.StringIO()):
            self.assertEqual(project.main(["setup"]), 0)
        source = self.root / project.SOURCE_REL
        self.assertEqual(json.loads((source / "public/i18n/ko.json").read_text())["hello"], "교정")
        self.assertIn("Korean build marker", (source / "Dockerfile").read_text())
        self.assertTrue((source / "tools/custom-check.mjs").is_file())
        deployment.dispatch.assert_called_once_with("setup", self.root, self.pin, source=source,
                                                    archive=None, image=None, start=False)

    def test_repeated_setup_cli_automatically_reapplies_changed_patch(self):
        deployment = types.ModuleType("deploy")
        deployment.DeploymentError = RuntimeError
        deployment.dispatch = Mock(return_value={"configured": True})
        with patch.object(project, "ROOT", self.root), patch.object(project, "ensure_upstream", return_value=self.upstream), patch.dict(sys.modules, {"deploy": deployment}), patch("sys.stdout", new=io.StringIO()):
            self.assertEqual(project.main(["setup"]), 0)
            self.layer["messages"]["hello"]["value"] = "다시 교정"
            project.write_json(self.root / "patches/ko-ui.json", self.layer)
            self.assertEqual(project.main(["setup"]), 0)
        source = self.root / project.SOURCE_REL
        self.assertEqual(json.loads((source / "public/i18n/ko.json").read_text())["hello"], "다시 교정")
        self.assertEqual(self.git(source, "status", "--porcelain"), "")
        self.assertEqual(deployment.dispatch.call_count, 2)

    def test_setup_cli_patch_conflict_stops_before_image_or_runtime_configuration(self):
        deployment = types.ModuleType("deploy")
        deployment.DeploymentError = RuntimeError
        deployment.dispatch = Mock()
        self.layer["messages"]["hello"]["base"] = "unexpected upstream"
        project.write_json(self.root / "patches/ko-ui.json", self.layer)
        with patch.object(project, "ROOT", self.root), patch.object(project, "ensure_upstream", return_value=self.upstream), patch.dict(sys.modules, {"deploy": deployment}), patch("sys.stderr", new=io.StringIO()):
            self.assertEqual(project.main(["setup"]), 1)
        deployment.dispatch.assert_not_called()
        self.assertFalse((self.root / project.SOURCE_REL).exists())

    def test_local_source_edits_are_preserved_until_explicit_force(self):
        source = self.prepare()
        target = source / "Dockerfile"
        edited = target.read_text() + "# local work\n"
        target.write_text(edited)
        with self.assertRaisesRegex(project.ProjectError, "capture them"):
            self.prepare()
        self.assertEqual(target.read_text(), edited)
        self.prepare(force=True)
        self.assertNotIn("local work", target.read_text())

    def test_failed_patch_preserves_previous_source_lock_and_metadata(self):
        source = self.prepare()
        before = {path: (self.root / path).read_bytes() for path in (
            "upstream.lock.json", ".build/Stronghold-Protocol/.stronghold-build.json",
            ".build/Stronghold-Protocol/Dockerfile")}
        head = self.git(source, "rev-parse", "HEAD")
        (self.root / "patches/05-001-Fix-broken.patch").write_text(
            "diff --git a/Dockerfile b/Dockerfile\n--- a/Dockerfile\n+++ b/Dockerfile\n"
            "@@ -1 +1 @@\n-does not exist\n+broken\n")
        with self.assertRaises(project.ProjectError):
            self.prepare()
        for path, expected in before.items():
            self.assertEqual((self.root / path).read_bytes(), expected)
        self.assertEqual(self.git(source, "rev-parse", "HEAD"), head)
        self.assertEqual(list((self.root / ".build").glob(".prepare-*")), [])

    def test_new_upstream_file_conflicting_with_overlay_is_rejected_before_replacement(self):
        source = self.prepare()
        head = self.git(source, "rev-parse", "HEAD")
        collision = self.upstream / "tools/custom-check.mjs"
        collision.parent.mkdir()
        collision.write_text("export const upstream = true;\n")
        candidate = copy.deepcopy(self.pin)
        candidate["commit"] = self.commit_fixture("Upstream adds a formerly custom tool")
        with self.assertRaisesRegex(project.ProjectError, "collides with upstream"):
            self.prepare(pin=candidate)
        self.assertEqual(self.git(source, "rev-parse", "HEAD"), head)
        self.assertEqual(project.load_pin(self.root), self.pin)

    def test_ui_guard_conflict_preserves_previous_source(self):
        source = self.prepare()
        head = self.git(source, "rev-parse", "HEAD")
        document = json.loads((self.upstream / "public/i18n/ko.json").read_text())
        document["hello"] = "원본의 새 교정"
        project.write_json(self.upstream / "public/i18n/ko.json", document)
        candidate = copy.deepcopy(self.pin)
        candidate["commit"] = self.commit_fixture("Upstream changes guarded text")
        with self.assertRaisesRegex(project.ProjectError, "review patch guards"):
            self.prepare(pin=candidate)
        self.assertEqual(self.git(source, "rev-parse", "HEAD"), head)
        self.assertEqual(json.loads((source / "public/i18n/ko.json").read_text())["hello"], "교정")

    def test_upstream_package_version_must_match_lock(self):
        project.write_json(self.upstream / "package.json", {"version": "9.9.9"})
        candidate = copy.deepcopy(self.pin)
        candidate["commit"] = self.commit_fixture("Incorrect release version")
        with self.assertRaisesRegex(project.ProjectError, "package version"):
            self.prepare(pin=candidate)
        self.assertFalse((self.root / project.SOURCE_REL).exists())

    def test_capture_tracked_edit_roundtrips_through_prepare(self):
        source = self.prepare()
        edited = (source / "Dockerfile").read_text() + "# additional local customization\n"
        (source / "Dockerfile").write_text(edited)
        result = project.capture(self.root, ["Dockerfile"], "03-001-Feat-extra.patch")
        self.assertEqual(result, {"patch": "patches/03-001-Feat-extra.patch", "overlays": []})
        self.assertIn("+# additional local customization", (self.root / result["patch"]).read_text())
        self.assertEqual([p.name for p in project.ordered_patches(self.root)],
                         ["01-001-Build-marker.patch", "03-001-Feat-extra.patch"])
        self.prepare()
        self.assertEqual((source / "Dockerfile").read_text(), edited)
        self.assertEqual(self.git(source, "status", "--porcelain"), "")

    def test_capture_new_source_file_roundtrips_as_overlay(self):
        source = self.prepare()
        patches_before = project.ordered_patches(self.root)
        addition = source / "tools/extra.mjs"
        addition.write_text("export const added = true;\n")
        result = project.capture(self.root, ["tools/extra.mjs"], "03-001-Feat-extra.patch")
        self.assertEqual(result, {"patch": None, "overlays": ["overlays/tools/extra.mjs"]})
        self.assertFalse((self.root / "patches/03-001-Feat-extra.patch").exists())
        self.assertEqual(project.ordered_patches(self.root), patches_before)
        self.prepare()
        self.assertEqual(addition.read_text(), "export const added = true;\n")
        self.assertEqual(self.git(source, "status", "--porcelain"), "")

    def test_capture_binary_edit_roundtrips_without_corruption(self):
        binary = self.upstream / "fixture.bin"
        binary.write_bytes(b"\x00\x01original\xff")
        self.pin["commit"] = self.commit_fixture("Binary source fixture")
        project.write_json(self.root / "upstream.lock.json", self.pin)
        source = self.prepare()
        changed = b"\x00\x02changed\xff\xfe"
        (source / "fixture.bin").write_bytes(changed)
        result = project.capture(self.root, ["fixture.bin"], "05-001-Fix-binary.patch")
        self.assertIn("GIT binary patch", (self.root / result["patch"]).read_text())
        self.prepare()
        self.assertEqual((source / "fixture.bin").read_bytes(), changed)

    def test_capture_preserves_other_staged_and_unstaged_edits_then_prepare_refuses(self):
        source = self.prepare()
        docker = source / "Dockerfile"
        docker.write_text(docker.read_text() + "# captured change\n")
        package = source / "package.json"
        package.write_text(package.read_text() + "\n")
        self.git(source, "add", "package.json")
        translations = source / "public/i18n/ko.json"
        document = json.loads(translations.read_text())
        document["untouched"] = "별도의 번역 작업"
        project.write_json(translations, document)
        expected_package, expected_translations = package.read_bytes(), translations.read_bytes()
        original_package = self.git(source, "show", "HEAD:package.json")
        project.capture(self.root, ["Dockerfile"], "05-001-Fix-captured.patch")
        self.assertIn("# captured change", self.git(source, "show", "HEAD:Dockerfile"))
        self.assertEqual(self.git(source, "show", "HEAD:package.json"), original_package)
        self.assertEqual(self.git(source, "show", ":package.json").encode(), expected_package)
        self.assertEqual(package.read_bytes(), expected_package)
        self.assertEqual(translations.read_bytes(), expected_translations)
        status = self.git(source, "status", "--porcelain")
        self.assertIn("M  package.json", status)
        self.assertIn(" M public/i18n/ko.json", status)
        self.assertNotIn("Dockerfile", status)
        index = (source / ".git/index").read_bytes()
        with self.assertRaisesRegex(project.ProjectError, "capture them"):
            self.prepare()
        self.assertEqual((source / ".git/index").read_bytes(), index)
        self.assertEqual(package.read_bytes(), expected_package)
        self.assertEqual(translations.read_bytes(), expected_translations)

    def test_capture_directory_rejects_before_exporting_subtree(self):
        source = self.prepare()
        translations = source / "public/i18n/ko.json"
        translations.write_text(translations.read_text() + "\n")
        with self.assertRaisesRegex(project.ProjectError, "individual source files"):
            project.capture(self.root, ["public"], "05-001-Fix-directory.patch")
        self.assertFalse((self.root / "patches/05-001-Fix-directory.patch").exists())
        self.assertTrue(self.git(source, "status", "--porcelain").strip())

    def assert_capture_failure_preserves_edits(self, source, failure):
        docker = source / "Dockerfile"
        docker.write_text(docker.read_text() + "# pending captured edit\n")
        new_file = source / "tools/new-check.mjs"
        new_file.write_text("export const check = 'pending';\n")
        package = source / "package.json"
        package.write_text(package.read_text() + "\n")
        self.git(source, "add", "package.json")
        original_index = (source / ".git/index").read_bytes()
        original_head = self.git(source, "rev-parse", "HEAD")
        expected = {file: file.read_bytes() for file in (docker, new_file, package)}
        with failure, self.assertRaises((project.ProjectError, OSError)):
            project.capture(self.root, ["Dockerfile", "tools/new-check.mjs"], "03-001-Feat-pending.patch")
        self.assertFalse((self.root / "patches/03-001-Feat-pending.patch").exists())
        self.assertFalse((self.root / "overlays/tools/new-check.mjs").exists())
        self.assertEqual((source / ".git/index").read_bytes(), original_index)
        self.assertEqual(self.git(source, "rev-parse", "HEAD"), original_head)
        for file, contents in expected.items():
            self.assertEqual(file.read_bytes(), contents)
        self.assertEqual(list((self.root / ".cache").glob(".capture-*")), [])

    def test_capture_commit_failure_removes_exports_and_restores_original_index(self):
        source = self.prepare()
        actual_git = project.git

        def failing_commit(checkout, *args, **kwargs):
            if Path(checkout) == source and "commit" in args:
                raise project.ProjectError("Simulated commit failure")
            return actual_git(checkout, *args, **kwargs)

        self.assert_capture_failure_preserves_edits(
            source, patch.object(project, "git", side_effect=failing_commit))

    def test_capture_publication_failure_removes_partial_exports_and_preserves_work(self):
        source = self.prepare()
        actual_replace = project.os.replace
        overlay = self.root / "overlays/tools/new-check.mjs"

        def failing_publication(original, destination):
            if Path(destination) == overlay:
                raise OSError("Simulated overlay publication failure")
            return actual_replace(original, destination)

        self.assert_capture_failure_preserves_edits(
            source, patch.object(project.os, "replace", side_effect=failing_publication))

    def test_capture_appends_after_existing_index_gap(self):
        folder = self.root / "patches"
        (folder / "01-001-Build-marker.patch").rename(folder / "01-007-Build-marker.patch")
        source = self.prepare()
        target = source / "Dockerfile"
        expected = target.read_text() + "# captured after a gap\n"
        target.write_text(expected)
        result = project.capture(self.root, ["Dockerfile"], "01-008-Build-gap.patch")
        self.assertEqual(result["patch"], "patches/01-008-Build-gap.patch")
        self.assertEqual([p.name for p in project.ordered_patches(self.root)],
                         ["01-007-Build-marker.patch", "01-008-Build-gap.patch"])
        self.prepare()
        self.assertEqual(target.read_text(), expected)

    def test_numeric_order_applies_dependent_patches_with_gaps(self):
        folder = self.root / "patches"
        (folder / "01-007-Build-followup.patch").write_text(
            "diff --git a/Dockerfile b/Dockerfile\n--- a/Dockerfile\n+++ b/Dockerfile\n"
            "@@ -1,2 +1,2 @@\n FROM scratch\n"
            "-# Korean build marker\n+# Korean build marker extended\n")
        source = self.prepare()
        expected = (source / "Dockerfile").read_bytes()
        self.assertIn(b"marker extended", expected)
        (folder / "01-001-Build-marker.patch").rename(folder / "01-008-Build-marker.patch")
        with self.assertRaises(project.ProjectError):
            self.prepare()
        self.assertEqual((source / "Dockerfile").read_bytes(), expected)

    def test_invalid_patch_names_and_duplicate_indices_keep_prepared_source(self):
        source = self.prepare()
        head = self.git(source, "rev-parse", "HEAD")
        for name in ["Build-unnumbered.patch", "02-000-UI-zero.patch", "0002-old.patch",
                     "02-001-UI-.patch", "02-0002-UI-wide.patch", "01-001-UI-wrong-group.patch",
                     "01-001-Build-duplicate.patch", "00-001-Build-zero.patch"]:
            with self.subTest(name=name):
                invalid = self.root / "patches" / name
                invalid.write_text("")
                try:
                    with self.assertRaises(project.ProjectError):
                        self.prepare()
                    self.assertEqual(self.git(source, "rev-parse", "HEAD"), head)
                finally:
                    invalid.unlink()
        invalid = self.root / "patches/02-001-UI-link.patch"
        invalid.symlink_to(self.root / "patches/01-001-Build-marker.patch")
        with self.assertRaises(project.ProjectError):
            self.prepare()
        invalid.unlink()
        invalid.mkdir()
        with self.assertRaisesRegex(project.ProjectError, "regular files"):
            self.prepare()
        self.assertEqual(self.git(source, "rev-parse", "HEAD"), head)

    def test_capture_first_patch_starts_at_one_without_order_file(self):
        (self.root / "patches/01-001-Build-marker.patch").unlink()
        source = self.prepare()
        target = source / "Dockerfile"
        expected = target.read_text() + "# first captured patch\n"
        target.write_text(expected)
        project.capture(self.root, ["Dockerfile"], "01-001-Build-first.patch")
        self.assertEqual([p.name for p in project.ordered_patches(self.root)], ["01-001-Build-first.patch"])
        self.assertFalse((self.root / "patches/series").exists())
        self.prepare()
        self.assertEqual(target.read_text(), expected)

    def test_capture_inserts_into_earlier_group_without_renumbering_later_groups(self):
        later = self.root / "patches/02-001-UI-marker.patch"
        later.write_text("diff --git a/ui.txt b/ui.txt\nnew file mode 100644\n"
                         "--- /dev/null\n+++ b/ui.txt\n@@ -0,0 +1 @@\n+UI marker\n")
        source = self.prepare()
        target = source / "Dockerfile"
        expected = target.read_text() + "# second build patch\n"
        target.write_text(expected)
        project.capture(self.root, ["Dockerfile"], "01-002-Build-extra.patch")
        self.assertEqual([p.name for p in project.ordered_patches(self.root)],
                         ["01-001-Build-marker.patch", "01-002-Build-extra.patch", later.name])
        self.prepare()
        self.assertEqual(target.read_text(), expected)
        self.assertEqual((source / "ui.txt").read_text(), "UI marker\n")

    def test_capture_rejects_earlier_group_diff_depending_on_later_group(self):
        later = self.root / "patches/02-001-UI-marker.patch"
        later.write_text("diff --git a/Dockerfile b/Dockerfile\n--- a/Dockerfile\n+++ b/Dockerfile\n"
                         "@@ -1,2 +1,2 @@\n FROM scratch\n-# Korean build marker\n+# Later UI marker\n")
        source = self.prepare()
        target = source / "Dockerfile"
        expected = target.read_text() + "# depends on the later UI context\n"
        target.write_text(expected)
        head = self.git(source, "rev-parse", "HEAD")
        with self.assertRaises(project.ProjectError):
            project.capture(self.root, ["Dockerfile"], "01-002-Build-extra.patch")
        self.assertEqual(target.read_text(), expected)
        self.assertEqual(self.git(source, "rev-parse", "HEAD"), head)
        self.assertFalse((self.root / "patches/01-002-Build-extra.patch").exists())
        self.assertEqual(list((self.root / ".cache").glob(".capture-*")), [])

    def test_capture_rejects_unnumbered_out_of_sequence_and_path_names_without_exporting(self):
        source = self.prepare()
        docker = source / "Dockerfile"
        expected = docker.read_text() + "# pending edit\n"
        docker.write_text(expected)
        patches_before = project.ordered_patches(self.root)
        for name in ["Feat-unnumbered.patch", "0002-old.patch", "02-000-UI-zero.patch",
                     "01-001-Build-reused.patch", "03-002-Feat-skipped.patch",
                     "../02-001-UI-escape.patch", "0002-UI-dir/change.patch"]:
            with self.subTest(name=name), self.assertRaises(project.ProjectError):
                project.capture(self.root, ["Dockerfile"], name)
        self.assertEqual(docker.read_text(), expected)
        self.assertEqual(project.ordered_patches(self.root), patches_before)

    def test_capture_rejects_private_downloaded_and_dependency_files(self):
        source = self.prepare()
        paths = [".env", "nested/.env.production", "public/assets/local/model.atlas",
                 "public/fonts/font.woff2", "public/vendor/vendor.js", "node_modules/module/index.js",
                 ".cache/download.bin", "data/assets.json", "data/local-assets.json"]
        for relative in paths:
            with self.subTest(path=relative):
                original = source / relative
                original.parent.mkdir(parents=True, exist_ok=True)
                original.write_text("downloaded or private fixture\n")
                with self.assertRaises(project.ProjectError):
                    project.capture(self.root, [relative], "05-001-Fix-private.patch")
                self.assertFalse((self.root / "patches/05-001-Fix-private.patch").exists())
                self.assertFalse((self.root / "overlays" / relative).exists())

    def test_capture_cannot_overwrite_existing_patch_or_escape_source(self):
        self.prepare()
        existing = (self.root / "patches/01-001-Build-marker.patch").read_bytes()
        with self.assertRaisesRegex(project.ProjectError, "existing patches"):
            project.capture(self.root, ["Dockerfile"], "01-001-Build-marker.patch")
        self.assertEqual((self.root / "patches/01-001-Build-marker.patch").read_bytes(), existing)
        for relative in ("../outside", "/tmp/outside", ".git/config", "tools/../../outside", "tools\\outside"):
            with self.subTest(path=relative), self.assertRaises(project.ProjectError):
                project.capture(self.root, [relative], "05-001-Fix-escape.patch")

    def test_overlay_symlink_is_rejected_without_creating_source(self):
        external = self.base / "private-file"
        external.write_text("private fixture")
        (self.root / "overlays/tools/linked").symlink_to(external)
        with self.assertRaisesRegex(project.ProjectError, "Symlinks"):
            self.prepare()
        self.assertFalse((self.root / project.SOURCE_REL).exists())

    def test_update_patch_failure_keeps_old_pin_and_working_source(self):
        source = self.prepare()
        head = self.git(source, "rev-parse", "HEAD")
        lock = (self.root / "upstream.lock.json").read_bytes()
        project.write_json(self.upstream / "package.json", {"version": "0.2.2"})
        (self.upstream / "Dockerfile").write_text("FROM scratch\n# upstream changed marker\n")
        candidate_commit = self.commit_fixture("Upstream v0.2.2 breaks the Docker patch context")
        with self.assertRaises(project.ProjectError):
            self.update_with_local_release("v0.2.2", candidate_commit)
        self.assertEqual((self.root / "upstream.lock.json").read_bytes(), lock)
        self.assertEqual(self.git(source, "rev-parse", "HEAD"), head)
        self.assertEqual((source / "Dockerfile").read_text(), "FROM scratch\n# Korean build marker\n")

    def test_successful_update_advances_lock_and_source_to_same_release(self):
        self.prepare()
        project.write_json(self.upstream / "package.json", {"version": "0.2.2"})
        document = json.loads((self.upstream / "public/i18n/ko.json").read_text())
        document["new upstream key"] = "새 번역"
        project.write_json(self.upstream / "public/i18n/ko.json", document)
        candidate_commit = self.commit_fixture("Compatible upstream v0.2.2")
        source = self.update_with_local_release("v0.2.2", candidate_commit)
        pin = project.load_pin(self.root)
        self.assertEqual(pin["ref"], "v0.2.2")
        self.assertEqual(pin["commit"], candidate_commit)
        self.assertEqual(pin["release"]["sha256"], "b" * 64)
        self.assertEqual(json.loads((source / "package.json").read_text())["version"], "0.2.2")
        self.assertEqual(json.loads((source / "public/i18n/ko.json").read_text())["new upstream key"], "새 번역")
        self.assertEqual(json.loads((source / ".stronghold-build.json").read_text())["upstream"], pin)
        self.assertEqual(self.git(source, "status", "--porcelain"), "")

    def test_update_rejects_digest_disagreeing_with_published_release(self):
        source = self.prepare()
        head = self.git(source, "rev-parse", "HEAD")
        lock = (self.root / "upstream.lock.json").read_bytes()
        with self.assertRaisesRegex(project.ProjectError, "differs from GitHub"):
            self.update_with_local_release("v0.2.2", self.pin["commit"], supplied_digest="c" * 64)
        self.assertEqual((self.root / "upstream.lock.json").read_bytes(), lock)
        self.assertEqual(self.git(source, "rev-parse", "HEAD"), head)

    def test_update_lock_write_failure_restores_previous_source_and_lock(self):
        source = self.prepare()
        original_head = self.git(source, "rev-parse", "HEAD")
        original_lock = (self.root / "upstream.lock.json").read_bytes()
        original_stamp = (source / ".stronghold-build.json").read_bytes()
        original_package = (source / "package.json").read_bytes()
        project.write_json(self.upstream / "package.json", {"version": "0.2.2"})
        candidate_commit = self.commit_fixture("Compatible upstream v0.2.2")
        actual_write = project.write_json

        def failing_lock(path, data):
            if Path(path) == self.root / "upstream.lock.json":
                raise PermissionError("Simulated lock publication failure")
            return actual_write(path, data)

        with patch.object(project, "write_json", side_effect=failing_lock):
            with self.assertRaises(PermissionError):
                self.update_with_local_release("v0.2.2", candidate_commit)
        self.assertEqual((self.root / "upstream.lock.json").read_bytes(), original_lock)
        self.assertEqual(self.git(source, "rev-parse", "HEAD"), original_head)
        self.assertEqual((source / ".stronghold-build.json").read_bytes(), original_stamp)
        self.assertEqual((source / "package.json").read_bytes(), original_package)
        self.assertEqual(self.git(source, "status", "--porcelain"), "")
        self.assertFalse((self.root / ".build/.previous-source").exists())
        self.assertEqual(list((self.root / ".build").glob(".prepare-*")), [])

    def test_invalid_hash_version_or_release_url_rejects_pin_before_source_access(self):
        mutations = [lambda pin: pin.update(commit="abcd"),
                     lambda pin: pin.update(version="0.2.2"),
                     lambda pin: pin["release"].update(sha256="not-a-sha"),
                     lambda pin: pin["release"].update(url="https://example.com/unpinned.zip"),
                     lambda pin: pin.update(ref="main"),
                     lambda pin: pin.update(repository="file:///tmp/upstream")]
        for index, mutate in enumerate(mutations):
            candidate = copy.deepcopy(self.pin)
            mutate(candidate)
            with self.subTest(case=index), patch.object(project, "ensure_upstream") as upstream:
                with self.assertRaises(project.ProjectError):
                    project.prepare(self.root, pin=candidate)
                upstream.assert_not_called()
        self.assertFalse((self.root / project.SOURCE_REL).exists())


if __name__ == "__main__":
    unittest.main()
