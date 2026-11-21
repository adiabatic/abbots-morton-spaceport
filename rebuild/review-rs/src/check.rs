//! The per-unit §7 contract check, `build.check_unit` in its `m1-audit` mode, in its two subsets: at `DRAFTED`, the form the worker runs ([`ContractCheck::check_drafted`]), with `build._is_delta_digest`, `unit_cache.is_content_id`, `subset_pack.is_junction_token`, `audit.machine_approved` and `audit.slim_fragment`; and at `PATCHED`, the form the corpus build's write runs over each fragment it patches ([`PatchedCheck::check`]).
//!
//! The check reads a fragment as the [`PyValue`] it is, whatever its shape, so where it reads Python's truthiness, `len`, iteration, hashing, `==`, sorting, `int(..., 16)` or `.get` on a value that is not a dict, it answers as Python does: the same error list, or a raise where Python raises ([`CheckError::raised`]).
//!
//! [`stage_check`] is the `check` stage of the stage fixtures, and [`stage_check_patched`] the build's `check-patched` stage.

use std::cmp::Ordering;
use std::fmt;
use std::io::{BufRead, Write};
use std::path::Path;

use ams_m1_kernel::emit::json_string;
use serde_json::Value;

use crate::corpus::input::BuildInput;
use crate::expect::ExpectParser;
use crate::fragment::{BASE58_ALPHABET, ID_SYMBOLS, SLIM_OMITTED_KEYS_NAME, lookup, py_eq};
use crate::pyfmt::repr::{repr, repr_str};
use crate::pyfmt::{PyValue, is_digit};
use crate::subset_pack::python_hex_int;
use crate::wire::Init;

/// The vocabulary name of `audit.MACHINE_CHECKS`, the flags that machine-approve a unit.
pub const MACHINE_CHECKS_NAME: &str = "rebuild.review.audit.MACHINE_CHECKS";

/// `subset_pack._JUNCTION_TOKENS`, the junction tokens that are not a height.
const JUNCTION_TOKENS: [&str; 3] = ["break", "lig", "absent"];

const LOWER_HEX: &str = "0123456789abcdef";
const UPPER_HEX: &str = "0123456789ABCDEF";
const RECT_KEYS: [&str; 3] = ["x_min", "x_max", "advance_total"];
const MARK_KEYS: [&str; 3] = ["index", "kind", "x"];
const DRAFT_KEYS: [&str; 3] = ["pin", "policy", "any_of"];
const PIN_KEYS: [&str; 5] = [
    "expect",
    "attribute",
    "syntax",
    "semantics_after_font",
    "suggested_home",
];
const PIN_ATTRIBUTES: [&str; 2] = ["data-expect", "data-expect-noncanonically"];
const POLICY_KEYS: [&str; 5] = [
    "file",
    "keypath",
    "suggested_record",
    "decided_stage",
    "why_stub",
];
const POLICY_KEYPATHS: [&str; 3] = ["policy.refuse[+]", "policy.prefer[+]", "policy.contract[+]"];
const JUNCTION_KEYS: [&str; 4] = ["pair", "before", "after", "primary_unit"];

/// A check that raises where Python raises, with the exception's class name, or a failure Python never meets, with none.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CheckError {
    pub raised: Option<&'static str>,
    pub message: String,
}

impl fmt::Display for CheckError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self.raised {
            Some(kind) => write!(formatter, "{kind}: {}", self.message),
            None => formatter.write_str(&self.message),
        }
    }
}

impl std::error::Error for CheckError {}

type Result<T> = std::result::Result<T, CheckError>;

fn raise<T>(kind: &'static str, message: impl Into<String>) -> Result<T> {
    Err(CheckError {
        raised: Some(kind),
        message: message.into(),
    })
}

fn engine<T>(message: impl Into<String>) -> Result<T> {
    Err(CheckError {
        raised: None,
        message: message.into(),
    })
}

// --- Python's readings of a value ----------------------------------------------------------------

/// `value.get(key)` on a dict, or None, absent and null alike being Python's None to every caller that tests it with `is None`.
fn get<'v>(value: Option<&'v PyValue>, key: &str) -> Option<&'v PyValue> {
    value.and_then(|value| lookup(value, key))
}

/// `key in value` on a dict.
fn has(value: &PyValue, key: &str) -> bool {
    lookup(value, key).is_some()
}

fn is_none(value: Option<&PyValue>) -> bool {
    matches!(value, None | Some(PyValue::None))
}

fn is_true(value: Option<&PyValue>) -> bool {
    matches!(value, Some(PyValue::Bool(true)))
}

fn is_false(value: Option<&PyValue>) -> bool {
    matches!(value, Some(PyValue::Bool(false)))
}

fn is_dict(value: Option<&PyValue>) -> bool {
    matches!(value, Some(PyValue::Dict(_)))
}

fn is_list(value: Option<&PyValue>) -> bool {
    matches!(value, Some(PyValue::List(_)))
}

fn is_bool(value: Option<&PyValue>) -> bool {
    matches!(value, Some(PyValue::Bool(_)))
}

fn is_str(value: Option<&PyValue>) -> bool {
    matches!(value, Some(PyValue::Str(_)))
}

/// `isinstance(value, str) and value`.
fn nonempty_str(value: Option<&PyValue>) -> bool {
    matches!(value, Some(PyValue::Str(text)) if !text.is_empty())
}

/// `isinstance(value, list) and value`.
fn nonempty_list(value: Option<&PyValue>) -> bool {
    matches!(value, Some(PyValue::List(items)) if !items.is_empty())
}

/// The value of an `int`, a bool counting as the int it is, or None for any other value.
fn int_of(value: Option<&PyValue>) -> Option<i64> {
    match value {
        Some(PyValue::Int(number)) => Some(*number),
        Some(PyValue::Bool(flag)) => Some(i64::from(*flag)),
        _ => None,
    }
}

