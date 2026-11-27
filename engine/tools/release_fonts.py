"""Upload the six fonts in site/ to a draft GitHub release, which a push of the release tag publishes (`uv run python -m engine.tools.release_fonts [--rehearse] [--delete] [--apply]`, `make draft-release`).

The fonts are built on the author's machine, because the Senior's table build needs more memory than a GitHub-hosted runner has (`make job-costs` reports what it holds), and CI publishes them. This command uploads them with a manifest to a draft release for the tag `release/<version>`; pushing that tag runs .github/workflows/deploy.yml, which finds the draft, checks the fonts it downloads (`verify`, below), makes the WOFF2s, deploys the site, attaches the release zip and publishes the draft. HACKING.md § Cutting a release gives the order of the whole release.

The version is the `version` scalar of glyph_data/metadata.yaml as written (`16.000`, the X.YYY form the bump skills keep), and it names the tag and the FONTLOG.md heading. Without `--apply` the command is a dry run: it prints every check, every refusal `--apply` would make and what `--apply` would do, the manifest included, writes nothing, and exits 1 when `--apply` would refuse. Its refusals, each with nothing written:

- the version is missing or not of the form X.YYY;
- the working tree is not clean (an untracked file counts, since the build reads the tree), or HEAD is on no branch of origin as last fetched, so the tag would name a commit the remote does not have;
- the six fonts in site/ are not what `make all` publishes for the tree (`fonts_staleness`): the build's Senior is not current (`build_fonts.senior_staleness`, the check `make all` builds on), site/'s Senior is not its copy, or site/'s Mono and Junior are not the bytes a build of them into a scratch directory writes. The command never builds; `make all` does;
- a font's version (`head.fontRevision` and name ID 5, read to three decimals) is not the metadata's;
- FONTLOG.md has no `### <version>` section with text in it, read as the workflow's `awk` reads it for the release notes (`changelog_section`);
- a published release for the tag exists, the tag is on origin, or a local tag of that name names another commit.

A draft release for the tag may exist, and `--apply` deletes it. `--apply` then creates the draft release for the tag, targeting HEAD's commit, titled with the tag and with the FONTLOG section as its notes, uploads the six OTFs from site/ and `fonts.json` (`MANIFEST_FORMAT`: the commit, the version, and each font's file name, size and sha256), and checks the draft's assets against what it uploaded by name, size and, where GitHub reports it, digest. It never tags or pushes: it prints the two commands that finish the release, `git tag release/<version> <commit>` and `git push origin release/<version>`. Every `git` and `gh` call goes through `run_command`, which the tests replace.

`--rehearse` is the rehearsal form, which exercises the workflow's GitHub steps with no public effect. It makes the same checks and the same calls, and uploads the same six fonts and manifest, to a draft release for the tag `rehearsal/<version>-<commit>` (`REHEARSAL_PREFIX` and the first `SHORT_COMMIT` hex digits of HEAD), titled and noted as a rehearsal that is safe to delete. The workflow's tag push takes only `release/*` tags, and the draft's tag is never created, so no push can publish it. It relaxes the refusals that exist only to protect a release, printing each one it relaxes as `relaxed`: a published release for the version, the FONTLOG.md section, and the version's tag on origin or here; it replaces only a draft for its own tag. It keeps every other refusal and adds one: HEAD must be the tip of a branch of origin as last fetched, because the rehearsal run checks out that branch's tip and checks fonts.json's commit against it. In place of the two finishing commands it prints the `gh workflow run` command that starts the workflow's rehearsal on that branch (`rehearsal_run_command`), and it never starts it. `--rehearse --delete` lists the rehearsal drafts, and with `--apply` deletes each one after reading it back by id as a draft whose tag starts with `REHEARSAL_PREFIX` (`rehearsal_problem`), so it never deletes a published release or a release whose tag starts with `release/`.

`uv run python -m engine.tools.release_fonts verify DIR --version V [--commit SHA]` is the workflow's check of the fonts it downloaded into DIR: the six OTFs under their shipped names and no other OTF, and, when DIR holds `fonts.json`, the manifest's format, version and commit (when `--commit` is given) and each font's size and sha256. With `--commit`, as on a tag push, a missing manifest is an error; without it, as on a redeploy from a release that predates the manifest, a DIR without `fonts.json` is checked by its file names alone.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from engine.pipeline import build_family, fingerprint, spec_load
from engine.pipeline.compile_font import FontSourceError
from engine.tools import build_fonts

REPO_ROOT = Path(__file__).resolve().parents[2]
METADATA = Path("glyph_data") / "metadata.yaml"
FONTLOG = Path("FONTLOG.md")
SITE = Path("site")
REMOTE = "origin"
TAG_PREFIX = "release/"
REHEARSAL_PREFIX = "rehearsal/"
SHORT_COMMIT = 12
MANIFEST_NAME = "fonts.json"
MANIFEST_FORMAT = "ams-release-fonts/1"
MAKE_TARGET = "make draft-release"
APPLY_COMMAND = f"{MAKE_TARGET} ARGS=--apply"
REHEARSE_APPLY_COMMAND = f"{MAKE_TARGET} ARGS='--rehearse --apply'"
REHEARSE_DELETE_COMMAND = f"{MAKE_TARGET} ARGS='--rehearse --delete --apply'"
WORKFLOW_FILE = "deploy.yml"
VERSION_FORM = re.compile(r"\d+\.\d{3}")
NAME_ID_5_FORM = re.compile(r"Version (\d+(?:\.\d+)?)")
RELEASES_ENDPOINT = "repos/{owner}/{repo}/releases"
RELEASE_FIELDS = ".[] | {id, tag_name, draft, html_url} | @json"
ONE_RELEASE_FIELDS = "{id, tag_name, draft, html_url} | @json"
ASSET_FIELDS = ".[] | {name, size, digest} | @json"
OK, REFUSE, RELAXED = "ok", "REFUSE", "relaxed"


@dataclass(frozen=True)
class Completed:
    returncode: int
    stdout: str
    stderr: str


Runner = Callable[[Sequence[str]], Completed]


def run_command(argv: Sequence[str]) -> Completed:
    """Run one `git` or `gh` command in the checkout and return its status and output. A command that cannot start returns 127 with the error as its stderr."""
    try:
        done = subprocess.run(list(argv), cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    except OSError as error:
        return Completed(127, "", str(error))
    return Completed(done.returncode, done.stdout, done.stderr)


class ReleaseError(Exception):
    pass


@dataclass(frozen=True)
class FontFile:
    name: str
    size: int
    sha256: str


@dataclass(frozen=True)
class Draft:
    id: int
    url: str
    tag: str


@dataclass
class Release:
    """What `--apply` would upload and replace, and the checks that decide whether it may. With `rehearsal` set, the upload goes to the rehearsal tag and the release-only refusals are relaxed."""

    version: str | None = None
    commit: str | None = None
    notes: str | None = None
    rehearsal: bool = False
    branch: str | None = None
    fonts: list[FontFile] = field(default_factory=list)
    drafts: list[Draft] = field(default_factory=list)
    tagged_locally: bool = False
    checks: list[tuple[str, str]] = field(default_factory=list)

    @property
    def release_tag(self) -> str:
        return f"{TAG_PREFIX}{self.version}"

    @property
    def tag(self) -> str:
        """The tag of the draft `--apply` makes: the release tag, or in a rehearsal `rehearsal/<version>-<commit>`."""
        if not self.rehearsal:
            return self.release_tag
        return f"{REHEARSAL_PREFIX}{self.version}-{(self.commit or '')[:SHORT_COMMIT]}"

    @property
    def title(self) -> str:
        if not self.rehearsal:
            return self.tag
        return f"Rehearsal of {self.release_tag} at {(self.commit or '')[:SHORT_COMMIT]} (safe to delete)"

    @property
    def apply_command(self) -> str:
        return REHEARSE_APPLY_COMMAND if self.rehearsal else APPLY_COMMAND

    @property
    def refusals(self) -> list[str]:
        return [text for status, text in self.checks if status == REFUSE]

    def ok(self, text: str) -> None:
        self.checks.append((OK, text))

    def refuse(self, text: str) -> None:
        self.checks.append((REFUSE, text))

    def refuse_release(self, text: str, relaxed: str) -> None:
        """A refusal that exists only to protect a release: a rehearsal records `relaxed` in its place, which says why it goes on."""
        if self.rehearsal:
            self.checks.append((RELAXED, relaxed))
        else:
            self.refuse(text)


def release_font_names() -> list[str]:
    """The six shipped file names, Mono, Junior and Senior in Regular and Bold, as `make all` publishes them into site/."""
    return [
        build_family.font_file_name(font, style)
        for font in build_fonts.FONTS
        for style, _bold in build_family.STYLES
    ]


def metadata_version(root: Path) -> str | None:
    """The `metadata.version` scalar of glyph_data/metadata.yaml as written, or None when the file or the key is missing. YAML reads `16.000` as the float 16.0, so the node's text is read instead of its value."""
    try:
        node: yaml.Node | None = yaml.compose((root / METADATA).read_text(encoding="utf-8"))
    except OSError, yaml.YAMLError:
        return None
    for key in ("metadata", "version"):
        if not isinstance(node, yaml.MappingNode):
            return None
        node = next(
            (value for name, value in node.value if isinstance(name, yaml.ScalarNode) and name.value == key),
            None,
        )
    return node.value if isinstance(node, yaml.ScalarNode) else None


