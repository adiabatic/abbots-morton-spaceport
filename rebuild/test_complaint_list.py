"""Tests for the complaint list (`rebuild/tools/complaint_list.py`): grouping reject and neither verdicts by the rune records that decided them (a reject groups by its draft target when it has one and otherwise by its exact provenance tuple, and a neither joins the reject group whose pointers overlap its own most), the new and older split at the manifest stamp, the lookup from a group's pointers to the blank units it can defer and the approved units a fix would likely change, and the defer file (skip verdicts stamped with the manifest's `generated_at`, one per defer candidate)."""

import json
import weakref

import pytest

from rebuild.review import accepted, audit
from rebuild.test_duplicate_verdicts import verdicts_layouts
from rebuild.tools import complaint_list as cl
from rebuild.tools.review_queue import QUEUE_SECTION, load_human_units

STAMP = "2026-07-10T00:00:00Z"
NEW = "2026-07-10T12:00:00Z"
OLD = "2026-07-01T00:00:00Z"

P_EXTEND_1 = "glyph_data/runes/qsDay.yaml:policy.extend[1]"
P_EXTEND_2 = "glyph_data/runes/qsDay.yaml:policy.extend[2]"
P_PREFER_0 = "glyph_data/runes/qsNo.yaml:policy.prefer[0]"


def policy_draft(keypath="policy.contract[+]", when="{left: {family: [qsTea]}}", codepoints="E652:E653"):
    return {
        "file": "glyph_data/runes/qsDay.yaml",
        "keypath": keypath,
        "suggested_record": f"{{entry: baseline, by: 1, when: {when}, why: 'Reviewer rejected the M1 outcome for {codepoints}'}}",
        "names_provenance": [],
        "decided_stage": "join-count",
        "schema_valid": True,
        "why_stub": "stub",
    }


def unit(uid, provenance=(), policy=None, batch: int | None = 1, no_verdict=False, cls="live-class"):
    number = uid.split("-")[1]
    return {
        "id": uid,
        "order": None if no_verdict or batch is None else int(number),
        "batch": None if no_verdict else batch,
        "no_verdict": no_verdict,
        "duplicate_group": f"e-{number}",
        "cluster": f"c-{number}",
        "class": cls,
        "group": "qsPea:qsTea",
        "codepoints": "E650:E652",
        "notation": "·Pea·Tea",
        "configs": ["default"],
        "provenance": list(provenance),
        "drafts": {"pin": None, "policy": policy, "any_of": None},
    }


def v(unit_id, verdict, note="", at=OLD):
    return {"unit": unit_id, "verdict": verdict, "note": note, "at": at}


@pytest.fixture
def repo(tmp_path):
    corpus = tmp_path / "corpus"
    (corpus / "units").mkdir(parents=True)
    (corpus / "manifest.json").write_text(
        json.dumps(
            {
                "generated_at": STAMP,
                "classes": [
                    {"id": "live-class", "status": "per-unit", "section": "open-questions", "shards": []},
                    {"id": "ruled-class", "status": "approved", "section": "spot-check", "shards": []},
                ],
            }
        )
    )
    return {
        "root": tmp_path,
        "corpus": corpus,
        "verdicts": tmp_path / "verdicts-autosave.json",
        "data_out": tmp_path / "complaints-data.json",
    }


def write_corpus(repo, units):
    """Write one shard per manifest class, as a real corpus does, so the tools find the units through the manifest."""
    corpus = repo["corpus"]
    manifest = json.loads((corpus / "manifest.json").read_text())
    by_class = {}
    for record in units:
        by_class.setdefault(record["class"], []).append(record)
    for entry in manifest["classes"]:
        shard = f"units/{entry['id']}.json"
        (corpus / shard).write_text(json.dumps(by_class.get(entry["id"], [])))
        entry["shards"] = [shard]
    (corpus / "manifest.json").write_text(json.dumps(manifest))


def write_verdicts(repo, verdicts, stamp=STAMP):
    repo["verdicts"].write_text(
        json.dumps(
            {
                "format": "ams-review-verdicts/1",
                "manifest_generated_at": stamp,
                "exported_at": stamp,
                "verdicts": list(verdicts),
            }
        )
    )


def run(repo, *args, **held):
    return cl.main(
        [
            str(repo["verdicts"]),
            "--corpus",
            str(repo["corpus"]),
            "--data-out",
            str(repo["data_out"]),
            "--defer-dir",
            str(repo["root"]),
            *args,
        ],
        **held,
    )


def data(repo):
    return json.loads(repo["data_out"].read_text())


