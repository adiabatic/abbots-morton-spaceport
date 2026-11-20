//! Expect strings as the drafter writes, parses and replays them: `drafts.expect_string` and `_token_for_span` ([`expect_string`]); `test_shaping.parse_expect` with `_letter_to_qs` ([`ExpectParser`]), `_expand_maybe_ligatures` ([`expand_maybe_ligatures`]), `_token_char_spans`, `_is_modifier_char` and `_token_input_char_count` ([`token_char_spans`]); `pins._check_interpretation` and `_expected_base` ([`check_interpretation`]); and the replay against the after font, `Drafter.validate_semantics` ([`Replayer`]).
//!
//! The parser reads the strings `parse_expect`'s regular expressions read, matched by hand in `re`'s order: `\s` and `strip` are `str.isspace` ([`is_space`]), `.` is any character but a newline, and the letter classes are ASCII. Positions are code point indices, as Python's are.
//!
//! [`stage_expect`] is the `expect` stage of the stage fixtures.

use std::collections::HashMap;
use std::fmt;
use std::io::{BufRead, Write};
use std::path::Path;

use ams_m1_kernel::emit::json_string;
use serde_json::Value;

use crate::classify::{BREAK, LIGATURE, Row, RowError, row_for};
use crate::enrich::{init_letters, letter_display};
use crate::fontfacts::FontFacts;
use crate::pyfmt::repr::{repr, repr_str};
use crate::pyfmt::{PyValue, is_space};
use crate::shape::{Features, Fonts, ShapeError, Shaper};
use crate::wire::{Init, WireError};

/// `drafts.CONNECTORS`: each junction token's connector in a drafted expect string.
const CONNECTORS: [(&str, &str); 5] = [
    ("break", " | "),
    ("y0", " ~b~ "),
    ("y5", " ~x~ "),
    ("y6", " ~6~ "),
    ("y8", " ~t~ "),
];

/// `drafts._BOUNDARY_EXPECT`: each boundary code point's token in a drafted expect string.
const BOUNDARY_EXPECT: [(u32, &str); 3] = [(0x0020, "◊space"), (0x200C, "◊ZWNJ"), (0x00B7, "\\·")];

/// `parse_expect`'s `HEIGHT_MAP`: each height connector's letter and the junction height it asserts.
const HEIGHT_MAP: [(char, i64); 4] = [('x', 5), ('b', 0), ('t', 8), ('6', 6)];

/// `LOZENGE_MAP`: each lozenge name and the glyph it stands for.
const LOZENGE_MAP: [(&str, &str); 2] = [("space", "space"), ("ZWNJ", "space")];

/// `model.SS10_COPY_SUFFIX`, which the replay strips from each shaped glyph name.
pub const SS10_COPY_SUFFIX: &str = ".ss10";

/// The traits `_check_interpretation` checks against a glyph's name; it skips every other variant assertion.
const NAMED_TRAITS: [&str; 2] = ["half", "alt"];

/// The middle dot that opens a letter token.
const LETTER_DOT: char = '·';

/// A failure the engine meets where Python would raise, with the exception's class name, or one Python never meets, with none.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ExpectError {
    pub raised: Option<&'static str>,
    pub message: String,
}

impl ExpectError {
    fn value(message: impl Into<String>) -> Self {
        Self {
            raised: Some("ValueError"),
            message: message.into(),
        }
    }

    fn engine(message: impl Into<String>) -> Self {
        Self {
            raised: None,
            message: message.into(),
        }
    }
}

impl fmt::Display for ExpectError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self.raised {
            Some(kind) => write!(formatter, "{kind}: {}", self.message),
            None => formatter.write_str(&self.message),
        }
    }
}

impl std::error::Error for ExpectError {}

impl From<WireError> for ExpectError {
    fn from(error: WireError) -> Self {
        Self::engine(error.0)
    }
}

impl From<ShapeError> for ExpectError {
    fn from(error: ShapeError) -> Self {
        Self::engine(error.0)
    }
}

impl From<RowError> for ExpectError {
    fn from(error: RowError) -> Self {
        match error {
            RowError::Shape(error) => error.into(),
            RowError::Value(message) => Self::value(message),
        }
    }
}

type Result<T> = std::result::Result<T, ExpectError>;

/// A token's ligature mode: `+?` or `+|`; a required ligature (`+`) has none.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum LigMode {
    Maybe,
    MaybeBreak,
}

impl LigMode {
    fn name(self) -> &'static str {
        match self {
            LigMode::Maybe => "maybe",
            LigMode::MaybeBreak => "maybe_break",
        }
    }
}

/// One `ExpectToken`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ExpectToken {
    pub base: String,
    pub lig_base: Option<String>,
    pub lig_mode: Option<LigMode>,
    pub variants: Vec<String>,
    pub neg_variants: Vec<String>,
    pub exact_glyph: bool,
}

impl ExpectToken {
    fn bare(base: impl Into<String>) -> Self {
        Self {
            base: base.into(),
            lig_base: None,
            lig_mode: None,
            variants: Vec::new(),
            neg_variants: Vec::new(),
            exact_glyph: false,
        }
    }

