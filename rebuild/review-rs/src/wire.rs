//! The records of the boundary between the build and the engine (`rebuild/CORPUS-UNITS-PORT-PLAN.md` § Boundary), with `rebuild/review/units_exec.py` as the Python half: the init file ([`Init`]), the files it names ([`InitFiles`]), and the request frames ([`Request`], one [`UnitRecord`] per `audit.Unit`). Every reader is strict: an object must hold exactly the keys its writer writes, every number is an integer, and a value of the wrong JSON type is refused with the field it was read for.
//!
//! [`stage_wire`] is the `wire` stage of the stage fixtures: it reads the init, its files and the units file, and writes each back as the Python value `units_exec` holds, in the typed encoding.

use std::fmt;
use std::io::Write;
use std::path::Path;

use ams_m1_kernel::model::Spec;
use ams_m1_kernel::parse::parse_spec;
use serde_json::{Map, Value};

use crate::pyfmt::PyValue;

/// The init file's format marker, `units_exec.INIT_FORMAT`.
pub const INIT_FORMAT: &str = "ams-review-units-init/1";

/// The vocabulary names the engine reads, each a constant `units_exec.VOCABULARY` names; an init that lacks one is refused.
pub const VOCABULARY: &[&str] = &[
    "rebuild.pipeline.labels.BOUNDARY_GLYPH_NAMES",
    "rebuild.review.audit.ACCEPTANCE_CONFIGS",
    "rebuild.review.audit.MACHINE_CHECKS",
    "rebuild.review.audit.SLIM_OMITTED_KEYS",
    "rebuild.review.build.CONTRACT_ERRORS_SHOWN",
    "rebuild.review.enrich.EXPLAIN_UNIT_BATCH_SIZE",
    "rebuild.review.enrich.LETTERS",
    "rebuild.review.feature_reach.DEFAULT",
];

/// The units file a stage dump writes beside its stage files.
pub const UNITS_FILE: &str = "units.ndjson";

/// A record the engine refuses, or a file it cannot read.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct WireError(pub String);

impl fmt::Display for WireError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for WireError {}

type Result<T> = std::result::Result<T, WireError>;

fn error<T>(message: impl Into<String>) -> Result<T> {
    Err(WireError(message.into()))
}

fn object<'a>(value: &'a Value, what: &str, keys: &[&str]) -> Result<&'a Map<String, Value>> {
    let Value::Object(map) = value else {
        return error(format!("{what} is not a JSON object"));
    };
    let missing: Vec<&str> = keys
        .iter()
        .copied()
        .filter(|key| !map.contains_key(*key))
        .collect();
    let extra: Vec<&str> = map
        .keys()
        .map(String::as_str)
        .filter(|key| !keys.contains(key))
        .collect();
    if !missing.is_empty() || !extra.is_empty() {
        return error(format!(
            "{what} lacks {missing:?} or holds keys it should not, {extra:?}"
        ));
    }
    Ok(map)
}

fn field<'a>(map: &'a Map<String, Value>, key: &str) -> &'a Value {
    &map[key]
}

fn string(value: &Value, what: &str) -> Result<String> {
    match value {
        Value::String(text) => Ok(text.clone()),
        _ => error(format!("{what} is not a string: {value}")),
    }
}

fn optional_string(value: &Value, what: &str) -> Result<Option<String>> {
    match value {
        Value::Null => Ok(None),
        _ => string(value, what).map(Some),
    }
}

fn boolean(value: &Value, what: &str) -> Result<bool> {
    match value {
        Value::Bool(flag) => Ok(*flag),
        _ => error(format!("{what} is not a boolean: {value}")),
    }
}

fn integer(value: &Value, what: &str) -> Result<i64> {
    match value {
        Value::Number(number) => number
            .as_i64()
            .ok_or_else(|| WireError(format!("{what} is not an integer: {number}"))),
        _ => error(format!("{what} is not an integer: {value}")),
    }
}

fn array<'a>(value: &'a Value, what: &str) -> Result<&'a [Value]> {
    match value {
        Value::Array(items) => Ok(items),
        _ => error(format!("{what} is not a JSON array: {value}")),
    }
}

fn tuple<'a, const N: usize>(value: &'a Value, what: &str) -> Result<&'a [Value; N]> {
    array(value, what)?
        .try_into()
        .map_err(|_| WireError(format!("{what} is not a {N}-item array: {value}")))
}