def test_rejects_sharing_a_draft_target_form_one_group_with_the_union_of_their_pointers(repo):
    write_corpus(
        repo,
        [
            unit("u-0001", [P_EXTEND_1], policy=policy_draft()),
            unit("u-0002", [P_EXTEND_2], policy=policy_draft(codepoints="E653:E654")),
        ],
    )
    write_verdicts(repo, [v("u-0001", "reject", at=NEW), v("u-0002", "reject", at=NEW)])
    assert run(repo) == 0
    payload = data(repo)
    assert payload["totals"]["groups"] == 1
    group = payload["groups"][0]
    assert group["kind"] == "policy"
    assert group["target"] == {"file": "glyph_data/runes/qsDay.yaml", "keypath": "policy.contract[+]"}
    assert group["pointers"] == [P_EXTEND_1, P_EXTEND_2]
    assert {entry["unit"] for entry in group["rejects"]["new"]} == {"u-0001", "u-0002"}
    assert len(group["suggested_records"]) == 1
    assert group["draft_conflicts"] is False


def test_draftless_rejects_group_by_their_exact_provenance_tuple(repo):
    write_corpus(
        repo,
        [
            unit("u-0001", [P_EXTEND_1, P_EXTEND_2]),
            unit("u-0002", [P_EXTEND_2, P_EXTEND_1]),
            unit("u-0003", [P_PREFER_0]),
        ],
    )
    write_verdicts(repo, [v(uid, "reject") for uid in ("u-0001", "u-0002", "u-0003")])
    assert run(repo) == 0
    payload = data(repo)
    assert payload["totals"]["groups"] == 2
    pair = next(group for group in payload["groups"] if len(group["rejects"]["older"]) == 2)
    assert pair["kind"] == "provenance"
    assert pair["target"] == {"pointers": [P_EXTEND_1, P_EXTEND_2]}
    assert pair["suggested_records"] == []


def test_a_neither_group_attaches_to_the_pointer_sharing_reject_group(repo):
    write_corpus(
        repo,
        [
            unit("u-0001", [P_EXTEND_1], policy=policy_draft()),
            unit("u-0002", [P_EXTEND_1, P_EXTEND_2]),
            unit("u-0003", [P_PREFER_0]),
        ],
    )
    write_verdicts(
        repo,
        [v("u-0001", "reject"), v("u-0002", "neither"), v("u-0003", "neither")],
    )
    assert run(repo) == 0
    payload = data(repo)
    assert payload["totals"]["groups"] == 2
    attached = next(group for group in payload["groups"] if group["kind"] == "policy")
    assert [entry["unit"] for entry in attached["neithers"]["older"]] == ["u-0002"]
    assert set(attached["pointers"]) == {P_EXTEND_1, P_EXTEND_2}
    standalone = next(group for group in payload["groups"] if group["kind"] == "provenance")
    assert [entry["unit"] for entry in standalone["neithers"]["older"]] == ["u-0003"]
    assert standalone["rejects"] == {"new": [], "older": []}


def test_new_and_older_split_on_the_manifest_stamp_and_since_overrides(repo):
    write_corpus(repo, [unit("u-0001", [P_EXTEND_1]), unit("u-0002", [P_EXTEND_1])])
    write_verdicts(repo, [v("u-0001", "reject", at=NEW), v("u-0002", "reject", at=OLD)])
    assert run(repo) == 0
    totals = data(repo)["totals"]
    assert (totals["new"], totals["older"]) == (1, 1)
    assert run(repo, "--since", "2026-06-01T00:00:00Z") == 0
    totals = data(repo)["totals"]
    assert (totals["new"], totals["older"]) == (2, 0)
    assert data(repo)["since"] == "2026-06-01T00:00:00Z"


def test_defer_candidates_are_the_blank_sharers_and_judged_sharers_are_at_risk(repo):
    write_corpus(
        repo,
        [
            unit("u-0001", [P_EXTEND_1], policy=policy_draft()),
            unit("u-0002", [P_EXTEND_1]),
            unit("u-0003", [P_EXTEND_1]),
            unit("u-0004", [P_EXTEND_1]),
            unit("u-0005", [P_EXTEND_1]),
            unit("u-0006", [P_PREFER_0]),
        ],
    )
    write_verdicts(
        repo,
        [
            v("u-0001", "reject", at=NEW),
            v("u-0003", "skip"),
            v("u-0004", "approve"),
            v("u-0005", "either"),
        ],
    )
    assert run(repo) == 0
    group = data(repo)["groups"][0]
    assert group["defer_candidates"]["unit_ids"] == ["u-0002", "u-0003"]
    assert group["defer_candidates"]["count"] == 2
    assert group["approved_units_at_risk"] == {"approve": 1, "either": 1, "identical": 0}
    assert data(repo)["totals"]["defer_candidates"] == 2
    assert data(repo)["totals"]["approved_units_at_risk"] == 1