    /// The token as Python holds it, a dict in `parse_expect`'s key order.
    pub fn to_py(&self) -> PyValue {
        let strings = |items: &[String]| PyValue::List(items.iter().map(PyValue::str).collect());
        PyValue::Dict(vec![
            (PyValue::str("base"), PyValue::str(&self.base)),
            (
                PyValue::str("lig_base"),
                PyValue::optional_str(self.lig_base.as_deref()),
            ),
            (
                PyValue::str("lig_mode"),
                PyValue::optional_str(self.lig_mode.map(LigMode::name)),
            ),
            (PyValue::str("variants"), strings(&self.variants)),
            (PyValue::str("neg_variants"), strings(&self.neg_variants)),
            (PyValue::str("exact_glyph"), PyValue::Bool(self.exact_glyph)),
        ])
    }
}

/// A connection's kind, as `Connection.kind` names it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ConnectionKind {
    Join,
    Height,
    Break,
    BreakNoIsolation,
    Maybe,
}

impl ConnectionKind {
    fn name(self) -> &'static str {
        match self {
            ConnectionKind::Join => "join",
            ConnectionKind::Height => "height",
            ConnectionKind::Break => "break",
            ConnectionKind::BreakNoIsolation => "break_no_isolation",
            ConnectionKind::Maybe => "maybe",
        }
    }
}

/// One `Connection`: its kind and, for a height connection, the height.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Connection {
    pub kind: ConnectionKind,
    pub y: Option<i64>,
}

impl Connection {
    fn of(kind: ConnectionKind) -> Self {
        Self { kind, y: None }
    }

    /// The connection as Python holds it, `{"kind": ..., "y": ...}`.
    pub fn to_py(&self) -> PyValue {
        PyValue::Dict(vec![
            (PyValue::str("kind"), PyValue::str(self.kind.name())),
            (
                PyValue::str("y"),
                self.y.map_or(PyValue::None, PyValue::Int),
            ),
        ])
    }
}

/// A parsed expect string, its tokens and the connections between them.
pub type Parsed = (Vec<ExpectToken>, Vec<Connection>);

/// A parse as Python holds it, a tuple of the two lists.
pub fn parsed_to_py((tokens, connections): &Parsed) -> PyValue {
    PyValue::Tuple(vec![
        PyValue::List(tokens.iter().map(ExpectToken::to_py).collect()),
        PyValue::List(connections.iter().map(Connection::to_py).collect()),
    ])
}

fn leading_space(chars: &[char]) -> usize {
    chars.iter().take_while(|&&letter| is_space(letter)).count()
}

fn chars_text(chars: &[char]) -> String {
    chars.iter().collect()
}

fn is_upper(letter: char) -> bool {
    letter.is_ascii_uppercase()
}

fn is_lower(letter: char) -> bool {
    letter.is_ascii_lowercase()
}

/// The length of `[A-Z][a-z]*` at the start of `chars`, or None.
fn capitalized(chars: &[char]) -> Option<usize> {
    if !chars.first().is_some_and(|&letter| is_upper(letter)) {
        return None;
    }
    Some(
        1 + chars[1..]
            .iter()
            .take_while(|&&letter| is_lower(letter))
            .count(),
    )
}

/// `CONN_RE.match`: the connection at the start of `chars` and the length it consumes, trying its four alternatives in order.
fn match_connection(chars: &[char]) -> Option<(Connection, usize)> {
    let start = leading_space(chars);
    let rest = &chars[start..];
    let (connection, length) = match rest {
        ['~', height, '~', ..] => {
            let (_, y) = HEIGHT_MAP.iter().find(|(letter, _)| letter == height)?;
            (
                Connection {
                    kind: ConnectionKind::Height,
                    y: Some(*y),
                },
                3,
            )
        }
        ['|', '?', '|', ..] => (Connection::of(ConnectionKind::BreakNoIsolation), 3),
        ['?', ..] => (Connection::of(ConnectionKind::Maybe), 1),
        ['|', ..] => (Connection::of(ConnectionKind::Break), 1),
        _ => return None,
    };
    let end = start + length;
    Some((connection, end + leading_space(&chars[end..])))
}

/// `TOKEN_RE.match` at the start of `chars`: its four groups as character slices (the ligature mode and partner None when the ligature group did not match) and the length it consumes.
struct TokenMatch<'c> {
    letter: &'c [char],
    lig_mode: Option<&'c [char]>,
    lig_partner: Option<&'c [char]>,
    variants: &'c [char],
    length: usize,
}

