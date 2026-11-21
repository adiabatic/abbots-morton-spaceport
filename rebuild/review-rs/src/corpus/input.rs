//! The build input, `ams-review-build/1` (`rebuild/CORPUS-PARENT-PORT-PLAN.md` § The build input), with `rebuild/review/corpus_exec.py` as its Python half: every value `build._rust_inputs` computes for one build, read as strictly as [`crate::wire`] reads the units init ([`BuildInput`]), and the audit's parts the process reads through the descriptors it inherits ([`BuildInput::held_audit`]). Rust computes none of these values; every stamp, digest and path is Python's.
//!
//! [`stage_build_input`] is the `build-input` stage of the stage fixtures: it writes each value back as the Python value `_rust_inputs` holds, in the typed encoding, and the size of each held audit part.

use std::fs::File;
use std::io::Write;
use std::path::Path;

use ams_m1_kernel::emit::json_string;
use serde_json::{Map, Value};

use crate::pyfmt::PyValue;
use crate::wire::{
    Result, WireError, array, boolean, error, integer, object, string, strings, tuple,
};

/// The build input's format marker, `corpus_exec.INPUT_FORMAT`.
pub const INPUT_FORMAT: &str = "ams-review-build/1";

/// The vocabulary names the build reads, each a constant `corpus_exec.VOCABULARY` names; an input that lacks one is refused.
pub const VOCABULARY: &[&str] = &[
    "rebuild.pipeline.row_delta.AUDIT_SEGMENT_CAP",
    "rebuild.pipeline.row_delta.CONTENT_TIME_STAMP",
    "rebuild.pipeline.row_delta.FOLD_OVERRUN",
    "rebuild.pipeline.row_delta.SEGMENT_CAP",
    "rebuild.review.app_index.APP_INDEX_FORMAT",
    "rebuild.review.app_index.APP_INDEX_NAME",
    "rebuild.review.app_index.LOCATOR_BLOCK_ROWS",
    "rebuild.review.app_index.LOCATOR_FORMAT",
    "rebuild.review.app_index.LOCATOR_NAME",
    "rebuild.review.app_index.LOCATOR_ROWS_NAME",
    "rebuild.review.audit.ACCEPTANCE_CONFIGS",
    "rebuild.review.audit.AUDIT_HEADER",
    "rebuild.review.audit.INPUT_KEY_BYTES",
    "rebuild.review.audit.LEDGER_STATUSES",
    "rebuild.review.audit.MACHINE_CHECKS",
    "rebuild.review.audit.RULED_STATUSES",
    "rebuild.review.audit.SECTIONS",
    "rebuild.review.audit.UNMATCHED_CLASS",
    "rebuild.review.build.CONTRACT_ERRORS_SHOWN",
    "rebuild.review.build.KEY_ONLY_PROCESSES",
    "rebuild.review.build.MANIFEST_FORMAT",
    "rebuild.review.build.PHASE1_HANDOUT_UNITS",
    "rebuild.review.build.VERIFICATION_SAMPLE",
    "rebuild.review.unit_cache.MANIFEST_MOVED_NOTE",
    "rebuild.review.unit_cache.NO_STORE_NOTE",
    "rebuild.review.unit_cache.REDERIVING_LABELS",
    "rebuild.review.unit_cache.SIGNATURE_STORE_FORMAT",
    "rebuild.review.unit_cache.SIGNATURE_STORE_NAME",
    "rebuild.review.unit_cache.STORE_FORMAT",
    "rebuild.review.unit_cache.STORE_NAME",
    "rebuild.review.unit_cache.UNREADABLE_NOTE",
    "rebuild.review.unit_index.ASSET_COMPONENTS",
    "rebuild.review.unit_index.INDEX_DEFLATE_THREADS",
    "rebuild.review.unit_index.INDEX_FORMAT",
    "rebuild.review.unit_index.INDEX_MEMBER_BYTES",
    "rebuild.review.unit_index.INDEX_NAME",
    "rebuild.review.unit_index.PART_CLOSING",
    "rebuild.review.unit_index.SHARD_PART_BYTES",
];

/// The build input's fields in order, `corpus_exec.INPUT_FIELDS`.
pub const INPUT_FIELDS: &[&str] = &[
    "format",
    "units_init",
    "out_dir",
    "scratch",
    "audit",
    "ledger",
    "family_of",
    "family_keys",
    "family_keys_digest",
    "change_groups",
    "environment",
    "signature_environment",
    "manifest",
    "fonts",
    "static",
    "repo_root",
    "recompute_all",
    "widths",
    "cycle",
    "vocabulary",
];

/// One gzip member of an audit part held as members, as the head's member index records it (`row_delta.Member`).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Member {
    pub section: String,
    pub length: i64,
    pub inflated: i64,
}

/// The audit as the Python build holds it (`audit.AuditParts`): its path, for messages; the descriptors the process inherits, base first, or None in an input no process inherits; the head document Python read, or None for a single file; each part's member index, None for a plain part; the identity the signature store records (`unit_cache.audit_identity`); and the segment rows and the parts held as members, for the load's note.
#[derive(Clone, Debug, PartialEq)]
pub struct AuditInput {
    pub path: String,
    pub descriptors: Option<Vec<i64>>,
    pub head: Option<Map<String, Value>>,
    pub members: Vec<Option<Vec<Member>>>,
    pub identity: Vec<i64>,
    pub segment_rows: i64,
    pub compressed: i64,
}

