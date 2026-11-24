//! The manifest, written once the write has committed and the fonts are copied: its dict in a fixed key order ([`manifest`]), its `machine_approved` record ([`machine_approved_meta`]) and its `ledger_classes` lookup ([`ledger_classes_meta`]), written as indented ASCII JSON through a staging file ([`write_json`]); then the shards the manifest does not name deleted ([`prune_orphan_shards`]) and the line that names them ([`pruned_line`]). Its identity is the scan's ([`super::scan::manifest_identity`]).

use std::collections::HashMap;
use std::fs;
use std::io::Write;
use std::path::Path;

use super::console;
use super::input::{BuildInput, json_map_py};
use super::scan::class_shards;
use super::store::UnitStore;
use super::table::UnitTable;
use super::write::Written;
use super::{BuildError, failed};
use crate::pyfmt::{PyValue, json};
use crate::wire::{Result, WireError};

/// The manifest's mode.
const MODE: &str = "m1-audit";

/// The names the manifest reads from the build input's vocabulary: its format (`build.MANIFEST_FORMAT`).
#[derive(Clone, Debug)]
pub struct ManifestNames {
    pub format: PyValue,
}

impl ManifestNames {
    /// The names as the build input's vocabulary gives them.
    pub fn of(input: &BuildInput) -> Result<Self> {
        let constant = |name: &str| {
            input
                .constant(name)
                .ok_or_else(|| WireError(format!("the build input's vocabulary holds no {name}")))
        };
        Ok(Self {
            format: constant("rebuild.review.build.MANIFEST_FORMAT")?.clone(),
        })
    }
}

fn text(value: &str) -> PyValue {
    PyValue::str(value)
}

fn dict(items: Vec<(&str, PyValue)>) -> PyValue {
    PyValue::Dict(
        items
            .into_iter()
            .map(|(key, value)| (text(key), value))
            .collect(),
    )
}

/// A dict of `pairs` as Python builds one by assignment: a repeated key keeps its first place with its last value.
fn assigned(pairs: impl IntoIterator<Item = (String, PyValue)>) -> PyValue {
    let mut entries: Vec<(PyValue, PyValue)> = Vec::new();
    let mut place: HashMap<String, usize> = HashMap::new();
    for (key, value) in pairs {
        match place.get(&key) {
            Some(&at) => entries[at].1 = value,
            None => {
                place.insert(key.clone(), entries.len());
                entries.push((PyValue::Str(key), value));
            }
        }
    }
    PyValue::Dict(entries)
}

/// `methods[name]`, the verification method the build input names for a machine check.
fn method<'m>(
    methods: &'m [(String, String)],
    name: &str,
) -> std::result::Result<&'m str, BuildError> {
    methods
        .iter()
        .find(|(held, _)| held == name)
        .map(|(_, method)| method.as_str())
        .ok_or_else(|| failed(format!("KeyError: '{name}'")))
}