fn match_token(chars: &[char]) -> Option<TokenMatch<'_>> {
    if chars.first() != Some(&LETTER_DOT) {
        return None;
    }
    let mut at = 1;
    let rest = &chars[at..];
    let letter_length = if rest.starts_with(&['-', 'i', 'n', 'g']) {
        4
    } else if let ['J', quote, 'a', 'i', ..] = rest
        && matches!(quote, '\'' | '\u{2019}')
    {
        4
    } else if rest.starts_with(&['J', 'a', 'i']) {
        3
    } else {
        capitalized(rest)?
    };
    let letter = &chars[at..at + letter_length];
    at += letter_length;
    let (mut lig_mode, mut lig_partner) = (None, None);
    if chars.get(at) == Some(&'+') {
        let after = at + 1;
        let with_mode = if matches!(chars.get(after), Some('?' | '|')) {
            capitalized(&chars[after + 1..]).map(|length| (1, length))
        } else {
            None
        };
        if let Some((mode_length, length)) =
            with_mode.or_else(|| capitalized(&chars[after..]).map(|length| (0, length)))
        {
            lig_mode = Some(&chars[after..after + mode_length]);
            lig_partner = Some(&chars[after + mode_length..after + mode_length + length]);
            at = after + mode_length + length;
        }
    }
    let variants_start = at;
    while chars.get(at) == Some(&'.') {
        let next = at + 1;
        let length = match chars.get(next) {
            Some('!') if chars.get(next + 1).is_some_and(|&letter| is_lower(letter)) => {
                2 + variant_tail(&chars[next + 2..])
            }
            Some(&letter) if is_lower(letter) => 1 + variant_tail(&chars[next + 1..]),
            Some('∅') => 1,
            _ => break,
        };
        at = next + length;
    }
    Some(TokenMatch {
        letter,
        lig_mode,
        lig_partner,
        variants: &chars[variants_start..at],
        length: at,
    })
}

/// The length of `[-a-z0-9]*` at the start of `chars`.
fn variant_tail(chars: &[char]) -> usize {
    chars
        .iter()
        .take_while(|&&letter| letter == '-' || is_lower(letter) || letter.is_ascii_digit())
        .count()
}

/// `_letter_to_qs`: a letter token's family name.
fn letter_to_qs(name: &str) -> String {
    if name == "-ing" {
        return "qsIng".to_owned();
    }
    format!("qs{}", name.replace(['\u{2019}', '\''], ""))
}

/// `parse_expect` over the init's `_CHAR_TO_GLYPH`, which names the glyph of an escaped character.
pub struct ExpectParser {
    glyph_names: HashMap<char, String>,
}

impl ExpectParser {
    /// The parser over the init's `glyph_names_by_char`.
    pub fn new(init: &Init) -> Self {
        Self {
            glyph_names: init.glyph_names_by_char.iter().cloned().collect(),
        }
    }

    /// `parse_expect(raw)`: the tokens and the connections between them, or the message of the ValueError it raises.
    pub fn parse(&self, raw: &str) -> std::result::Result<Parsed, String> {
        let chars: Vec<char> = raw.chars().collect();
        let start = leading_space(&chars);
        let end = chars.len()
            - chars[start..]
                .iter()
                .rev()
                .take_while(|&&letter| is_space(letter))
                .count();
        let raw = &chars[start..end];
        let raw_repr = || repr_str(&chars_text(raw));
        let mut tokens = Vec::new();
        let mut connections = Vec::new();
        let mut pos = 0;
        let mut first = true;
        while pos < raw.len() {
            if first {
                first = false;
            } else {
                match match_connection(&raw[pos..]) {
                    Some((connection, length)) => {
                        connections.push(connection);
                        pos += length;
                    }
                    None => {
                        let space = leading_space(&raw[pos..]);
                        if space == 0 {
                            return Err(format!(
                                "Expected connection operator at pos {pos}: {}",
                                repr_str(&chars_text(&raw[pos..]))
                            ));
                        }
                        connections.push(Connection::of(ConnectionKind::Join));
                        pos += space;
                    }
                }
            }
            pos += leading_space(&raw[pos..]);
            let remaining = &raw[pos..];

            if let ['\\', escaped, ..] = remaining
                && *escaped != '\n'
            {
                let name = self
                    .glyph_names
                    .get(escaped)
                    .cloned()
                    .unwrap_or_else(|| format!("uni{:04X}", u32::from(*escaped)));
                tokens.push(ExpectToken::bare(name));
                pos += 2;
                continue;
            }

            if remaining.first() == Some(&'◊') {
                let length = remaining[1..]
                    .iter()
                    .take_while(|letter| letter.is_ascii_alphabetic())
                    .count();
                if length > 0 {
                    let key = chars_text(&remaining[1..1 + length]);
                    let Some((_, glyph)) = LOZENGE_MAP.iter().find(|(name, _)| *name == key) else {
                        return Err(format!("Unknown lozenge name: ◊{key}"));
                    };
                    tokens.push(ExpectToken::bare(*glyph));
                    pos += 1 + length;
                    continue;
                }
            }

            let Some(matched) = match_token(remaining) else {
                return Err(format!(
                    "Expected glyph token at pos {pos}: {}",
                    repr_str(&chars_text(remaining))
                ));
            };
            let lig_mode = match (matched.lig_partner, matched.lig_mode) {
                (Some(_), Some(['?'])) => Some(LigMode::Maybe),
                (Some(_), Some(['|'])) => Some(LigMode::MaybeBreak),
                _ => None,
            };
            let mut token = ExpectToken::bare(letter_to_qs(&chars_text(matched.letter)));
            token.lig_base = matched
                .lig_partner
                .map(|partner| letter_to_qs(&chars_text(partner)));
            token.lig_mode = lig_mode;
            let variant_text = chars_text(matched.variants);
            for variant in variant_text.split('.') {
                if variant.is_empty() {
                    continue;
                }
                if variant == "∅" {
                    if token.exact_glyph {
                        return Err(format!(
                            "Exact glyph assertion .∅ appears more than once in {}",
                            raw_repr()
                        ));
                    }
                    token.exact_glyph = true;
                } else if let Some(negated) = variant.strip_prefix('!') {
                    token.neg_variants.push(negated.to_owned());
                } else {
                    token.variants.push(variant.to_owned());
                }
            }
            if token.exact_glyph && !(token.variants.is_empty() && token.neg_variants.is_empty()) {
                return Err(format!(
                    "Exact glyph assertion .∅ cannot be combined with other variant assertions in {}",
                    raw_repr()
                ));
            }
            tokens.push(token);
            pos += matched.length;
        }
        Ok((tokens, connections))
    }
}

