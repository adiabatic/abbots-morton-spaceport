"""Tests for the snapshot of the accepted build (`rebuild/review/snapshot.py`): what `take` writes from a run_m1 build directory, its certificates' texts against the witness stage's spelling of the same token streams, the guard certificates, the digest and the directory it names, the `current` file and how many snapshots are kept, and what `load` refuses.

The build directory is a real table build of the pipeline's mini fixture spec (`run_m1.build_tables`, which writes the tables and each configuration's windows head), with the frozen mini bundle's font and a small kern table copied beside it: a snapshot copies the font and the kern table and reads neither.
"""

import json
import shutil
from pathlib import Path

import pytest

from rebuild.pipeline import fixtures, run_m1, witness
from rebuild.pipeline import table as table_module
from rebuild.review import snapshot, tablediff

REPO_ROOT = Path(__file__).resolve().parent.parent
MINI_FONT = REPO_ROOT / "rebuild" / "review" / "fixtures" / "mini" / "M1.otf"
SPEC = fixtures.mini_spec()


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("snapshot-build")
    run_m1.build_tables(SPEC, out, inputs="fp-snapshot")
    shutil.copyfile(MINI_FONT, out / "M1.otf")
    (out / "kern.tsv").write_text("# kern table\nleading\tleading_side\n", encoding="utf-8")
    return out


@pytest.fixture
def taken(built, tmp_path):
    return snapshot.take(built, tmp_path / "home", spec=SPEC)


def test_take_writes_every_configurations_tables_and_certificates_and_the_record_checks_them(taken, built):
    record = taken.record
    assert record["format"] == snapshot.SNAPSHOT_FORMAT
    assert taken.configs == tablediff.SETTLED_CONFIGS
    assert taken.directory.name == taken.digest[: snapshot.DIGEST_NAME_LENGTH]
    assert taken.tables_stamp == "fp-snapshot"
    assert record["source"] == str(built)
    expected = {snapshot.FONT_NAME, snapshot.KERN_NAME} | {
        name(config)
        for config in tablediff.SETTLED_CONFIGS
        for name in (snapshot.settlement_name, snapshot.joins_name, snapshot.certificates_name)
    }
    assert set(record["files"]) == expected
    assert sorted(path.name for path in taken.directory.iterdir()) == sorted(
        expected | {snapshot.RECORD_NAME}
    )
    assert snapshot.current(taken.directory.parent) == taken.directory
    assert snapshot.load(taken.directory).record == record


def test_the_snapshot_tables_read_as_the_builds(taken, built):
    for config in tablediff.SETTLED_CONFIGS:
        assert taken.settlement(config) == tablediff.load_settlement(built / f"settlement-{config}.tsv")
        assert taken.joins(config) == tablediff.load_joins(built / f"joins-{config}.tsv")
        assert tablediff.same_table(taken.settlement_path(config), built / f"settlement-{config}.tsv")
    assert taken.settlement("ss10") == {} and taken.certificates("ss10") == []


def test_the_certificates_spell_what_the_witness_stage_settles(taken, built):
    """Every certificate's text is `witness._token_text` of the token stream its windows head holds for the rule, and the snapshot holds one certificate per rule in rule order."""
    for config in tablediff.SETTLED_CONFIGS:
        _stamp, decision = table_module.read_windows_head(built, config)
        certificates = taken.certificates(config)
        assert [certificate.rule for certificate in certificates] == list(range(len(decision.rules)))
        assert len(certificates) == len(tablediff.load_settlement(built / f"settlement-{config}.tsv"))
        for tokens, certificate in zip(decision.certificates, certificates):
            stream = tokens[2:] if tokens and tokens[0] == witness.GUARD_MARKER else tokens
            spelled = witness._token_text(SPEC, stream)
            assert certificate.text == (tuple(map(ord, spelled)) if spelled else None)


def test_a_certificate_spells_boundaries_and_ligatures_as_the_witness_stage_does():
    tokens = ("uni200C", "qsTea_qsOy", "space", "qsDay_qsUtter", "periodcentered", "qsMay")
    assert snapshot.certificate_text(SPEC, tokens) == tuple(map(ord, witness._token_text(SPEC, tokens)))
    assert snapshot.GUARD_MARKER == witness.GUARD_MARKER


