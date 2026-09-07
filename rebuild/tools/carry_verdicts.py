import argparse
import collections
import hashlib
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from rebuild.review import unit_index  # noqa: E402
from rebuild.review.ink import InkComparator  # noqa: E402
from rebuild.review.unit_cache import (  # noqa: E402
    CARRY_PRESENTATION_KEYS,
    carry_projection,
    is_content_id,
    is_positional_id,
    unit_id_for,
)
from rebuild.tools.verdict_notes import cap_markers  # noqa: E402

OUT = ROOT / "verdicts-carried-forward.json"
CURRENT_SURFACE = ROOT / "rebuild/out/review"
# The exclusion set and the projection recipe live in rebuild.review.unit_cache so the build stamps each unit with exactly the hash this tool probes; the rationale for what is excluded rides the definition there.
PRESENTATION_KEYS = CARRY_PRESENTATION_KEYS


def iter_surface(root):
    """Every unit on a surface, in the slim shape rebuild.review.unit_index defines: the sidecar the build writes beside the manifest, or — for an archived snapshot, which is every surface older than the sidecar — the shards themselves, a shard at a time, each unit's batch read off that manifest's index. On that fallback the projection also fills in the `content_key` a pre-stamp surface never carried, because it is the one field the whole fragment is needed to compute and the one field the carry cannot proceed without."""
    if unit_index.index_is_current(root):
        yield from unit_index.iter_units(root)
        return
    slot = unit_index.slot_reader(root)
    for fragment in unit_index.iter_shard_fragments(root):
        record = unit_index.index_record(fragment, **slot(fragment))
        if not record["content_key"]:
            record["content_key"] = hashlib.sha256(content_key(fragment).encode()).hexdigest()
        yield record


def surface_is_positional(root):
    """Whether the surface under `root` names its units positionally: read off the manifest's triage index alone, since every verdict names a human unit and the index lists exactly those, so a surface whose index holds no positional id has nothing a migration could rewrite and is never walked to find that out."""
    try:
        manifest = json.loads((pathlib.Path(root) / "manifest.json").read_text())
        index = manifest.get("human_unit_ids") or ()
    except OSError, ValueError, AttributeError:
        return False
    return any(is_positional_id(unit_id) for unit_id in index)


def id_migration(root):
    """The rewrite that carries a surface's positional unit ids onto content ids: for every unit of the surface under `root` whose id is of the positional shape (`unit_cache.is_positional_id`), the content id its carry key names (`unit_cache.unit_id_for`), keyed by the positional id. Empty for a surface already content-addressed (`surface_is_positional`, which costs a manifest read and no walk), which is what makes the cutover a one-time event: the chain runs this over the snapshot it carries from, and once every snapshot carries content ids there is nothing to rewrite. The content key is the same on both sides of the cutover — only the id scheme moved — so the mapping is exact for every unit whose content did not move in the same cycle, and a unit whose content did move maps to the id of what was judged, which is the identity the journal should keep."""
    if not surface_is_positional(root):
        return {}
    return {
        unit["id"]: unit_id_for(content_hash(unit))
        for unit in iter_surface(root)
        if is_positional_id(unit["id"])
    }


def load_surface(root):
    return list(iter_surface(root))


def content_key(unit):
    return carry_projection(unit)


def content_hash(unit):
    """The unit's carry identity as a digest: the build-time `content_key` stamp when the surface carries one, else the sha256 of the projection computed here — the same value, so stamped and unstamped surfaces (every snapshot predating the stamp) resolve against each other freely."""
    stamped = unit.get("content_key")
    return stamped if stamped else hashlib.sha256(content_key(unit).encode()).hexdigest()


def surface_comparator(root):
    manifest = json.loads((root / "manifest.json").read_text())
    return InkComparator(
        root / manifest["fonts"]["before"]["file"], root / manifest["fonts"]["after"]["file"]
    )