/// One divergence-ledger entry (`audit.LedgerClass`), its exemplar keys `(config, codepoints)` sorted.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct LedgerEntry {
    pub id: String,
    pub status: String,
    pub why: String,
    pub ink_identical: bool,
    pub no_verdict: bool,
    pub exemplar_keys: Vec<(String, String)>,
    pub title: String,
    pub summary: String,
    pub note: String,
}

/// One change group's copy (`change_groups.ChangeGroupProse`).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct GroupProse {
    pub title: String,
    pub summary: String,
    pub why: String,
}

/// A whole-store stamp as Python computed it: its value, its `label\tdigest` lines, the lines behind each detailed label, and the header fields its store writes (`unit_cache._stamp_header`).
#[derive(Clone, Debug, PartialEq)]
pub struct Stamp {
    pub value: String,
    pub lines: Vec<String>,
    pub detail: Vec<(String, Vec<String>)>,
    pub header: Map<String, Value>,
}

/// One font's record in the manifest, as `_copy_font` returns it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct FontRecord {
    pub file: String,
    pub family: String,
    pub source: String,
    pub sha256: String,
    pub upem: i64,
}

/// The manifest's values Python computes before the build (`_write_corpus`'s literal, but for what the build itself counts and orders).
#[derive(Clone, Debug, PartialEq)]
pub struct ManifestInput {
    pub generated_at: String,
    pub repo_head: String,
    pub inputs_fingerprint: Map<String, Value>,
    pub source_audit: String,
    pub source_ledger: String,
    pub fonts: Vec<(String, FontRecord)>,
    pub migrated: i64,
    pub total: i64,
    pub configs: Vec<String>,
    pub feature_descriptions: Vec<(String, String)>,
    pub batch_size: i64,
    pub junior_source: String,
    pub junior_sha256: String,
    pub verification_methods: Vec<(String, String)>,
    pub build_command: String,
    pub serve_command: String,
}

/// One font the write copies into the corpus: `_copy_font`'s source, target name, family and the digest taken at load.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct FontCopy {
    pub source: String,
    pub name: String,
    pub family: String,
    pub sha256: String,
}

/// The widths the build runs at.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Widths {
    pub threads: i64,
    pub gates_idle_threads: Option<i64>,
    pub gates_idle_marker: Option<String>,
    pub signature_threads: i64,
    pub write_threads: Option<i64>,
}

/// What the Python build reads from the environment: the cycle's run, the file that holds a cycle's fold claim, and the output-counts file.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Cycle {
    pub run: Option<String>,
    pub fold_claim: String,
    pub counts: Option<String>,
}

/// The build input, `ams-review-build/1`.
#[derive(Clone, Debug, PartialEq)]
pub struct BuildInput {
    pub units_init: String,
    pub out_dir: String,
    pub scratch: String,
    pub audit: AuditInput,
    pub ledger_path: String,
    pub ledger: Vec<LedgerEntry>,
    pub family_of: Vec<(i64, String)>,
    pub family_keys: Vec<(String, String)>,
    pub family_keys_digest: String,
    pub group_order: Vec<String>,
    pub group_prose: Vec<(String, GroupProse)>,
    pub environment: Stamp,
    pub signature_environment: Stamp,
    pub manifest: ManifestInput,
    pub fonts: Vec<(String, FontCopy)>,
    pub static_files: Vec<(String, String)>,
    pub fallback_index: String,
    pub repo_root: String,
    pub recompute_all: bool,
    pub widths: Widths,
    pub cycle: Cycle,
    pub vocabulary: Vec<(String, PyValue)>,
}

fn optional_string(value: &Value, what: &str) -> Result<Option<String>> {
    match value {
        Value::Null => Ok(None),
        _ => string(value, what).map(Some),
    }
}

fn optional_integer(value: &Value, what: &str) -> Result<Option<i64>> {
    match value {
        Value::Null => Ok(None),
        _ => integer(value, what).map(Some),
    }
}

fn dict<'a>(value: &'a Value, what: &str) -> Result<&'a Map<String, Value>> {
    match value {
        Value::Object(map) => Ok(map),
        _ => error(format!("{what} is not a JSON object: {value}")),
    }
}

fn string_map(value: &Value, what: &str) -> Result<Vec<(String, String)>> {
    dict(value, what)?
        .iter()
        .map(|(key, item)| Ok((key.clone(), string(item, what)?)))
        .collect()
}

fn string_pairs(value: &Value, what: &str) -> Result<Vec<(String, String)>> {
    array(value, what)?
        .iter()
        .map(|item| {
            let [left, right] = tuple(item, what)?;
            Ok((string(left, what)?, string(right, what)?))
        })
        .collect()
}

/// A JSON value as Python's `json.loads` gives it: an object a dict of `str` keys in its order, an array a list, and every number an int; anything else, a float among them, is refused.
fn json_py(value: &Value) -> Result<PyValue> {
    Ok(match value {
        Value::Null => PyValue::None,
        Value::Bool(flag) => PyValue::Bool(*flag),
        Value::Number(number) => {
            PyValue::Int(number.as_i64().ok_or_else(|| {
                WireError(format!("{number} is not an integer that fits 64 bits"))
            })?)
        }
        Value::String(text) => PyValue::str(text),
        Value::Array(items) => PyValue::List(items.iter().map(json_py).collect::<Result<_>>()?),
        Value::Object(map) => PyValue::Dict(
            map.iter()
                .map(|(key, item)| Ok((PyValue::str(key), json_py(item)?)))
                .collect::<Result<_>>()?,
        ),
    })
}