fn strings(value: &Value, what: &str) -> Result<Vec<String>> {
    array(value, what)?
        .iter()
        .map(|item| string(item, what))
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

fn code_point(value: &Value, what: &str) -> Result<char> {
    let number = integer(value, what)?;
    u32::try_from(number)
        .ok()
        .and_then(char::from_u32)
        .ok_or_else(|| WireError(format!("{what} is not a code point: {number}")))
}

fn read_file(path: &str, what: &str) -> Result<Vec<u8>> {
    std::fs::read(path)
        .map_err(|failure| WireError(format!("cannot read {what} {path}: {failure}")))
}

fn str_dict(pairs: &[(String, String)]) -> PyValue {
    PyValue::Dict(
        pairs
            .iter()
            .map(|(key, value)| (PyValue::str(key), PyValue::str(value)))
            .collect(),
    )
}

fn entries(items: Vec<(&str, PyValue)>) -> PyValue {
    PyValue::Dict(
        items
            .into_iter()
            .map(|(key, value)| (PyValue::str(key), value))
            .collect(),
    )
}

// --- the init file -------------------------------------------------------------------------------

/// The two settlement mode booleans, `kernel_exec`'s semantics defaults as the build read them.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Settlement {
    pub simulated_prospect: bool,
    pub follower_prefer_slots: bool,
}

/// The paths of the three fonts a worker shapes with.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Fonts {
    pub before: String,
    pub after: String,
    pub junior: String,
}

/// One alias map entry's value: a `CellId`'s fields, or one of the words `boundary`, `ignore` and `pending`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Alias {
    Cell {
        rune: String,
        stance: String,
        entry: Option<String>,
        exit: Option<String>,
        adjustments: Vec<String>,
    },
    Word(String),
}

/// The subset pack's path and the header values `SubsetPack.open` checks it against.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SubsetPackInit {
    pub path: String,
    pub format: String,
    pub packer: String,
    pub byteorder: String,
    pub digests: Vec<(String, String)>,
}

/// `ConfigFold`'s configurations in order, and the trigger letters of each one that folds.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Fold {
    pub configs: Vec<String>,
    pub triggers: Vec<(String, Vec<char>)>,
}

/// One site corpus index entry: the pinned text and its configuration token, and where the first such cell is.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CorpusEntry {
    pub text: String,
    pub config: String,
    pub source: String,
    pub attribute: String,
}

/// The init file, `ams-review-units-init/1`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Init {
    pub spec: String,
    pub settlement: Settlement,
    pub harfbuzz: String,
    pub fonts: Fonts,
    pub aliases: Vec<(String, Alias)>,
    pub subset_pack: SubsetPackInit,
    pub fold: Fold,
    pub identical_classes: Vec<String>,
    pub corpus_index: Vec<CorpusEntry>,
    pub rune_schema: String,
    pub glyph_names_by_char: Vec<(char, String)>,
    pub dictionary: String,
    pub vocabulary: Vec<(String, PyValue)>,
}

const INIT_KEYS: &[&str] = &[
    "format",
    "spec",
    "settlement",
    "harfbuzz",
    "fonts",
    "aliases",
    "subset_pack",
    "fold",
    "identical_classes",
    "corpus_index",
    "rune_schema",
    "glyph_names_by_char",
    "dictionary",
    "vocabulary",
];

impl Init {
    /// Read the init file at `path`.
    pub fn read(path: &Path) -> Result<Self> {
        let text = std::fs::read_to_string(path).map_err(|failure| {
            WireError(format!(
                "cannot read the init {}: {failure}",
                path.display()
            ))
        })?;
        Self::parse(&text)
    }

