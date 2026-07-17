"""Generate the review docket for the live surface: cluster the blank human units across echo groups by (configs, class, per-config ink delta) — the echo key minus the judged pair — so one visual question that recurs across letter contexts is briefed and decided once instead of once per context. The page is a work order, not a gallery: every decision is one `index.html#units=…` worklist link that stacks the decision's representatives in the app, where the actual judging happens with the keyboard flow and echo-fill; only the current tranche of top cluster decisions carries an exemplar render pair, and everything else is a compact row. Writes docket.html next to index.html plus a machine-readable tmp/docket-data.json for recommendation authoring, and folds an optional recommendations JSON (tmp/docket-recs.json) back into the page. The page also calls out ledger classes already ruled intended/reviewed-approved that still hold blank units (bulk-verdict candidates whose rationale the manifest carries verbatim from the ledger), and lists echo groups whose recorded verdicts disagree (the echo_verdicts.py conflict audit, rendered with a stacked-worklist link per group)."""

import argparse
import collections
import hashlib
import html
import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from rebuild.review.ink import InkComparator  # noqa: E402

SURFACE = ROOT / "rebuild/out/review"
DATA_OUT = ROOT / "tmp/docket-data.json"
RECOMMENDATIONS = ROOT / "tmp/docket-recs.json"
RULED_STATUSES = ("intended", "reviewed-approved", "reviewed-rejected")
FONT_SIZE = 88
UNIT_ID = re.compile(r"\bu-\d{4}\b")
TRANCHE_SIZE = 25
SINGLETON_CHUNK = 40

DOCKET_CSS = """
body.docket {
  max-width: 100rem;
  margin: 0 auto;
  padding: 1rem 2rem 4rem;

  & > header {
    p {
      max-width: 60rem;
    }

    .provenance {
      color: var(--muted);
      font-size: 0.85rem;
    }

    .warning {
      color: var(--reject);
      font-weight: 600;
    }
  }

  h1,
  h2 {
    font-weight: 600;
  }

  section {
    margin-top: 2.5rem;
  }

  article {
    background: var(--card);
    border: 1px solid var(--hairline);
    border-radius: 8px;
    padding: 1rem 1.25rem;
    margin: 1rem 0;

    & > header {
      display: flex;
      flex-wrap: wrap;
      align-items: baseline;
      gap: 0.75rem;

      .size {
        font-size: 1.1rem;
        font-weight: 600;
      }

      .configs {
        color: var(--muted);
        font-family: var(--code-font);
        font-size: 0.8rem;
      }
    }
  }

  .chip {
    font-family: var(--code-font);
    font-size: 0.75rem;
    border: 1px solid var(--hairline);
    border-radius: 8px;
    padding: 0.1em 0.6em;
    background: light-dark(#f0f0f0, #333);
  }

  .verdict-chip {
    font-weight: 700;
    border-radius: 8px;
    padding: 0.1em 0.7em;
    color: light-dark(#fff, #1a1a1a);

    &.approve {
      background: var(--approve);
    }

    &.reject {
      background: var(--reject);
    }

    &.either {
      background: var(--either);
    }

    &.identical {
      background: var(--identical);
    }

    &.neither {
      background: var(--neither);
    }

    &.skip {
      background: var(--skip);
    }
  }

  .recommendation {
    border-left: 4px solid var(--accent);
    padding: 0.4rem 0.9rem;
    margin: 0.75rem 0;
    background: light-dark(rgba(0, 102, 204, 0.05), rgba(74, 158, 255, 0.07));

    .reasoning {
      white-space: pre-wrap;
      margin: 0.4rem 0 0;
    }
  }

  .ruled .why {
    white-space: pre-wrap;
    border-left: 4px solid var(--neither);
    padding: 0.4rem 0.9rem;
    margin: 0.75rem 0;
    color: var(--muted);
    font-size: 0.9rem;
  }

  .render-pair {
    margin: 0.75rem 0;

    .config-label {
      color: var(--muted);
      font-family: var(--code-font);
      font-size: 0.8rem;
      margin-bottom: 0.25rem;
    }
  }

  .evidence,
  .reps {
    font-size: 0.9rem;

    .note {
      color: var(--muted);
      font-style: italic;
    }
  }

  details {
    font-size: 0.85rem;

    a {
      margin-right: 0.6em;
    }
  }

  table.conflict {
    border-collapse: collapse;
    font-size: 0.85rem;

    td {
      padding: 0.15rem 0.8rem 0.15rem 0;
      vertical-align: baseline;
    }
  }

  .preamble {
    white-space: pre-wrap;
    max-width: 60rem;
  }

  .open-app {
    display: inline-block;
    font-weight: 600;
    color: var(--accent);
    border: 1px solid var(--accent);
    border-radius: 6px;
    padding: 0.15em 0.7em;
    text-decoration: none;
    white-space: nowrap;

    &:hover {
      background: light-dark(rgba(0, 102, 204, 0.08), rgba(74, 158, 255, 0.12));
    }
  }

  table.workorder {
    border-collapse: collapse;
    font-size: 0.9rem;

    th {
      text-align: left;
      font-weight: 600;
      color: var(--muted);
    }

    td,
    th {
      padding: 0.3rem 1rem 0.3rem 0;
      vertical-align: baseline;
      border-bottom: 1px solid var(--hairline);
    }

    tr:last-child td {
      border-bottom: none;
    }
  }

  .chunk-links {
    font-size: 0.9rem;

    .open-app {
      margin: 0 0.5em 0.4em 0;
    }
  }
}
"""