def changelog_section(text: str, version: str) -> str | None:
    """The release notes the workflow extracts for `version`, or None when FONTLOG.md has no heading for it. The workflow's `awk` takes every line after a line that begins `### <version>` up to the next line that begins `### ` (a `####` heading is not one), and this reads the text the same way."""
    heading = f"### {version}"
    found = seen = False
    lines: list[str] = []
    for line in text.splitlines():
        if line.startswith(heading):
            found = seen = True
        elif line.startswith("### "):
            found = False
        elif found:
            lines.append(line)
    return "\n".join(lines) if seen else None


def font_version(path: Path) -> str:
    """A font's version to three decimals, from `head.fontRevision` and name ID 5, or both readings when they disagree. The build writes the metadata's version as a number, so name ID 5 reads `Version 16.0` for 16.000."""
    from fontTools.ttLib import TTFont

    # The shipped fonts carry fixed timestamps, which fontTools reports on every read.
    logging.getLogger("fontTools.ttLib.tables._h_e_a_d").setLevel(logging.ERROR)
    with TTFont(path, lazy=True) as font:
        revision = f"{font['head'].fontRevision:.3f}"  # pyright: ignore[reportAttributeAccessIssue]
        name = font["name"].getDebugName(5) or ""
    match = NAME_ID_5_FORM.fullmatch(name)
    named = f"{float(match.group(1)):.3f}" if match else repr(name)
    return revision if named == revision else f"{revision} in head and {named} in name ID 5"