    /// Parse an init file's text, refusing another format or an init that lacks a [`VOCABULARY`] name.
    pub fn parse(text: &str) -> Result<Self> {
        let value: Value = serde_json::from_str(text)
            .map_err(|failure| WireError(format!("the init is not JSON: {failure}")))?;
        let map = object(&value, "the init", INIT_KEYS)?;
        let format = string(field(map, "format"), "the init's format")?;
        if format != INIT_FORMAT {
            return error(format!("the init is {format:?}, not {INIT_FORMAT}"));
        }
        let settlement = object(
            field(map, "settlement"),
            "the init's settlement",
            &["simulated_prospect", "follower_prefer_slots"],
        )?;
        let fonts = object(
            field(map, "fonts"),
            "the init's fonts",
            &["before", "after", "junior"],
        )?;
        let pack = object(
            field(map, "subset_pack"),
            "the init's subset pack",
            &["path", "format", "packer", "byteorder", "digests"],
        )?;
        let Value::Object(digest_map) = &pack["digests"] else {
            return error("the subset pack's digests are not a JSON object");
        };
        let fold = object(
            field(map, "fold"),
            "the init's fold",
            &["configs", "triggers"],
        )?;
        let vocabulary = array(field(map, "vocabulary"), "the init's vocabulary")?
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
            return error(format!("the init's vocabulary lacks {lacking:?}"));
        }
        Ok(Self {
            spec: string(field(map, "spec"), "the init's spec")?,
            settlement: Settlement {
                simulated_prospect: boolean(
                    &settlement["simulated_prospect"],
                    "simulated_prospect",
                )?,
                follower_prefer_slots: boolean(
                    &settlement["follower_prefer_slots"],
                    "follower_prefer_slots",
                )?,
            },
            harfbuzz: string(field(map, "harfbuzz"), "the init's harfbuzz")?,
            fonts: Fonts {
                before: string(&fonts["before"], "the before font")?,
                after: string(&fonts["after"], "the after font")?,
                junior: string(&fonts["junior"], "the Junior font")?,
            },
            aliases: array(field(map, "aliases"), "the init's aliases")?
                .iter()
                .map(|item| {
                    let [name, alias] = tuple(item, "an alias entry")?;
                    Ok((string(name, "an alias name")?, Self::alias(alias)?))
                })
                .collect::<Result<_>>()?,
            subset_pack: SubsetPackInit {
                path: string(&pack["path"], "the subset pack's path")?,
                format: string(&pack["format"], "the subset pack's format")?,
                packer: string(&pack["packer"], "the subset pack's packer digest")?,
                byteorder: string(&pack["byteorder"], "the subset pack's byte order")?,
                digests: digest_map
                    .iter()
                    .map(|(config, digest)| Ok((config.clone(), string(digest, "a table digest")?)))
                    .collect::<Result<_>>()?,
            },
            fold: Fold {
                configs: strings(&fold["configs"], "the fold's configurations")?,
                triggers: array(&fold["triggers"], "the fold's triggers")?
                    .iter()
                    .map(|item| {
                        let [config, letters] = tuple(item, "a fold trigger")?;
                        Ok((
                            string(config, "a fold configuration")?,
                            array(letters, "a fold trigger's code points")?
                                .iter()
                                .map(|letter| code_point(letter, "a trigger code point"))
                                .collect::<Result<_>>()?,
                        ))
                    })
                    .collect::<Result<_>>()?,
            },
            identical_classes: strings(
                field(map, "identical_classes"),
                "the init's identical classes",
            )?,
            corpus_index: array(field(map, "corpus_index"), "the init's corpus index")?
                .iter()
                .map(|item| {
                    let [text, config, source, attribute] = tuple(item, "a corpus index entry")?;
                    Ok(CorpusEntry {
                        text: string(text, "a corpus index text")?,
                        config: string(config, "a corpus index configuration")?,
                        source: string(source, "a corpus index source")?,
                        attribute: string(attribute, "a corpus index attribute")?,
                    })
                })
                .collect::<Result<_>>()?,
            rune_schema: string(field(map, "rune_schema"), "the init's rune schema")?,
            glyph_names_by_char: array(
                field(map, "glyph_names_by_char"),
                "the init's glyph names",
            )?
            .iter()
            .map(|item| {
                let [letter, name] = tuple(item, "a glyph name entry")?;
                Ok((
                    code_point(letter, "a glyph name's code point")?,
                    string(name, "a glyph name")?,
                ))
            })
            .collect::<Result<_>>()?,
            dictionary: string(field(map, "dictionary"), "the init's dictionary")?,
            vocabulary,
        })
    }

    fn alias(value: &Value) -> Result<Alias> {
        if let Value::String(word) = value {
            return Ok(Alias::Word(word.clone()));
        }
        let cell = object(
            value,
            "an alias cell",
            &["rune", "stance", "entry", "exit", "adjustments"],
        )?;
        Ok(Alias::Cell {
            rune: string(&cell["rune"], "an alias rune")?,
            stance: string(&cell["stance"], "an alias stance")?,
            entry: optional_string(&cell["entry"], "an alias entry height")?,
            exit: optional_string(&cell["exit"], "an alias exit height")?,
            adjustments: strings(&cell["adjustments"], "an alias's adjustments")?,
        })
    }

    /// One vocabulary constant by its dotted name.
    pub fn constant(&self, name: &str) -> Option<&PyValue> {
        self.vocabulary
            .iter()
            .find(|(held, _)| held == name)
            .map(|(_, value)| value)
    }

    /// The init's values as Python holds them (`units_exec.init_values`), in its order.
    pub fn to_py(&self) -> PyValue {
        let aliases = self
            .aliases
            .iter()
            .map(|(name, alias)| {
                let value = match alias {
                    Alias::Word(word) => PyValue::str(word),
                    Alias::Cell {
                        rune,
                        stance,
                        entry,
                        exit,
                        adjustments,
                    } => PyValue::Record(
                        "CellId".to_owned(),
                        vec![
                            ("rune".to_owned(), PyValue::str(rune)),
                            ("stance".to_owned(), PyValue::str(stance)),
                            ("entry".to_owned(), PyValue::optional_str(entry.as_deref())),
                            ("exit".to_owned(), PyValue::optional_str(exit.as_deref())),
                            ("adjustments".to_owned(), PyValue::str_tuple(adjustments)),
                        ],
                    ),
                };
                (PyValue::str(name), value)
            })
            .collect();
        let pack = &self.subset_pack;
        entries(vec![
            ("format", PyValue::str(INIT_FORMAT)),
            (
                "settlement",
                entries(vec![
                    (
                        "simulated_prospect",
                        PyValue::Bool(self.settlement.simulated_prospect),
                    ),
                    (
                        "follower_prefer_slots",
                        PyValue::Bool(self.settlement.follower_prefer_slots),
                    ),
                ]),
            ),
            ("harfbuzz", PyValue::str(&self.harfbuzz)),
            (
                "fonts",
                entries(vec![
                    ("before", PyValue::str(&self.fonts.before)),
                    ("after", PyValue::str(&self.fonts.after)),
                    ("junior", PyValue::str(&self.fonts.junior)),
                ]),
            ),
            ("aliases", PyValue::Dict(aliases)),
            (
                "subset_pack",
                entries(vec![
                    ("path", PyValue::str(&pack.path)),
                    ("format", PyValue::str(&pack.format)),
                    ("packer", PyValue::str(&pack.packer)),
                    ("byteorder", PyValue::str(&pack.byteorder)),
                    ("digests", str_dict(&pack.digests)),
                ]),
            ),
            (
                "fold",
                entries(vec![
                    ("configs", PyValue::str_tuple(&self.fold.configs)),
                    (
                        "triggers",
                        PyValue::Dict(
                            self.fold
                                .triggers
                                .iter()
                                .map(|(config, letters)| {
                                    (
                                        PyValue::str(config),
                                        PyValue::Set(
                                            letters
                                                .iter()
                                                .map(|letter| PyValue::str(letter.to_string()))
                                                .collect(),
                                        ),
                                    )
                                })
                                .collect(),
                        ),
                    ),
                ]),
            ),
            (
                "identical_classes",
                PyValue::Set(self.identical_classes.iter().map(PyValue::str).collect()),
            ),
            (
                "corpus_index",
                PyValue::Dict(
                    self.corpus_index
                        .iter()
                        .map(|entry| {
                            (
                                PyValue::Tuple(vec![
                                    PyValue::str(&entry.text),
                                    PyValue::str(&entry.config),
                                ]),
                                entries(vec![
                                    ("source", PyValue::str(&entry.source)),
                                    ("attribute", PyValue::str(&entry.attribute)),
                                ]),
                            )
                        })
                        .collect(),
                ),
            ),
            ("rune_schema", PyValue::str(&self.rune_schema)),
            (
                "glyph_names_by_char",
                PyValue::Dict(
                    self.glyph_names_by_char
                        .iter()
                        .map(|(letter, name)| {
                            (PyValue::str(letter.to_string()), PyValue::str(name))
                        })
                        .collect(),
                ),
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
        ])
    }

    /// Read the files the init names: the spec dump through the kernel's parser, the preset dictionary's bytes, and the rune schema as JSON.
    pub fn files(&self) -> Result<InitFiles> {
        let spec_text = String::from_utf8(read_file(&self.spec, "the spec dump")?)
            .map_err(|_| WireError(format!("the spec dump {} is not UTF-8", self.spec)))?;
        let spec = parse_spec(&spec_text).map_err(|failure| WireError(failure.to_string()))?;
        let dictionary = read_file(&self.dictionary, "the preset dictionary")?;
        let rune_schema = serde_json::from_slice(&read_file(&self.rune_schema, "the rune schema")?)
            .map_err(|failure| {
                WireError(format!(
                    "the rune schema {} is not JSON: {failure}",
                    self.rune_schema
                ))
            })?;
        Ok(InitFiles {
            spec,
            dictionary,
            rune_schema,
        })
    }
}