/// The unit and row totals across the machine checks, the units by class in the order `machine_units` first names each class, and one record a machine check with its counts and verification method, the Junior check's with the Junior font's source and digest. `machine_units` gives each machine-approved unit's class, row count and check, in triage order; `methods` holds each check's verification method by its name.
pub fn machine_approved_meta<'u>(
    machine_units: impl IntoIterator<Item = (&'u str, i64, &'u str)>,
    methods: &[(String, String)],
    junior: (&str, &str),
) -> std::result::Result<PyValue, BuildError> {
    let names = ["ink_identical", "picture_identical", "junior_equivalent"];
    let mut counts = [(0i64, 0i64); 3];
    let mut by_class: Vec<(String, i64)> = Vec::new();
    let mut class_place: HashMap<&str, usize> = HashMap::new();
    let (mut units, mut rows) = (0i64, 0i64);
    for (class_id, row_count, check) in machine_units {
        units += 1;
        match class_place.get(class_id) {
            Some(&at) => by_class[at].1 += 1,
            None => {
                class_place.insert(class_id, by_class.len());
                by_class.push((class_id.to_owned(), 1));
            }
        }
        rows += row_count;
        let at = names
            .iter()
            .position(|&name| name == check)
            .ok_or_else(|| failed(format!("KeyError: '{check}'")))?;
        counts[at].0 += 1;
        counts[at].1 += row_count;
    }
    let ink = method(methods, "ink_identical")?;
    let check = |at: usize, method: &str| {
        vec![
            ("units", PyValue::Int(counts[at].0)),
            ("rows", PyValue::Int(counts[at].1)),
            ("method", text(method)),
        ]
    };
    let mut junior_check = check(2, method(methods, "junior_equivalent")?);
    junior_check.push((
        "junior_font",
        dict(vec![("source", text(junior.0)), ("sha256", text(junior.1))]),
    ));
    Ok(dict(vec![
        ("units", PyValue::Int(units)),
        ("rows", PyValue::Int(rows)),
        ("method", text(ink)),
        (
            "by_class",
            PyValue::Dict(
                by_class
                    .into_iter()
                    .map(|(class_id, count)| (PyValue::Str(class_id), PyValue::Int(count)))
                    .collect(),
            ),
        ),
        (
            "checks",
            dict(vec![
                ("ink_identical", dict(check(0, ink))),
                (
                    "picture_identical",
                    dict(check(1, method(methods, "picture_identical")?)),
                ),
                ("junior_equivalent", dict(junior_check)),
            ]),
        ),
    ]))
}

/// Every ledger entry's title, summary, status and why by its id, in ledger order.
pub fn ledger_classes_meta(input: &BuildInput) -> PyValue {
    assigned(input.ledger.iter().map(|entry| {
        (
            entry.id.clone(),
            dict(vec![
                ("title", text(&entry.title)),
                ("summary", text(&entry.summary)),
                ("status", text(&entry.status)),
                ("why", text(&entry.why)),
            ]),
        )
    }))
}

/// What the manifest takes from the build beside the input and the write: the table and the store over every unit, the triage order, the audit's row count, the batch and duplicate-group counts, the secondary-junction counts in their dict's order, and the classes' ids in manifest order.
pub struct ManifestParts<'p> {
    pub table: &'p UnitTable,
    pub store: &'p UnitStore,
    pub order: &'p [u32],
    pub row_total: usize,
    pub batches: u32,
    pub duplicate_groups: usize,
    pub junctions: Vec<(&'static str, i64)>,
    pub classes: Vec<&'p str>,
}