def ink_key(comparator, unit):
    """The cross-surface identity of a unit's visual question: its window plus the rendered-outcome signature both fonts produce for it. Ink-duplicate merging re-keys and re-configures units without moving any ink, so a prior verdict whose content key no longer exists still applies to the current unit with the same ink_key. None when the unit's own configs disagree (no single signature to carry against)."""
    text = "".join(chr(int(part, 16)) for part in unit["codepoints"].split(":"))
    signatures = {comparator.signature(text, config) for config in unit["configs"]}
    if len(signatures) != 1:
        return None
    return (unit["codepoints"], signatures.pop())


def latest_verdicts(payload):
    best = {}
    for record in payload["verdicts"]:
        unit = record["unit"]
        if unit not in best or record["at"] > best[unit]["at"]:
            best[unit] = record
    return best


def check_source_stamps(root, verdict_file, payload):
    """Refuse a source pair whose stamps disagree: the verdicts' unit ids only mean anything on the surface they were recorded against, so resolving them against a different snapshot silently carries them onto the wrong windows (the qsEt cycle carried 589 that way before the mistake surfaced)."""
    recorded = payload.get("manifest_generated_at")
    held = json.loads((root / "manifest.json").read_text()).get("generated_at")
    if recorded != held:
        raise SystemExit(
            f"{verdict_file.name} is stamped {recorded}, but {root} holds the surface generated at {held}. "
            "These verdicts were recorded against a different surface, and resolving their unit ids here would carry them onto the wrong windows. "
            "Pair the file with the surface it was recorded on — the stamp-matching var/review-pre-* snapshot — and rerun."
        )


def identity_of(unit):
    """The cross-surface identity a verdict carries by: the unit's content id — its own id on a content-addressed surface, and `unit_cache.unit_id_for` over its carry hash on one that named its units positionally — so a verdict recorded on either kind of surface resolves against either kind."""
    unit_id = unit["id"]
    return unit_id if is_content_id(unit_id) else unit_id_for(content_hash(unit))


def resolve_prior(sources):
    """Every source surface's verdicts keyed by the content id of the unit they name, newest `at` winning. A verdict that already names a content id is its own resolution — the id is the identity, and the source surface is never opened for it — where one naming a positional id is resolved through the surface it was recorded on. The units held back for the ink fallback are only those a positional resolution read on the way; a content-id verdict's unit is fetched later, and only if it turns out stranded (`stranded_units`). Each source's units are held only for the length of its own pass — the whole point of the helper is that a prior surface's four hundred thousand records leave memory before the current surface's are read, rather than the two piles coexisting for the sake of the fifty thousand entries that survive into the result."""
    prior = {}
    surface_roots = {}
    for root, verdict_file in sources:
        payload = json.loads(verdict_file.read_text())
        check_source_stamps(root, verdict_file, payload)
        surface_roots[root.name] = root
        verdicts = latest_verdicts(payload)
        positional = {unit_id for unit_id in verdicts if not is_content_id(unit_id)}
        units_by_id = {u["id"]: u for u in iter_surface(root) if u["id"] in positional} if positional else {}
        used = 0
        by_identity = 0
        for unit_id, record in verdicts.items():
            if unit_id in positional:
                unit = units_by_id.get(unit_id)
                if unit is None:
                    continue
                key = identity_of(unit)
            else:
                unit = None
                key = unit_id
                by_identity += 1
            if key not in prior or record["at"] > prior[key][0]["at"]:
                prior[key] = (record, root.name, unit)
            used += 1
        print(
            f"{verdict_file.name}: {used} verdicts resolved against {root.name} "
            f"({by_identity} by identity, {used - by_identity} through the surface)"
        )
    return prior, surface_roots


def stranded_units(root, stranded):
    """The prior surface's records for the stranded verdicts resolved by identity, which never read one: `stranded` is a list of (record, source, unit-or-None) over one source, and the units still None are fetched off that surface in one pass, keyed by the content id the verdict names."""
    wanted = {record["unit"] for record, _source, unit in stranded if unit is None}
    if not wanted:
        return stranded
    found = {u["id"]: u for u in iter_surface(root) if u["id"] in wanted}
    return [
        (record, source, unit if unit is not None else found.get(record["unit"]))
        for record, source, unit in stranded
    ]