def test_ruled_class_blanks_are_counted_but_not_deferred(repo):
    write_corpus(
        repo,
        [
            unit("u-0001", [P_EXTEND_1]),
            unit("u-0002", [P_EXTEND_1], cls="ruled-class"),
            unit("u-0003", [P_EXTEND_1]),
        ],
    )
    write_verdicts(repo, [v("u-0001", "reject")])
    assert run(repo) == 0
    group = data(repo)["groups"][0]
    assert group["defer_candidates"]["unit_ids"] == ["u-0003"]
    assert group["ruled_class_blanks"] == {"count": 1, "by_class": {"ruled-class": 1}}


def test_a_manifest_without_sections_is_refused(repo):
    """The section decides which blanks are ruled, so a corpus built before the manifest carried it is refused rather than read as one where every class is open."""
    write_corpus(repo, [unit("u-0001", [P_EXTEND_1]), unit("u-0002", [P_EXTEND_1], cls="ruled-class")])
    manifest = json.loads((repo["corpus"] / "manifest.json").read_text())
    for entry in manifest["classes"]:
        del entry["section"]
    (repo["corpus"] / "manifest.json").write_text(json.dumps(manifest))
    write_verdicts(repo, [v("u-0001", "reject")])
    with pytest.raises(SystemExit, match="predates the review sections"):
        run(repo)
    assert not repo["data_out"].exists()


def test_the_queue_section_is_the_audits_open_questions():
    """The review-queue tools spell the Queue's section out instead of importing the audit, so this pins the copy to `audit.class_section`'s value."""
    assert QUEUE_SECTION == audit.OPEN_QUESTIONS


def test_defer_emits_skip_verdicts_at_the_manifest_stamp_covering_exact_blanks(repo):
    write_corpus(
        repo,
        [
            unit("u-0001", [P_EXTEND_1], policy=policy_draft()),
            unit("u-0002", [P_EXTEND_1]),
            unit("u-0003", [P_EXTEND_1]),
        ],
    )
    write_verdicts(repo, [v("u-0001", "reject", at=NEW), v("u-0003", "approve")])
    assert run(repo) == 0
    group = data(repo)["groups"][0]
    assert group["defer_file"].startswith("verdicts-deferred-qsDay-policy-contract-")
    assert run(repo, "--defer", group["id"], "--note", "fix the contract first") == 0
    payload = json.loads((repo["root"] / group["defer_file"]).read_text())
    assert payload["format"] == "ams-review-verdicts/1"
    assert payload["manifest_generated_at"] == STAMP
    assert [record["unit"] for record in payload["verdicts"]] == ["u-0002"]
    record = payload["verdicts"][0]
    assert record["verdict"] == "skip"
    assert record["at"] == STAMP
    assert record["note"] == (
        f"[deferred: qsDay.yaml policy.contract(+) — complaint list {STAMP}] fix the contract first"
    )


def test_defer_refuses_unknown_ids_and_empty_candidate_sets(repo):
    write_corpus(repo, [unit("u-0001", [P_EXTEND_1])])
    write_verdicts(repo, [v("u-0001", "reject")])
    assert run(repo, "--defer", "g-00000000") == 1
    payload = data(repo)
    assert run(repo, "--defer", payload["groups"][0]["id"]) == 1


def test_refuses_a_verdicts_file_from_another_manifest(repo, capsys):
    """The refusal names the stamp the file's head settles, and nothing past the head is parsed, so a file whose records are not JSON is refused as one whose records are. A head that does not settle the stamp leaves it to the whole parse, which refuses the file, or fails as `json.loads` fails on text that is not JSON. A file stamped for the corpus gives the same complaint list in every layout."""
    write_corpus(repo, [unit("u-0001", [P_EXTEND_1])])
    write_verdicts(repo, [v("u-0001", "reject")], stamp="2026-07-01T00:00:00Z")
    assert run(repo) == 1
    assert "has not been carried onto this corpus" in capsys.readouterr().err
    assert not repo["data_out"].exists()
    refusal = f"{repo['verdicts']} is stamped {OLD} but the corpus is {STAMP}, so it has not been carried onto this corpus"
    for text in (
        f'{{"format": "ams-review-verdicts/1", "manifest_generated_at": "{OLD}", "verdicts": [not json',
        *verdicts_layouts(OLD, [v("u-0001", "reject")])[1:],
    ):
        repo["verdicts"].write_text(text)
        assert run(repo) == 1
        assert capsys.readouterr().err.startswith(refusal)
        assert not repo["data_out"].exists()
    repo["verdicts"].write_text('{"verdicts": [not json')
    with pytest.raises(json.JSONDecodeError):
        run(repo)
    written = set()
    for text in verdicts_layouts(STAMP, [v("u-0001", "reject", at=NEW)]):
        repo["verdicts"].write_text(text)
        assert run(repo) == 0
        written.add((repo["data_out"].read_bytes(), *capsys.readouterr()))
    assert len(written) == 1
    assert data(repo)["totals"]["complaints"] == 1