pub(crate) fn json_map_py(map: &Map<String, Value>) -> Result<PyValue> {
    json_py(&Value::Object(map.clone()))
}

fn entries(items: Vec<(&str, PyValue)>) -> PyValue {
    PyValue::Dict(
        items
            .into_iter()
            .map(|(key, value)| (PyValue::str(key), value))
            .collect(),
    )
}

fn str_dict(pairs: &[(String, String)]) -> PyValue {
    PyValue::Dict(
        pairs
            .iter()
            .map(|(key, value)| (PyValue::str(key), PyValue::str(value)))
            .collect(),
    )
}

fn optional_int(value: Option<i64>) -> PyValue {
    value.map_or(PyValue::None, PyValue::Int)
}

impl BuildInput {
    /// Read the build input at `path`.
    pub fn read(path: &Path) -> Result<Self> {
        let text = std::fs::read_to_string(path).map_err(|failure| {
            WireError(format!(
                "cannot read the build input {}: {failure}",
                path.display()
            ))
        })?;
        Self::parse(&text)
    }

    /// Parse a build input's text, refusing another format, an object with a missing, an extra or a mistyped field, and an input that lacks a [`VOCABULARY`] name. Every value is checked here, so [`BuildInput::to_py`] cannot fail.
    pub fn parse(text: &str) -> Result<Self> {
        let value: Value = serde_json::from_str(text)
            .map_err(|failure| WireError(format!("the build input is not JSON: {failure}")))?;
        let map = object(&value, "the build input", INPUT_FIELDS)?;
        let format = string(&map["format"], "the build input's format")?;
        if format != INPUT_FORMAT {
            return error(format!("the build input is {format:?}, not {INPUT_FORMAT}"));
        }
        let vocabulary = array(&map["vocabulary"], "the build input's vocabulary")?
            .iter()
            .map(|item| {
                let [name, value] = tuple(item, "a vocabulary entry")?;
                Ok((
                    string(name, "a vocabulary name")?,
                    PyValue::from_typed(value).map_err(WireError)?,
                ))
            })
            .collect::<Result<Vec<_>>>()?;
        let lacking: Vec<&str> = VOCABULARY
            .iter()
            .copied()
            .filter(|name| !vocabulary.iter().any(|(held, _)| held == name))
            .collect();
        if !lacking.is_empty() {
            return error(format!("the build input's vocabulary lacks {lacking:?}"));
        }
        let ledger = object(&map["ledger"], "the ledger", &["path", "entries"])?;
        let groups = object(
            &map["change_groups"],
            "the change groups",
            &["order", "prose"],
        )?;
        let fixed = object(
            &map["static"],
            "the static files",
            &["files", "fallback_index"],
        )?;
        let input = Self {
            units_init: string(&map["units_init"], "the units init")?,
            out_dir: string(&map["out_dir"], "the output directory")?,
            scratch: string(&map["scratch"], "the scratch directory")?,
            audit: Self::audit(&map["audit"])?,
            ledger_path: string(&ledger["path"], "the ledger's path")?,
            ledger: array(&ledger["entries"], "the ledger's entries")?
                .iter()
                .map(Self::ledger_entry)
                .collect::<Result<_>>()?,
            family_of: array(&map["family_of"], "the families")?
                .iter()
                .map(|item| {
                    let [codepoint, family] = tuple(item, "a code point's family")?;
                    let codepoint = integer(codepoint, "a family's code point")?;
                    if u32::try_from(codepoint)
                        .ok()
                        .and_then(char::from_u32)
                        .is_none()
                    {
                        return error(format!("{codepoint} is not a code point"));
                    }
                    Ok((codepoint, string(family, "a code point's family")?))
                })
                .collect::<Result<_>>()?,
            family_keys: string_map(&map["family_keys"], "the family keys")?,
            family_keys_digest: string(&map["family_keys_digest"], "the family keys' digest")?,
            group_order: strings(&groups["order"], "the change groups' order")?,
            group_prose: array(&groups["prose"], "the change groups' prose")?
                .iter()
                .map(|item| {
                    let [group, prose] = tuple(item, "a change group's prose")?;
                    let prose = object(
                        prose,
                        "a change group's prose",
                        &["title", "summary", "why"],
                    )?;
                    Ok((
                        string(group, "a change group")?,
                        GroupProse {
                            title: string(&prose["title"], "a change group's title")?,
                            summary: string(&prose["summary"], "a change group's summary")?,
                            why: string(&prose["why"], "a change group's why")?,
                        },
                    ))
                })
                .collect::<Result<_>>()?,
            environment: Self::stamp(&map["environment"], "the unit store's stamp")?,
            signature_environment: Self::stamp(
                &map["signature_environment"],
                "the ink-signature store's stamp",
            )?,
            manifest: Self::manifest(&map["manifest"])?,
            fonts: dict(&map["fonts"], "the fonts")?
                .iter()
                .map(|(side, copy)| {
                    let copy = object(
                        copy,
                        "a font's copy",
                        &["source", "name", "family", "sha256"],
                    )?;
                    Ok((
                        side.clone(),
                        FontCopy {
                            source: string(&copy["source"], "a font's source")?,
                            name: string(&copy["name"], "a font's name")?,
                            family: string(&copy["family"], "a font's family")?,
                            sha256: string(&copy["sha256"], "a font's digest")?,
                        },
                    ))
                })
                .collect::<Result<_>>()?,
            static_files: string_pairs(&fixed["files"], "a static file")?,
            fallback_index: string(&fixed["fallback_index"], "the fallback index")?,
            repo_root: string(&map["repo_root"], "the repository root")?,
            recompute_all: boolean(&map["recompute_all"], "recompute_all")?,
            widths: {
                let widths = object(
                    &map["widths"],
                    "the widths",
                    &[
                        "threads",
                        "gates_idle_threads",
                        "gates_idle_marker",
                        "signature_threads",
                        "write_threads",
                    ],
                )?;
                Widths {
                    threads: integer(&widths["threads"], "the threads")?,
                    gates_idle_threads: optional_integer(
                        &widths["gates_idle_threads"],
                        "the gates-idle threads",
                    )?,
                    gates_idle_marker: optional_string(
                        &widths["gates_idle_marker"],
                        "the gates-idle marker",
                    )?,
                    signature_threads: integer(
                        &widths["signature_threads"],
                        "the signature threads",
                    )?,
                    write_threads: optional_integer(&widths["write_threads"], "the write threads")?,
                }
            },
            cycle: {
                let cycle = object(&map["cycle"], "the cycle", &["run", "fold_claim", "counts"])?;
                Cycle {
                    run: optional_string(&cycle["run"], "the cycle's run")?,
                    fold_claim: string(&cycle["fold_claim"], "the fold claim's file")?,
                    counts: optional_string(&cycle["counts"], "the output-counts file")?,
                }
            },
            vocabulary,
        };
        if input
            .fonts
            .iter()
            .map(|(side, _)| side.as_str())
            .ne(["before", "after"])
        {
            return error("the fonts are not before and after, in that order");
        }
        if let Some(descriptors) = &input.audit.descriptors
            && descriptors.len() != input.audit.members.len()
        {
            return error(format!(
                "the audit holds {} descriptors for {} parts",
                descriptors.len(),
                input.audit.members.len()
            ));
        }
        for map in [
            &input.environment.header,
            &input.signature_environment.header,
            &input.manifest.inputs_fingerprint,
        ]
        .into_iter()
        .chain(&input.audit.head)
        {
            json_map_py(map)?;
        }
        Ok(input)
    }