def load_units(surface):
    units = []
    for path in sorted((surface / "units").glob("*.json")):
        units.extend(json.loads(path.read_text()))
    return units


def latest_verdicts(path):
    best = {}
    for record in json.loads(path.read_text())["verdicts"]:
        unit = record["unit"]
        if unit not in best or record["at"] > best[unit]["at"]:
            best[unit] = record
    return best


def text_of(unit):
    return "".join(chr(int(codepoint, 16)) for codepoint in unit["codepoints"].split(":"))


def feature_settings(config):
    if not config or config == "default":
        return "normal"
    return ", ".join(f'"{part}" 1' for part in config.split("+"))


def render_groups_of(unit):
    raw = unit.get("render_groups") or [{"configs": unit["configs"]}]
    return [
        {"label": ", ".join(group["configs"]), "features": feature_settings(group["configs"][0])}
        for group in raw
    ]


def repo_head():
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
        )
        return result.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def unit_link(unit_id, label=None):
    return f'<a href="index.html#unit={unit_id}">{html.escape(label or unit_id)}</a>'


def worklist_href(unit_ids):
    return "index.html#units=" + ",".join(unit_ids)


def app_button(href, label):
    return f'<a class="open-app" href="{html.escape(href)}">{html.escape(label)} ↗</a>'


def cluster_reps(cluster):
    return [group["unit_ids"][0] for group in cluster["echo_groups"]]


def linkify(text):
    return UNIT_ID.sub(lambda match: unit_link(match.group(0)), html.escape(text))


def sample_html(unit, side, features, upem):
    band = ""
    highlight = (unit.get("highlight") or {}).get(side)
    if unit.get("pair") is not None and highlight:
        scale = FONT_SIZE / upem
        left = highlight["x_min"] * scale
        width = (highlight["x_max"] - highlight["x_min"]) * scale
        band = f'<span class="pair-band" style="left: {left:.1f}px; width: {width:.1f}px"></span>'
    return (
        f'<div class="qs {side}" style="font-feature-settings: {html.escape(features)}">'
        f'<span class="run">{unit["text_entities"]}</span>{band}</div>'
    )


def exemplar_html(unit, manifest):
    parts = []
    for group in render_groups_of(unit):
        label = html.escape(group["label"])
        before = sample_html(unit, "before", group["features"], manifest["fonts"]["before"]["upem"])
        after = sample_html(unit, "after", group["features"], manifest["fonts"]["after"]["upem"])
        parts.append(f'<div class="render-pair"><div class="config-label">{label}</div>{before}{after}</div>')
    return "".join(parts)


def verdict_chip(verdict):
    return f'<span class="verdict-chip {html.escape(verdict)}">{html.escape(verdict)}</span>'


def recommendation_html(recommendation):
    if not recommendation:
        return ""
    chip = verdict_chip(recommendation["verdict"]) if recommendation.get("verdict") else ""
    reasoning = linkify(recommendation.get("reasoning", ""))
    return f'<div class="recommendation">{chip}<p class="reasoning">{reasoning}</p></div>'