/// `_expand_maybe_ligatures`: the parse itself when no token is a maybe-ligature, and otherwise one interpretation per way of keeping or splitting each, in `itertools.product([True, False], ...)` order, keeping first. A split token becomes its two letters, bare, with a maybe connection for `+?` and a break for `+|` between them; every kept token loses its ligature mode.
pub fn expand_maybe_ligatures(tokens: &[ExpectToken], connections: &[Connection]) -> Vec<Parsed> {
    let maybe: Vec<usize> = tokens
        .iter()
        .enumerate()
        .filter(|(_, token)| token.lig_mode.is_some())
        .map(|(index, _)| index)
        .collect();
    if maybe.is_empty() {
        return vec![(tokens.to_vec(), connections.to_vec())];
    }
    let count = maybe.len();
    (0..1usize << count)
        .map(|combo| {
            let keeps = |index: usize| -> bool {
                maybe
                    .iter()
                    .position(|&held| held == index)
                    .is_none_or(|slot| (combo >> (count - 1 - slot)) & 1 == 0)
            };
            let mut new_tokens = Vec::new();
            let mut new_connections = Vec::new();
            for (index, token) in tokens.iter().enumerate() {
                if index > 0 {
                    new_connections.push(connections[index - 1]);
                }
                if keeps(index) {
                    new_tokens.push(ExpectToken {
                        lig_mode: None,
                        ..token.clone()
                    });
                } else {
                    let kind = if token.lig_mode == Some(LigMode::Maybe) {
                        ConnectionKind::Maybe
                    } else {
                        ConnectionKind::Break
                    };
                    new_tokens.push(ExpectToken::bare(token.base.clone()));
                    new_connections.push(Connection::of(kind));
                    new_tokens.push(ExpectToken::bare(
                        token
                            .lig_base
                            .clone()
                            .expect("a maybe-ligature token has a partner"),
                    ));
                }
            }
            (new_tokens, new_connections)
        })
        .collect()
}

/// `_is_modifier_char`: whether a character is a variation selector, U+FE00 to U+FE0F.
fn is_modifier_char(letter: char) -> bool {
    ('\u{FE00}'..='\u{FE0F}').contains(&letter)
}

/// `_token_input_char_count`: two input characters for a ligature token, one otherwise.
fn token_input_char_count(token: &ExpectToken) -> usize {
    if token.lig_base.is_some() { 2 } else { 1 }
}

fn token_repr(token: &ExpectToken) -> String {
    repr(&token.to_py()).expect("a token has a repr")
}

/// `_token_char_spans`: each token's half-open code point range in `text`, with the variation selectors after its last character, or the ValueError's message when the text runs out or has characters left over.
pub fn token_char_spans(
    text: &[char],
    tokens: &[ExpectToken],
) -> std::result::Result<Vec<(usize, usize)>, String> {
    let mut spans = Vec::with_capacity(tokens.len());
    let mut index = 0;
    let skip_modifiers = |mut index: usize| {
        while index < text.len() && is_modifier_char(text[index]) {
            index += 1;
        }
        index
    };
    for token in tokens {
        index = skip_modifiers(index);
        if index >= text.len() {
            return Err(format!(
                "Not enough input chars for token {} at char {index}",
                token_repr(token)
            ));
        }
        let start = index;
        let base = token_input_char_count(token);
        let mut consumed = 0;
        while consumed < base {
            if index >= text.len() {
                return Err(format!(
                    "Token {} overruns text at char {index}",
                    token_repr(token)
                ));
            }
            if !is_modifier_char(text[index]) {
                consumed += 1;
            }
            index += 1;
        }
        index = skip_modifiers(index);
        spans.push((start, index));
    }
    if index != text.len() {
        return Err(format!(
            "Text not fully consumed by tokens: used {index} of {} chars",
            text.len()
        ));
    }
    Ok(spans)
}