    fn audit(value: &Value) -> Result<AuditInput> {
        let audit = object(
            value,
            "the audit",
            &[
                "path",
                "descriptors",
                "head",
                "members",
                "identity",
                "segment_rows",
                "compressed",
            ],
        )?;
        Ok(AuditInput {
            path: string(&audit["path"], "the audit's path")?,
            descriptors: match &audit["descriptors"] {
                Value::Null => None,
                descriptors => Some(
                    array(descriptors, "the audit's descriptors")?
                        .iter()
                        .map(|descriptor| integer(descriptor, "an audit descriptor"))
                        .collect::<Result<_>>()?,
                ),
            },
            head: match &audit["head"] {
                Value::Null => None,
                head => Some(dict(head, "the audit's head")?.clone()),
            },
            members: array(&audit["members"], "the audit's member indexes")?
                .iter()
                .map(|members| match members {
                    Value::Null => Ok(None),
                    members => array(members, "a part's member index")?
                        .iter()
                        .map(|member| {
                            let [section, length, inflated] = tuple(member, "a member")?;
                            Ok(Member {
                                section: string(section, "a member's section")?,
                                length: integer(length, "a member's length")?,
                                inflated: integer(inflated, "a member's inflated length")?,
                            })
                        })
                        .collect::<Result<_>>()
                        .map(Some),
                })
                .collect::<Result<_>>()?,
            identity: array(&audit["identity"], "the audit's identity")?
                .iter()
                .map(|item| integer(item, "the audit's identity"))
                .collect::<Result<_>>()?,
            segment_rows: integer(&audit["segment_rows"], "the audit's segment rows")?,
            compressed: integer(&audit["compressed"], "the audit's compressed parts")?,
        })
    }

    fn ledger_entry(value: &Value) -> Result<LedgerEntry> {
        let entry = object(
            value,
            "a ledger entry",
            &[
                "id",
                "status",
                "why",
                "ink_identical",
                "no_verdict",
                "exemplar_keys",
                "title",
                "summary",
                "note",
            ],
        )?;
        Ok(LedgerEntry {
            id: string(&entry["id"], "a ledger entry's id")?,
            status: string(&entry["status"], "a ledger entry's status")?,
            why: string(&entry["why"], "a ledger entry's why")?,
            ink_identical: boolean(&entry["ink_identical"], "a ledger entry's ink_identical")?,
            no_verdict: boolean(&entry["no_verdict"], "a ledger entry's no_verdict")?,
            exemplar_keys: string_pairs(&entry["exemplar_keys"], "an exemplar key")?,
            title: string(&entry["title"], "a ledger entry's title")?,
            summary: string(&entry["summary"], "a ledger entry's summary")?,
            note: string(&entry["note"], "a ledger entry's note")?,
        })
    }