def tranche_cards(clusters, recommendations, manifest):
    cards = []
    for position, cluster in enumerate(clusters, 1):
        exemplar = cluster["exemplar_unit"]
        reps = cluster_reps(cluster)
        evidence = cluster["evidence"]
        if evidence["counts"]:
            tallies = ", ".join(f"{verdict} ×{count}" for verdict, count in evidence["counts"].items())
            samples = "; ".join(
                f'{unit_link(sample["unit"])} {html.escape(sample["verdict"])}'
                + (f' <span class="note">{html.escape(sample["note"][:90])}</span>' if sample["note"] else "")
                for sample in evidence["samples"]
            )
            evidence_html = f'<p class="evidence">Same delta already judged elsewhere: {tallies}. {samples}</p>'
        else:
            evidence_html = '<p class="evidence">No verdicted unit shares this delta — a fresh question.</p>'
        members = " ".join(
            unit_link(unit_id) for group in cluster["echo_groups"] for unit_id in group["unit_ids"]
        )
        summary = f'<p class="summary">{linkify(exemplar["summary"])}</p>' if exemplar.get("summary") else ""
        rep_label = f"Judge {len(reps)} rep{'s' if len(reps) != 1 else ''} in the app"
        judge = (
            f"{app_button(worklist_href(reps), rep_label)}"
            f' <span class="note">— one per echo group; each verdict echo-fills its group, covering all {cluster["size"]} units.</span>'
        )
        cards.append(
            f'<article class="cluster" id="{cluster["id"]}">'
            f'<header><span class="size">{position}. {cluster["size"]} unit{"s" if cluster["size"] != 1 else ""}</span>'
            f'<span>in {len(cluster["echo_groups"])} echo group{"s" if len(cluster["echo_groups"]) != 1 else ""}</span>'
            f'<span class="chip">{html.escape(cluster["class"])}</span>'
            f'<span class="configs">{html.escape(", ".join(cluster["configs"]))}</span>'
            f'<span class="configs">{cluster["id"]}</span></header>'
            f'{recommendation_html(recommendations.get("clusters", {}).get(cluster["id"]))}'
            f"{summary}"
            f'{exemplar_html(exemplar, manifest)}'
            f'<p class="reps">{judge}</p>'
            f"{evidence_html}"
            f"<details><summary>All {cluster['size']} members</summary>{members}</details>"
            f"</article>"
        )
    return "".join(cards)