/// Python's `==` between two values either of which may be absent, which reads as None.
fn same(left: Option<&PyValue>, right: Option<&PyValue>) -> bool {
    py_eq(
        left.unwrap_or(&PyValue::None),
        right.unwrap_or(&PyValue::None),
    )
}

fn str_is(value: Option<&PyValue>, text: &str) -> bool {
    matches!(value, Some(PyValue::Str(held)) if held == text)
}

fn truthy(value: &PyValue) -> bool {
    match value {
        PyValue::None => false,
        PyValue::Bool(flag) => *flag,
        PyValue::Int(number) => *number != 0,
        PyValue::Str(text) => !text.is_empty(),
        PyValue::Bytes(bytes) => !bytes.is_empty(),
        PyValue::Tuple(items) | PyValue::List(items) | PyValue::Set(items) => !items.is_empty(),
        PyValue::Dict(entries) => !entries.is_empty(),
        PyValue::Record(..) => true,
    }
}

/// `len(value)`, raising TypeError for a value without one.
fn length(value: &PyValue) -> Result<usize> {
    match value {
        PyValue::Str(text) => Ok(text.chars().count()),
        PyValue::Bytes(bytes) => Ok(bytes.len()),
        PyValue::Tuple(items) | PyValue::List(items) | PyValue::Set(items) => Ok(items.len()),
        PyValue::Dict(entries) => Ok(entries.len()),
        other => raise("TypeError", format!("{} has no len()", other.kind())),
    }
}

/// What a `for` loop over `value` walks: a string's characters, the ints of bytes, a container's items or a dict's keys, raising TypeError for a value that is not iterable.
fn members(value: &PyValue) -> Result<Vec<PyValue>> {
    match value {
        PyValue::Str(text) => Ok(text
            .chars()
            .map(|letter| PyValue::Str(letter.into()))
            .collect()),
        PyValue::Bytes(bytes) => Ok(bytes
            .iter()
            .map(|&byte| PyValue::Int(byte.into()))
            .collect()),
        PyValue::Tuple(items) | PyValue::List(items) | PyValue::Set(items) => Ok(items.clone()),
        PyValue::Dict(entries) => Ok(entries.iter().map(|(key, _)| key.clone()).collect()),
        other => raise("TypeError", format!("{} is not iterable", other.kind())),
    }
}

fn hashable(value: &PyValue) -> bool {
    match value {
        PyValue::None
        | PyValue::Bool(_)
        | PyValue::Int(_)
        | PyValue::Str(_)
        | PyValue::Bytes(_) => true,
        PyValue::Tuple(items) => items.iter().all(hashable),
        PyValue::List(_) | PyValue::Dict(_) | PyValue::Set(_) | PyValue::Record(..) => false,
    }
}

/// `set(items)`: the distinct members by Python's `==`, raising TypeError for an unhashable one.
fn distinct(items: &[PyValue]) -> Result<Vec<&PyValue>> {
    let mut held: Vec<&PyValue> = Vec::with_capacity(items.len());
    for item in items {
        if !hashable(item) {
            return raise("TypeError", format!("unhashable type: {}", item.kind()));
        }
        if !held.iter().any(|other| py_eq(other, item)) {
            held.push(item);
        }
    }
    Ok(held)
}

fn subset(smaller: &[&PyValue], larger: &[&PyValue]) -> bool {
    smaller
        .iter()
        .all(|item| larger.iter().any(|other| py_eq(other, item)))
}

/// The order Python's `<` gives two values: strings by code point, ints and bools by value, bytes by byte, and tuples or lists lexicographically from their first unequal item; any other pair raises TypeError.
fn compare(left: &PyValue, right: &PyValue) -> Result<Ordering> {
    match (left, right) {
        (PyValue::Str(left), PyValue::Str(right)) => Ok(left.cmp(right)),
        (PyValue::Bytes(left), PyValue::Bytes(right)) => Ok(left.cmp(right)),
        (PyValue::Tuple(left), PyValue::Tuple(right))
        | (PyValue::List(left), PyValue::List(right)) => {
            match left.iter().zip(right).find(|(a, b)| !py_eq(a, b)) {
                Some((a, b)) => compare(a, b),
                None => Ok(left.len().cmp(&right.len())),
            }
        }
        (PyValue::Set(_), _) | (_, PyValue::Set(_)) | (PyValue::Record(..), _) => engine(format!(
            "the check does not order {} and {}",
            left.kind(),
            right.kind()
        )),
        _ => match (int_of(Some(left)), int_of(Some(right))) {
            (Some(left), Some(right)) => Ok(left.cmp(&right)),
            _ => raise(
                "TypeError",
                format!(
                    "'<' not supported between {} and {}",
                    left.kind(),
                    right.kind()
                ),
            ),
        },
    }
}

/// `sorted(items)`, raising where a comparison between two of the items raises.
fn sorted(items: &[PyValue]) -> Result<Vec<&PyValue>> {
    for (index, left) in items.iter().enumerate() {
        for right in &items[index + 1..] {
            compare(left, right)?;
        }
    }
    let mut ordered: Vec<&PyValue> = items.iter().collect();
    ordered.sort_by(|left, right| compare(left, right).unwrap_or(Ordering::Equal));
    Ok(ordered)
}

/// `str(value)`, which is `repr` for every value the check names but a string.
fn text_of(value: &PyValue) -> Result<String> {
    match value {
        PyValue::Str(text) => Ok(text.clone()),
        other => repr(other).or_else(|failure| engine(failure.0)),
    }
}

/// `repr` of a value that may be absent, which reads as None.
fn shown(value: Option<&PyValue>) -> Result<String> {
    repr(value.unwrap_or(&PyValue::None)).or_else(|failure| engine(failure.0))
}

/// `build._is_delta_digest`: `d-` and twelve lowercase hex digits.
fn is_delta_digest(value: &PyValue) -> bool {
    matches!(value, PyValue::Str(text)
        if text.chars().count() == 14
            && text.starts_with("d-")
            && text.chars().skip(2).all(|letter| LOWER_HEX.contains(letter)))
}