def fonts_staleness(root: Path = REPO_ROOT) -> str | None:
    """Why the six fonts in `root`'s site/ are not what `make all` publishes for the tree on disk, or None when they are: the build's Senior is current and site/'s Senior is its copy, and site/'s Mono and Junior hold the bytes a build of them writes now, which builds into a scratch directory so that a dry run writes nothing."""
    reason = build_fonts.senior_staleness(root)
    if reason is not None:
        return f"the build's Senior is not current: {reason}"
    expected = {
        build_family.font_file_name("senior", style): fingerprint.file_sha256(
            root / build_fonts.BUILD_OUT / source
        )
        for style, source in build_fonts.SENIOR_SOURCES.items()
    }
    try:
        with tempfile.TemporaryDirectory(prefix="release-fonts-") as scratch:
            for path in build_family.build_mono_and_junior(Path(scratch), ["mono", "junior"], root):
                expected[path.name] = fingerprint.file_sha256(path)
    except (build_family.FamilyBuildError, spec_load.SpecError, FontSourceError) as error:
        return f"Mono and Junior do not build: {error}"
    site = root / SITE
    differ = [
        f"site/{name}"
        for name in release_font_names()
        if not (site / name).is_file() or fingerprint.file_sha256(site / name) != expected.get(name)
    ]
    if differ:
        return f"{', '.join(differ)} {'is' if len(differ) == 1 else 'are'} not what `make all` publishes for this tree"
    return None


def manifest_document(commit: str, version: str, fonts: Sequence[FontFile]) -> dict:
    return {
        "format": MANIFEST_FORMAT,
        "commit": commit,
        "version": version,
        "fonts": [{"file": font.name, "size": font.size, "sha256": font.sha256} for font in fonts],
    }


def manifest_text(commit: str, version: str, fonts: Sequence[FontFile]) -> str:
    return json.dumps(manifest_document(commit, version, fonts), indent=2) + "\n"


def _first_line(completed: Completed) -> str:
    text = (completed.stderr or completed.stdout).strip()
    return text.splitlines()[0] if text else f"exit {completed.returncode}"


def list_releases(runner: Runner) -> list[dict] | str:
    """Every release of the repository, drafts among them, which the API lists only to a token with push access, or the reason the listing failed."""
    listing = runner(["gh", "api", "--paginate", f"{RELEASES_ENDPOINT}?per_page=100", "--jq", RELEASE_FIELDS])
    if listing.returncode != 0:
        return f"`gh api` could not list the releases ({_first_line(listing)}); is `gh` signed in?"
    try:
        return [json.loads(line) for line in listing.stdout.splitlines() if line.strip()]
    except ValueError:
        return "`gh api` listed the releases in a form this command does not read"