def test_a_guard_certificate_names_the_configuration_it_settles_under():
    decision = table_module.DecisionTable(
        config="default", certificates=((snapshot.GUARD_MARKER, "ss03", "qsTea", "qsMay"), ("qsPea",), ())
    )
    assert snapshot.certificates_of(SPEC, decision) == [
        snapshot.Certificate(0, (0xE652, 0xE665), "ss03"),
        snapshot.Certificate(1, (0xE650,), None),
        snapshot.Certificate(2, None, None),
    ]
    with pytest.raises(snapshot.SnapshotError, match="does not spell out"):
        snapshot.certificates_of(
            SPEC, table_module.DecisionTable(config="default", certificates=(("qsNowhere",),))
        )


def test_a_build_taken_again_names_the_same_directory_and_a_new_one_keeps_the_previous(built, tmp_path):
    home = tmp_path / "home"
    first = snapshot.take(built, home, spec=SPEC)
    again = snapshot.take(built, home, spec=SPEC)
    assert again.directory == first.directory and again.digest == first.digest
    names = []
    for number in range(3):
        moved = tmp_path / f"build-{number}"
        shutil.copytree(built, moved)
        (moved / "kern.tsv").write_text(f"# kern table {number}\n", encoding="utf-8")
        names.append(snapshot.take(moved, home, spec=SPEC).directory.name)
    assert len(set(names) | {first.directory.name}) == 4
    kept = sorted(path.name for path in home.iterdir() if path.is_dir())
    assert kept == sorted(names[1:])
    assert snapshot.current(home) == home / names[2]
    assert not [path for path in home.iterdir() if path.name.startswith(".")]


def test_take_refuses_a_build_missing_a_head_or_holding_two_builds_heads(built, tmp_path):
    home = tmp_path / "home"
    missing = tmp_path / "missing"
    shutil.copytree(built, missing)
    table_module.windows_head_file(missing, "ss05").unlink()
    with pytest.raises(snapshot.SnapshotError, match="ss05 windows head does not read"):
        snapshot.take(missing, home, spec=SPEC)
    assert sorted(home.iterdir()) == []
    mixed = tmp_path / "mixed"
    shutil.copytree(built, mixed)
    run_m1.build_tables(SPEC, mixed, inputs="fp-other", configs=["ss04"])
    with pytest.raises(snapshot.SnapshotError, match="2 tables' stamps"):
        snapshot.take(mixed, home, spec=SPEC)
    no_font = tmp_path / "no-font"
    shutil.copytree(built, no_font)
    (no_font / "M1.otf").unlink()
    with pytest.raises(snapshot.SnapshotError, match="has no M1.otf"):
        snapshot.take(no_font, home, spec=SPEC)
    assert sorted(home.iterdir()) == []


def test_load_refuses_another_format_a_changed_file_and_a_digest_that_is_not_its_own(taken, tmp_path):
    def broken(edit) -> Path:
        copy = tmp_path / f"copy-{len(list(tmp_path.iterdir()))}"
        shutil.copytree(taken.directory, copy)
        edit(copy)
        return copy

    def record_edit(change):
        def edit(directory: Path) -> None:
            path = directory / snapshot.RECORD_NAME
            record = json.loads(path.read_text(encoding="utf-8"))
            change(record)
            path.write_text(json.dumps(record), encoding="utf-8")

        return edit

    with pytest.raises(snapshot.SnapshotError, match="reads only ams-review-snapshot/2"):
        snapshot.load(broken(record_edit(lambda record: record.update(format="ams-review-snapshot/1"))))
    with pytest.raises(snapshot.SnapshotError, match="is not the file"):
        snapshot.load(broken(lambda directory: (directory / snapshot.KERN_NAME).write_bytes(b"other")))
    with pytest.raises(snapshot.SnapshotError, match="digest is not the digest"):
        snapshot.load(broken(record_edit(lambda record: record.update(commit="0" * 40))))
    with pytest.raises(snapshot.SnapshotError, match="names none of"):
        snapshot.load(broken(record_edit(lambda record: record["files"].pop(snapshot.FONT_NAME))))


def test_current_names_nothing_for_a_missing_or_stray_pointer(tmp_path):
    assert snapshot.current(tmp_path) is None
    (tmp_path / snapshot.CURRENT_NAME).write_text("../elsewhere\n", encoding="utf-8")
    assert snapshot.current(tmp_path) is None
    (tmp_path / snapshot.CURRENT_NAME).write_text("0123456789ab\n", encoding="utf-8")
    assert snapshot.current(tmp_path) is None


def test_show_describes_the_current_snapshot(taken, capsys):
    snapshot.main(["show", "--home", str(taken.directory.parent)])
    out = capsys.readouterr().out
    assert taken.digest in out and "default, ss03" in out
    with pytest.raises(SystemExit, match="has no current snapshot"):
        snapshot.main(["show", "--home", str(taken.directory)])