/// `unit_cache.is_content_id`: `u-` or `e-` and [`ID_SYMBOLS`] base58 symbols, matched as `re.match` matches `^...$`, whose `$` also matches before a final newline.
pub(crate) fn is_content_id(value: Option<&PyValue>) -> bool {
    let Some(PyValue::Str(text)) = value else {
        return false;
    };
    let text = text.strip_suffix('\n').unwrap_or(text);
    let bytes = text.as_bytes();
    bytes.len() == 2 + ID_SYMBOLS
        && matches!(bytes[0], b'u' | b'e')
        && bytes[1] == b'-'
        && bytes[2..]
            .iter()
            .all(|symbol| BASE58_ALPHABET.contains(symbol))
}

/// `subset_pack.is_junction_token`: `break`, `lig`, `absent`, or `y` and digits as `str.isdigit` reads them.
fn is_junction_token(value: &PyValue) -> bool {
    let PyValue::Str(token) = value else {
        return false;
    };
    JUNCTION_TOKENS.contains(&token.as_str())
        || token
            .strip_prefix('y')
            .is_some_and(|height| !height.is_empty() && height.chars().all(is_digit))
}

/// `unit_cache.unit_id_for` over any string: the first sixteen characters read by `int(..., 16)`, which raises ValueError for text it refuses, and the value's base58 digits by Python's floor `divmod`, which keeps a negative value's digits coming.
fn unit_id_for(content_key: &str) -> Result<String> {
    let head: String = content_key.chars().take(16).collect();
    let Some((negative, magnitude)) = python_hex_int(&head) else {
        return raise(
            "ValueError",
            format!(
                "invalid literal for int() with base 16: {}",
                repr_str(&head)
            ),
        );
    };
    let mut value = i128::from(magnitude);
    if negative {
        value = -value;
    }
    let mut symbols = [0u8; ID_SYMBOLS];
    for symbol in symbols.iter_mut().rev() {
        let remainder = usize::try_from(value.rem_euclid(58)).expect("a remainder below 58");
        *symbol = BASE58_ALPHABET[remainder];
        value = value.div_euclid(58);
    }
    Ok(format!(
        "u-{}",
        symbols
            .iter()
            .map(|&symbol| char::from(symbol))
            .collect::<String>()
    ))
}

// --- the check -----------------------------------------------------------------------------------

/// The error list one fragment's check builds, each line naming the fragment's id as `str` writes it.
struct Needs {
    identifier: String,
    errors: Vec<String>,
}

impl Needs {
    /// The empty list for `fragment`, named by `unit.get("id", "<missing>")`, which raises AttributeError for a value that is not a dict.
    fn of(fragment: &PyValue) -> Result<Self> {
        if !matches!(fragment, PyValue::Dict(_)) {
            return raise(
                "AttributeError",
                format!("{} has no attribute 'get'", fragment.kind()),
            );
        }
        let identifier = match lookup(fragment, "id") {
            Some(value) => text_of(value)?,
            None => "<missing>".to_owned(),
        };
        Ok(Self {
            identifier,
            errors: Vec::new(),
        })
    }

    fn need(&mut self, condition: bool, message: impl fmt::Display) {
        if !condition {
            self.errors
                .push(format!("unit {}: {message}", self.identifier));
        }
    }

    /// `need_rect`: a highlight rectangle carries three ints in order.
    fn need_rect(&mut self, record: Option<&PyValue>, label: &str) {
        let ints = match record {
            Some(PyValue::Dict(_)) => {
                let [x_min, x_max, advance_total] = RECT_KEYS.map(|key| int_of(get(record, key)));
                x_min.zip(x_max).zip(advance_total)
            }
            _ => None,
        };
        self.need(
            ints.is_some(),
            format_args!("{label} must carry integer x_min/x_max/advance_total"),
        );
        if let Some(((x_min, x_max), advance_total)) = ints {
            self.need(
                x_min <= x_max && x_max <= advance_total,
                format_args!("{label} must satisfy x_min <= x_max <= advance_total"),
            );
        }
    }
}

/// `build.check_unit(fragment, at=(DRAFTED,))` over the init's `MACHINE_CHECKS` and `SLIM_OMITTED_KEYS`, with `parse_expect` over its `_CHAR_TO_GLYPH`.
pub struct ContractCheck {
    machine_checks: Vec<String>,
    slim_omitted: Vec<String>,
    parser: ExpectParser,
}

fn str_tuple(init: &Init, name: &str) -> Result<Vec<String>> {
    let Some(PyValue::Tuple(items)) = init.constant(name) else {
        return engine(format!("the init's {name} is not a tuple"));
    };
    items
        .iter()
        .map(|item| match item {
            PyValue::Str(text) => Ok(text.clone()),
            other => engine(format!(
                "the init's {name} holds {}, not a str",
                other.kind()
            )),
        })
        .collect()
}

impl ContractCheck {
    /// The check over the init's vocabulary and glyph names.
    pub fn new(init: &Init) -> Result<Self> {
        Ok(Self {
            machine_checks: str_tuple(init, MACHINE_CHECKS_NAME)?,
            slim_omitted: str_tuple(init, SLIM_OMITTED_KEYS_NAME)?,
            parser: ExpectParser::new(init),
        })
    }

    /// `audit.machine_approved`: whether any machine check is `True` on the fragment.
    pub fn machine_approved(&self, fragment: &PyValue) -> bool {
        self.machine_checks
            .iter()
            .any(|check| is_true(lookup(fragment, check)))
    }

    /// `audit.slim_fragment`: machine-approved, or exempt by its `no_verdict`.
    pub fn slim_fragment(&self, fragment: &PyValue) -> bool {
        self.machine_approved(fragment) || is_true(lookup(fragment, "no_verdict"))
    }