def _check_tree(release: Release, runner: Runner) -> None:
    status = runner(["git", "status", "--porcelain", "--untracked-files=normal"])
    changed = [line[3:] for line in status.stdout.splitlines() if line.strip()]
    if status.returncode != 0:
        release.refuse(f"`git status` failed ({_first_line(status)})")
    elif changed:
        shown = ", ".join(changed[:5]) + (f" and {len(changed) - 5} more" if len(changed) > 5 else "")
        release.refuse(
            f"the working tree is not clean ({shown}); commit or stash it, so that the fonts are the build of the commit the tag names"
        )
    else:
        release.ok("the working tree is clean")
    head = runner(["git", "rev-parse", "--verify", "HEAD^{commit}"])
    if head.returncode != 0 or not head.stdout.strip():
        release.refuse("HEAD names no commit")
        return
    release.commit = head.stdout.strip()
    branches = runner(
        ["git", "branch", "--remotes", "--contains", release.commit, "--format=%(refname:short)"]
    )
    on_remote = [
        line for line in branches.stdout.split() if line.startswith(f"{REMOTE}/") and line != f"{REMOTE}/HEAD"
    ]
    if branches.returncode != 0 or not on_remote:
        release.refuse(
            f"HEAD ({release.commit[:12]}) is on no branch of {REMOTE} as last fetched; push it first, so that the tag names a commit {REMOTE} has"
        )
    else:
        release.ok(f"HEAD ({release.commit[:12]}) is on {', '.join(on_remote)}")
    if release.rehearsal:
        _check_branch_tip(release, runner)


def _check_branch_tip(release: Release, runner: Runner) -> None:
    """A rehearsal run is a manual run on a branch, which checks out the branch's tip and checks fonts.json's commit against it, so HEAD must be that tip."""
    assert release.commit is not None
    tips = runner(["git", "branch", "--remotes", "--points-at", release.commit, "--format=%(refname:short)"])
    at_tip = [
        line for line in tips.stdout.split() if line.startswith(f"{REMOTE}/") and line != f"{REMOTE}/HEAD"
    ]
    if tips.returncode != 0 or not at_tip:
        release.refuse(
            f"HEAD ({release.commit[:12]}) is the tip of no branch of {REMOTE} as last fetched, and the rehearsal run checks fonts.json's commit against the tip of the branch it runs on; push HEAD, or rehearse from a branch tip"
        )
        return
    release.branch = at_tip[0].removeprefix(f"{REMOTE}/")
    release.ok(f"HEAD is the tip of {at_tip[0]}, the branch the rehearsal run checks out")


def _check_fonts(release: Release, root: Path) -> None:
    reason = fonts_staleness(root)
    if reason is not None:
        release.refuse(
            f"{reason}; run `make all`, which builds the Senior when it is stale and publishes the six fonts into site/"
        )
    else:
        release.ok("site/ holds the six fonts `make all` publishes for this tree")
    site = root / SITE
    present = [site / name for name in release_font_names() if (site / name).is_file()]
    release.fonts = [
        FontFile(path.name, path.stat().st_size, fingerprint.file_sha256(path)) for path in present
    ]
    if release.version is None or not present:
        return
    versions = {path.name: font_version(path) for path in present}
    wrong = [f"{name} carries {found}" for name, found in versions.items() if found != release.version]
    if wrong:
        release.refuse(
            f"the fonts' version is not the metadata's {release.version}: {'; '.join(wrong)}; run `make all`"
        )
    else:
        release.ok(f"the fonts in site/ carry version {release.version}")


def _check_fontlog(release: Release, root: Path) -> None:
    assert release.version is not None
    relaxed = "; a rehearsal's draft carries a rehearsal note instead, but the run's changelog step reads the section and fails without it"
    try:
        text = (root / FONTLOG).read_text(encoding="utf-8")
    except OSError:
        release.refuse_release(
            "FONTLOG.md is missing, and the workflow takes the release notes from it",
            f"FONTLOG.md is missing{relaxed}",
        )
        return
    section = changelog_section(text, release.version)
    heading = f"`### {release.version}`"
    if section is None:
        release.refuse_release(
            f"FONTLOG.md has no {heading} heading, which the workflow takes the release notes from; the bump-minor and bump-major skills open one",
            f"FONTLOG.md has no {heading} heading{relaxed}",
        )
    elif not section.strip():
        release.refuse_release(
            f"FONTLOG.md's {heading} section is empty, and it becomes the release notes",
            f"FONTLOG.md's {heading} section is empty{relaxed}",
        )
    else:
        release.notes = section
        lines = len([line for line in section.splitlines() if line.strip()])
        release.ok(f"FONTLOG.md's {heading} section holds {lines} lines of release notes")


def _drafts_for(releases: list[dict], tag: str) -> list[Draft]:
    return [
        Draft(int(entry["id"]), str(entry.get("html_url", "")), tag)
        for entry in releases
        if entry.get("tag_name") == tag and entry.get("draft")
    ]


def _replaced(release: Release) -> str:
    if not release.drafts:
        return ""
    return f"; --apply replaces the draft {', '.join(str(draft.id) for draft in release.drafts)}"


