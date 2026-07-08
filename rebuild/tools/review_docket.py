"""Generate the review docket for the live surface: cluster the blank human units across echo groups by (configs, class, per-config ink delta) — the echo key minus the judged pair — so one visual question that recurs across letter contexts is briefed and decided once instead of once per context. Writes docket.html next to index.html (reusing the surface's dual-font rendering and deep-linking into the app by unit id) plus a machine-readable tmp/docket-data.json for recommendation authoring, and folds an optional recommendations JSON (tmp/docket-recs.json) back into the page. The page also calls out ledger classes already ruled intended/reviewed-approved that still hold blank units (bulk-verdict candidates whose rationale the manifest carries verbatim from the ledger), and lists echo groups whose recorded verdicts disagree (the echo_verdicts.py conflict audit, rendered)."""

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


def cluster_cards(clusters, recommendations, manifest):
    cards = []
    for cluster in clusters:
        exemplar = cluster["exemplar_unit"]
        reps = " ".join(
            unit_link(group["unit_ids"][0], f'{group["unit_ids"][0]} ({group["notations"][0]})')
            for group in cluster["echo_groups"][:8]
        )
        more = ""
        if len(cluster["echo_groups"]) > 8:
            more = f' <span class="note">(+{len(cluster["echo_groups"]) - 8} more groups below)</span>'
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
        cards.append(
            f'<article class="cluster" id="{cluster["id"]}">'
            f'<header><span class="size">{cluster["size"]} unit{"s" if cluster["size"] != 1 else ""}</span>'
            f'<span>in {len(cluster["echo_groups"])} echo group{"s" if len(cluster["echo_groups"]) != 1 else ""}</span>'
            f'<span class="chip">{html.escape(cluster["class"])}</span>'
            f'<span class="configs">{html.escape(", ".join(cluster["configs"]))}</span>'
            f'<span class="configs">{cluster["id"]}</span></header>'
            f'{recommendation_html(recommendations.get("clusters", {}).get(cluster["id"]))}'
            f"{summary}"
            f'{exemplar_html(exemplar, manifest)}'
            f"{evidence_html}"
            f'<p class="reps">Decide one representative per echo group: {reps}{more}</p>'
            f"<details><summary>All {cluster['size']} members</summary>{members}</details>"
            f"</article>"
        )
    return "".join(cards)


def ruled_cards(ruled, recommendations, units_by_id, manifest):
    cards = []
    for entry in ruled:
        exemplar = units_by_id[entry["exemplar_ids"][0]]
        links = " ".join(unit_link(unit_id) for unit_id in entry["exemplar_ids"])
        recommendation = recommendation_html(recommendations.get("classes", {}).get(entry["id"]))
        cards.append(
            f'<article class="ruled">'
            f'<header><span class="size">{entry["blank_count"]} blank units</span>'
            f'<span>in {entry["echo_group_count"]} echo groups</span>'
            f'<span class="chip">{html.escape(entry["id"])}</span>'
            f'<span class="chip">{html.escape(entry["status"])}</span></header>'
            f"<p>The ledger already records this phenomenon as <strong>{html.escape(entry['status'])}</strong>, "
            f"so these blanks are candidates for one bulk decision rather than per-unit review.</p>"
            f'{recommendation}'
            f'<div class="why">{html.escape(entry["why"])}</div>'
            f'{exemplar_html(exemplar, manifest)}'
            f'<p class="reps">Exemplars: {links}</p>'
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
            f'<span class="chip">{html.escape(conflict["class"])}</span></header>'
            f'<table class="conflict">{"".join(rows)}</table></article>'
        )
    return "".join(cards)


def build_page(manifest, data, recommendations, units_by_id, verdicts_name, head_warning):
    preamble = ""
    if recommendations.get("preamble"):
        preamble = f'<p class="preamble">{linkify(recommendations["preamble"])}</p>'
    warning = f'<p class="warning">{html.escape(head_warning)}</p>' if head_warning else ""
    totals = data["totals"]
    ruled_section = ""
    if data["ruled_classes"]:
        ruled_section = (
            "<section><h2>Already ruled in the ledger — bulk candidates</h2>"
            + ruled_cards(data["ruled_classes"], recommendations, units_by_id, manifest)
            + "</section>"
        )
    conflict_section = ""
    if data["conflicts"]:
        conflict_section = (
            f'<section><h2>Echo groups with disagreeing verdicts ({len(data["conflicts"])})</h2>'
            "<p>The same visual change judged differently across contexts — worth a re-check.</p>"
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
{warning}
{preamble}
</header>
{ruled_section}
<section>
<h2>Clusters — one visual question each, largest first</h2>
<p>Each cluster shares one before→after ink delta (the echo key minus the judged pair), so a decision on any representative should hold for every member. Deciding a representative in the app echo-fills its own group; the other groups in the cluster each need one representative, or ask for a proposals import file covering the whole cluster.</p>
{cluster_cards(data["clusters"], recommendations, manifest)}
</section>
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

    docket_data = {
        "manifest_generated_at": manifest["generated_at"],
        "verdicts_file": verdicts_path.name,
        "totals": {
            "blank_units": len(blanks),
            "echo_groups": len({unit.get("echo") or unit["id"] for unit in blanks}),
            "clusters": len(clusters),
            "multi_clusters": sum(1 for cluster in clusters if cluster["size"] > 1),
            "singleton_clusters": sum(1 for cluster in clusters if cluster["size"] == 1),
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

    page_data = {"totals": docket_data["totals"], "clusters": clusters, "ruled_classes": ruled, "conflicts": conflicts}
    out = pathlib.Path(args.out) if args.out else surface / "docket.html"
    out.write_text(build_page(manifest, page_data, recommendations, units_by_id, verdicts_path.name, head_warning))

    totals = docket_data["totals"]
    print(
        f"wrote {out} and {data_out}: {totals['blank_units']} blank units in {totals['echo_groups']} echo groups → "
        f"{totals['clusters']} clusters ({totals['multi_clusters']} multi-unit, {totals['singleton_clusters']} singleton); "
        f"{len(ruled)} already-ruled classes hold blanks; {len(conflicts)} echo groups disagree"
    )


if __name__ == "__main__":
    main()