/// What the init's files hold.
pub struct InitFiles {
    pub spec: Spec,
    pub dictionary: Vec<u8>,
    pub rune_schema: Value,
}

impl InitFiles {
    /// Each rune's name, codepoint, ligature sequence and stance names in the spec's order, as `(codepoint, sequence, stances)` under the name, the shape the `wire` stage records from `enrich.load_spec`.
    pub fn spec_runes(&self) -> PyValue {
        let symbols = &self.spec.symbols;
        PyValue::Dict(
            self.spec
                .root
                .runes
                .iter()
                .map(|(name, rune)| {
                    (
                        PyValue::str(symbols.resolve(*name)),
                        PyValue::Tuple(vec![
                            rune.codepoint.map_or(PyValue::None, PyValue::Int),
                            rune.sequence.as_ref().map_or(PyValue::None, |sequence| {
                                PyValue::Tuple(
                                    sequence
                                        .iter()
                                        .map(|letter| PyValue::str(symbols.resolve(*letter)))
                                        .collect(),
                                )
                            }),
                            PyValue::Tuple(
                                rune.stances
                                    .iter()
                                    .map(|(stance, _)| PyValue::str(symbols.resolve(*stance)))
                                    .collect(),
                            ),
                        ]),
                    )
                })
                .collect(),
        )
    }
}