def _check_release_and_tag(release: Release, runner: Runner) -> None:
    """The release tag's checks, which a rehearsal relaxes, and in a rehearsal its own tag's: a rehearsal replaces only the drafts for its own tag."""
    tag = release.release_tag
    releases = list_releases(runner)
    published: list[dict] = []
    if isinstance(releases, str):
        release.refuse(releases)
    else:
        published = [entry for entry in releases if entry.get("tag_name") == tag and not entry.get("draft")]
        release.drafts = _drafts_for(releases, release.tag)
        if published:
            url = published[0].get("html_url", "")
            release.refuse_release(
                f"{tag} is already a published release ({url}); bump the version with the bump-minor or bump-major skill",
                f"{tag} is already a published release ({url}); a rehearsal uploads to {release.tag} and leaves it alone",
            )
        else:
            release.ok(f"no published release for {tag}{'' if release.rehearsal else _replaced(release)}")
        if release.rehearsal:
            rehearsed = [
                entry for entry in releases if entry.get("tag_name") == release.tag and not entry.get("draft")
            ]
            if rehearsed:
                release.refuse(
                    f"{release.tag} is a published release ({rehearsed[0].get('html_url', '')}), and a rehearsal's release is a draft; delete it on GitHub"
                )
            else:
                release.ok(f"no published release for {release.tag}{_replaced(release)}")
    never = "a rehearsal never tags or pushes"
    remote = runner(["git", "ls-remote", "--tags", REMOTE, f"refs/tags/{tag}"])
    if remote.returncode != 0:
        release.refuse(f"`git ls-remote {REMOTE}` failed ({_first_line(remote)})")
    elif remote.stdout.strip() and published:
        release.refuse_release(
            f"the tag {tag} is already on {REMOTE}", f"the tag {tag} is on {REMOTE}; {never}"
        )
    elif remote.stdout.strip():
        release.refuse_release(
            f"the tag {tag} is already on {REMOTE} and its release is not published; re-run its failed workflow from the Actions page, or delete the tag there (`git push {REMOTE} :refs/tags/{tag}`), upload again and push it again",
            f"the tag {tag} is on {REMOTE} and its release is not published; {never}",
        )
    else:
        release.ok(f"{REMOTE} has no tag {tag}")
    local = runner(["git", "rev-parse", "-q", "--verify", f"refs/tags/{tag}^{{commit}}"])
    named = local.stdout.strip() if local.returncode == 0 else ""
    if named and named != release.commit:
        remedy = "" if published else f"; delete it with `git tag -d {tag}`"
        release.refuse_release(
            f"the local tag {tag} names {named[:12]}, not HEAD{remedy}",
            f"the local tag {tag} names {named[:12]}, not HEAD; {never}",
        )
    elif named:
        release.tagged_locally = True
        release.ok(f"the local tag {tag} names HEAD")


def prepare(root: Path = REPO_ROOT, runner: Runner = run_command, rehearsal: bool = False) -> Release:
    """Run every check `--apply` makes, and gather what it would upload and replace; with `rehearsal`, for the rehearsal draft."""
    root = Path(root)
    release = Release(version=metadata_version(root), rehearsal=rehearsal)
    if release.version is None or not VERSION_FORM.fullmatch(release.version):
        release.refuse(
            f"glyph_data/metadata.yaml's version is {release.version!r}, not of the form X.YYY that names the tag and the FONTLOG.md heading"
        )
        release.version = None
    _check_tree(release, runner)
    _check_fonts(release, root)
    if release.version is not None:
        _check_fontlog(release, root)
        _check_release_and_tag(release, runner)
    if rehearsal:
        release.notes = rehearsal_notes(release)
    return release


def rehearsal_notes(release: Release) -> str:
    """The rehearsal draft's body, in place of the FONTLOG section a release carries."""
    return (
        f"A rehearsal of the release path for {release.release_tag}, uploaded by `{REHEARSE_APPLY_COMMAND}` from commit {release.commit}. It is safe to delete, and `{REHEARSE_DELETE_COMMAND}` deletes it.\n\n"
        f"It stays a draft: its tag is not a release tag, so no push runs the release on it, and the workflow's rehearsal attaches the zip to it without publishing it.\n"
    )


def rehearsal_run_command(release: Release) -> str:
    """The command that starts the workflow's rehearsal on the rehearsal draft, on the branch whose tip is HEAD."""
    return f"gh workflow run {WORKFLOW_FILE} --ref {release.branch} -f rehearse=true -f tag={release.tag}"


def create_argv(release: Release, root: Path, manifest: Path, notes: Path) -> list[str]:
    assert release.commit is not None
    return [
        "gh",
        "release",
        "create",
        release.tag,
        "--draft",
        "--target",
        release.commit,
        "--title",
        release.title,
        "--notes-file",
        str(notes),
        *(str(root / SITE / font.name) for font in release.fonts),
        str(manifest),
    ]


