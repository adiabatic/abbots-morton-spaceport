"""Tests for `engine/tools/release_fonts.py`, the upload of the six fonts to a draft GitHub release: each refusal, over a synthetic root and a stand-in for `run_command` that answers the tool's `git` and `gh` calls from state the test sets; the call sequence `--apply` makes and the manifest and notes it uploads; the check of what the draft holds afterward; the currency check of the fonts in site/; the release notes read as the workflow's `awk` reads them; the workflow's `verify` of the fonts it downloads; and the workflow's calls of the tool, from .github/workflows/deploy.yml."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pytest
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen

from engine.pipeline import build_family, fingerprint
from engine.test_build_fonts import _fake_build
from engine.tools import build_fonts, release_fonts
from engine.tools.release_fonts import Completed, FontFile

REPO_ROOT = Path(__file__).resolve().parents[1]
VERSION = "16.001"
TAG = f"release/{VERSION}"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
OTHER_COMMIT = "fedcba9876543210fedcba9876543210fedcba98"
FONTLOG = f"""# Fontlog

## Changelog

### {VERSION}

New letters.

#### Quikscript Senior

- A join.

### 16.000

Older letters.
"""


def _font(path: Path, version: float) -> None:
    """Write a one-glyph font named for its file, carrying `version` as the shipped fonts do: `head.fontRevision` and name ID 5 from the same number."""
    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder([".notdef"])
    builder.setupCharacterMap({})
    builder.setupGlyf({".notdef": TTGlyphPen(None).glyph()})
    builder.setupHorizontalMetrics({".notdef": (500, 0)})
    builder.setupHorizontalHeader()
    builder.setupNameTable({"familyName": path.stem, "styleName": "Regular", "version": f"Version {version}"})
    builder.setupOS2()
    builder.setupPost()
    builder.font["head"].fontRevision = version  # pyright: ignore[reportAttributeAccessIssue]
    builder.save(str(path))


@pytest.fixture
def root(tmp_path, monkeypatch) -> Path:
    """A root that passes every file check: the metadata's version, a FONTLOG section for it, and the six fonts in site/ carrying it, with the currency check passed, since `fonts_staleness` has tests of its own below."""
    root = tmp_path / "root"
    (root / "glyph_data").mkdir(parents=True)
    (root / "glyph_data" / "metadata.yaml").write_text(
        f"metadata:\n  font_name: Test\n  version: {VERSION}\n  units_per_em: 550\n"
    )
    (root / "FONTLOG.md").write_text(FONTLOG)
    (root / "site").mkdir()
    for name in release_fonts.release_font_names():
        _font(root / "site" / name, float(VERSION))
    monkeypatch.setattr(release_fonts, "fonts_staleness", lambda at: None)
    return root