/// The manifest, in its fixed key order: the values the build input carries, the fonts' records the write copied, the human units' ids along the triage order, the totals, the `machine_approved` record over the machine-approved units in triage order ([`machine_approved_meta`]), the secondary-junction counts, each class's record the write filled (`written`'s, by id in shard order) taken in manifest order, and the `ledger_classes` lookup ([`ledger_classes_meta`]).
pub fn manifest(
    input: &BuildInput,
    names: &ManifestNames,
    parts: &ManifestParts<'_>,
    shard_ids: &[&str],
    written: &Written<'_>,
) -> std::result::Result<PyValue, BuildError> {
    let given = &input.manifest;
    let (table, store) = (parts.table, parts.store);
    let metas: HashMap<&str, &PyValue> = shard_ids.iter().copied().zip(&written.metas).collect();
    let classes = parts
        .classes
        .iter()
        .map(|&id| {
            metas
                .get(id)
                .map(|&meta| meta.clone())
                .ok_or_else(|| failed(format!("KeyError: '{id}'")))
        })
        .collect::<std::result::Result<Vec<_>, _>>()?;
    let machine_units = parts.order.iter().filter_map(|&ordinal| {
        let ordinal = ordinal as usize;
        store.machine_check(ordinal).map(|check| {
            (
                table.class_id(ordinal),
                i64::from(table.row_count(ordinal)),
                check,
            )
        })
    });
    let machine_approved = machine_approved_meta(
        machine_units,
        &given.verification_methods,
        (&given.junior_source, &given.junior_sha256),
    )?;
    Ok(dict(vec![
        ("format", names.format.clone()),
        ("mode", text(MODE)),
        ("generated_at", text(&given.generated_at)),
        ("repo_head", text(&given.repo_head)),
        (
            "inputs_fingerprint",
            json_map_py(&given.inputs_fingerprint).map_err(failed)?,
        ),
        (
            "source",
            dict(vec![
                ("audit", text(&given.source_audit)),
                ("ledger", text(&given.source_ledger)),
            ]),
        ),
        (
            "fonts",
            PyValue::Dict(
                written
                    .fonts
                    .iter()
                    .map(|(side, record)| (text(side), record.clone()))
                    .collect(),
            ),
        ),
        (
            "alphabet",
            dict(vec![
                ("migrated", PyValue::Int(given.migrated)),
                ("total", PyValue::Int(given.total)),
            ]),
        ),
        (
            "configs",
            PyValue::List(given.configs.iter().map(PyValue::str).collect()),
        ),
        (
            "feature_descriptions",
            assigned(
                given
                    .feature_descriptions
                    .iter()
                    .map(|(name, description)| (name.clone(), text(description))),
            ),
        ),
        ("batch_size", PyValue::Int(given.batch_size)),
        (
            "human_unit_ids",
            PyValue::List(
                parts
                    .order
                    .iter()
                    .map(|&ordinal| ordinal as usize)
                    .filter(|&ordinal| table.batch(ordinal).is_some())
                    .map(|ordinal| PyValue::Str(store.unit_id(ordinal)))
                    .collect(),
            ),
        ),
        (
            "totals",
            dict(vec![
                ("units", PyValue::Int(table.n as i64)),
                ("rows", PyValue::Int(parts.row_total as i64)),
                ("batches", PyValue::Int(i64::from(parts.batches))),
                (
                    "duplicate_groups",
                    PyValue::Int(parts.duplicate_groups as i64),
                ),
            ]),
        ),
        ("machine_approved", machine_approved),
        (
            "secondary_junctions",
            dict(
                parts
                    .junctions
                    .iter()
                    .map(|&(name, count)| (name, PyValue::Int(count)))
                    .collect(),
            ),
        ),
        ("classes", PyValue::List(classes)),
        ("ledger_classes", ledger_classes_meta(input)),
        ("build_command", text(&given.build_command)),
        ("serve_command", text(&given.serve_command)),
    ]))
}

/// A dict `value` as `json.dumps(value, indent=1, ensure_ascii=True)` and a newline, written to `path` through its `.partial` staging file, which is renamed into place, the parent directory made first. A staging file left by a failure is removed.
pub fn write_json(path: &Path, value: &PyValue) -> std::result::Result<(), BuildError> {
    let mut body = json::dumps_indent(value).map_err(failed)?;
    body.push('\n');
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(failed)?;
    }
    let staging = super::sink::staging_path(path);
    let written = fs::write(&staging, body.as_bytes()).and_then(|()| fs::rename(&staging, path));
    let _ = fs::remove_file(&staging);
    written.map_err(failed)
}

/// `build._prune_orphan_shards`: delete each file directly under the corpus's `units/` that `*.json` matches and no class entry of `manifest` names among its parts (by the part's basename, `unit_index.class_shards`), and return the deleted names sorted; none when there is no `units/`. A directory, a staged `.partial` file and anything below `units/` are left.
pub fn prune_orphan_shards(
    out_dir: &Path,
    manifest: &PyValue,
) -> std::result::Result<Vec<String>, BuildError> {
    let units_dir = out_dir.join("units");
    if !units_dir.is_dir() {
        return Ok(Vec::new());
    }
    let PyValue::Dict(entries) = manifest else {
        return Err(failed("TypeError: the manifest is not a dict"));
    };
    let Some((_, PyValue::List(classes))) = entries
        .iter()
        .find(|(key, _)| matches!(key, PyValue::Str(key) if key == "classes"))
    else {
        return Err(failed("KeyError: 'classes'"));
    };
    let mut keep = std::collections::HashSet::new();
    for meta in classes {
        for part in class_shards(meta).map_err(|failure| failed(format!("{failure:?}")))? {
            if let Some(name) = Path::new(&part).file_name() {
                keep.insert(name.to_os_string());
            }
        }
    }
    let mut removed = Vec::new();
    for entry in fs::read_dir(&units_dir).map_err(failed)? {
        let path = entry.map_err(failed)?.path();
        let Some(name) = path.file_name() else {
            continue;
        };
        if name.as_encoded_bytes().ends_with(b".json") && path.is_file() && !keep.contains(name) {
            fs::remove_file(&path).map_err(failed)?;
            removed.push(name.to_string_lossy().into_owned());
        }
    }
    removed.sort();
    Ok(removed)
}