def cluster_rows(clusters, recommendations):
    rows = []
    for cluster in clusters:
        recommendation = recommendations.get("clusters", {}).get(cluster["id"]) or {}
        chip = verdict_chip(recommendation["verdict"]) if recommendation.get("verdict") else ""
        reps = cluster_reps(cluster)
        rows.append(
            f'<tr><td>{cluster["size"]}</td>'
            f'<td><span class="chip">{html.escape(cluster["class"])}</span></td>'
            f'<td>{html.escape(cluster["exemplar_unit"]["notation"])}</td>'
            f"<td>{chip}</td>"
            f'<td>{app_button(worklist_href(reps), f"Judge {len(reps)}")}</td></tr>'
        )
    return (
        '<table class="workorder"><thead><tr><th>Units</th><th>Class</th><th>Exemplar</th>'
        "<th>Rec</th><th></th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


def singleton_section(singletons):
    chunk_links = []
    for start in range(0, len(singletons), SINGLETON_CHUNK):
        chunk = singletons[start : start + SINGLETON_CHUNK]
        chunk_links.append(
            app_button(
                worklist_href([cluster["exemplar_unit"]["id"] for cluster in chunk]),
                f"Judge {start + 1}–{start + len(chunk)}",
            )
        )
    rows = "".join(
        f'<tr><td>{unit_link(cluster["exemplar_unit"]["id"])}</td>'
        f'<td>{html.escape(cluster["exemplar_unit"]["notation"])}</td>'
        f'<td><span class="chip">{html.escape(cluster["class"])}</span></td></tr>'
        for cluster in singletons
    )
    return (
        f'<p class="chunk-links">Work them as app worklists, {SINGLETON_CHUNK} at a time: {" ".join(chunk_links)}</p>'
        f'<details><summary>All {len(singletons)} singletons by name</summary>'
        f'<table class="workorder"><tbody>{rows}</tbody></table></details>'
    )


def ruled_cards(ruled, recommendations):
    cards = []
    for position, entry in enumerate(ruled, 1):
        recommendation = recommendation_html(recommendations.get("classes", {}).get(entry["id"]))
        class_href = f'index.html#class={entry["id"]}&status=unverdicted'
        cards.append(
            f'<article class="ruled">'
            f'<header><span class="size">{position}. {entry["blank_count"]} blank units</span>'
            f'<span>in {entry["echo_group_count"]} echo groups</span>'
            f'<span class="chip">{html.escape(entry["id"])}</span>'
            f'<span class="chip">{html.escape(entry["status"])}</span>'
            f'{app_button(class_href, "Judge in the app")}</header>'
            f'{recommendation}'
            f"<p>The ledger already records this phenomenon as <strong>{html.escape(entry['status'])}</strong> — "
            f"one decision covers the class: judge it in the app, or bless it and import a bulk proposals file.</p>"
            f'<details><summary>Ledger rationale</summary><div class="why">{html.escape(entry["why"])}</div></details>'
            f"</article>"
        )
    return "".join(cards)


def conflict_cards(conflicts, units_by_id):
    cards = []
    for conflict in conflicts:
        rows = []
        for unit_id in conflict["unit_ids"]:
            unit = units_by_id[unit_id]
            record = conflict["records"].get(unit_id)
            verdict = verdict_chip(record["verdict"]) if record else '<span class="note">(blank)</span>'
            note = f'<span class="note">{html.escape(record["note"][:90])}</span>' if record and record["note"] else ""
            rows.append(
                f"<tr><td>{unit_link(unit_id)}</td><td>{html.escape(unit['notation'])}</td>"
                f"<td>{verdict}</td><td>{note}</td></tr>"
            )
        cards.append(
            f'<article class="conflict"><header><span class="chip">{conflict["echo"]}</span>'
            f'<span class="chip">{html.escape(conflict["class"])}</span>'
            f'{app_button(worklist_href(conflict["unit_ids"]), "View stacked in the app")}</header>'
            f'<table class="conflict">{"".join(rows)}</table></article>'
        )
    return "".join(cards)


def build_page(manifest, data, recommendations, units_by_id, verdicts_name, head_warning):
    preamble = ""
    if recommendations.get("preamble"):
        preamble = (
            f'<details><summary>Sitting notes</summary><p class="preamble">{linkify(recommendations["preamble"])}</p></details>'
        )
    warning = f'<p class="warning">{html.escape(head_warning)}</p>' if head_warning else ""
    totals = data["totals"]
    ruled_units = sum(entry["blank_count"] for entry in data["ruled_classes"])
    ruled_section = ""
    if data["ruled_classes"]:
        ruled_section = (
            f'<section><h2>1 · Class rulings — {len(data["ruled_classes"])} decisions cover {ruled_units} units</h2>'
            + ruled_cards(data["ruled_classes"], recommendations)
            + "</section>"
        )
    tranche_section = ""
    if data["tranche"]:
        tranche_units = sum(cluster["size"] for cluster in data["tranche"])
        tranche_section = (
            f'<section><h2>2 · This tranche — top {len(data["tranche"])} cluster decisions, {tranche_units} units</h2>'
            "<p>Each cluster shares one before→after ink delta, so one look answers the whole card. "
            "Judge the representatives in the app — echo-fill multiplies each verdict across its group. "
            "Re-bake the docket after a working session to get the next tranche.</p>"
            + tranche_cards(data["tranche"], recommendations, manifest)
            + "</section>"
        )
    later_section = ""
    if data["later"]:
        later_units = sum(cluster["size"] for cluster in data["later"])
        later_section = (
            f"<section><h2>3 · Later tranches — {len(data['later'])} smaller clusters, {later_units} units</h2>"
            f"<details><summary>Compact list (re-bake promotes these as the tranche above clears)</summary>"
            f'{cluster_rows(data["later"], recommendations)}</details></section>'
        )
    singleton_sec = ""
    if data["singletons"]:
        singleton_sec = (
            f'<section><h2>4 · Singletons — {len(data["singletons"])} one-off units</h2>'
            + singleton_section(data["singletons"])
            + "</section>"
        )
    conflict_section = ""
    if data["conflicts"]:
        conflict_section = (
            f'<section><h2>5 · Echo groups with disagreeing verdicts ({len(data["conflicts"])})</h2>'
            "<p>The same visual change judged differently across contexts — worth a re-check when convenient.</p>"
            + conflict_cards(data["conflicts"], units_by_id)
            + "</section>"
        )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Review docket — {html.escape(manifest["generated_at"])}</title>
<link rel="stylesheet" href="app.css">
<style>{DOCKET_CSS}</style>
</head>
<body class="docket">
<header>
<h1>Review docket</h1>
<p class="provenance">Surface {html.escape(manifest["generated_at"])} at {html.escape(manifest["repo_head"])} ·
verdicts from <code>{html.escape(verdicts_name)}</code> ·
{totals["blank_units"]} blank units in {totals["echo_groups"]} echo groups →
<strong>{totals["clusters"]} clusters</strong> ({totals["multi_clusters"]} multi-unit, {totals["singleton_clusters"]} singleton) ·
<a href="index.html">open the review app</a></p>
<p>Every button below opens the review app with the decision stacked as a worklist — all judging happens there, with the keyboard flow and echo-fill. This page is just the order of battle.</p>
{warning}
{preamble}
</header>
{ruled_section}
{tranche_section}
{later_section}
{singleton_sec}
{conflict_section}
<footer><p class="provenance">Generated by rebuild/tools/review_docket.py — regenerate with
<code>uv run python rebuild/tools/review_docket.py {html.escape(verdicts_name)}</code></p></footer>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__.split(":")[0] + ".")
    parser.add_argument("verdicts", help="the verdicts file for the current frontier (an export or the autosave)")
    parser.add_argument("--surface", default=str(SURFACE))
    parser.add_argument("--recommendations", default=str(RECOMMENDATIONS))
    parser.add_argument("--out", default=None, help="defaults to <surface>/docket.html")
    parser.add_argument("--data-out", default=str(DATA_OUT))
    args = parser.parse_args()

    surface = pathlib.Path(args.surface)
    manifest = json.loads((surface / "manifest.json").read_text())
    verdicts_path = pathlib.Path(args.verdicts)
    data = json.loads(verdicts_path.read_text())
    if data.get("manifest_generated_at") != manifest["generated_at"]:
        raise SystemExit(
            f"{args.verdicts} is stamped {data.get('manifest_generated_at')} but the surface is "
            f"{manifest['generated_at']}; unit ids must never be joined across manifests — carry it forward first"
        )
    records = latest_verdicts(verdicts_path)

    head = repo_head()
    head_warning = None
    if head and head != manifest["repo_head"]:
        head_warning = (
            f"The surface was built at {manifest['repo_head']} but HEAD is {head} — rune edits since then are "
            f"not reflected here; rebuild the surface (or adjudicate knowingly against the older build)."
        )

    units = load_units(surface)
    units_by_id = {unit["id"]: unit for unit in units}
    human = [unit for unit in units if unit["batch"] is not None]
    blanks = [
        unit
        for unit in human
        if unit["id"] not in records or records[unit["id"]]["verdict"] == "skip"
    ]

    comparator = InkComparator(surface / "fonts/before.otf", surface / "fonts/after.otf")
    keys = {}
    for unit in human:
        diffs = tuple(comparator.config_diff(text_of(unit), config) for config in unit["configs"])
        keys[unit["id"]] = (tuple(unit["configs"]), unit["class"], diffs)

    clusters_by_key = collections.defaultdict(list)
    for unit in blanks:
        clusters_by_key[keys[unit["id"]]].append(unit)

    evidence_by_key = collections.defaultdict(list)
    for unit in human:
        record = records.get(unit["id"])
        if record and record["verdict"] != "skip":
            evidence_by_key[keys[unit["id"]]].append((unit, record))

    clusters = []
    for key, members in clusters_by_key.items():
        cluster_id = "c-" + hashlib.sha1(repr(key).encode()).hexdigest()[:8]
        groups = collections.defaultdict(list)
        for unit in members:
            groups[unit.get("echo") or unit["id"]].append(unit)
        echo_groups = [
            {
                "echo": echo,
                "unit_ids": [unit["id"] for unit in group],
                "notations": [unit["notation"] for unit in group],
            }
            for echo, group in sorted(groups.items())
        ]
        judged = evidence_by_key.get(key, [])
        counts = collections.Counter(record["verdict"] for _unit, record in judged)
        samples = [
            {"unit": unit["id"], "verdict": record["verdict"], "note": record["note"]}
            for unit, record in judged[:3]
        ]
        exemplar = members[0]
        clusters.append(
            {
                "id": cluster_id,
                "class": key[1],
                "configs": list(key[0]),
                "size": len(members),
                "echo_groups": echo_groups,
                "exemplar": {
                    "id": exemplar["id"],
                    "notation": exemplar["notation"],
                    "summary": exemplar.get("summary"),
                },
                "exemplar_unit": exemplar,
                "evidence": {"counts": dict(counts.most_common()), "samples": samples},
            }
        )
    clusters.sort(key=lambda cluster: (-cluster["size"], cluster["class"], cluster["id"]))

    blank_by_class = collections.Counter(unit["class"] for unit in blanks)
    ruled = []
    for entry in manifest["classes"]:
        if entry["status"] in RULED_STATUSES and blank_by_class.get(entry["id"]):
            class_blanks = [unit for unit in blanks if unit["class"] == entry["id"]]
            ruled.append(
                {
                    "id": entry["id"],
                    "status": entry["status"],
                    "why": entry["why"],
                    "blank_count": len(class_blanks),
                    "echo_group_count": len({unit.get("echo") or unit["id"] for unit in class_blanks}),
                    "exemplar_ids": [unit["id"] for unit in class_blanks[:3]],
                }
            )
    ruled.sort(key=lambda entry: -entry["blank_count"])

    echo_members = collections.defaultdict(list)
    for unit in human:
        if unit.get("echo"):
            echo_members[unit["echo"]].append(unit)
    conflicts = []
    for echo, members in sorted(echo_members.items()):
        judged = {
            unit["id"]: records[unit["id"]]
            for unit in members
            if unit["id"] in records and records[unit["id"]]["verdict"] != "skip"
        }
        if len({record["verdict"] for record in judged.values()}) > 1:
            conflicts.append(
                {
                    "echo": echo,
                    "class": members[0]["class"],
                    "unit_ids": [unit["id"] for unit in members],
                    "records": judged,
                }
            )

    recommendations = {}
    recommendations_path = pathlib.Path(args.recommendations)
    if recommendations_path.exists():
        recommendations = json.loads(recommendations_path.read_text())

    ruled_ids = {entry["id"] for entry in ruled}
    unruled = [cluster for cluster in clusters if cluster["class"] not in ruled_ids]
    multi = [cluster for cluster in clusters if cluster["size"] > 1 and cluster["class"] not in ruled_ids]
    tranche = multi[:TRANCHE_SIZE]
    later = multi[TRANCHE_SIZE:]
    singletons = [cluster for cluster in unruled if cluster["size"] == 1]

    docket_data = {
        "manifest_generated_at": manifest["generated_at"],
        "verdicts_file": verdicts_path.name,
        "totals": {
            "blank_units": len(blanks),
            "echo_groups": len({unit.get("echo") or unit["id"] for unit in blanks}),
            "clusters": len(clusters),
            "multi_clusters": sum(1 for cluster in clusters if cluster["size"] > 1),
            "singleton_clusters": sum(1 for cluster in clusters if cluster["size"] == 1),
            "ruled_units": sum(entry["blank_count"] for entry in ruled),
            "tranche_clusters": len(tranche),
            "tranche_units": sum(cluster["size"] for cluster in tranche),
        },
        "clusters": [{key: value for key, value in cluster.items() if key != "exemplar_unit"} for cluster in clusters],
        "ruled_classes": [{key: value for key, value in entry.items() if key != "why"} for entry in ruled],
        "conflicts": [
            {
                "echo": conflict["echo"],
                "class": conflict["class"],
                "unit_ids": conflict["unit_ids"],
                "verdicts": {unit_id: record["verdict"] for unit_id, record in conflict["records"].items()},
            }
            for conflict in conflicts
        ],
    }
    data_out = pathlib.Path(args.data_out)
    data_out.parent.mkdir(parents=True, exist_ok=True)
    data_out.write_text(json.dumps(docket_data, ensure_ascii=False, indent=1) + "\n")

    page_data = {
        "totals": docket_data["totals"],
        "ruled_classes": ruled,
        "tranche": tranche,
        "later": later,
        "singletons": singletons,
        "conflicts": conflicts,
    }
    out = pathlib.Path(args.out) if args.out else surface / "docket.html"
    out.write_text(build_page(manifest, page_data, recommendations, units_by_id, verdicts_path.name, head_warning))

    totals = docket_data["totals"]
    print(
        f"wrote {out} and {data_out}: {totals['blank_units']} blank units in {totals['echo_groups']} echo groups → "
        f"{len(ruled)} class rulings ({totals['ruled_units']} units) + a {len(tranche)}-cluster tranche "
        f"({totals['tranche_units']} units) + {len(later)} later clusters + {len(singletons)} singletons; "
        f"{len(conflicts)} echo groups disagree"
    )


if __name__ == "__main__":
    main()