/// `_expected_base`: the glyph name a token's base must be, its letters joined with `_` for a ligature.
fn expected_base(token: &ExpectToken) -> String {
    match &token.lig_base {
        Some(partner) => format!("{}_{partner}", token.base),
        None => token.base.clone(),
    }
}

/// `pins._check_interpretation` without its counts: the first assertion of one interpretation that `glyphs` (the row's glyph names as the replay renames them), `clusters` and `junctions` refute, None when none does, or the ValueError a span or a covering raises.
pub fn check_interpretation(
    text: &[char],
    tokens: &[ExpectToken],
    connections: &[Connection],
    glyphs: &[String],
    clusters: &[u32],
    junctions: &[String],
) -> Result<Option<String>> {
    let spans = token_char_spans(text, tokens).map_err(ExpectError::value)?;
    for (index, token) in tokens.iter().enumerate() {
        let (start, end) = spans[index];
        let glyph = &glyphs
            [crate::classify::last_glyph_covering(clusters, start).map_err(ExpectError::value)?];
        let mut parts = glyph.split('.');
        let base = parts.next().unwrap_or_default();
        let name_parts: Vec<&str> = parts.collect();
        let expected = expected_base(token);
        if base != expected {
            return Ok(Some(format!(
                "token {index}: expected base {expected}, got {}",
                repr_str(glyph)
            )));
        }
        if token.exact_glyph && *glyph != expected {
            return Ok(Some(format!(
                "token {index}: expected exact glyph {expected}, got {}",
                repr_str(glyph)
            )));
        }
        for variant in &token.variants {
            if NAMED_TRAITS.contains(&variant.as_str()) && !name_parts.contains(&variant.as_str()) {
                return Ok(Some(format!(
                    "token {index}: expected trait {} in {}",
                    repr_str(variant),
                    repr_str(glyph)
                )));
            }
        }
        for variant in &token.neg_variants {
            if NAMED_TRAITS.contains(&variant.as_str()) && name_parts.contains(&variant.as_str()) {
                return Ok(Some(format!(
                    "token {index}: trait {} must not appear in {}",
                    repr_str(variant),
                    repr_str(glyph)
                )));
            }
        }
        let inner = start..end.saturating_sub(1);
        if let Some((k, junction)) = junctions[inner.clone()]
            .iter()
            .zip(inner)
            .map(|(junction, k)| (k, junction))
            .find(|(_, junction)| *junction != LIGATURE)
        {
            return Ok(Some(format!(
                "token {index}: expected ligature junction at {k}, got {}",
                repr_str(junction)
            )));
        }
    }
    for (index, connection) in connections.iter().enumerate() {
        let junction_index = spans[index + 1].0 - 1;
        let junction = &junctions[junction_index];
        let refuted = match connection.kind {
            ConnectionKind::Maybe => continue,
            ConnectionKind::Height => {
                let y = connection.y.expect("a height connection has a height");
                (*junction != format!("y{y}")).then(|| {
                    format!(
                        "connection {index}: expected y{y} at junction {junction_index}, got {}",
                        repr_str(junction)
                    )
                })
            }
            ConnectionKind::Join => (!junction.starts_with('y')).then(|| {
                format!(
                    "connection {index}: expected a join at junction {junction_index}, got {}",
                    repr_str(junction)
                )
            }),
            ConnectionKind::Break | ConnectionKind::BreakNoIsolation => {
                (junction != BREAK).then(|| {
                    format!(
                        "connection {index}: expected break at junction {junction_index}, got {}",
                        repr_str(junction)
                    )
                })
            }
        };
        if refuted.is_some() {
            return Ok(refuted);
        }
    }
    Ok(None)
}

/// `_token_for_span`: a span's token, its letters joined with `+` for a ligature, a letter's display, or a boundary's token.
fn token_for_span(
    letters: &HashMap<u32, String>,
    values: &[u32],
    (start, end): (usize, usize),
) -> Result<String> {
    let letter = |value: u32| {
        letters
            .get(&value)
            .map(|family| letter_display(family))
            .ok_or_else(|| ExpectError::engine(format!("{value:04X} is no letter")))
    };
    if end - start > 1 {
        let parts = values[start..end]
            .iter()
            .map(|&value| letter(value))
            .collect::<Result<Vec<_>>>()?;
        let tail: String = parts[1..]
            .iter()
            .map(|part| part.trim_start_matches(LETTER_DOT))
            .collect();
        return Ok(format!("{}+{tail}", parts[0]));
    }
    let value = values[start];
    if letters.contains_key(&value) {
        return letter(value);
    }
    BOUNDARY_EXPECT
        .iter()
        .find(|(boundary, _)| *boundary == value)
        .map(|(_, token)| (*token).to_owned())
        .ok_or_else(|| {
            ExpectError::engine(format!("{value:04X} is neither a letter nor a boundary"))
        })
}