    fn stamp(value: &Value, what: &str) -> Result<Stamp> {
        let stamp = object(value, what, &["value", "lines", "detail", "header"])?;
        Ok(Stamp {
            value: string(&stamp["value"], what)?,
            lines: strings(&stamp["lines"], what)?,
            detail: array(&stamp["detail"], what)?
                .iter()
                .map(|item| {
                    let [label, lines] = tuple(item, what)?;
                    Ok((string(label, what)?, strings(lines, what)?))
                })
                .collect::<Result<_>>()?,
            header: dict(&stamp["header"], what)?.clone(),
        })
    }

    fn manifest(value: &Value) -> Result<ManifestInput> {
        let manifest = object(
            value,
            "the manifest's values",
            &[
                "generated_at",
                "repo_head",
                "inputs_fingerprint",
                "source",
                "fonts",
                "alphabet",
                "configs",
                "feature_descriptions",
                "batch_size",
                "junior_font",
                "verification_methods",
                "build_command",
                "serve_command",
            ],
        )?;
        let source = object(
            &manifest["source"],
            "the manifest's source",
            &["audit", "ledger"],
        )?;
        let alphabet = object(
            &manifest["alphabet"],
            "the manifest's alphabet",
            &["migrated", "total"],
        )?;
        let junior = object(
            &manifest["junior_font"],
            "the manifest's Junior font",
            &["source", "sha256"],
        )?;
        let fonts = dict(&manifest["fonts"], "the manifest's fonts")?
            .iter()
            .map(|(side, record)| {
                let record = object(
                    record,
                    "a manifest font record",
                    &["file", "family", "source", "sha256", "upem"],
                )?;
                Ok((
                    side.clone(),
                    FontRecord {
                        file: string(&record["file"], "a font record's file")?,
                        family: string(&record["family"], "a font record's family")?,
                        source: string(&record["source"], "a font record's source")?,
                        sha256: string(&record["sha256"], "a font record's digest")?,
                        upem: integer(&record["upem"], "a font record's upem")?,
                    },
                ))
            })
            .collect::<Result<Vec<_>>>()?;
        if fonts
            .iter()
            .map(|(side, _)| side.as_str())
            .ne(["before", "after"])
        {
            return error("the manifest's fonts are not before and after, in that order");
        }
        Ok(ManifestInput {
            generated_at: string(&manifest["generated_at"], "generated_at")?,
            repo_head: string(&manifest["repo_head"], "repo_head")?,
            inputs_fingerprint: dict(&manifest["inputs_fingerprint"], "the inputs fingerprint")?
                .clone(),
            source_audit: string(&source["audit"], "the manifest's audit source")?,
            source_ledger: string(&source["ledger"], "the manifest's ledger source")?,
            fonts,
            migrated: integer(&alphabet["migrated"], "the migrated letters")?,
            total: integer(&alphabet["total"], "the alphabet's letters")?,
            configs: strings(&manifest["configs"], "the manifest's configurations")?,
            feature_descriptions: string_map(
                &manifest["feature_descriptions"],
                "a feature description",
            )?,
            batch_size: integer(&manifest["batch_size"], "the batch size")?,
            junior_source: string(&junior["source"], "the Junior font's source")?,
            junior_sha256: string(&junior["sha256"], "the Junior font's digest")?,
            verification_methods: string_map(
                &manifest["verification_methods"],
                "a verification method",
            )?,
            build_command: string(&manifest["build_command"], "the build command")?,
            serve_command: string(&manifest["serve_command"], "the serve command")?,
        })
    }

    /// One vocabulary constant by its dotted name.
    pub fn constant(&self, name: &str) -> Option<&PyValue> {
        self.vocabulary
            .iter()
            .find(|(held, _)| held == name)
            .map(|(_, value)| value)
    }

    /// The audit's parts as files, base first, through the descriptors the process inherited ([`crate::ffi::inherited_files`]), which a process takes once.
    pub fn held_audit(&self) -> Result<Vec<File>> {
        let Some(descriptors) = &self.audit.descriptors else {
            return error("the build input passes no audit descriptors");
        };
        crate::ffi::inherited_files(descriptors)
            .map_err(|failure| WireError(format!("the audit {}: {failure}", self.audit.path)))
    }