    /// `check_unit(fragment, at=(DRAFTED,))` in `m1-audit` mode: the errors in Python's order, or the raise Python meets first.
    pub fn check_drafted(&self, fragment: &PyValue) -> Result<Vec<String>> {
        let mut n = Needs::of(fragment)?;
        let unit = Some(fragment);
        let approving = self
            .machine_checks
            .iter()
            .filter(|check| is_true(get(unit, check)))
            .count();
        let human = approving == 0 && !is_true(get(unit, "no_verdict"));
        let pair = get(unit, "pair");

        let id = get(unit, "id");
        n.need(
            is_content_id(id) && matches!(id, Some(PyValue::Str(text)) if text.starts_with("u-")),
            format_args!("id must be u- and {ID_SYMBOLS} base58 symbols"),
        );
        n.need(
            is_bool(get(unit, "ink_identical")),
            "ink_identical must be a bool",
        );
        n.need(
            is_bool(get(unit, "picture_identical")),
            "picture_identical must be a bool",
        );
        n.need(
            is_bool(get(unit, "junior_equivalent").or(Some(&PyValue::Bool(false)))),
            "junior_equivalent must be a bool",
        );
        let deltas = get(unit, "ink_deltas");
        n.need(is_dict(deltas), "ink_deltas must be a mapping");
        if let Some(PyValue::Dict(entries)) = deltas {
            n.need(
                entries.iter().all(|(config, _)| nonempty_str(Some(config)))
                    && entries.iter().all(|(_, value)| is_delta_digest(value)),
                "ink_deltas must map configs to d- delta digests",
            );
            if let Some(PyValue::List(configs)) = get(unit, "configs") {
                let keys: Vec<&PyValue> = entries.iter().map(|(config, _)| config).collect();
                let configs = distinct(configs)?;
                n.need(
                    subset(&keys, &configs),
                    "ink_deltas keys must be a subset of configs",
                );
            }
            let ink = get(unit, "ink_identical");
            let picture = get(unit, "picture_identical");
            if is_true(ink) || is_true(picture) {
                n.need(
                    entries.is_empty(),
                    "ink- and picture-identical units must carry empty ink_deltas",
                );
            } else if is_false(ink) && is_false(picture) {
                n.need(
                    !entries.is_empty(),
                    "units with a visible ink change must carry a nonempty ink_deltas",
                );
            }
        }
        let stamp = get(unit, "content_key");
        n.need(
            matches!(stamp, Some(PyValue::Str(text))
                if text.chars().count() == 64 && text.chars().all(|letter| LOWER_HEX.contains(letter))),
            "content_key must be a sha256 hex stamp",
        );
        if let Some(PyValue::Str(text)) = stamp
            && text.chars().count() == 64
        {
            let own = PyValue::Str(unit_id_for(text)?);
            n.need(same(id, Some(&own)), "id must be the content key's own");
        }
        n.need(
            is_bool(get(unit, "no_verdict")),
            "no_verdict must be a bool",
        );
        n.need(
            !has(fragment, "batch"),
            "a fragment carries no batch; the manifest's human_unit_ids is the index",
        );
        n.need(
            approving <= 1,
            "at most one machine check may approve a unit",
        );
        for key in ["class", "group", "notation", "summary"] {
            n.need(
                nonempty_str(get(unit, key)),
                format_args!("{key} must be a nonempty string"),
            );
        }
        let ledger_class = get(unit, "ledger_class");
        n.need(
            is_none(ledger_class)
                || (nonempty_str(ledger_class) && !same(ledger_class, get(unit, "class")) && human),
            "ledger_class must be absent, null, or on a human unit the id of a class other than its own",
        );
        let slim = self.slim_fragment(fragment);
        if slim {
            for key in &self.slim_omitted {
                n.need(
                    !has(fragment, key),
                    format_args!("machine-approved and no-verdict units omit {key}"),
                );
            }
        } else {
            n.need(
                nonempty_str(get(unit, "explain")),
                "explain must be a nonempty string",
            );
        }
        if let Some(PyValue::Str(summary)) = get(unit, "summary") {
            n.need(
                summary.starts_with("New: "),
                "summary must open with the New: clause",
            );
            n.need(!summary.contains('\n'), "summary must be one line");
        }
        n.need(
            nonempty_list(get(unit, "configs")),
            "configs must be a nonempty list",
        );
        n.need(has(fragment, "config_note"), "config_note must be present");
        let note = get(unit, "config_note");
        n.need(
            is_none(note) || nonempty_str(note),
            "config_note must be null or a nonempty string",
        );
        n.need(has(fragment, "config_gate"), "config_gate must be present");
        let clauses = get(unit, "config_gate");
        n.need(
            is_none(clauses) || nonempty_list(clauses),
            "config_gate must be null or a nonempty clause list",
        );
        if let Some(PyValue::List(items)) = clauses {
            for clause in items {
                let clause = Some(clause);
                n.need(
                    is_dict(clause)
                        && is_str(get(clause, "feature"))
                        && (str_is(get(clause, "state"), "on")
                            || str_is(get(clause, "state"), "off"))
                        && nonempty_str(get(clause, "text")),
                    "config_gate clauses must carry a feature, an on/off state, and nonempty text",
                );
            }
            if !items.is_empty() {
                let mut texts = Vec::with_capacity(items.len());
                for clause in items {
                    if !matches!(clause, PyValue::Dict(_)) {
                        return raise(
                            "AttributeError",
                            format!("{} has no attribute 'get'", clause.kind()),
                        );
                    }
                    texts.push(lookup(clause, "text"));
                }
                let mut joined = Vec::with_capacity(texts.len());
                for text in texts {
                    match text {
                        None => joined.push(""),
                        Some(PyValue::Str(text)) => joined.push(text.as_str()),
                        Some(other) => {
                            return raise(
                                "TypeError",
                                format!(
                                    "sequence item: expected str instance, {} found",
                                    other.kind()
                                ),
                            );
                        }
                    }
                }
                let joined = PyValue::Str(joined.join(" "));
                n.need(
                    same(note, Some(&joined)),
                    "config_note must be the config_gate clause texts joined",
                );
            }
        }
        let groups = get(unit, "render_groups");
        n.need(
            nonempty_list(groups),
            "render_groups must be a nonempty list",
        );
        if let Some(PyValue::List(items)) = groups {
            n.need(
                items.len() == 1,
                "m1-audit units must carry exactly one render group",
            );
        }
        let mut grouped: Vec<PyValue> = Vec::new();
        if let Some(PyValue::List(items)) = groups {
            for group in items {
                let group = Some(group);
                let configs = get(group, "configs");
                n.need(
                    is_dict(group) && nonempty_list(configs),
                    "render_groups entries must carry a nonempty configs list",
                );
                if let Some(PyValue::List(configs)) = configs {
                    grouped.extend(configs.iter().cloned());
                }
            }
        }
        if let Some(PyValue::List(configs)) = get(unit, "configs")
            && !grouped.is_empty()
        {
            let partition = distinct(&grouped)?.len() == grouped.len() && {
                let grouped = sorted(&grouped)?;
                let configs = sorted(configs)?;
                grouped.len() == configs.len()
                    && grouped.iter().zip(&configs).all(|(a, b)| py_eq(a, b))
            };
            n.need(partition, "render_groups must partition configs exactly");
        }
        n.need(
            nonempty_list(get(unit, "kinds")),
            "kinds must be a nonempty list",
        );
        n.need(is_bool(get(unit, "exemplar")), "exemplar must be a bool");
        n.need(
            is_list(get(unit, "provenance")),
            "provenance must be a list",
        );
        n.need(
            is_list(get(unit, "boundary_marks")),
            "boundary_marks must be a list",
        );
        if let Some(marks) = get(unit, "boundary_marks")
            && truthy(marks)
        {
            for mark in members(marks)? {
                n.need(
                    matches!(mark, PyValue::Dict(_)) && MARK_KEYS.iter().all(|key| has(&mark, key)),
                    "boundary marks must carry index/kind/x",
                );
            }
        }

        let codepoints = get(unit, "codepoints");
        let renderable = !is_none(codepoints);
        n.need(renderable, "codepoints must be present in m1-audit mode");
        if renderable {
            n.need(
                matches!(codepoints, Some(PyValue::Str(text))
                    if text.chars().all(|letter| letter == ':' || UPPER_HEX.contains(letter))),
                "codepoints must be colon-joined uppercase hex",
            );
            n.need(
                matches!(get(unit, "text_entities"), Some(PyValue::Str(text))
                    if text.starts_with("&#x") && text.ends_with(';')),
                "text_entities must be numeric character references",
            );
        }

        let before = get(unit, "before");
        let after = get(unit, "after");
        n.need(
            is_dict(before) && is_list(get(before, "glyphs")),
            "before.glyphs must be a list",
        );
        n.need(
            is_dict(before) && is_list(get(before, "junctions")),
            "before.junctions must be a list",
        );
        n.need(
            is_dict(after) && is_list(get(after, "cells")),
            "after.cells must be a list",
        );
        n.need(
            is_dict(after) && is_list(get(after, "junctions")),
            "after.junctions must be a list",
        );
        n.need(
            is_dict(after) && is_list(get(after, "extensions")),
            "after.extensions must be a list",
        );
        for (side, label) in [(before, "before"), (after, "after")] {
            if let Some(PyValue::List(junctions)) = get(side, "junctions") {
                n.need(
                    junctions.iter().all(is_junction_token),
                    format_args!("{label}.junctions must be break/lig/yN tokens"),
                );
            }
        }
        if is_dict(before) && is_dict(after) {
            let size = |side: Option<&PyValue>, key: &str| -> Result<usize> {
                get(side, key).map_or(Ok(0), length)
            };
            let junctions = size(before, "junctions")?;
            let glyphs = size(before, "glyphs")?;
            n.need(
                junctions == glyphs.saturating_sub(1),
                "before.junctions must have one entry per inter-glyph gap",
            );
            let junctions = size(after, "junctions")?;
            let cells = size(after, "cells")?;
            n.need(
                junctions == cells.saturating_sub(1),
                "after.junctions must have one entry per inter-cell gap",
            );
            let extensions = size(after, "extensions")?;
            let junctions = size(after, "junctions")?;
            n.need(
                extensions == junctions,
                "after.extensions must parallel after.junctions",
            );
        }

        n.need(
            is_list(get(unit, "diff_positions")),
            "diff_positions must be a list",
        );
        if !is_none(pair) {
            n.need(
                is_dict(pair)
                    && matches!(
                        (int_of(get(pair, "left")), int_of(get(pair, "right"))),
                        (Some(left), Some(right)) if left < right
                    ),
                "pair must be {left, right} with left < right",
            );
        }

        let tokens = get(unit, "notation_tokens");
        n.need(
            matches!(tokens, Some(PyValue::List(items))
                if !items.is_empty() && items.iter().all(|token| nonempty_str(Some(token)))),
            "notation_tokens must be a nonempty list of nonempty strings in m1-audit mode",
        );
        if let (Some(PyValue::Str(codepoints)), Some(PyValue::List(tokens))) = (codepoints, tokens)
        {
            n.need(
                tokens.len() == codepoints.split(':').count(),
                "notation_tokens must align one-to-one with codepoint positions",
            );
        }
        n.need(
            has(fragment, "pair_codepoints"),
            "pair_codepoints must be present",
        );
        let span = get(unit, "pair_codepoints");
        if !is_none(span) {
            let bounds = match span {
                Some(PyValue::List(items)) if items.len() == 2 => {
                    int_of(Some(&items[0])).zip(int_of(Some(&items[1])))
                }
                _ => None,
            };
            n.need(
                bounds.is_some_and(|(start, end)| 0 <= start && start <= end),
                "pair_codepoints must be [start, end] with 0 <= start <= end",
            );
            if let (Some(PyValue::List(items)), Some(PyValue::List(tokens))) = (span, tokens)
                && items.len() == 2
            {
                let count = i64::try_from(tokens.len()).expect("a token count fits an i64");
                n.need(
                    int_of(Some(&items[1])).is_some_and(|end| end < count),
                    "pair_codepoints must stay within the codepoint positions",
                );
            }
        }
        if !is_none(pair) {
            n.need(
                is_list(span),
                "pair_codepoints must be non-null when pair is present",
            );
        }

        let highlight = get(unit, "highlight");
        if !slim {
            n.need(
                !is_none(highlight),
                "highlight must be present in m1-audit mode",
            );
        }
        if !is_none(highlight) {
            for side in ["before", "after"] {
                let record = if is_dict(highlight) {
                    get(highlight, side)
                } else {
                    None
                };
                n.need_rect(record, &format!("highlight.{side}"));
            }
        }

        let drafts = get(unit, "drafts");
        if !slim {
            n.need(
                is_dict(drafts) && DRAFT_KEYS.iter().all(|key| get(drafts, key).is_some()),
                "drafts must carry pin/policy/any_of",
            );
        }
        if is_dict(drafts) {
            self.check_drafts(
                &mut n,
                unit,
                get(drafts, "pin"),
                get(drafts, "policy"),
                get(drafts, "any_of"),
            )?;
        }
        Ok(n.errors)
    }