def test_exempt_units_never_complain_and_are_never_deferred(repo):
    write_corpus(
        repo,
        [
            unit("u-0001", [P_EXTEND_1]),
            unit("u-0002", [P_EXTEND_1], batch=None),
            unit("u-0003", [P_EXTEND_1], no_verdict=True),
            unit("u-0004", [P_EXTEND_1], batch=None),
        ],
    )
    write_verdicts(repo, [v("u-0001", "reject"), v("u-0002", "reject"), v("u-0003", "reject")])
    assert run(repo) == 0
    payload = data(repo)
    assert payload["totals"]["complaints"] == 1
    assert payload["groups"][0]["defer_candidates"]["unit_ids"] == []


def test_the_absent_unit_warning_counts_against_every_id_on_the_corpus(repo, capsys):
    """A verdict on a unit that is not a human unit names a unit that is on the corpus, so it is not absent; only an id missing from the whole corpus is. The fixture corpus has shards but no index, so the tool goes through the loader's shard fallback."""
    write_corpus(repo, [unit("u-0001", [P_EXTEND_1]), unit("u-0002", [P_EXTEND_1], batch=None)])
    write_verdicts(repo, [v("u-0001", "reject"), v("u-0002", "reject")])
    assert run(repo) == 0
    assert "absent from this corpus" not in capsys.readouterr().err
    write_verdicts(repo, [v("u-0001", "reject"), v("u-0002", "reject"), v("u-0009", "reject")])
    assert run(repo) == 0
    assert "warning: 1 verdict records name units absent from this corpus" in capsys.readouterr().err


def test_the_human_records_without_the_id_set_are_refused(repo):
    """`main` exits when passed `units` without `unit_ids`, or the reverse. The absent-unit warning needs every corpus id, and the human records alone would count every verdict on a machine unit as absent."""
    write_corpus(repo, [unit("u-0001", [P_EXTEND_1])])
    write_verdicts(repo, [v("u-0001", "reject")])
    with pytest.raises(SystemExit):
        run(repo, units=[unit("u-0001", [P_EXTEND_1])])
    with pytest.raises(SystemExit):
        run(repo, unit_ids={"u-0001"})


def test_streamed_records_preserve_complaint_list_and_defer_file_bytes_without_retaining_records(
    repo, capsys
):
    write_corpus(
        repo,
        [
            unit("u-0003", [P_EXTEND_1]),
            unit("u-0001", [P_EXTEND_1], policy=policy_draft()),
            unit("u-0002", [P_EXTEND_1, P_EXTEND_2]),
            unit("u-0004", [P_EXTEND_2]),
            unit("u-0005", [P_EXTEND_1], cls="ruled-class"),
            unit("u-0006", [P_EXTEND_1]),
            unit("u-0007", [P_EXTEND_1], batch=None),
        ],
    )
    write_verdicts(
        repo,
        [
            v("u-0001", "reject", at=NEW),
            v("u-0002", "neither"),
            v("u-0004", "approve"),
            v("u-0006", "skip"),
            v("u-0007", "reject"),
            v("u-0009", "reject"),
        ],
    )
    units, unit_ids = load_human_units(repo["corpus"])
    assert run(repo, units=units, unit_ids=unit_ids) == 0
    group = data(repo)["groups"][0]
    assert run(repo, "--defer", group["id"], units=units, unit_ids=unit_ids) == 0
    expected = repo["data_out"].read_bytes()
    defer_path = repo["root"] / group["defer_file"]
    expected_defer = defer_path.read_bytes()
    capsys.readouterr()

    class StreamRecord(dict):
        pass

    references = []

    def stream():
        for record in units:
            assert sum(reference() is not None for reference in references) <= 1
            streamed = StreamRecord(record)
            references.append(weakref.ref(streamed))
            yield streamed
            del streamed

    assert run(repo, "--defer", group["id"], units=stream(), unit_ids=unit_ids) == 0
    assert repo["data_out"].read_bytes() == expected
    assert defer_path.read_bytes() == expected_defer
    assert all(reference() is None for reference in references)
    assert capsys.readouterr().err == "warning: 1 verdict records name units absent from this corpus\n"


