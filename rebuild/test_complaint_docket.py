"""Tests for the complaint docket: grouping reject/neither verdicts by the deciding rune records (policy fix site first, exact provenance tuple as the fallback), the fresh-vs-standing split against the manifest stamp, the reverse index from a group's pointer basis to blank park candidates and judged churn, and park-file emission under the bulk discipline (skip verdicts at the manifest stamp, every member enumerated)."""

import json

import pytest

from rebuild.tools import complaint_docket as cd

STAMP = "2026-07-10T00:00:00Z"
FRESH = "2026-07-10T12:00:00Z"
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


def unit(uid, provenance=(), policy=None, batch=1, no_verdict=False, cls="live-class"):
    number = uid.split("-")[1]
    return {
        "id": uid,
        "batch": batch,
        "no_verdict": no_verdict,
        "echo": f"e-{number}",
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
    surface = tmp_path / "surface"
    (surface / "units").mkdir(parents=True)
    (surface / "manifest.json").write_text(
        json.dumps(
            {
                "generated_at": STAMP,
                "classes": [
                    {"id": "live-class", "status": "diff"},
                    {"id": "ruled-class", "status": "intended"},
                ],
            }
        )
    )
    return {
        "root": tmp_path,
        "surface": surface,
        "verdicts": tmp_path / "verdicts.json",
        "data_out": tmp_path / "complaints-data.json",
    }


def write_surface(repo, units):
    (repo["surface"] / "units" / "all.json").write_text(json.dumps(units))


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


def run(repo, *args):
    return cd.main(
        [
            str(repo["verdicts"]),
            "--surface",
            str(repo["surface"]),
            "--data-out",
            str(repo["data_out"]),
            "--park-dir",
            str(repo["root"]),
            *args,
        ]
    )


def data(repo):
    return json.loads(repo["data_out"].read_text())


def test_rejects_sharing_a_policy_target_form_one_group_with_a_union_basis(repo):
    write_surface(
        repo,
        [
            unit("u-0001", [P_EXTEND_1], policy=policy_draft()),
            unit("u-0002", [P_EXTEND_2], policy=policy_draft(codepoints="E653:E654")),
        ],
    )
    write_verdicts(repo, [v("u-0001", "reject", at=FRESH), v("u-0002", "reject", at=FRESH)])
    assert run(repo) == 0
    payload = data(repo)
    assert payload["totals"]["groups"] == 1
    group = payload["groups"][0]
    assert group["kind"] == "policy"
    assert group["target"] == {"file": "glyph_data/runes/qsDay.yaml", "keypath": "policy.contract[+]"}
    assert group["pointers"] == [P_EXTEND_1, P_EXTEND_2]
    assert {entry["unit"] for entry in group["rejects"]["fresh"]} == {"u-0001", "u-0002"}
    assert len(group["suggested_records"]) == 1
    assert group["draft_conflicts"] is False


def test_draftless_rejects_group_by_their_exact_provenance_tuple(repo):
    write_surface(
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
    pair = next(group for group in payload["groups"] if len(group["rejects"]["standing"]) == 2)
    assert pair["kind"] == "provenance"
    assert pair["target"] == {"pointers": [P_EXTEND_1, P_EXTEND_2]}
    assert pair["suggested_records"] == []


def test_a_neither_strand_attaches_to_the_pointer_sharing_reject_group(repo):
    write_surface(
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
    assert [entry["unit"] for entry in attached["neithers"]["standing"]] == ["u-0002"]
    assert set(attached["pointers"]) == {P_EXTEND_1, P_EXTEND_2}
    standalone = next(group for group in payload["groups"] if group["kind"] == "provenance")
    assert [entry["unit"] for entry in standalone["neithers"]["standing"]] == ["u-0003"]
    assert standalone["rejects"] == {"fresh": [], "standing": []}


def test_fresh_and_standing_split_on_the_manifest_stamp_and_since_overrides(repo):
    write_surface(repo, [unit("u-0001", [P_EXTEND_1]), unit("u-0002", [P_EXTEND_1])])
    write_verdicts(repo, [v("u-0001", "reject", at=FRESH), v("u-0002", "reject", at=OLD)])
    assert run(repo) == 0
    totals = data(repo)["totals"]
    assert (totals["fresh"], totals["standing"]) == (1, 1)
    assert run(repo, "--since", "2026-06-01T00:00:00Z") == 0
    totals = data(repo)["totals"]
    assert (totals["fresh"], totals["standing"]) == (2, 0)
    assert data(repo)["since"] == "2026-06-01T00:00:00Z"


def test_park_candidates_are_the_blank_sharers_and_judged_sharers_forecast_churn(repo):
    write_surface(
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
            v("u-0001", "reject", at=FRESH),
            v("u-0003", "skip"),
            v("u-0004", "approve"),
            v("u-0005", "either"),
        ],
    )
    assert run(repo) == 0
    group = data(repo)["groups"][0]
    assert group["park_candidates"]["unit_ids"] == ["u-0002", "u-0003"]
    assert group["park_candidates"]["count"] == 2
    assert group["churn_if_fixed"] == {"approve": 1, "either": 1, "identical": 0}
    assert data(repo)["totals"]["park_candidates"] == 2
    assert data(repo)["totals"]["approved_sharing"] == 1


def test_ruled_class_blanks_are_counted_but_not_parked(repo):
    write_surface(
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
    assert group["park_candidates"]["unit_ids"] == ["u-0003"]
    assert group["ruled_class_blanks"] == {"count": 1, "by_class": {"ruled-class": 1}}


def test_park_emits_skip_verdicts_at_the_manifest_stamp_covering_exact_blanks(repo):
    write_surface(
        repo,
        [
            unit("u-0001", [P_EXTEND_1], policy=policy_draft()),
            unit("u-0002", [P_EXTEND_1]),
            unit("u-0003", [P_EXTEND_1]),
        ],
    )
    write_verdicts(repo, [v("u-0001", "reject", at=FRESH), v("u-0003", "approve")])
    assert run(repo) == 0
    group = data(repo)["groups"][0]
    assert group["park_file"].startswith("verdicts-park-qsDay-policy-contract-")
    assert run(repo, "--park", group["id"], "--note", "fix the contract first") == 0
    payload = json.loads((repo["root"] / group["park_file"]).read_text())
    assert payload["format"] == "ams-review-verdicts/1"
    assert payload["manifest_generated_at"] == STAMP
    assert [record["unit"] for record in payload["verdicts"]] == ["u-0002"]
    record = payload["verdicts"][0]
    assert record["verdict"] == "skip"
    assert record["at"] == STAMP
    assert record["note"] == (
        f"[parked: qsDay.yaml policy.contract(+) — docket {STAMP}] fix the contract first"
    )


def test_park_refuses_unknown_ids_and_empty_candidate_sets(repo):
    write_surface(repo, [unit("u-0001", [P_EXTEND_1])])
    write_verdicts(repo, [v("u-0001", "reject")])
    assert run(repo, "--park", "g-00000000") == 1
    payload = data(repo)
    assert run(repo, "--park", payload["groups"][0]["id"]) == 1


def test_refuses_a_verdicts_file_from_another_manifest(repo, capsys):
    write_surface(repo, [unit("u-0001", [P_EXTEND_1])])
    write_verdicts(repo, [v("u-0001", "reject")], stamp="2026-07-01T00:00:00Z")
    assert run(repo) == 1
    assert "carry it forward first" in capsys.readouterr().err
    assert not repo["data_out"].exists()


def test_exempt_units_never_complain_and_never_park(repo):
    write_surface(
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
    assert payload["groups"][0]["park_candidates"]["unit_ids"] == []


def test_conflicting_mechanical_drafts_on_one_fix_site_are_flagged(repo):
    write_surface(
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


def test_complaints_with_no_provenance_land_in_a_terminal_unattributed_group(repo):
    write_surface(repo, [unit("u-0001", []), unit("u-0002", [P_EXTEND_1])])
    write_verdicts(repo, [v("u-0001", "reject", at=FRESH), v("u-0002", "reject")])
    assert run(repo) == 0
    payload = data(repo)
    assert [group["kind"] for group in payload["groups"]] == ["provenance", "unattributed"]
    unattributed = payload["groups"][-1]
    assert unattributed["park_file"] is None
    assert unattributed["park_candidates"]["count"] == 0


def test_no_open_complaints_still_writes_a_valid_empty_feed(repo, capsys):
    write_surface(repo, [unit("u-0001", [P_EXTEND_1])])
    write_verdicts(repo, [])
    assert run(repo) == 0
    assert "no open complaints" in capsys.readouterr().out
    payload = data(repo)
    assert payload["groups"] == []
    assert payload["totals"]["complaints"] == 0