def finishing_commands(release: Release) -> list[str]:
    assert release.commit is not None
    if release.rehearsal:
        return [rehearsal_run_command(release)]
    tag = [] if release.tagged_locally else [f"git tag {release.tag} {release.commit}"]
    return [*tag, f"git push {REMOTE} {release.tag}"]


def uploaded_problems(release: Release, manifest: FontFile, runner: Runner) -> list[str]:
    """What the draft release GitHub now holds for the tag lacks or adds against the six fonts and the manifest the upload sent."""
    releases = list_releases(runner)
    if isinstance(releases, str):
        return [releases]
    drafts = [entry for entry in releases if entry.get("tag_name") == release.tag and entry.get("draft")]
    if len(drafts) != 1:
        return [f"GitHub lists {len(drafts)} draft releases for {release.tag} after the upload, not one"]
    listing = runner(
        [
            "gh",
            "api",
            "--paginate",
            f"{RELEASES_ENDPOINT}/{drafts[0]['id']}/assets?per_page=100",
            "--jq",
            ASSET_FIELDS,
        ]
    )
    if listing.returncode != 0:
        return [f"`gh api` could not list the draft's assets ({_first_line(listing)})"]
    try:
        entries = [json.loads(line) for line in listing.stdout.splitlines() if line.strip()]
    except ValueError:
        return ["`gh api` listed the draft's assets in a form this command does not read"]
    assets = {entry["name"]: entry for entry in entries}
    problems: list[str] = []
    for sent in (*release.fonts, manifest):
        asset = assets.get(sent.name)
        if asset is None:
            problems.append(f"the draft holds no {sent.name}")
        elif asset.get("size") != sent.size:
            problems.append(f"the draft's {sent.name} is {asset.get('size')} bytes, not {sent.size}")
        elif asset.get("digest") and asset["digest"] != f"sha256:{sent.sha256}":
            problems.append(
                f"the draft's {sent.name} has the digest {asset['digest']}, not sha256:{sent.sha256}"
            )
    extra = sorted(set(assets) - {sent.name for sent in (*release.fonts, manifest)})
    if extra:
        problems.append(f"the draft also holds {', '.join(extra)}")
    return problems


def apply(release: Release, root: Path = REPO_ROOT, runner: Runner = run_command) -> None:
    """Replace any draft release for the tag with a new one holding the six fonts and the manifest, and check what GitHub holds. Raises `ReleaseError` at the first failed call, leaving whatever it had made."""
    assert release.commit is not None and release.version is not None and release.notes is not None
    assert all(draft.tag == release.tag for draft in release.drafts)
    assert not release.rehearsal or release.tag.startswith(REHEARSAL_PREFIX)
    with tempfile.TemporaryDirectory(prefix="release-fonts-") as scratch:
        manifest_path = Path(scratch) / MANIFEST_NAME
        manifest_path.write_text(
            manifest_text(release.commit, release.version, release.fonts), encoding="utf-8"
        )
        manifest = FontFile(
            MANIFEST_NAME, manifest_path.stat().st_size, fingerprint.file_sha256(manifest_path)
        )
        notes_path = Path(scratch) / "notes.md"
        notes_path.write_text(release.notes, encoding="utf-8")
        for draft in release.drafts:
            print(f"deleting the draft release {draft.id} ({draft.url})")
            deleted = runner(["gh", "api", "--method", "DELETE", f"{RELEASES_ENDPOINT}/{draft.id}"])
            if deleted.returncode != 0:
                raise ReleaseError(f"could not delete the draft release {draft.id} ({_first_line(deleted)})")
        print(f"creating the draft release {release.tag} and uploading the six fonts and {MANIFEST_NAME}")
        created = runner(create_argv(release, root, manifest_path, notes_path))
        if created.returncode != 0:
            raise ReleaseError(f"`gh release create` failed ({_first_line(created)})")
        if created.stdout.strip():
            print(created.stdout.strip())
    problems = uploaded_problems(release, manifest, runner)
    if problems:
        raise ReleaseError(
            f"the draft release does not hold what was uploaded: {'; '.join(problems)}. Run `{release.apply_command}` again, which replaces the draft"
        )
    print(f"the draft release holds the six fonts and {MANIFEST_NAME}, by name, size and digest")


def plan_lines(release: Release) -> list[str]:
    """What `--apply` does, in order, for the dry run to print."""
    assert release.commit is not None and release.version is not None
    lines = [f"  delete the draft release {draft.id} ({draft.url})" for draft in release.drafts]
    notes = (
        "a note that says it is a rehearsal and safe to delete"
        if release.rehearsal
        else f"FONTLOG.md's `### {release.version}` section"
    )
    lines.append(
        f"  create the draft release {release.tag}, targeting {release.commit}, titled {release.title}, with {notes} as its notes"
    )
    lines.append("  upload to it:")
    lines += [f"    {SITE / font.name}" for font in release.fonts]
    lines.append(f"    {MANIFEST_NAME}:")
    lines += [
        f"      {line}" for line in manifest_text(release.commit, release.version, release.fonts).splitlines()
    ]
    lines.append("  check the draft's assets against what it uploaded, by name, size and digest")
    return lines