    /// Each field's value as Python holds it in the dict `build._rust_inputs` returns, by field, in [`INPUT_FIELDS`] order; the audit's descriptors, which that dict does not hold, are left out.
    pub fn to_py(&self) -> Vec<(&'static str, PyValue)> {
        let checked = "the parse checked every JSON value";
        let audit = &self.audit;
        let manifest = &self.manifest;
        vec![
            ("format", PyValue::str(INPUT_FORMAT)),
            ("units_init", PyValue::str(&self.units_init)),
            ("out_dir", PyValue::str(&self.out_dir)),
            ("scratch", PyValue::str(&self.scratch)),
            (
                "audit",
                entries(vec![
                    ("path", PyValue::str(&audit.path)),
                    (
                        "head",
                        audit
                            .head
                            .as_ref()
                            .map_or(PyValue::None, |head| json_map_py(head).expect(checked)),
                    ),
                    (
                        "members",
                        PyValue::Tuple(
                            audit
                                .members
                                .iter()
                                .map(|members| {
                                    members.as_ref().map_or(PyValue::None, |members| {
                                        PyValue::Tuple(
                                            members
                                                .iter()
                                                .map(|member| {
                                                    PyValue::Record(
                                                        "Member".into(),
                                                        vec![
                                                            (
                                                                "section".into(),
                                                                PyValue::str(&member.section),
                                                            ),
                                                            (
                                                                "length".into(),
                                                                PyValue::Int(member.length),
                                                            ),
                                                            (
                                                                "inflated".into(),
                                                                PyValue::Int(member.inflated),
                                                            ),
                                                        ],
                                                    )
                                                })
                                                .collect(),
                                        )
                                    })
                                })
                                .collect(),
                        ),
                    ),
                    (
                        "identity",
                        PyValue::List(audit.identity.iter().copied().map(PyValue::Int).collect()),
                    ),
                    ("segment_rows", PyValue::Int(audit.segment_rows)),
                    ("compressed", PyValue::Int(audit.compressed)),
                ]),
            ),
            (
                "ledger",
                entries(vec![
                    ("path", PyValue::str(&self.ledger_path)),
                    (
                        "entries",
                        PyValue::List(self.ledger.iter().map(LedgerEntry::to_py).collect()),
                    ),
                ]),
            ),
            (
                "family_of",
                PyValue::Dict(
                    self.family_of
                        .iter()
                        .map(|(codepoint, family)| (PyValue::Int(*codepoint), PyValue::str(family)))
                        .collect(),
                ),
            ),
            ("family_keys", str_dict(&self.family_keys)),
            ("family_keys_digest", PyValue::str(&self.family_keys_digest)),
            (
                "change_groups",
                entries(vec![
                    ("order", PyValue::str_tuple(&self.group_order)),
                    (
                        "prose",
                        PyValue::Dict(
                            self.group_prose
                                .iter()
                                .map(|(group, prose)| {
                                    (
                                        PyValue::str(group),
                                        PyValue::Record(
                                            "ChangeGroupProse".into(),
                                            vec![
                                                ("title".into(), PyValue::str(&prose.title)),
                                                ("summary".into(), PyValue::str(&prose.summary)),
                                                ("why".into(), PyValue::str(&prose.why)),
                                            ],
                                        ),
                                    )
                                })
                                .collect(),
                        ),
                    ),
                ]),
            ),
            ("environment", self.environment.to_py()),
            ("signature_environment", self.signature_environment.to_py()),
            (
                "manifest",
                entries(vec![
                    ("generated_at", PyValue::str(&manifest.generated_at)),
                    ("repo_head", PyValue::str(&manifest.repo_head)),
                    (
                        "inputs_fingerprint",
                        json_map_py(&manifest.inputs_fingerprint).expect(checked),
                    ),
                    (
                        "source",
                        entries(vec![
                            ("audit", PyValue::str(&manifest.source_audit)),
                            ("ledger", PyValue::str(&manifest.source_ledger)),
                        ]),
                    ),
                    (
                        "fonts",
                        PyValue::Dict(
                            manifest
                                .fonts
                                .iter()
                                .map(|(side, record)| {
                                    (
                                        PyValue::str(side),
                                        entries(vec![
                                            ("file", PyValue::str(&record.file)),
                                            ("family", PyValue::str(&record.family)),
                                            ("source", PyValue::str(&record.source)),
                                            ("sha256", PyValue::str(&record.sha256)),
                                            ("upem", PyValue::Int(record.upem)),
                                        ]),
                                    )
                                })
                                .collect(),
                        ),
                    ),
                    (
                        "alphabet",
                        entries(vec![
                            ("migrated", PyValue::Int(manifest.migrated)),
                            ("total", PyValue::Int(manifest.total)),
                        ]),
                    ),
                    (
                        "configs",
                        PyValue::List(manifest.configs.iter().map(PyValue::str).collect()),
                    ),
                    (
                        "feature_descriptions",
                        str_dict(&manifest.feature_descriptions),
                    ),
                    ("batch_size", PyValue::Int(manifest.batch_size)),
                    (
                        "junior_font",
                        entries(vec![
                            ("source", PyValue::str(&manifest.junior_source)),
                            ("sha256", PyValue::str(&manifest.junior_sha256)),
                        ]),
                    ),
                    (
                        "verification_methods",
                        str_dict(&manifest.verification_methods),
                    ),
                    ("build_command", PyValue::str(&manifest.build_command)),
                    ("serve_command", PyValue::str(&manifest.serve_command)),
                ]),
            ),
            (
                "fonts",
                PyValue::Dict(
                    self.fonts
                        .iter()
                        .map(|(side, copy)| {
                            (
                                PyValue::str(side),
                                entries(vec![
                                    ("source", PyValue::str(&copy.source)),
                                    ("name", PyValue::str(&copy.name)),
                                    ("family", PyValue::str(&copy.family)),
                                    ("sha256", PyValue::str(&copy.sha256)),
                                ]),
                            )
                        })
                        .collect(),
                ),
            ),
            (
                "static",
                entries(vec![
                    (
                        "files",
                        PyValue::List(
                            self.static_files
                                .iter()
                                .map(|(relative, source)| {
                                    PyValue::Tuple(vec![
                                        PyValue::str(relative),
                                        PyValue::str(source),
                                    ])
                                })
                                .collect(),
                        ),
                    ),
                    ("fallback_index", PyValue::str(&self.fallback_index)),
                ]),
            ),
            ("repo_root", PyValue::str(&self.repo_root)),
            ("recompute_all", PyValue::Bool(self.recompute_all)),
            (
                "widths",
                entries(vec![
                    ("threads", PyValue::Int(self.widths.threads)),
                    (
                        "gates_idle_threads",
                        optional_int(self.widths.gates_idle_threads),
                    ),
                    (
                        "gates_idle_marker",
                        PyValue::optional_str(self.widths.gates_idle_marker.as_deref()),
                    ),
                    (
                        "signature_threads",
                        PyValue::Int(self.widths.signature_threads),
                    ),
                    ("write_threads", optional_int(self.widths.write_threads)),
                ]),
            ),
            (
                "cycle",
                entries(vec![
                    ("run", PyValue::optional_str(self.cycle.run.as_deref())),
                    ("fold_claim", PyValue::str(&self.cycle.fold_claim)),
                    (
                        "counts",
                        PyValue::optional_str(self.cycle.counts.as_deref()),
                    ),
                ]),
            ),
            (
                "vocabulary",
                PyValue::Dict(
                    self.vocabulary
                        .iter()
                        .map(|(name, value)| (PyValue::str(name), value.clone()))
                        .collect(),
                ),
            ),
        ]
    }
}