    fn check_drafts(
        &self,
        n: &mut Needs,
        unit: Option<&PyValue>,
        pin: Option<&PyValue>,
        policy: Option<&PyValue>,
        any_of: Option<&PyValue>,
    ) -> Result<()> {
        n.need(!is_none(pin), "drafts.pin must be present in m1-audit mode");
        if !is_none(pin) {
            if !is_dict(pin) {
                return raise("AttributeError", "drafts.pin has no attribute 'get'");
            }
            for key in PIN_KEYS {
                n.need(
                    nonempty_str(get(pin, key)),
                    format_args!("drafts.pin.{key} must be a nonempty string"),
                );
            }
            let attribute = get(pin, "attribute");
            n.need(
                PIN_ATTRIBUTES.iter().any(|name| str_is(attribute, name)),
                "drafts.pin.attribute must be a data-expect attribute name",
            );
            let set = get(pin, "stylistic_set");
            n.need(
                is_none(set) || is_str(set),
                "drafts.pin.stylistic_set must be null or a string",
            );
            for key in ["syntax", "semantics_after_font"] {
                let value = get(pin, key);
                if !str_is(value, "pass") {
                    n.need(false, format_args!("drafts.pin.{key} is {}", shown(value)?));
                }
            }
        }
        if !is_none(policy) {
            if !is_dict(policy) {
                return raise("AttributeError", "drafts.policy has no attribute 'get'");
            }
            for key in POLICY_KEYS {
                n.need(
                    nonempty_str(get(policy, key)),
                    format_args!("drafts.policy.{key} must be a nonempty string"),
                );
            }
            let names = get(policy, "names_provenance");
            n.need(
                is_list(names),
                "drafts.policy.names_provenance must be a list",
            );
            let valid = get(policy, "schema_valid");
            n.need(is_bool(valid), "drafts.policy.schema_valid must be a bool");
            n.need(
                is_true(valid),
                "drafts.policy.suggested_record must validate against the rune schema",
            );
            let keypath = get(policy, "keypath");
            if !POLICY_KEYPATHS.iter().any(|path| str_is(keypath, path)) {
                n.need(
                    false,
                    format_args!("drafts.policy.keypath is {}", shown(keypath)?),
                );
            }
            if let (Some(PyValue::List(names)), Some(PyValue::List(provenance))) =
                (names, get(unit, "provenance"))
            {
                let names = distinct(names)?;
                let provenance = distinct(provenance)?;
                n.need(
                    subset(&names, &provenance),
                    "drafts.policy.names_provenance must come from the unit's own provenance",
                );
            }
        }
        n.need(
            !is_none(any_of),
            "drafts.any_of must be present in m1-audit mode",
        );
        if !is_none(any_of) {
            if !is_dict(any_of) {
                return raise("AttributeError", "drafts.any_of has no attribute 'get'");
            }
            n.need(
                nonempty_str(get(any_of, "text")),
                "drafts.any_of.text must be a nonempty string",
            );
            n.need(
                is_dict(get(any_of, "features")),
                "drafts.any_of.features must be a mapping",
            );
            let candidates = get(any_of, "candidates");
            n.need(
                nonempty_list(candidates),
                "drafts.any_of.candidates must be a nonempty list",
            );
            if let Some(PyValue::List(candidates)) = candidates {
                n.need(
                    distinct(candidates)?.len() == candidates.len(),
                    "drafts.any_of.candidates must not repeat a behavior",
                );
                let expect = if is_dict(pin) {
                    get(pin, "expect")
                } else {
                    None
                };
                for candidate in candidates {
                    let PyValue::Str(text) = candidate else {
                        continue;
                    };
                    if same(Some(candidate), expect) {
                        continue;
                    }
                    if let Err(message) = self.parser.parse(text) {
                        n.need(
                            false,
                            format_args!(
                                "drafts.any_of candidate {} does not parse: {message}",
                                repr_str(text)
                            ),
                        );
                    }
                }
            }
        }
        Ok(())
    }
}