def rehearsal_problem(entry: dict) -> str | None:
    """Why a release, as the API lists it, is not a rehearsal draft that `--rehearse --delete` may delete, or None when it is one."""
    tag = str(entry.get("tag_name", ""))
    if tag.startswith(TAG_PREFIX):
        return f"its tag {tag} is a release tag"
    if not tag.startswith(REHEARSAL_PREFIX):
        return f"its tag {tag} does not start with {REHEARSAL_PREFIX}"
    if entry.get("draft") is not True:
        return f"{tag} is a published release"
    return None


def _read_release(release_id: int, runner: Runner) -> dict | str:
    reading = runner(["gh", "api", f"{RELEASES_ENDPOINT}/{release_id}", "--jq", ONE_RELEASE_FIELDS])
    if reading.returncode != 0:
        return f"`gh api` could not read the release {release_id} ({_first_line(reading)})"
    try:
        entry = json.loads(reading.stdout)
    except ValueError:
        return f"`gh api` read the release {release_id} in a form this command does not read"
    if not isinstance(entry, dict) or entry.get("id") != release_id:
        return f"`gh api` read another release in place of {release_id}"
    return entry


def delete_rehearsals(apply: bool, runner: Runner = run_command) -> int:
    """List the rehearsal drafts, and with `apply` delete each one after reading it back by id as a rehearsal draft (`rehearsal_problem`). A release under the rehearsal prefix that is published is kept and fails the run."""
    mode = "deleting" if apply else f"a dry run, which deletes nothing; {REHEARSE_DELETE_COMMAND} deletes"
    print(f"release_fonts: {mode} the draft releases whose tag starts with {REHEARSAL_PREFIX}")
    releases = list_releases(runner)
    if isinstance(releases, str):
        print(f"release_fonts: {releases}", file=sys.stderr)
        return 1
    found = [entry for entry in releases if str(entry.get("tag_name", "")).startswith(REHEARSAL_PREFIX)]
    if not found:
        print(f"  there is no release whose tag starts with {REHEARSAL_PREFIX}")
    status = 0
    for entry in found:
        release_id, named = int(entry["id"]), f"{entry.get('tag_name')} ({entry.get('html_url', '')})"
        problem = rehearsal_problem(entry)
        if problem is None and apply:
            current = _read_release(release_id, runner)
            problem = current if isinstance(current, str) else rehearsal_problem(current)
        if problem is not None:
            print(f"  keep    {named}: {problem}; only a release read back as a rehearsal draft is deleted")
            status = 1
            continue
        if not apply:
            print(f"  delete  {named}")
            continue
        deleted = runner(["gh", "api", "--method", "DELETE", f"{RELEASES_ENDPOINT}/{release_id}"])
        if deleted.returncode != 0:
            print(f"  FAILED  {named}: could not delete it ({_first_line(deleted)})")
            status = 1
        else:
            print(f"  deleted {named}")
    return status


def verify_fonts(directory: Path, version: str, commit: str | None = None) -> list[str]:
    """What is wrong with the fonts the workflow downloaded into `directory` (the module docstring gives the checks), or an empty list."""
    directory = Path(directory)
    expected = release_font_names()
    present = sorted(path.name for path in directory.glob("*.otf"))
    problems = [f"{name} is missing" for name in expected if name not in present]
    problems += [f"{name} is not one of the six fonts" for name in present if name not in expected]
    manifest_path = directory / MANIFEST_NAME
    if not manifest_path.is_file():
        if commit is not None:
            problems.append(f"{MANIFEST_NAME} is missing")
        return problems
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return [*problems, f"{MANIFEST_NAME} does not read as JSON"]
    if not isinstance(manifest, dict) or manifest.get("format") != MANIFEST_FORMAT:
        format_found = manifest.get("format") if isinstance(manifest, dict) else None
        return [*problems, f"{MANIFEST_NAME}'s format is {format_found!r}, not {MANIFEST_FORMAT!r}"]
    if manifest.get("version") != version:
        problems.append(f"{MANIFEST_NAME} is for version {manifest.get('version')!r}, not {version!r}")
    if commit is not None and manifest.get("commit") != commit:
        problems.append(f"{MANIFEST_NAME} is for the commit {manifest.get('commit')!r}, not {commit!r}")
    listed = {entry.get("file"): entry for entry in manifest.get("fonts", []) if isinstance(entry, dict)}
    if sorted(listed, key=str) != sorted(expected):
        problems.append(
            f"{MANIFEST_NAME} lists {', '.join(sorted(map(str, listed))) or 'no fonts'}, not the six fonts"
        )
    for name in expected:
        entry, path = listed.get(name), directory / name
        if entry is None or not path.is_file():
            continue
        if path.stat().st_size != entry.get("size"):
            problems.append(
                f"{name} is {path.stat().st_size} bytes, and {MANIFEST_NAME} says {entry.get('size')}"
            )
        elif fingerprint.file_sha256(path) != entry.get("sha256"):
            problems.append(f"{name}'s sha256 is not the one {MANIFEST_NAME} records")
    return problems