class FakeGitHub:
    """Answers each `git` and `gh` call the tool makes from the state a test sets, and records the calls. `gh release create` reads the files it is given, as gh uploads them, and adds a draft holding them, less any name in `drop`."""

    def __init__(
        self,
        releases: Sequence[dict] = (),
        dirty: str = "",
        branches: str = "origin/HEAD\norigin/master\n",
        remote_tag: str = "",
        local_tag: str = "",
        failing: Sequence[str] = (),
        drop: Sequence[str] = (),
    ):
        self.releases = [dict(entry) for entry in releases]
        self.dirty, self.branches, self.remote_tag, self.local_tag = dirty, branches, remote_tag, local_tag
        self.failing, self.drop = tuple(failing), tuple(drop)
        self.calls: list[list[str]] = []
        self.uploaded: dict[str, bytes] = {}
        self.notes = ""
        self.assets: dict[int, list[dict]] = {}

    def __call__(self, argv: Sequence[str]) -> Completed:
        argv = list(argv)
        self.calls.append(argv)
        if any(" ".join(argv).startswith(prefix) for prefix in self.failing):
            return Completed(1, "", "HTTP 401: Bad credentials")
        match argv:
            case ["git", "status", *_]:
                return Completed(0, self.dirty, "")
            case ["git", "rev-parse", "--verify", "HEAD^{commit}"]:
                return Completed(0, f"{COMMIT}\n", "")
            case ["git", "branch", "--remotes", *_]:
                return Completed(0, self.branches, "")
            case ["git", "ls-remote", *_]:
                return Completed(0, f"{self.remote_tag}\trefs/tags/{TAG}\n" if self.remote_tag else "", "")
            case ["git", "rev-parse", "-q", "--verify", _]:
                return Completed(
                    0 if self.local_tag else 1, f"{self.local_tag}\n" if self.local_tag else "", ""
                )
            case ["gh", "api", "--paginate", endpoint, "--jq", _] if "/assets" in endpoint:
                release_id = int(endpoint.split("/releases/")[1].split("/")[0])
                lines = [json.dumps(asset) for asset in self.assets.get(release_id, [])]
                return Completed(0, "".join(f"{line}\n" for line in lines), "")
            case ["gh", "api", "--paginate", *_]:
                return Completed(0, "".join(f"{json.dumps(entry)}\n" for entry in self.releases), "")
            case ["gh", "api", "--method", "DELETE", endpoint]:
                gone = int(endpoint.rsplit("/", 1)[1])
                self.releases = [entry for entry in self.releases if entry["id"] != gone]
                return Completed(0, "", "")
            case ["gh", "release", "create", tag, *rest]:
                files = [Path(arg) for arg in rest[rest.index("--notes-file") + 2 :]]
                self.notes = Path(rest[rest.index("--notes-file") + 1]).read_text()
                self.uploaded = {path.name: path.read_bytes() for path in files}
                new_id = 1 + max((entry["id"] for entry in self.releases), default=100)
                self.releases.append(
                    {"id": new_id, "tag_name": tag, "draft": True, "html_url": f"draft/{new_id}"}
                )
                self.assets[new_id] = [
                    {"name": name, "size": len(data), "digest": f"sha256:{_sha256(data)}"}
                    for name, data in self.uploaded.items()
                    if name not in self.drop
                ]
                return Completed(0, f"https://github.com/o/r/releases/tag/untagged-{new_id}\n", "")
        raise AssertionError(f"unexpected call {argv}")

    def writes(self) -> list[list[str]]:
        return [call for call in self.calls if call[:3] == ["gh", "release", "create"] or "--method" in call]