def main(argv=None, *, current_units=None):
    """`current_units` lets a caller that already holds the live surface's index hand it over rather than have this tool read it again; rebuild.tools.verdict_chain is the one caller that does."""
    parser = argparse.ArgumentParser(
        description="Re-resolve prior verdicts against the surfaces they were recorded on and carry them onto the live surface."
    )
    parser.add_argument(
        "--source",
        nargs=2,
        action="append",
        required=True,
        metavar=("SURFACE_DIR", "VERDICTS_JSON"),
        help="a prior surface directory and the verdicts file recorded against it; repeatable",
    )
    parser.add_argument("--out", default=str(OUT), help="output verdicts file (default: %(default)s)")
    parser.add_argument(
        "--current-surface",
        type=pathlib.Path,
        default=CURRENT_SURFACE,
        help="the freshly built surface to carry onto (default: the live review surface)",
    )
    args = parser.parse_args(argv)
    sources = [(pathlib.Path(directory), pathlib.Path(verdicts)) for directory, verdicts in args.source]

    prior, surface_roots = resolve_prior(sources)

    manifest = json.loads((args.current_surface / "manifest.json").read_text())
    current = current_units if current_units is not None else load_surface(args.current_surface)
    human = [u for u in current if u.get("batch") is not None]

    key_by_id = {u["id"]: identity_of(u) for u in current}

    carried = []
    kinds = collections.Counter()

    def carry(unit, record, source):
        provenance = f"[carried {record['unit']}@{source}, verdicted {record['at'][:10]}]"
        note = cap_markers(f"{provenance} {record['note']}".strip())
        carried.append({"unit": unit["id"], "verdict": record["verdict"], "note": note, "at": record["at"]})
        kinds[record["verdict"]] += 1

    unhit = []
    for unit in human:
        hit = prior.get(key_by_id[unit["id"]])
        if hit is None:
            unhit.append(unit)
            continue
        record, source, _prior_unit = hit
        if record["verdict"] == "skip":
            continue
        carry(unit, record, source)

    current_keys = set(key_by_id.values())
    stranded = [
        (record, source, unit)
        for key, (record, source, unit) in prior.items()
        if key not in current_keys and record["verdict"] != "skip"
    ]
    if stranded and unhit:
        current_comparator = surface_comparator(args.current_surface)
        prior_comparators = {name: surface_comparator(root) for name, root in surface_roots.items()}
        stranded_by_ink = collections.defaultdict(list)
        for name, root in surface_roots.items():
            for record, source, unit in stranded_units(root, [s for s in stranded if s[1] == name]):
                if unit is None:
                    continue
                key = ink_key(prior_comparators[source], unit)
                if key is not None:
                    stranded_by_ink[key].append((record, source))
        ink_carried = 0
        conflicts = []
        for unit in unhit:
            key = ink_key(current_comparator, unit)
            matches = stranded_by_ink.get(key) if key is not None else None
            if not matches:
                continue
            if len({record["verdict"] for record, _ in matches}) > 1:
                conflicts.append((unit["id"], matches))
                continue
            record, source = max(matches, key=lambda match: match[0]["at"])
            carry(unit, record, source)
            ink_carried += 1
        print(f"ink fallback: {ink_carried} verdicts carried onto re-keyed (merged) units")
        if conflicts:
            print(
                f"{len(conflicts)} merged units had conflicting prior verdicts and were left unverdicted for a fresh look:"
            )
            for unit_id, matches in conflicts:
                sides = "; ".join(
                    f"{record['unit']}@{source}={record['verdict']}" for record, source in matches
                )
                print(f"  {unit_id} <- {sides}")

    carried.sort(key=lambda r: r["unit"])
    payload = {
        "format": "ams-review-verdicts/1",
        "manifest_generated_at": manifest["generated_at"],
        "exported_at": manifest["generated_at"],
        "verdicts": carried,
    }
    out = pathlib.Path(args.out)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(f"wrote {out.name}: {len(carried)} carried onto manifest {manifest['generated_at']}")
    print(f"kinds: {dict(kinds)}")
    print(f"human queue: {len(human)} -> {len(human) - len(carried)} still needing fresh verdicts")
    return 0


if __name__ == "__main__":
    main()