/// `drafts.expect_string`: one token per span, each junction's connector between them.
pub fn expect_string(
    letters: &HashMap<u32, String>,
    values: &[u32],
    spans: &[(usize, usize)],
    junctions: &[String],
) -> Result<String> {
    let mut text = token_for_span(letters, values, spans[0])?;
    for (index, junction) in junctions.iter().enumerate() {
        let Some((_, connector)) = CONNECTORS.iter().find(|(token, _)| token == junction) else {
            return Err(ExpectError::engine(format!(
                "the junction {junction:?} has no connector"
            )));
        };
        text.push_str(connector);
        text.push_str(&token_for_span(letters, values, spans[index + 1])?);
    }
    Ok(text)
}

/// The drafter's replay of an expect string against the after font, `Drafter.validate_semantics`, over the init's escape names and the after font's facts.
pub struct Replayer<'a> {
    parser: ExpectParser,
    facts: &'a FontFacts,
}

impl<'a> Replayer<'a> {
    /// The replayer over the init's `glyph_names_by_char` and the after font in `facts`.
    pub fn new(init: &Init, facts: &'a FontFacts) -> Self {
        Self {
            parser: ExpectParser::new(init),
            facts,
        }
    }

    /// The parser the replay reads expect strings with.
    pub fn parser(&self) -> &ExpectParser {
        &self.parser
    }

    /// `row_for` as the replay calls it: the after font under the features with `kern` off.
    pub fn row(&self, shaper: &mut Shaper, text: &str, features: &Features) -> Result<Row> {
        Ok(row_for(shaper, self.facts, text, &features.kern_neutral())?)
    }

    /// `validate_semantics`: "pass" when some interpretation of `expect` holds for `text` shaped with the after font, "fail: unparseable: ..." when it does not parse, and otherwise "fail: " and each interpretation's first refuted assertion joined with " // ". Shaped `uni200C` glyphs read as `space`, and each glyph name loses [`SS10_COPY_SUFFIX`], before the check.
    pub fn validate_semantics(
        &self,
        shaper: &mut Shaper,
        text: &str,
        expect: &str,
        features: &Features,
    ) -> Result<String> {
        let (tokens, connections) = match self.parser.parse(expect) {
            Ok(parsed) => parsed,
            Err(message) => return Ok(format!("fail: unparseable: {message}")),
        };
        let row = self.row(shaper, text, features)?;
        let glyphs: Vec<String> = row
            .glyphs
            .iter()
            .map(|glyph| {
                if glyph == "uni200C" {
                    "space".to_owned()
                } else {
                    glyph
                        .strip_suffix(SS10_COPY_SUFFIX)
                        .unwrap_or(glyph)
                        .to_owned()
                }
            })
            .collect();
        let chars: Vec<char> = text.chars().collect();
        let mut errors = Vec::new();
        for (tokens, connections) in expand_maybe_ligatures(&tokens, &connections) {
            match check_interpretation(
                &chars,
                &tokens,
                &connections,
                &glyphs,
                &row.clusters,
                &row.junctions,
            )? {
                None => return Ok("pass".to_owned()),
                Some(error) => errors.push(error),
            }
        }
        Ok(format!("fail: {}", errors.join(" // ")))
    }
}

// --- the expect stage ---------------------------------------------------------------------------

fn raised_value(error: ExpectError) -> Result<PyValue> {
    match error.raised {
        Some(kind) => Ok(PyValue::Dict(vec![
            (PyValue::str("raised"), PyValue::str(kind)),
            (PyValue::str("message"), PyValue::str(error.message)),
        ])),
        None => Err(error),
    }
}

fn line_field(record: &Value, field: &str, key: &str) -> Result<PyValue> {
    PyValue::from_typed(&record[field])
        .map_err(|failure| ExpectError::engine(format!("{key}: {field}: {failure}")))
}

fn line_str(record: &Value, field: &str, key: &str) -> Result<String> {
    match line_field(record, field, key)? {
        PyValue::Str(text) => Ok(text),
        other => Err(ExpectError::engine(format!(
            "{key}: the {field} is {}, not a str",
            other.kind()
        ))),
    }
}

fn int_of(value: &PyValue, key: &str) -> Result<i64> {
    match value {
        PyValue::Int(number) => Ok(*number),
        other => Err(ExpectError::engine(format!(
            "{key}: {} where an int belongs",
            other.kind()
        ))),
    }
}

fn items<'v>(value: &'v PyValue, key: &str) -> Result<&'v [PyValue]> {
    match value {
        PyValue::Tuple(items) | PyValue::List(items) => Ok(items),
        other => Err(ExpectError::engine(format!(
            "{key}: {} where a sequence belongs",
            other.kind()
        ))),
    }
}

fn index_of(value: &PyValue, key: &str) -> Result<usize> {
    usize::try_from(int_of(value, key)?)
        .map_err(|_| ExpectError::engine(format!("{key}: a negative index")))
}