// --- the request frames -------------------------------------------------------------------------

/// One `audit.Unit` as a request frame carries it (§ Batches in).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct UnitRecord {
    pub ordinal: i64,
    pub codepoints: String,
    pub codepoint_values: Vec<u32>,
    pub new: Vec<String>,
    pub class: String,
    pub configs: Vec<String>,
    pub kinds: Vec<String>,
    pub group: String,
    pub exemplar: bool,
    pub unit_id: String,
    pub input_key: String,
    pub render_groups: Vec<Vec<String>>,
    pub no_verdict: bool,
    pub config_classes: Vec<(String, String)>,
    pub duplicate_group: Option<String>,
    pub cluster: Option<String>,
    pub ledger_class: String,
}

const UNIT_KEYS: &[&str] = &[
    "ordinal",
    "codepoints",
    "new",
    "class",
    "configs",
    "kinds",
    "group",
    "exemplar",
    "unit_id",
    "input_key",
    "render_groups",
    "no_verdict",
    "config_classes",
    "duplicate_group",
    "cluster",
    "ledger_class",
];

impl UnitRecord {
    /// Read one unit record.
    pub fn from_json(value: &Value) -> Result<Self> {
        let map = object(value, "a unit record", UNIT_KEYS)?;
        let codepoints = string(&map["codepoints"], "a unit's codepoints")?;
        let codepoint_values = codepoints
            .split(':')
            .map(|part| {
                u32::from_str_radix(part, 16)
                    .ok()
                    .filter(|_| {
                        !part.is_empty() && part.bytes().all(|byte| byte.is_ascii_hexdigit())
                    })
                    .ok_or_else(|| {
                        WireError(format!(
                            "a unit's codepoints {codepoints:?} are not colon-joined hex"
                        ))
                    })
            })
            .collect::<Result<_>>()?;
        Ok(Self {
            ordinal: integer(&map["ordinal"], "a unit's ordinal")?,
            codepoints,
            codepoint_values,
            new: strings(&map["new"], "a unit's new cells")?,
            class: string(&map["class"], "a unit's class")?,
            configs: strings(&map["configs"], "a unit's configurations")?,
            kinds: strings(&map["kinds"], "a unit's kinds")?,
            group: string(&map["group"], "a unit's group")?,
            exemplar: boolean(&map["exemplar"], "a unit's exemplar flag")?,
            unit_id: string(&map["unit_id"], "a unit's id")?,
            input_key: string(&map["input_key"], "a unit's input key")?,
            render_groups: array(&map["render_groups"], "a unit's render groups")?
                .iter()
                .map(|group| strings(group, "a unit's render group"))
                .collect::<Result<_>>()?,
            no_verdict: boolean(&map["no_verdict"], "a unit's no-verdict flag")?,
            config_classes: string_pairs(&map["config_classes"], "a unit's configuration classes")?,
            duplicate_group: optional_string(&map["duplicate_group"], "a unit's duplicate group")?,
            cluster: optional_string(&map["cluster"], "a unit's cluster")?,
            ledger_class: string(&map["ledger_class"], "a unit's ledger class")?,
        })
    }