def test_conflicting_mechanical_drafts_on_one_draft_target_are_flagged(repo):
    write_corpus(
        repo,
        [
            unit("u-0001", [P_EXTEND_1], policy=policy_draft(when="{left: {family: [qsTea]}}")),
            unit("u-0002", [P_EXTEND_2], policy=policy_draft(when="{left: {family: [qsNo]}}")),
        ],
    )
    write_verdicts(repo, [v("u-0001", "reject"), v("u-0002", "reject")])
    assert run(repo) == 0
    group = data(repo)["groups"][0]
    assert len(group["suggested_records"]) == 2
    assert group["draft_conflicts"] is True


def test_complaints_with_no_provenance_go_in_a_terminal_unattributed_group(repo):
    write_corpus(repo, [unit("u-0001", []), unit("u-0002", [P_EXTEND_1])])
    write_verdicts(repo, [v("u-0001", "reject", at=NEW), v("u-0002", "reject")])
    assert run(repo) == 0
    payload = data(repo)
    assert [group["kind"] for group in payload["groups"]] == ["provenance", "unattributed"]
    unattributed = payload["groups"][-1]
    assert unattributed["defer_file"] is None
    assert unattributed["defer_candidates"]["count"] == 0


def test_no_open_complaints_still_writes_a_valid_empty_feed(repo, capsys):
    """With no complaint open, the list is empty and says so. When the caller hands over the human records and the corpus ids, as the verdict update does, the records are never read, and the data file, the printed lines, the warning about records on units off the corpus and an unknown `--defer` group's refusal are a standalone run's, which walks the corpus. One override reject on an accepted unit sends the handed run through the records, and it writes what a standalone run writes."""
    write_corpus(repo, [unit("u-0001", [P_EXTEND_1])])
    write_verdicts(repo, [])
    assert run(repo) == 0
    assert "no open complaints" in capsys.readouterr().out
    payload = data(repo)
    assert payload["groups"] == []
    assert payload["totals"]["complaints"] == 0

    listed_unit, overridden = "u-1AAAAAAAAAA", "u-1BBBBBBBBBB"
    write_corpus(
        repo,
        [
            unit("u-0001", [P_EXTEND_1]),
            unit("u-0002", [P_EXTEND_1, P_EXTEND_2]),
            unit("u-0003", [P_EXTEND_2], cls="ruled-class"),
            unit("u-0004", [P_EXTEND_1]),
            {**unit("u-0005", [P_EXTEND_1]), "id": listed_unit},
            {**unit("u-0006", [P_EXTEND_2]), "id": overridden},
            unit("u-0007", [P_EXTEND_1], batch=None),
        ],
    )
    path = repo["root"] / "rebuild" / "accepted-units.txt"
    path.parent.mkdir()
    path.write_bytes(accepted.render("2026-07-05T00:00:00Z", {listed_unit: "approve", overridden: "either"}))
    units, unit_ids = load_human_units(repo["corpus"])

    def write_store(override):
        repo["verdicts"].write_text(
            json.dumps(
                {
                    "format": "ams-review-verdicts/1",
                    "manifest_generated_at": STAMP,
                    "exported_at": STAMP,
                    "verdicts": [
                        v("u-0001", "approve"),
                        v("u-0002", "identical", at=NEW),
                        v("u-0004", "skip"),
                        v("u-0007", "approve"),
                        v("u-0009", "approve"),
                    ],
                    "overrides": {"verdicts": [v(overridden, override, note="looks off", at=NEW)]},
                    "accepted": accepted.read(path).field(),
                }
            )
        )

    def outcome(code, *args, **held):
        assert run(repo, *args, **held) == code
        return repo["data_out"].read_bytes(), *capsys.readouterr()

    def unread():
        raise AssertionError("a handed stream is read with no complaint open")
        yield

    def read(log):
        for record in units:
            log.append(record["id"])
            yield record

    write_store("approve")
    for args, code in (((), 0), (("--defer", "g-00000000"), 1)):
        walked = outcome(code, *args)
        assert outcome(code, *args, units=unread(), unit_ids=set(unit_ids)) == walked
        assert walked[1] == "no open complaints\n"
    assert walked[2] == (
        "warning: 1 verdict records name units absent from this corpus\nunknown group id g-00000000 (known: none)\n"
    )

    write_store("reject")
    walked = outcome(0)
    log = []
    assert outcome(0, units=read(log), unit_ids=set(unit_ids)) == walked
    assert log == [record["id"] for record in units]
    assert data(repo)["totals"]["rejects"] == 1