fn string_line(letters: &HashMap<u32, String>, record: &Value, key: &str) -> Result<PyValue> {
    let values = items(&line_field(record, "codepoints", key)?, key)?
        .iter()
        .map(|value| {
            u32::try_from(int_of(value, key)?)
                .map_err(|_| ExpectError::engine(format!("{key}: a code point out of range")))
        })
        .collect::<Result<Vec<u32>>>()?;
    let spans = items(&line_field(record, "spans", key)?, key)?
        .iter()
        .map(|span| match items(span, key)? {
            [start, end] => Ok((index_of(start, key)?, index_of(end, key)?)),
            _ => Err(ExpectError::engine(format!("{key}: a span is not a pair"))),
        })
        .collect::<Result<Vec<_>>>()?;
    let junctions = items(&line_field(record, "junctions", key)?, key)?
        .iter()
        .map(|junction| match junction {
            PyValue::Str(junction) => Ok(junction.clone()),
            other => Err(ExpectError::engine(format!(
                "{key}: a junction is {}, not a str",
                other.kind()
            ))),
        })
        .collect::<Result<Vec<_>>>()?;
    Ok(PyValue::str(expect_string(
        letters, &values, &spans, &junctions,
    )?))
}

fn pin_line(
    replayer: &Replayer,
    shaper: &mut Shaper,
    record: &Value,
    key: &str,
) -> Result<PyValue> {
    let text = line_str(record, "text", key)?;
    let expect = line_str(record, "expect", key)?;
    let features = Features::from_py(&line_field(record, "features", key)?)
        .map_err(|failure| ExpectError::engine(format!("{key}: {failure}")))?;
    let (parse, interpretations, row) = match replayer.parser().parse(&expect) {
        Err(message) => (PyValue::str(message), PyValue::None, PyValue::None),
        Ok(parsed) => {
            let interpretations = PyValue::List(
                expand_maybe_ligatures(&parsed.0, &parsed.1)
                    .iter()
                    .map(parsed_to_py)
                    .collect(),
            );
            let row = match replayer.row(shaper, &text, &features) {
                Ok(row) => row.to_py(),
                Err(error) => raised_value(error)?,
            };
            (parsed_to_py(&parsed), interpretations, row)
        }
    };
    let verdict = match replayer.validate_semantics(shaper, &text, &expect, &features) {
        Ok(verdict) => PyValue::str(verdict),
        Err(error) => raised_value(error)?,
    };
    Ok(PyValue::Dict(vec![
        (PyValue::str("parse"), parse),
        (PyValue::str("interpretations"), interpretations),
        (PyValue::str("row"), row),
        (PyValue::str("verdict"), verdict),
    ]))
}