/// The line the build prints once it has pruned shards.
pub fn pruned_line(pruned: &[String]) -> String {
    format!(
        "Pruned {} orphan shard(s): {}",
        pruned.len(),
        pruned.join(", ")
    )
}

/// Write the manifest under `out_dir` ([`write_json`]), prune the shards it no longer names ([`prune_orphan_shards`]) and, when any were, say so on `console` ([`pruned_line`]); return the pruned names.
pub fn write_manifest(
    out_dir: &Path,
    manifest: &PyValue,
    console: &mut impl Write,
) -> std::result::Result<Vec<String>, BuildError> {
    write_json(&out_dir.join("manifest.json"), manifest)?;
    let pruned = prune_orphan_shards(out_dir, manifest)?;
    if !pruned.is_empty() {
        console::say(console, &pruned_line(&pruned)).map_err(failed)?;
    }
    Ok(pruned)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scratch(name: &str) -> std::path::PathBuf {
        let directory =
            std::env::temp_dir().join(format!("ams-review-manifest-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&directory);
        fs::create_dir_all(&directory).expect("scratch");
        directory
    }

    #[test]
    fn the_manifest_is_written_indented_with_a_final_newline_and_its_orphans_pruned_and_named() {
        let directory = scratch("write");
        let units = directory.join("units");
        fs::create_dir_all(&units).expect("units");
        for name in ["a.json", "a.000.json", "gone.json", "a.x000.json.partial"] {
            fs::write(units.join(name), b"[]\n").expect("part");
        }
        let manifest = dict(vec![
            ("note", text("\u{b7}Pea")),
            (
                "classes",
                PyValue::List(vec![dict(vec![
                    ("id", text("a")),
                    ("shards", PyValue::List(vec![text("units/a.json")])),
                ])]),
            ),
        ]);
        let mut console = Vec::new();
        let pruned = write_manifest(&directory, &manifest, &mut console).expect("written");
        assert_eq!(pruned, ["a.000.json", "gone.json"]);
        assert_eq!(
            String::from_utf8(console).expect("text"),
            "Pruned 2 orphan shard(s): a.000.json, gone.json\n"
        );
        assert_eq!(
            fs::read_to_string(directory.join("manifest.json")).expect("manifest"),
            "{\n \"note\": \"\\u00b7Pea\",\n \"classes\": [\n  {\n   \"id\": \"a\",\n   \"shards\": [\n    \"units/a.json\"\n   ]\n  }\n ]\n}\n"
        );
        assert!(!directory.join("manifest.json.partial").exists());
        assert!(units.join("a.x000.json.partial").exists());
        let _ = fs::remove_dir_all(&directory);
    }

    #[test]
    fn the_machine_approved_record_counts_each_check_and_keeps_classes_in_first_appearance_order() {
        let methods = [
            ("ink_identical".to_owned(), "ink".to_owned()),
            ("picture_identical".to_owned(), "picture".to_owned()),
            ("junior_equivalent".to_owned(), "junior".to_owned()),
        ];
        let record = machine_approved_meta(
            [
                ("b", 2, "ink_identical"),
                ("a", 3, "junior_equivalent"),
                ("b", 1, "ink_identical"),
            ],
            &methods,
            ("junior.otf", "f00"),
        )
        .expect("record");
        assert_eq!(
            json::dumps(&record).expect("json"),
            r#"{"units": 3, "rows": 6, "method": "ink", "by_class": {"b": 2, "a": 1}, "checks": {"ink_identical": {"units": 2, "rows": 3, "method": "ink"}, "picture_identical": {"units": 0, "rows": 0, "method": "picture"}, "junior_equivalent": {"units": 1, "rows": 3, "method": "junior", "junior_font": {"source": "junior.otf", "sha256": "f00"}}}}"#
        );
    }
}