    /// The unit as the Python worker holds it: an `audit.Unit` record of the fields that cross, in the unit's declaration order (`units_exec.UNIT_FIELDS`).
    pub fn to_py(&self) -> PyValue {
        let optional = |value: &Option<String>| PyValue::optional_str(value.as_deref());
        PyValue::Record(
            "Unit".to_owned(),
            vec![
                ("codepoints", PyValue::str(&self.codepoints)),
                ("new", PyValue::str_tuple(&self.new)),
                ("class_id", PyValue::str(&self.class)),
                ("configs", PyValue::str_tuple(&self.configs)),
                ("kinds", PyValue::str_tuple(&self.kinds)),
                ("group", PyValue::str(&self.group)),
                ("exemplar", PyValue::Bool(self.exemplar)),
                ("unit_id", PyValue::str(&self.unit_id)),
                ("input_key", PyValue::str(&self.input_key)),
                ("ordinal", PyValue::Int(self.ordinal)),
                (
                    "render_groups",
                    PyValue::Tuple(
                        self.render_groups
                            .iter()
                            .map(|group| PyValue::str_tuple(group))
                            .collect(),
                    ),
                ),
                ("no_verdict", PyValue::Bool(self.no_verdict)),
                ("config_classes", str_dict(&self.config_classes)),
                ("duplicate_group", optional(&self.duplicate_group)),
                ("cluster", optional(&self.cluster)),
                ("ledger_class", PyValue::str(&self.ledger_class)),
            ]
            .into_iter()
            .map(|(name, value)| (name.to_owned(), value))
            .collect(),
        )
    }
}

/// The whole-corpus values a verification sample's unit is patched with before its recompute: `_recompute_fragment`'s `injection`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Injection {
    pub duplicate_group: Option<String>,
    pub cluster: Option<String>,
    pub class: String,
    pub primary_units: Vec<(Option<String>, bool)>,
}

impl Injection {
    fn from_json(value: &Value) -> Result<Self> {
        let map = object(
            value,
            "an injection",
            &["duplicate_group", "cluster", "class", "primary_units"],
        )?;
        Ok(Self {
            duplicate_group: optional_string(
                &map["duplicate_group"],
                "an injected duplicate group",
            )?,
            cluster: optional_string(&map["cluster"], "an injected cluster")?,
            class: string(&map["class"], "an injected class")?,
            primary_units: array(&map["primary_units"], "the injected primary units")?
                .iter()
                .map(|item| {
                    let [primary, suppressed] = tuple(item, "an injected primary unit")?;
                    Ok((
                        optional_string(primary, "a primary unit id")?,
                        boolean(suppressed, "a primary unit's suppressed flag")?,
                    ))
                })
                .collect::<Result<_>>()?,
        })
    }

    /// The injection as the Python worker holds it: `(duplicate group, cluster, final class, primary units)`.
    pub fn to_py(&self) -> PyValue {
        PyValue::Tuple(vec![
            PyValue::optional_str(self.duplicate_group.as_deref()),
            PyValue::optional_str(self.cluster.as_deref()),
            PyValue::str(&self.class),
            PyValue::Tuple(
                self.primary_units
                    .iter()
                    .map(|(primary, suppressed)| {
                        PyValue::Tuple(vec![
                            PyValue::optional_str(primary.as_deref()),
                            PyValue::Bool(*suppressed),
                        ])
                    })
                    .collect(),
            ),
        ])
    }
}

/// One request frame.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Request {
    Batch { batch: i64, units: Vec<UnitRecord> },
    Verify { units: Vec<(UnitRecord, Injection)> },
    Stop,
}

impl Request {
    /// Parse one frame line.
    pub fn parse(line: &str) -> Result<Self> {
        let value: Value = serde_json::from_str(line)
            .map_err(|failure| WireError(format!("a request frame is not JSON: {failure}")))?;
        let Value::Object(map) = &value else {
            return error("a request frame is not a JSON object");
        };
        match map.get("op") {
            Some(Value::String(op)) if op == "batch" => {
                let map = object(&value, "a batch frame", &["op", "batch", "units"])?;
                Ok(Self::Batch {
                    batch: integer(&map["batch"], "a batch's number")?,
                    units: array(&map["units"], "a batch's units")?
                        .iter()
                        .map(UnitRecord::from_json)
                        .collect::<Result<_>>()?,
                })
            }
            Some(Value::String(op)) if op == "verify" => {
                let map = object(&value, "a verify frame", &["op", "units"])?;
                Ok(Self::Verify {
                    units: array(&map["units"], "a verify frame's units")?
                        .iter()
                        .map(|item| {
                            let item = object(item, "a verify item", &["unit", "injection"])?;
                            Ok((
                                UnitRecord::from_json(&item["unit"])?,
                                Injection::from_json(&item["injection"])?,
                            ))
                        })
                        .collect::<Result<_>>()?,
                })
            }
            Some(Value::String(op)) if op == "stop" => {
                object(&value, "a stop frame", &["op"])?;
                Ok(Self::Stop)
            }
            _ => error(format!(
                "a request frame names no op this engine answers: {line}"
            )),
        }
    }
}