def _sha256(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def _kind(call: list[str]) -> str:
    if call[:2] == ["gh", "api"]:
        if "--method" in call:
            return f"gh api {call[call.index('--method') + 1]}"
        return "gh api assets" if "/assets" in call[3] else "gh api releases"
    return " ".join(call[:3] if call[:2] == ["gh", "release"] else call[:2])


def _site_fonts(root: Path) -> list[FontFile]:
    return [
        FontFile(name, (root / "site" / name).stat().st_size, fingerprint.file_sha256(root / "site" / name))
        for name in release_fonts.release_font_names()
    ]


class TestTheUpload:
    def test_a_dry_run_prints_the_checks_the_plan_and_the_two_commands_and_writes_nothing(self, root, capsys):
        github = FakeGitHub(
            releases=[{"id": 5, "tag_name": "release/16.000", "draft": False, "html_url": "u"}]
        )
        assert release_fonts.main([], root=root, runner=github) == 0
        out = capsys.readouterr().out
        assert "REFUSE" not in out
        assert "--apply would:" in out
        assert f"create the draft release {TAG}, targeting {COMMIT}" in out
        for name in release_fonts.release_font_names():
            assert f"site/{name}" in out
        assert f'"commit": "{COMMIT}"' in out and f'"version": "{VERSION}"' in out
        assert out.rstrip().endswith(f"  git tag {TAG} {COMMIT}\n  git push origin {TAG}")
        assert github.writes() == []

    def test_apply_replaces_the_draft_uploads_the_fonts_and_manifest_and_checks_the_draft(self, root, capsys):
        """`--apply` makes its checks' read-only calls, deletes the draft already made for the tag, creates the new draft targeting HEAD with the six fonts from site/ and fonts.json, and reads the draft back; it tags and pushes nothing, and prints the two commands that do."""
        github = FakeGitHub(
            releases=[
                {"id": 5, "tag_name": "release/16.000", "draft": False, "html_url": "u5"},
                {"id": 7, "tag_name": TAG, "draft": True, "html_url": "u7"},
            ]
        )
        assert release_fonts.main(["--apply"], root=root, runner=github) == 0
        assert [_kind(call) for call in github.calls] == [
            "git status",
            "git rev-parse",
            "git branch",
            "gh api releases",
            "git ls-remote",
            "git rev-parse",
            "gh api DELETE",
            "gh release create",
            "gh api releases",
            "gh api assets",
        ]
        delete, create = github.writes()
        assert delete == ["gh", "api", "--method", "DELETE", "repos/{owner}/{repo}/releases/7"]
        assert create[:10] == [
            "gh",
            "release",
            "create",
            TAG,
            "--draft",
            "--target",
            COMMIT,
            "--title",
            TAG,
            "--notes-file",
        ]
        assert create[11:-1] == [str(root / "site" / name) for name in release_fonts.release_font_names()]
        assert Path(create[-1]).name == release_fonts.MANIFEST_NAME
        assert json.loads(github.uploaded[release_fonts.MANIFEST_NAME]) == {
            "format": release_fonts.MANIFEST_FORMAT,
            "commit": COMMIT,
            "version": VERSION,
            "fonts": [{"file": f.name, "size": f.size, "sha256": f.sha256} for f in _site_fonts(root)],
        }
        assert github.notes == release_fonts.changelog_section(FONTLOG, VERSION)
        drafts = [entry["id"] for entry in github.releases if entry["tag_name"] == TAG]
        assert len(drafts) == 1 and drafts != [7]
        out = capsys.readouterr().out
        assert "deleting the draft release 7 (u7)" in out
        assert out.rstrip().endswith(f"  git tag {TAG} {COMMIT}\n  git push origin {TAG}")
        assert not any(call[:2] in (["git", "tag"], ["git", "push"]) for call in github.calls)

    def test_apply_fails_when_the_draft_does_not_hold_what_it_uploaded(self, root, capsys):
        github = FakeGitHub(drop=["AbbotsMortonSpaceportMono-Bold.otf"])
        assert release_fonts.main(["--apply"], root=root, runner=github) == 1
        captured = capsys.readouterr()
        assert "the draft holds no AbbotsMortonSpaceportMono-Bold.otf" in captured.err
        assert release_fonts.APPLY_COMMAND in captured.err
        assert f"  git push origin {TAG}" not in captured.out.splitlines()

    def test_a_local_tag_at_head_leaves_only_the_push(self, root, capsys):
        assert release_fonts.main([], root=root, runner=FakeGitHub(local_tag=COMMIT)) == 0
        out = capsys.readouterr().out
        assert "git tag " not in out and out.rstrip().endswith(f"  git push origin {TAG}")


def _set_version(root: Path, version: str | None) -> None:
    line = f"  version: {version}\n" if version is not None else ""
    (root / "glyph_data" / "metadata.yaml").write_text(f"metadata:\n  font_name: Test\n{line}")


REFUSALS = {
    "no version": (lambda root, mp: _set_version(root, None), {}, "version is None, not of the form X.YYY"),
    "a version not of the form X.YYY": (
        lambda root, mp: _set_version(root, "16.0"),
        {},
        "version is '16.0', not of the form X.YYY",
    ),
    "a dirty tree": (
        None,
        {"dirty": " M glyph_data/runes/qsPea.yaml\n?? new.yaml\n"},
        "the working tree is not clean (glyph_data/runes/qsPea.yaml, new.yaml)",
    ),
    "an unpushed HEAD": (None, {"branches": "origin/HEAD\n"}, "is on no branch of origin"),
    "stale fonts": (
        lambda root, mp: mp.setattr(
            release_fonts, "fonts_staleness", lambda at: "the build's Senior is not current: x"
        ),
        {},
        "the build's Senior is not current: x; run `make all`",
    ),
    "a font of another version": (
        lambda root, mp: _font(root / "site" / "AbbotsMortonSpaceportSansJunior-Bold.otf", 16.0),
        {},
        "AbbotsMortonSpaceportSansJunior-Bold.otf carries 16.000",
    ),
    "no FONTLOG heading": (
        lambda root, mp: (root / "FONTLOG.md").write_text(FONTLOG.replace(f"### {VERSION}", "### 16.002")),
        {},
        f"FONTLOG.md has no `### {VERSION}` heading",
    ),
    "an empty FONTLOG section": (
        lambda root, mp: (root / "FONTLOG.md").write_text(
            f"## Changelog\n\n### {VERSION}\n\n### 16.000\n\nOld.\n"
        ),
        {},
        f"FONTLOG.md's `### {VERSION}` section is empty",
    ),
    "a published release": (
        None,
        {"releases": [{"id": 9, "tag_name": TAG, "draft": False, "html_url": "u9"}]},
        f"{TAG} is already a published release (u9)",
    ),
    "a pushed tag": (
        None,
        {"remote_tag": OTHER_COMMIT},
        f"the tag {TAG} is already on origin and its release is not published",
    ),
    "a local tag at another commit": (
        None,
        {"local_tag": OTHER_COMMIT},
        f"the local tag {TAG} names {OTHER_COMMIT[:12]}, not HEAD",
    ),
    "gh unable to list the releases": (
        None,
        {"failing": ["gh api --paginate"]},
        "`gh api` could not list the releases (HTTP 401: Bad credentials)",
    ),
}


@pytest.mark.parametrize("case", sorted(REFUSALS))
def test_each_refusal_stops_apply_before_any_write_and_fails_the_dry_run(case, root, monkeypatch, capsys):
    """Every refusal is printed beside the checks that passed, fails the dry run, and leaves `--apply` with no call that writes."""
    edit, state, refusal = REFUSALS[case]
    if edit is not None:
        edit(root, monkeypatch)
    for argv in ([], ["--apply"]):
        github = FakeGitHub(**state)
        assert release_fonts.main(argv, root=root, runner=github) == 1
        out = capsys.readouterr().out
        assert any(line.startswith("  REFUSE  ") and refusal in line for line in out.splitlines()), out
        assert "nothing was written" in out
        assert github.writes() == []
        assert f"  git push origin {TAG}" not in out.splitlines()


class TestTheFontsCurrency:
    @pytest.fixture
    def build_root(self, tmp_path, monkeypatch) -> Path:
        """A root with a passing build, site/ holding its Senior copies, and Mono and Junior standing in as bytes named for each file, which the stand-in build writes too."""
        root = tmp_path / "build-root"
        _fake_build(root)
        site = root / "site"
        site.mkdir()
        build_fonts.build(site, ("senior",), root)

        def fake_build(out_dir, fonts, at):
            names = [
                build_family.font_file_name(font, style) for font in fonts for style, _ in build_family.STYLES
            ]
            for name in names:
                (Path(out_dir) / name).write_bytes(name.encode())
            return [Path(out_dir) / name for name in names]

        monkeypatch.setattr(build_family, "build_mono_and_junior", fake_build)
        for name in release_fonts.release_font_names()[:4]:
            (site / name).write_bytes(name.encode())
        return root

    def test_site_fonts_that_make_all_would_publish_are_current(self, build_root):
        assert release_fonts.fonts_staleness(build_root) is None

    def test_a_junior_that_differs_from_its_build_is_named(self, build_root):
        (build_root / "site" / "AbbotsMortonSpaceportSansJunior-Regular.otf").write_bytes(b"older")
        assert release_fonts.fonts_staleness(build_root) == (
            "site/AbbotsMortonSpaceportSansJunior-Regular.otf is not what `make all` publishes for this tree"
        )

    def test_a_senior_that_is_not_the_builds_copy_is_named(self, build_root):
        (build_root / "site" / "AbbotsMortonSpaceportSansSenior-Bold.otf").unlink()
        assert release_fonts.fonts_staleness(build_root) == (
            "site/AbbotsMortonSpaceportSansSenior-Bold.otf is not what `make all` publishes for this tree"
        )

    def test_a_stale_senior_build_is_the_reason(self, build_root):
        (build_root / build_fonts.BUILD_OUT / fingerprint.STAGE_A_FILENAME).unlink()
        assert (release_fonts.fonts_staleness(build_root) or "").startswith(
            "the build's Senior is not current: engine/out/build holds no Stage A record"
        )


class TestTheReleaseNotes:
    def test_the_section_runs_from_its_heading_to_the_next_level_3_heading(self):
        section = release_fonts.changelog_section(FONTLOG, VERSION)
        assert section == "\nNew letters.\n\n#### Quikscript Senior\n\n- A join.\n"

    def test_a_version_without_a_heading_has_no_section(self):
        assert release_fonts.changelog_section(FONTLOG, "16.002") is None

    def test_the_metadata_version_is_read_as_written(self, tmp_path):
        (tmp_path / "glyph_data").mkdir()
        (tmp_path / "glyph_data" / "metadata.yaml").write_text("metadata:\n  version: 16.000\n")
        assert release_fonts.metadata_version(tmp_path) == "16.000"

    def test_a_fonts_version_is_read_to_three_decimals(self, tmp_path):
        _font(tmp_path / "a.otf", 16.0)
        _font(tmp_path / "b.otf", 16.001)
        assert [release_fonts.font_version(tmp_path / name) for name in ("a.otf", "b.otf")] == [
            "16.000",
            "16.001",
        ]


class TestTheWorkflowsCheck:
    @pytest.fixture
    def downloaded(self, root) -> Path:
        """The six fonts and the manifest `--apply` uploads, as the workflow downloads them."""
        directory = root / "site"
        text = release_fonts.manifest_text(COMMIT, VERSION, _site_fonts(root))
        (directory / release_fonts.MANIFEST_NAME).write_text(text)
        return directory

    def test_the_uploaded_fonts_check_out(self, downloaded):
        assert release_fonts.verify_fonts(downloaded, VERSION, COMMIT) == []

    def test_another_commit_or_version_fails(self, downloaded):
        assert release_fonts.verify_fonts(downloaded, "16.002", OTHER_COMMIT) == [
            f"fonts.json is for version '{VERSION}', not '16.002'",
            f"fonts.json is for the commit '{COMMIT}', not '{OTHER_COMMIT}'",
        ]

    def test_a_changed_or_missing_or_extra_font_fails(self, downloaded):
        _font(downloaded / "AbbotsMortonSpaceportMono-Regular.otf", 16.002)
        (downloaded / "AbbotsMortonSpaceportSansSenior-Bold.otf").unlink()
        (downloaded / "Extra.otf").write_bytes(b"extra")
        problems = release_fonts.verify_fonts(downloaded, VERSION, COMMIT)
        assert problems[:2] == [
            "AbbotsMortonSpaceportSansSenior-Bold.otf is missing",
            "Extra.otf is not one of the six fonts",
        ]
        assert any(problem.startswith("AbbotsMortonSpaceportMono-Regular.otf") for problem in problems[2:])

    def test_a_manifest_that_lists_other_fonts_fails(self, downloaded):
        manifest = json.loads((downloaded / release_fonts.MANIFEST_NAME).read_text())
        manifest["fonts"] = manifest["fonts"][:5]
        (downloaded / release_fonts.MANIFEST_NAME).write_text(json.dumps(manifest))
        assert release_fonts.verify_fonts(downloaded, VERSION, COMMIT)[0].endswith("not the six fonts")

    def test_a_tag_push_needs_the_manifest_and_a_redeploy_from_an_older_release_does_not(
        self, downloaded, capsys
    ):
        (downloaded / release_fonts.MANIFEST_NAME).unlink()
        assert release_fonts.verify_fonts(downloaded, VERSION, COMMIT) == ["fonts.json is missing"]
        assert release_fonts.verify_fonts(downloaded, VERSION) == []
        assert release_fonts.main(["verify", str(downloaded), "--version", VERSION, "--commit", COMMIT]) == 1
        assert release_fonts.APPLY_COMMAND in capsys.readouterr().err

    def test_the_workflow_runs_this_check_and_names_the_upload_target(self):
        """.github/workflows/deploy.yml cannot run here, so this holds the names it shares with the tool: the tag pattern, the manifest's name, the `verify` call against the tagged commit, and the make target its errors name."""
        workflow = (REPO_ROOT / ".github" / "workflows" / "deploy.yml").read_text()
        assert f"- '{release_fonts.TAG_PREFIX}*'" in workflow
        assert (
            'uv run python -m engine.tools.release_fonts verify fonts --version "$VERSION" --commit "$GITHUB_SHA"'
            in workflow
        )
        assert release_fonts.MANIFEST_NAME in workflow
        assert release_fonts.APPLY_COMMAND in workflow