impl LedgerEntry {
    /// The entry as `audit.LedgerClass`, its exemplar keys a frozenset of `(config, codepoints)` tuples.
    pub fn to_py(&self) -> PyValue {
        PyValue::Record(
            "LedgerClass".into(),
            vec![
                ("id".into(), PyValue::str(&self.id)),
                ("status".into(), PyValue::str(&self.status)),
                ("why".into(), PyValue::str(&self.why)),
                ("ink_identical".into(), PyValue::Bool(self.ink_identical)),
                ("no_verdict".into(), PyValue::Bool(self.no_verdict)),
                (
                    "exemplar_keys".into(),
                    PyValue::Set(
                        self.exemplar_keys
                            .iter()
                            .map(|(config, codepoints)| {
                                PyValue::Tuple(vec![PyValue::str(config), PyValue::str(codepoints)])
                            })
                            .collect(),
                    ),
                ),
                ("title".into(), PyValue::str(&self.title)),
                ("summary".into(), PyValue::str(&self.summary)),
                ("note".into(), PyValue::str(&self.note)),
            ],
        )
    }
}

impl Stamp {
    /// The stamp as `build._stamp_values` gives it: its lines and detail as tuples, its header as the dict `unit_cache._stamp_header` returns.
    pub fn to_py(&self) -> PyValue {
        entries(vec![
            ("value", PyValue::str(&self.value)),
            ("lines", PyValue::str_tuple(&self.lines)),
            (
                "detail",
                PyValue::Tuple(
                    self.detail
                        .iter()
                        .map(|(label, lines)| {
                            PyValue::Tuple(vec![PyValue::str(label), PyValue::str_tuple(lines)])
                        })
                        .collect(),
                ),
            ),
            (
                "header",
                json_map_py(&self.header).expect("the parse checked the header"),
            ),
        ])
    }
}

fn stage_line(out: &mut impl Write, key: &str, value: &PyValue) -> Result<()> {
    writeln!(
        out,
        "{{\"key\":{},\"value\":{}}}",
        json_string(key),
        value.to_typed()
    )
    .map_err(|failure| WireError(format!("cannot write a stage line: {failure}")))
}