// --- the wire stage -----------------------------------------------------------------------------

fn stage_line(out: &mut impl Write, key: &str, value: &PyValue) -> Result<()> {
    writeln!(
        out,
        "{{\"key\":{},\"value\":{}}}",
        ams_m1_kernel::emit::json_string(key),
        value.to_typed()
    )
    .map_err(|failure| WireError(format!("cannot write a stage line: {failure}")))
}

/// The `wire` stage: write the init's values with the spec's runes and the dictionary's bytes under `init`, each unit of the units file beside `stage_file` under `unit:<ordinal>`, and each verify frame a line of `stage_file` carries under that line's key, as `(unit, injection)` pairs.
pub fn stage_wire(init_path: &Path, stage_file: &Path, out: &mut impl Write) -> Result<()> {
    let init = Init::read(init_path)?;
    let files = init.files()?;
    let PyValue::Dict(mut values) = init.to_py() else {
        unreachable!("the init is a dict");
    };
    values.push((PyValue::str("spec"), files.spec_runes()));
    values.push((PyValue::str("dictionary"), PyValue::Bytes(files.dictionary)));
    stage_line(out, "init", &PyValue::Dict(values))?;
    let units_path = stage_file.with_file_name(UNITS_FILE);
    let units = std::fs::read_to_string(&units_path)
        .map_err(|failure| WireError(format!("cannot read {}: {failure}", units_path.display())))?;
    for line in units.lines() {
        let Request::Batch { units, .. } = Request::parse(line)? else {
            return error(format!(
                "{} holds a frame that is not a batch",
                units_path.display()
            ));
        };
        for unit in units {
            stage_line(out, &format!("unit:{}", unit.ordinal), &unit.to_py())?;
        }
    }
    let lines = std::fs::read_to_string(stage_file)
        .map_err(|failure| WireError(format!("cannot read {}: {failure}", stage_file.display())))?;
    for line in lines.lines() {
        let value: Value = serde_json::from_str(line)
            .map_err(|failure| WireError(format!("a stage line is not JSON: {failure}")))?;
        let Some(frame) = value.get("frame") else {
            continue;
        };
        let key = string(&value["key"], "a stage line's key")?;
        let Request::Verify { units } = Request::parse(&string(frame, "a stage line's frame")?)?
        else {
            return error(format!(
                "the stage line {key} carries a frame that is not a verify frame"
            ));
        };
        let items = units
            .iter()
            .map(|(unit, injection)| PyValue::Tuple(vec![unit.to_py(), injection.to_py()]))
            .collect();
        stage_line(out, &key, &PyValue::List(items))?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    const UNIT: &str = r#"{"ordinal":3,"codepoints":"E650:0020","new":["a","b"],"class":"c","configs":["default","ss10"],"kinds":["cell"],"group":"g","exemplar":true,"unit_id":"","input_key":"k","render_groups":[["default"],["ss10"]],"no_verdict":false,"config_classes":[["default","c"],["ss10","d"]],"duplicate_group":null,"cluster":"x","ledger_class":""}"#;

    #[test]
    fn a_batch_frame_reads_back_as_the_unit_python_holds() {
        let Request::Batch { batch, units } =
            Request::parse(&format!(r#"{{"op":"batch","batch":7,"units":[{UNIT}]}}"#))
                .expect("frame")
        else {
            panic!("not a batch");
        };
        assert_eq!(batch, 7);
        assert_eq!(units[0].codepoint_values, vec![0xE650, 0x20]);
        assert_eq!(
            units[0].to_py().to_typed(),
            r#"{"record":["Unit",[["codepoints","E650:0020"],["new",{"tuple":["a","b"]}],["class_id","c"],["configs",{"tuple":["default","ss10"]}],["kinds",{"tuple":["cell"]}],["group","g"],["exemplar",true],["unit_id",""],["input_key","k"],["ordinal",3],["render_groups",{"tuple":[{"tuple":["default"]},{"tuple":["ss10"]}]}],["no_verdict",false],["config_classes",{"dict":[["default","c"],["ss10","d"]]}],["duplicate_group",null],["cluster","x"],["ledger_class",""]]]}"#
        );
    }

    #[test]
    fn a_verify_frame_carries_each_units_injection() {
        let frame = format!(
            r#"{{"op":"verify","units":[{{"unit":{UNIT},"injection":{{"duplicate_group":"d","cluster":null,"class":"c","primary_units":[[null,false],["u",true]]}}}}]}}"#
        );
        let Request::Verify { units } = Request::parse(&frame).expect("frame") else {
            panic!("not a verify frame");
        };
        assert_eq!(
            units[0].1.to_py().to_typed(),
            r#"{"tuple":["d",null,"c",{"tuple":[{"tuple":[null,false]},{"tuple":["u",true]}]}]}"#
        );
        assert_eq!(Request::parse(r#"{"op":"stop"}"#), Ok(Request::Stop));
    }

    #[test]
    fn a_record_with_a_missing_an_extra_or_a_mistyped_field_is_refused() {
        for (from, to) in [
            (r#""ledger_class":"""#, r#""ledger_clas":"""#),
            (r#""ordinal":3"#, r#""ordinal":3.0"#),
            (r#""exemplar":true"#, r#""exemplar":1"#),
            (
                r#""codepoints":"E650:0020""#,
                r#""codepoints":"E650::0020""#,
            ),
            (r#""cluster":"x""#, r#""cluster":"x","flags":[]"#),
        ] {
            let frame = format!(
                r#"{{"op":"batch","batch":0,"units":[{}]}}"#,
                UNIT.replace(from, to)
            );
            assert!(Request::parse(&frame).is_err(), "{to} was accepted");
        }
        assert!(Request::parse(r#"{"op":"phase1"}"#).is_err());
    }

    fn init_text(vocabulary: &str) -> String {
        format!(
            r#"{{"format":"ams-review-units-init/1","spec":"s","settlement":{{"simulated_prospect":true,"follower_prefer_slots":false}},"harfbuzz":"12.3.2","fonts":{{"before":"b","after":"a","junior":"j"}},"aliases":[["uni200C","boundary"],["qsPea.half",{{"rune":"qsPea","stance":"half","entry":null,"exit":"y6","adjustments":["locked"]}}]],"subset_pack":{{"path":"p","format":"ams-subset-pack/1","packer":"d","byteorder":"little","digests":{{"default":"x"}}}},"fold":{{"configs":["ss03"],"triggers":[["ss03",[58960]]]}},"identical_classes":["i"],"corpus_index":[["t","default","f:1","data-expect"]],"rune_schema":"r","glyph_names_by_char":[[58960,"qsPea"]],"dictionary":"y","vocabulary":[{vocabulary}]}}"#
        )
    }

    fn full_vocabulary() -> String {
        VOCABULARY
            .iter()
            .map(|name| format!(r#"["{name}",1]"#))
            .collect::<Vec<_>>()
            .join(",")
    }

    #[test]
    fn an_init_reads_back_as_the_values_python_holds() {
        let init = Init::parse(&init_text(&full_vocabulary())).expect("init");
        let text = init.to_py().to_typed();
        assert!(text.contains(r#"["qsPea.half",{"record":["CellId",[["rune","qsPea"],["stance","half"],["entry",null],["exit","y6"],["adjustments",{"tuple":["locked"]}]]]}]"#), "{text}");
        assert!(
            text.contains(r#"["triggers",{"dict":[["ss03",{"set":["\ue650"]}]]}]"#),
            "{text}"
        );
        assert!(text.contains(r#"[{"tuple":["t","default"]},{"dict":[["source","f:1"],["attribute","data-expect"]]}]"#), "{text}");
        assert_eq!(init.constant(VOCABULARY[0]), Some(&PyValue::Int(1)));
    }

    #[test]
    fn an_init_that_lacks_a_vocabulary_name_or_names_another_format_is_refused() {
        let short = format!(
            "[{}",
            full_vocabulary().split_once("],[").expect("two names").1
        );
        let refusal = Init::parse(&init_text(&short)).expect_err("accepted");
        assert!(refusal.0.contains(VOCABULARY[0]), "{refusal}");
        let other = init_text(&full_vocabulary()).replace("units-init/1", "units-init/2");
        assert!(Init::parse(&other).is_err());
        let extra = init_text(&full_vocabulary()).replace(
            r#""dictionary":"y""#,
            r#""dictionary":"y","font_facts":"f""#,
        );
        assert!(Init::parse(&extra).is_err());
    }
}