def _verify_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m engine.tools.release_fonts verify",
        description="Check the fonts a release workflow downloaded against the six shipped names and fonts.json.",
    )
    parser.add_argument(
        "directory", type=Path, help="the directory the fonts and fonts.json were downloaded into"
    )
    parser.add_argument("--version", required=True, help="the version the release tag names")
    parser.add_argument(
        "--commit", help="the commit the release tag names, which fonts.json must record; requires fonts.json"
    )
    args = parser.parse_args(argv)
    problems = verify_fonts(args.directory, args.version, args.commit)
    if problems:
        for problem in problems:
            print(f"release_fonts verify: {problem}", file=sys.stderr)
        remedy = (
            f"the release does not hold the fonts `{APPLY_COMMAND}` uploads for this commit; run it on the tagged commit, which replaces the draft release (in a rehearsal, run `{REHEARSE_APPLY_COMMAND}` on the commit the run checks out)"
            if args.commit is not None
            else f"a redeploy takes its fonts from a release `{MAKE_TARGET}` made, or from an older one whose zip holds the six fonts under these names (release/12.000 and later)"
        )
        print(f"release_fonts verify: {remedy}", file=sys.stderr)
        return 1
    checked = "and fonts.json" if (args.directory / MANIFEST_NAME).is_file() else "by name (no fonts.json)"
    print(f"release_fonts verify: the six fonts in {args.directory} check out {checked}")
    return 0


def main(argv: Sequence[str] | None = None, root: Path = REPO_ROOT, runner: Runner = run_command) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "verify":
        return _verify_main(argv[1:])
    parser = argparse.ArgumentParser(
        prog="python -m engine.tools.release_fonts",
        description="Upload the six fonts in site/ and fonts.json to a draft GitHub release for the version in glyph_data/metadata.yaml; without --apply, a dry run.",
    )
    parser.add_argument("--apply", action="store_true", help="replace the draft release and upload the fonts")
    parser.add_argument(
        "--rehearse",
        action="store_true",
        help=f"upload to a rehearsal draft ({REHEARSAL_PREFIX}<version>-<commit>), which no push publishes, relaxing the refusals that only protect a release",
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="with --rehearse, delete the rehearsal drafts instead (a dry run without --apply)",
    )
    args = parser.parse_args(argv)
    if args.delete and not args.rehearse:
        parser.error("--delete deletes only rehearsal drafts, so it needs --rehearse")
    if args.delete:
        return delete_rehearsals(args.apply, runner)
    release = prepare(root, runner, rehearsal=args.rehearse)
    if args.rehearse:
        mode = (
            "a rehearsal; "
            if args.apply
            else f"a rehearsal's dry run, which writes nothing; {REHEARSE_APPLY_COMMAND} makes the rehearsal draft. "
        )
    else:
        mode = "" if args.apply else "a dry run, which writes nothing; ARGS=--apply makes the draft release. "
    named = f"version {release.version}, tag {release.tag}" if release.version else "no version"
    print(f"release_fonts: {mode}{named}, commit {release.commit or 'none'}")
    for status, line in release.checks:
        print(f"  {status:<8}{line}")
    if release.refusals:
        print(f"release_fonts: {'refused' if args.apply else '--apply would refuse'}; nothing was written")
        return 1
    if not args.apply:
        print("--apply would:")
        for line in plan_lines(release):
            print(line)
    else:
        try:
            apply(release, root, runner)
        except ReleaseError as error:
            print(f"release_fonts: {error}", file=sys.stderr)
            return 1
    if args.rehearse:
        start = "Then start" if not args.apply else "Start"
        print(
            f"{start} the rehearsal run with the command below, which this command never runs; the run deploys nothing and leaves the draft a draft, and `{REHEARSE_DELETE_COMMAND}` deletes the rehearsal drafts afterward:"
        )
    else:
        then = "Then finish" if not args.apply else "Finish"
        print(
            f"{then} the release with these; the tag's push runs .github/workflows/deploy.yml, which publishes it:"
        )
    for command in finishing_commands(release):
        print(f"  {command}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