/// The `expect` stage: for each `string:` line of `stage_file`, the expect string [`expect_string`] writes for its `codepoints`, `spans` and `junctions`; for each `pin:` line, the parse of its `expect` or the parse's error, its interpretations, the after font's row for its `text` under its `features` and the replay's verdict, or what Python raises in their place; each as one `{"key", "value"}` line in the typed encoding, through one [`Shaper`].
pub fn stage_expect(init_path: &Path, stage_file: &Path, out: &mut impl Write) -> Result<()> {
    let init = Init::read(init_path)?;
    let facts = FontFacts::read(&init.font_facts)?;
    let fonts = Fonts::open(&init, &facts)?;
    let letters = init_letters(&init).map_err(|error| ExpectError::engine(error.0))?;
    let replayer = Replayer::new(&init, &facts);
    let mut shaper = Shaper::new(&fonts, &facts);
    let file = std::fs::File::open(stage_file).map_err(|failure| {
        ExpectError::engine(format!("cannot read {}: {failure}", stage_file.display()))
    })?;
    for line in std::io::BufReader::new(file).lines() {
        let line = line.map_err(|failure| {
            ExpectError::engine(format!("cannot read {}: {failure}", stage_file.display()))
        })?;
        let record: Value = serde_json::from_str(&line).map_err(|failure| {
            ExpectError::engine(format!("a stage line is not JSON: {failure}"))
        })?;
        let Some(key) = record["key"].as_str() else {
            return Err(ExpectError::engine(format!(
                "a stage line has no key: {line:.200}"
            )));
        };
        let value = if key.starts_with("string:") {
            string_line(&letters, &record, key)?
        } else if key.starts_with("pin:") {
            pin_line(&replayer, &mut shaper, &record, key)?
        } else {
            return Err(ExpectError::engine(format!(
                "{key}: the expect stage answers string: and pin: lines"
            )));
        };
        writeln!(
            out,
            "{{\"key\":{},\"value\":{}}}",
            json_string(key),
            value.to_typed()
        )
        .map_err(|failure| ExpectError::engine(format!("cannot write a stage line: {failure}")))?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn parser() -> ExpectParser {
        ExpectParser {
            glyph_names: [
                ('\u{E650}', "qsPea".to_owned()),
                ('·', "periodcentered".to_owned()),
            ]
            .into_iter()
            .collect(),
        }
    }

    fn bases(raw: &str) -> Vec<String> {
        parser()
            .parse(raw)
            .expect("parses")
            .0
            .into_iter()
            .map(|token| token.base)
            .collect()
    }

    #[test]
    fn letters_escapes_and_lozenges_name_their_glyphs() {
        assert_eq!(
            bases("·-ing ·J'ai ·J’ai ·Jai ·Jay \\· \\\u{E650} \\x ◊space ◊ZWNJ"),
            [
                "qsIng",
                "qsJai",
                "qsJai",
                "qsJai",
                "qsJay",
                "periodcentered",
                "qsPea",
                "uni0078",
                "space",
                "space"
            ]
        );
    }

    #[test]
    fn connections_read_in_the_patterns_order() {
        let (_, connections) = parser()
            .parse("·Pea ~x~ ·Pea |?| ·Pea ? ·Pea | ·Pea\u{1c}·Pea~6~·Pea")
            .expect("parses");
        let kinds: Vec<_> = connections
            .iter()
            .map(|connection| (connection.kind.name(), connection.y))
            .collect();
        assert_eq!(
            kinds,
            [
                ("height", Some(5)),
                ("break_no_isolation", None),
                ("maybe", None),
                ("break", None),
                ("join", None),
                ("height", Some(6)),
            ]
        );
    }

    #[test]
    fn ligatures_and_variants_fill_the_token() {
        let (tokens, _) = parser()
            .parse("·Tea+?Utter.half.!alt ·Day+|Oy ·Pea+Tea.∅")
            .expect("parses");
        assert_eq!(tokens[0].lig_base.as_deref(), Some("qsUtter"));
        assert_eq!(tokens[0].lig_mode, Some(LigMode::Maybe));
        assert_eq!(tokens[0].variants, ["half"]);
        assert_eq!(tokens[0].neg_variants, ["alt"]);
        assert_eq!(tokens[1].lig_mode, Some(LigMode::MaybeBreak));
        assert_eq!(tokens[2].lig_mode, None);
        assert!(tokens[2].exact_glyph);
    }

    #[test]
    fn malformed_strings_raise_pythons_messages() {
        let error = |raw: &str| parser().parse(raw).expect_err("refused");
        assert_eq!(error("·Pea 'x'"), "Expected glyph token at pos 5: \"'x'\"");
        assert_eq!(
            error("·Pea+tea"),
            "Expected connection operator at pos 4: '+tea'"
        );
        assert_eq!(error("◊zwnj"), "Unknown lozenge name: ◊zwnj");
        assert_eq!(
            error(" ·Pea.∅.∅ "),
            "Exact glyph assertion .∅ appears more than once in '·Pea.∅.∅'"
        );
        assert_eq!(error("·Pea |"), "Expected glyph token at pos 6: ''");
        assert_eq!(error("\\"), "Expected glyph token at pos 0: '\\\\'");
    }

    #[test]
    fn maybe_ligatures_expand_in_product_order() {
        let (tokens, connections) = parser().parse("·Tea+?Utter ·Day+|Oy").expect("parses");
        let expanded = expand_maybe_ligatures(&tokens, &connections);
        let shapes: Vec<Vec<&str>> = expanded
            .iter()
            .map(|(tokens, _)| tokens.iter().map(|token| token.base.as_str()).collect())
            .collect();
        assert_eq!(
            shapes,
            [
                vec!["qsTea", "qsDay"],
                vec!["qsTea", "qsDay", "qsOy"],
                vec!["qsTea", "qsUtter", "qsDay"],
                vec!["qsTea", "qsUtter", "qsDay", "qsOy"],
            ]
        );
        assert_eq!(expanded[1].1[1].kind, ConnectionKind::Break);
        assert_eq!(expanded[2].1[0].kind, ConnectionKind::Maybe);
        assert!(
            expanded
                .iter()
                .all(|(tokens, _)| tokens.iter().all(|token| token.lig_mode.is_none()))
        );
    }

    #[test]
    fn spans_take_variation_selectors_after_a_token() {
        let (tokens, _) = parser().parse("·Pea ·Tea+Utter").expect("parses");
        let text: Vec<char> = "\u{FE00}a\u{FE00}bc\u{FE01}".chars().collect();
        assert_eq!(token_char_spans(&text, &tokens), Ok(vec![(1, 3), (3, 6)]));
        let short: Vec<char> = "a".chars().collect();
        assert_eq!(
            token_char_spans(&short, &tokens),
            Err("Not enough input chars for token {'base': 'qsTea', 'lig_base': 'qsUtter', 'lig_mode': None, 'variants': [], 'neg_variants': [], 'exact_glyph': False} at char 1".to_owned())
        );
    }

    #[test]
    fn a_drafted_string_joins_span_tokens_with_connectors() {
        let letters: HashMap<u32, String> = [
            (0xE652, "qsTea".to_owned()),
            (0xE679, "qsOy".to_owned()),
            (0xE65D, "qsJai".to_owned()),
        ]
        .into_iter()
        .collect();
        assert_eq!(
            expect_string(
                &letters,
                &[0x200C, 0xE652, 0xE679, 0x00B7, 0xE65D],
                &[(0, 1), (1, 3), (3, 4), (4, 5)],
                &["break".to_owned(), "y5".to_owned(), "y0".to_owned()]
            ),
            Ok("◊ZWNJ | ·Tea+Oy ~x~ \\· ~b~ ·J’ai".to_owned())
        );
    }
}