/// `build.check_unit(fragment, "m1-audit", at=(PATCHED,))` over the build input's `MACHINE_CHECKS`: the predicates over the fields `patch_fragment` assigns after the whole-corpus passes, the duplicate group, the cluster and the secondary junctions with their primary units.
pub struct PatchedCheck {
    machine_checks: Vec<String>,
}

impl PatchedCheck {
    /// The check over the machine checks in their order.
    pub fn new(machine_checks: Vec<String>) -> Self {
        Self { machine_checks }
    }

    /// The check over the build input's vocabulary.
    pub fn of(input: &BuildInput) -> Result<Self> {
        let Some(PyValue::Tuple(items)) = input.constant(MACHINE_CHECKS_NAME) else {
            return engine(format!(
                "the build input's {MACHINE_CHECKS_NAME} is not a tuple"
            ));
        };
        let machine_checks = items
            .iter()
            .map(|item| match item {
                PyValue::Str(text) => Ok(text.clone()),
                other => engine(format!(
                    "the build input's {MACHINE_CHECKS_NAME} holds {}, not a str",
                    other.kind()
                )),
            })
            .collect::<Result<_>>()?;
        Ok(Self::new(machine_checks))
    }

    /// `check_unit(fragment, "m1-audit", at=(PATCHED,))`: the errors in Python's order, or the raise Python meets first.
    pub fn check(&self, fragment: &PyValue) -> Result<Vec<String>> {
        let mut n = Needs::of(fragment)?;
        let unit = Some(fragment);
        let approving = self
            .machine_checks
            .iter()
            .any(|check| is_true(get(unit, check)));
        let human = !approving && !is_true(get(unit, "no_verdict"));
        let pair = get(unit, "pair");

        n.need(
            has(fragment, "duplicate_group"),
            "duplicate_group must be present",
        );
        let duplicate_group = get(unit, "duplicate_group");
        n.need(
            is_none(duplicate_group)
                || matches!(duplicate_group, Some(PyValue::Str(text)) if text.starts_with("e-")),
            "duplicate_group must be null or an e- group id",
        );
        if human {
            n.need(
                is_str(duplicate_group),
                "human units must carry a duplicate group id",
            );
        } else {
            n.need(
                is_none(duplicate_group),
                "machine-approved and no-verdict units must carry duplicate_group null",
            );
        }
        n.need(has(fragment, "cluster"), "cluster must be present");
        let cluster = get(unit, "cluster");
        n.need(
            is_none(cluster)
                || matches!(cluster, Some(PyValue::Str(text)) if text.starts_with("c-")),
            "cluster must be null or a c-XXXXXXXX signature id",
        );
        if human {
            n.need(
                is_str(cluster),
                "human units must carry a cluster signature id",
            );
        } else {
            n.need(
                is_none(cluster),
                "machine-approved and no-verdict units must carry cluster null",
            );
        }

        let junctions = get(unit, "secondary_junctions");
        if is_none(junctions) {
            return Ok(n.errors);
        }
        n.need(
            nonempty_list(junctions),
            "secondary_junctions must be null or a nonempty list",
        );
        n.need(
            !is_true(get(unit, "ink_identical")) && !is_true(get(unit, "picture_identical")),
            "ink-identical and picture-identical units must not carry secondary_junctions",
        );
        let Some(PyValue::List(junctions)) = junctions else {
            return Ok(n.errors);
        };
        for (index, junction) in junctions.iter().enumerate() {
            let label = format!("secondary_junctions[{index}]");
            if !(matches!(junction, PyValue::Dict(_))
                && JUNCTION_KEYS.iter().all(|key| has(junction, key)))
            {
                n.need(
                    false,
                    format_args!("{label} must carry pair/before/after/primary_unit"),
                );
                continue;
            }
            let junction = Some(junction);
            let junction_pair = get(junction, "pair");
            n.need(
                is_dict(junction_pair)
                    && matches!(
                        (int_of(get(junction_pair, "left")), int_of(get(junction_pair, "right"))),
                        (Some(left), Some(right)) if left < right
                    ),
                format_args!("{label}.pair must be {{left, right}} with left < right"),
            );
            if is_dict(pair) && is_dict(junction_pair) {
                n.need(
                    !(same(get(junction_pair, "left"), get(pair, "left"))
                        && same(get(junction_pair, "right"), get(pair, "right"))),
                    format_args!("{label} must not duplicate the primary pair"),
                );
            }
            n.need_rect(get(junction, "before"), &format!("{label}.before"));
            n.need_rect(get(junction, "after"), &format!("{label}.after"));
            let primary_unit = get(junction, "primary_unit");
            n.need(
                is_none(primary_unit)
                    || matches!(primary_unit, Some(PyValue::Str(text)) if text.starts_with("u-")),
                format_args!("{label}.primary_unit must be null or a unit id"),
            );
        }
        Ok(n.errors)
    }
}

