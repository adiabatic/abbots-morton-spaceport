//! The units init, `ams-review-units-init/1`, which `units_exec.write_init` writes and the build's units phase reads ([`Init`]): the spec dump, the three fonts and their font facts (which [`crate::fontfacts`] reads), the subset pack, the configuration fold, the preset dictionary and the vocabulary; and a unit as the units phase hands it to a worker ([`UnitRecord`]) with what a verification sample's unit is patched with ([`Injection`]). Every reader is strict: an object must hold exactly the keys its writer writes, every number is an integer, and a value of the wrong JSON type is refused with the field it was read for.

use std::fmt;
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
    "rebuild.review.audit.WINDOW_SCOPED_CONFIGS",
    "rebuild.review.build.CONTRACT_ERRORS_SHOWN",
    "rebuild.review.enrich.EXPLAIN_UNIT_BATCH_SIZE",
    "rebuild.review.enrich.LETTERS",
    "rebuild.review.feature_reach.DEFAULT",
    "rebuild.review.ink.IDENTITY_DIFF",
    "rebuild.validation.classify.PIXEL_SIZE",
];

/// A record the engine refuses, or a file it cannot read.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct WireError(pub String);

impl fmt::Display for WireError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for WireError {}

pub(crate) type Result<T> = std::result::Result<T, WireError>;

pub(crate) fn error<T>(message: impl Into<String>) -> Result<T> {
    Err(WireError(message.into()))
}

pub(crate) fn object<'a>(
    value: &'a Value,
    what: &str,
    keys: &[&str],
) -> Result<&'a Map<String, Value>> {
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

pub(crate) fn string(value: &Value, what: &str) -> Result<String> {
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

pub(crate) fn boolean(value: &Value, what: &str) -> Result<bool> {
    match value {
        Value::Bool(flag) => Ok(*flag),
        _ => error(format!("{what} is not a boolean: {value}")),
    }
}

pub(crate) fn integer(value: &Value, what: &str) -> Result<i64> {
    match value {
        Value::Number(number) => number
            .as_i64()
            .ok_or_else(|| WireError(format!("{what} is not an integer: {number}"))),
        _ => error(format!("{what} is not an integer: {value}")),
    }
}

pub(crate) fn array<'a>(value: &'a Value, what: &str) -> Result<&'a [Value]> {
    match value {
        Value::Array(items) => Ok(items),
        _ => error(format!("{what} is not a JSON array: {value}")),
    }
}

pub(crate) fn tuple<'a, const N: usize>(value: &'a Value, what: &str) -> Result<&'a [Value; N]> {
    array(value, what)?
        .try_into()
        .map_err(|_| WireError(format!("{what} is not a {N}-item array: {value}")))
}

pub(crate) fn strings(value: &Value, what: &str) -> Result<Vec<String>> {
    array(value, what)?
        .iter()
        .map(|item| string(item, what))
        .collect()
}

fn code_point(value: &Value, what: &str) -> Result<char> {
    let number = integer(value, what)?;
    u32::try_from(number)
        .ok()
        .and_then(char::from_u32)
        .ok_or_else(|| WireError(format!("{what} is not a code point: {number}")))
}

pub(crate) fn read_file(path: &str, what: &str) -> Result<Vec<u8>> {
    std::fs::read(path)
        .map_err(|failure| WireError(format!("cannot read {what} {path}: {failure}")))
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

/// The subset pack's path and the header values the pack is checked against ([`crate::subset_pack`]).
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
    pub font_facts: String,
    pub junior_tracking: i64,
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
    "font_facts",
    "junior_tracking",
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
            font_facts: string(field(map, "font_facts"), "the init's font facts")?,
            junior_tracking: integer(field(map, "junior_tracking"), "the Junior tracking")?,
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

    /// Read the spec dump the init names through the kernel's parser.
    pub fn read_spec(&self) -> Result<Spec> {
        let spec_text = String::from_utf8(read_file(&self.spec, "the spec dump")?)
            .map_err(|_| WireError(format!("the spec dump {} is not UTF-8", self.spec)))?;
        parse_spec(&spec_text).map_err(|failure| WireError(failure.to_string()))
    }
}

// --- the units a worker takes --------------------------------------------------------------------

/// One unit of the table as the units phase hands it to a worker.
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

/// The whole-corpus values a verification sample's unit is patched with before its recompute.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Injection {
    pub duplicate_group: Option<String>,
    pub cluster: Option<String>,
    pub class: String,
    pub primary_units: Vec<(Option<String>, bool)>,
}

#[cfg(test)]
mod tests {
    use super::*;

    fn init_text(vocabulary: &str) -> String {
        format!(
            r#"{{"format":"ams-review-units-init/1","spec":"s","settlement":{{"simulated_prospect":true,"follower_prefer_slots":false}},"harfbuzz":"12.3.2","fonts":{{"before":"b","after":"a","junior":"j"}},"font_facts":"f","junior_tracking":50,"aliases":[["uni200C","boundary"],["qsPea.half",{{"rune":"qsPea","stance":"half","entry":null,"exit":"y6","adjustments":["locked"]}}]],"subset_pack":{{"path":"p","format":"ams-subset-pack/1","packer":"d","byteorder":"little","digests":{{"default":"x"}}}},"fold":{{"configs":["ss03"],"triggers":[["ss03",[58960]]]}},"identical_classes":["i"],"corpus_index":[["t","default","f:1","data-expect"]],"rune_schema":"r","glyph_names_by_char":[[58960,"qsPea"]],"dictionary":"y","vocabulary":[{vocabulary}]}}"#
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
    fn an_init_reads_its_vocabulary() {
        let init = Init::parse(&init_text(&full_vocabulary())).expect("init");
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
        let extra = init_text(&full_vocabulary())
            .replace(r#""dictionary":"y""#, r#""dictionary":"y","tracking":50"#);
        assert!(Init::parse(&extra).is_err());
    }
}