/// The `build-input` stage: read the build input at `input_path`, and write each field's value as Python holds it under `input:<field>` ([`BuildInput::to_py`]), then the size of each audit part, read through the inherited descriptors, under `audit.held`, as a tuple of ints.
pub fn stage_build_input(input_path: &Path, out: &mut impl Write) -> Result<()> {
    let input = BuildInput::read(input_path)?;
    for (field, value) in input.to_py() {
        stage_line(out, &format!("input:{field}"), &value)?;
    }
    let sizes = input
        .held_audit()?
        .iter()
        .map(|part| {
            let size = part
                .metadata()
                .map_err(|failure| WireError(format!("cannot stat an audit part: {failure}")))?
                .len();
            i64::try_from(size)
                .map(PyValue::Int)
                .map_err(|_| WireError(format!("an audit part's size {size} outruns an int")))
        })
        .collect::<Result<_>>()?;
    stage_line(out, "audit.held", &PyValue::Tuple(sizes))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn vocabulary() -> String {
        VOCABULARY
            .iter()
            .map(|name| format!(r#"["{name}",1]"#))
            .collect::<Vec<_>>()
            .join(",")
    }

    fn input_text(vocabulary: &str) -> String {
        let stamp = r#"{"value":"v","lines":["format\tf","code\tc"],"detail":[["code",["a.py\td"]]],"header":{"environment":"v","environment_lines":["format\tf"],"environment_detail":{"code":["a.py\td"]}}}"#;
        format!(
            concat!(
                r#"{{"format":"ams-review-build/1","units_init":"/i/init.json","out_dir":"/o","scratch":"/s","#,
                r#""audit":{{"path":"audit.tsv","descriptors":null,"head":null,"members":[null,[["",10,20],["default",5,9]]],"identity":[1,2,3,4],"segment_rows":0,"compressed":1}},"#,
                r#""ledger":{{"path":"l.yaml","entries":[{{"id":"a","status":"per-unit","why":"w","ink_identical":true,"no_verdict":false,"exemplar_keys":[["default","E650"]],"title":"t","summary":"s","note":""}}]}},"#,
                r#""family_of":[[58960,"qsPea"]],"family_keys":{{"qsPea":"k"}},"family_keys_digest":"d","#,
                r#""change_groups":{{"order":["g"],"prose":[["g",{{"title":"t","summary":"s","why":"w"}}]]}},"#,
                r#""environment":{stamp},"signature_environment":{stamp},"#,
                r#""manifest":{{"generated_at":"2026-10-11T08:52:00Z","repo_head":"abc","inputs_fingerprint":{{"data":null,"static":"x"}},"source":{{"audit":"a","ledger":"l"}},"#,
                r#""fonts":{{"before":{{"file":"fonts/before.otf","family":"B","source":"b.otf","sha256":"1","upem":1000}},"after":{{"file":"fonts/after.otf","family":"A","source":"a.otf","sha256":"2","upem":1000}}}},"#,
                r#""alphabet":{{"migrated":3,"total":44}},"configs":["default"],"feature_descriptions":{{"ss02":"x"}},"batch_size":300,"junior_font":{{"source":"j.otf","sha256":"3"}},"#,
                r#""verification_methods":{{"ink_identical":"m"}},"build_command":"b","serve_command":"s"}},"#,
                r#""fonts":{{"before":{{"source":"/b.otf","name":"before.otf","family":"B","sha256":"1"}},"after":{{"source":"/a.otf","name":"after.otf","family":"A","sha256":"2"}}}},"#,
                r#""static":{{"files":[["app.js","/s/app.js"]],"fallback_index":"<html>"}},"repo_root":"/r","recompute_all":false,"#,
                r#""widths":{{"threads":4,"gates_idle_threads":null,"gates_idle_marker":null,"signature_threads":2,"write_threads":1}},"#,
                r#""cycle":{{"run":null,"fold_claim":"/c/fold-claim.json","counts":"/c/counts"}},"vocabulary":[{vocabulary}]}}"#
            ),
            stamp = stamp,
            vocabulary = vocabulary
        )
    }

    #[test]
    fn an_input_reads_back_as_the_values_python_holds() {
        let input = BuildInput::parse(&input_text(&vocabulary())).expect("input");
        assert_eq!(input.audit.members[1].as_ref().map(Vec::len), Some(2));
        assert_eq!(input.constant(VOCABULARY[0]), Some(&PyValue::Int(1)));
        let values = input.to_py();
        assert_eq!(
            values.iter().map(|(field, _)| *field).collect::<Vec<_>>(),
            INPUT_FIELDS
        );
        let text = |field: &str| {
            values
                .iter()
                .find(|(name, _)| *name == field)
                .expect("field")
                .1
                .to_typed()
        };
        assert_eq!(
            text("audit"),
            r#"{"dict":[["path","audit.tsv"],["head",null],["members",{"tuple":[null,{"tuple":[{"record":["Member",[["section",""],["length",10],["inflated",20]]]},{"record":["Member",[["section","default"],["length",5],["inflated",9]]]}]}]}],["identity",{"list":[1,2,3,4]}],["segment_rows",0],["compressed",1]]}"#
        );
        assert_eq!(
            text("ledger"),
            r#"{"dict":[["path","l.yaml"],["entries",{"list":[{"record":["LedgerClass",[["id","a"],["status","per-unit"],["why","w"],["ink_identical",true],["no_verdict",false],["exemplar_keys",{"set":[{"tuple":["default","E650"]}]}],["title","t"],["summary","s"],["note",""]]]}]}]]}"#
        );
        assert_eq!(text("family_of"), r#"{"dict":[[58960,"qsPea"]]}"#);
        assert_eq!(
            text("environment"),
            r#"{"dict":[["value","v"],["lines",{"tuple":["format\tf","code\tc"]}],["detail",{"tuple":[{"tuple":["code",{"tuple":["a.py\td"]}]}]}],["header",{"dict":[["environment","v"],["environment_lines",{"list":["format\tf"]}],["environment_detail",{"dict":[["code",{"list":["a.py\td"]}]]}]]}]]}"#
        );
        assert_eq!(
            text("widths"),
            r#"{"dict":[["threads",4],["gates_idle_threads",null],["gates_idle_marker",null],["signature_threads",2],["write_threads",1]]}"#
        );
    }

    #[test]
    fn an_input_with_another_format_a_missing_field_a_float_or_a_short_vocabulary_is_refused() {
        let full = input_text(&vocabulary());
        for (from, to) in [
            ("ams-review-build/1", "ams-review-build/0"),
            (r#""scratch":"/s","#, ""),
            (r#""upem":1000},"after""#, r#""upem":1000.5},"after""#),
            (r#""static":"x""#, r#""static":1.5"#),
            (r#""recompute_all":false"#, r#""recompute_all":0"#),
            (r#""descriptors":null"#, r#""descriptors":[3]"#),
            (r#""family_of":[[58960"#, r#""family_of":[[-1"#),
        ] {
            assert!(full.contains(from), "{from}");
            assert!(
                BuildInput::parse(&full.replacen(from, to, 1)).is_err(),
                "{to} was accepted"
            );
        }
        let short = input_text(
            &VOCABULARY[1..]
                .iter()
                .map(|name| format!(r#"["{name}",1]"#))
                .collect::<Vec<_>>()
                .join(","),
        );
        let refusal = BuildInput::parse(&short).expect_err("refused");
        assert!(refusal.0.contains(VOCABULARY[0]), "{refusal}");
    }
}