/// Answer each line of `stage_file` with `check` over its `fragment`: one `{"key", "value"}` line, the value the error list, or `{"raised": name}` where Python raises, in the typed encoding. A failure Python never meets stops the stage.
fn check_lines(
    stage_file: &Path,
    out: &mut impl Write,
    check: impl Fn(&PyValue) -> Result<Vec<String>>,
) -> Result<()> {
    let file = std::fs::File::open(stage_file)
        .or_else(|failure| engine(format!("cannot read {}: {failure}", stage_file.display())))?;
    for line in std::io::BufReader::new(file).lines() {
        let line = line.or_else(|failure| {
            engine(format!("cannot read {}: {failure}", stage_file.display()))
        })?;
        let line: Value = serde_json::from_str(&line)
            .or_else(|failure| engine(format!("a stage line is not JSON: {failure}")))?;
        let Some(key) = line["key"].as_str() else {
            return engine("a stage line has no key");
        };
        let fragment = PyValue::from_typed(&line["fragment"])
            .or_else(|failure| engine(format!("{key}: {failure}")))?;
        let value = match check(&fragment) {
            Ok(errors) => PyValue::List(errors.into_iter().map(PyValue::Str).collect()),
            Err(CheckError {
                raised: Some(kind), ..
            }) => PyValue::Dict(vec![(PyValue::str("raised"), PyValue::str(kind))]),
            Err(failure) => return engine(format!("{key}: {failure}")),
        };
        writeln!(
            out,
            "{{\"key\":{},\"value\":{}}}",
            json_string(key),
            value.to_typed()
        )
        .or_else(|failure| engine(format!("cannot write a stage line: {failure}")))?;
    }
    Ok(())
}

/// The `check` stage: each line's `fragment` checked at `DRAFTED` over the init's vocabulary and glyph names ([`ContractCheck::check_drafted`]).
pub fn stage_check(init_path: &Path, stage_file: &Path, out: &mut impl Write) -> Result<()> {
    let init = Init::read(init_path).or_else(|failure| engine(failure.0))?;
    let check = ContractCheck::new(&init)?;
    check_lines(stage_file, out, |fragment| check.check_drafted(fragment))
}

/// The build's `check-patched` stage: each line's `fragment` checked at `PATCHED` over the build input's machine checks ([`PatchedCheck::check`]).
pub fn stage_check_patched(
    input_path: &Path,
    stage_file: &Path,
    out: &mut impl Write,
) -> Result<()> {
    let input = BuildInput::read(input_path).or_else(|failure| engine(failure.0))?;
    let check = PatchedCheck::of(&input)?;
    check_lines(stage_file, out, |fragment| check.check(fragment))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn strs(items: &[&str]) -> PyValue {
        PyValue::List(items.iter().map(|item| PyValue::str(*item)).collect())
    }

    #[test]
    fn an_id_matches_with_one_final_newline_and_only_base58_symbols() {
        let id = |text: &str| is_content_id(Some(&PyValue::str(text)));
        assert!(id("u-21ndiEFZW2y"));
        assert!(id("u-21ndiEFZW2y\n"));
        assert!(id("e-21ndiEFZW2y"));
        assert!(!id("u-21ndiEFZW2y\n\n"));
        assert!(!id("u-21ndiEFZW20"));
        assert!(!id("u-21ndiEFZW2"));
        assert!(!is_content_id(Some(&PyValue::Int(1))));
    }

    #[test]
    fn a_content_key_is_read_as_int_reads_it_and_a_negative_value_floors() {
        assert_eq!(
            unit_id_for("060f4922c295fdc66eeb9ad33f1bc2f5ce3c1a194b0ba1688b5e29cdb0bda04e"),
            Ok("u-21ndiEFZW2y".to_owned())
        );
        assert_eq!(
            unit_id_for("-1"),
            Ok(format!("u-{}", "z".repeat(ID_SYMBOLS)))
        );
        assert_eq!(
            unit_id_for("g").map_err(|failure| failure.raised),
            Err(Some("ValueError"))
        );
    }

    #[test]
    fn a_junction_token_is_a_named_token_or_y_and_unicode_digits() {
        for token in [
            "break",
            "lig",
            "absent",
            "y0",
            "y12",
            "y\u{0663}",
            "y\u{00b2}",
        ] {
            assert!(is_junction_token(&PyValue::str(token)), "{token}");
        }
        for token in ["y", "Y5", "y0+y5", "y-1", "brk"] {
            assert!(!is_junction_token(&PyValue::str(token)), "{token}");
        }
    }

    #[test]
    fn the_patched_check_reads_the_scaffold_and_each_secondary_junction() {
        let check = PatchedCheck::new(vec!["ink_identical".to_owned()]);
        let dict = |entries: Vec<(&str, PyValue)>| {
            PyValue::Dict(
                entries
                    .into_iter()
                    .map(|(key, value)| (PyValue::str(key), value))
                    .collect(),
            )
        };
        let pair = |left, right| {
            dict(vec![
                ("left", PyValue::Int(left)),
                ("right", PyValue::Int(right)),
            ])
        };
        let rect = dict(vec![
            ("x_min", PyValue::Int(0)),
            ("x_max", PyValue::Int(1)),
            ("advance_total", PyValue::Int(2)),
        ]);
        let junction = dict(vec![
            ("pair", pair(0, 1)),
            ("before", rect.clone()),
            ("after", rect),
            ("primary_unit", PyValue::None),
        ]);
        let fragment = dict(vec![
            ("id", PyValue::str("u-x")),
            ("pair", pair(0, 1)),
            ("duplicate_group", PyValue::str("e-x")),
            ("cluster", PyValue::str("c-x")),
            (
                "secondary_junctions",
                PyValue::List(vec![junction, PyValue::Int(5)]),
            ),
        ]);
        assert_eq!(
            check.check(&fragment),
            Ok(vec![
                "unit u-x: secondary_junctions[0] must not duplicate the primary pair".to_owned(),
                "unit u-x: secondary_junctions[1] must carry pair/before/after/primary_unit"
                    .to_owned(),
            ])
        );
        let machine = dict(vec![
            ("ink_identical", PyValue::Bool(true)),
            ("cluster", PyValue::None),
        ]);
        assert_eq!(
            check.check(&machine),
            Ok(vec![
                "unit <missing>: duplicate_group must be present".to_owned(),
            ])
        );
        assert_eq!(
            check
                .check(&PyValue::List(Vec::new()))
                .map_err(|failure| failure.raised),
            Err(Some("AttributeError"))
        );
    }

    #[test]
    fn sorting_raises_only_across_types_python_cannot_order() {
        assert!(sorted(&[PyValue::Int(2), PyValue::Bool(true)]).is_ok());
        assert_eq!(
            sorted(&[PyValue::Int(1), PyValue::str("a")]).map_err(|failure| failure.raised),
            Err(Some("TypeError"))
        );
        assert_eq!(
            sorted(&[PyValue::None, PyValue::None]).map_err(|failure| failure.raised),
            Err(Some("TypeError"))
        );
        assert!(sorted(&[PyValue::None]).is_ok());
        assert_eq!(
            distinct(&[strs(&["a"])]).map_err(|failure| failure.raised),
            Err(Some("TypeError"))
        );
        assert_eq!(
            distinct(&[PyValue::Int(1), PyValue::Bool(true)]).map(|held| held.len()),
            Ok(1)
        );
    }
}
