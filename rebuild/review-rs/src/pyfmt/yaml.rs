//! PyYAML's flow dump over [`PyValue`], as `Drafter.draft_policy` ships a policy record: `yaml.safe_dump(record, default_flow_style=True, width=10**6, allow_unicode=True, sort_keys=False).strip()`.
//!
//! The dump follows PyYAML 6's pure-Python path, which `safe_dump` takes: `SafeRepresenter` turns the value into nodes (a tuple as a sequence, a dict's entries in insertion order, every collection in flow style), `Resolver`'s implicit resolvers decide whether a string would read back as a string, and `Emitter` picks each scalar's style from its analysis and writes it, tracking the column, the indent and the whitespace flags as the emitter does, so a multiline quoted scalar, a complex key and a line past the width come out as PyYAML writes them. Bytes, a set and a record are refused: `draft_policy` dumps none, and the safe representer writes the first two with tags and refuses the third. Since a [`PyValue`] has no identity, no anchor is ever written; a Python record that holds one container twice dumps with an anchor this writer cannot reproduce.

use std::sync::LazyLock;

use super::{PyFmtError, PyValue, is_space};

/// `width=10**6`, the emitter's `best_width`.
const BEST_WIDTH: usize = 1_000_000;
/// The emitter's default `best_indent`.
const BEST_INDENT: usize = 2;
/// `check_simple_key`'s limit on a key's tag and text together.
const SIMPLE_KEY_LIMIT: usize = 128;

/// The text `Drafter.draft_policy` ships for `value`: its flow dump with surrounding whitespace stripped as `str.strip` strips it.
pub fn flow_dump(value: &PyValue) -> Result<String, PyFmtError> {
    let node = represent(value)?;
    let mut emitter = Emitter::default();
    emitter.document(&node)?;
    Ok(emitter.out.trim_matches(is_space).to_string())
}

/// The tags `SafeRepresenter` gives the scalars a [`PyValue`] holds, and the other tags the implicit resolvers can name.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Tag {
    Str,
    Int,
    Bool,
    Null,
    Float,
    Merge,
    Timestamp,
    Value,
    Yaml,
}

impl Tag {
    /// The tag's suffix under `tag:yaml.org,2002:`.
    fn name(self) -> &'static str {
        match self {
            Self::Str => "str",
            Self::Int => "int",
            Self::Bool => "bool",
            Self::Null => "null",
            Self::Float => "float",
            Self::Merge => "merge",
            Self::Timestamp => "timestamp",
            Self::Value => "value",
            Self::Yaml => "yaml",
        }
    }

    /// The length of the tag as `prepare_tag` writes it (`!!str`), which `check_simple_key` counts whether or not the tag is written.
    fn prepared_length(self) -> usize {
        2 + self.name().len()
    }
}

enum Node {
    Scalar(Tag, Vec<char>),
    Sequence(Vec<Node>),
    Mapping(Vec<(Node, Node)>),
}

fn represent(value: &PyValue) -> Result<Node, PyFmtError> {
    let scalar = |tag, text: &str| Ok(Node::Scalar(tag, text.chars().collect()));
    match value {
        PyValue::None => scalar(Tag::Null, "null"),
        PyValue::Bool(true) => scalar(Tag::Bool, "true"),
        PyValue::Bool(false) => scalar(Tag::Bool, "false"),
        PyValue::Int(number) => scalar(Tag::Int, &number.to_string()),
        PyValue::Str(text) => scalar(Tag::Str, text),
        PyValue::Tuple(items) | PyValue::List(items) => items
            .iter()
            .map(represent)
            .collect::<Result<_, _>>()
            .map(Node::Sequence),
        PyValue::Dict(entries) => entries
            .iter()
            .map(|(key, item)| Ok((represent(key)?, represent(item)?)))
            .collect::<Result<_, _>>()
            .map(Node::Mapping),
        PyValue::Bytes(_) | PyValue::Set(_) | PyValue::Record(..) => Err(PyFmtError(format!(
            "the policy record's flow dump writes no {}",
            value.kind()
        ))),
    }
}

// --- the implicit resolvers ----------------------------------------------------------------------

/// A regular expression from `Resolver`'s implicit resolvers, transliterated: each node matches as Python's `re` does, with backtracking.
enum Pattern {
    Literal(&'static str),
    Class(fn(char) -> bool),
    Sequence(Vec<Pattern>),
    Either(Vec<Pattern>),
    Optional(Box<Pattern>),
    ZeroOrMore(Box<Pattern>),
    OneOrMore(Box<Pattern>),
}

use Pattern::{Class, Either, Literal, OneOrMore, Optional, Sequence, ZeroOrMore};

fn optional(pattern: Pattern) -> Pattern {
    Optional(Box::new(pattern))
}

fn any(pattern: Pattern) -> Pattern {
    ZeroOrMore(Box::new(pattern))
}

fn some(pattern: Pattern) -> Pattern {
    OneOrMore(Box::new(pattern))
}

fn words(alternatives: &[&'static str]) -> Pattern {
    Either(alternatives.iter().map(|word| Literal(word)).collect())
}

fn digit() -> Pattern {
    Class(|letter| letter.is_ascii_digit())
}

fn digit_or_underscore() -> Pattern {
    Class(|letter| letter.is_ascii_digit() || letter == '_')
}

fn sign() -> Pattern {
    Class(|letter| letter == '-' || letter == '+')
}

/// `(?::[0-5]?[0-9])+`, the sexagesimal groups of the int and float resolvers.
fn sexagesimal() -> Pattern {
    some(Sequence(vec![
        Literal(":"),
        optional(Class(|letter| ('0'..='5').contains(&letter))),
        digit(),
    ]))
}

impl Pattern {
    fn run(&self, text: &[char], at: usize, then: &dyn Fn(usize) -> bool) -> bool {
        match self {
            Literal(word) => {
                let mut end = at;
                for letter in word.chars() {
                    if text.get(end) != Some(&letter) {
                        return false;
                    }
                    end += 1;
                }
                then(end)
            }
            Class(accepts) => text.get(at).is_some_and(|&letter| accepts(letter)) && then(at + 1),
            Sequence(parts) => Self::run_sequence(parts, text, at, then),
            Either(alternatives) => alternatives
                .iter()
                .any(|alternative| alternative.run(text, at, then)),
            Optional(inner) => inner.run(text, at, then) || then(at),
            ZeroOrMore(inner) => Self::run_any(inner, text, at, then),
            OneOrMore(inner) => inner.run(text, at, &|next| Self::run_any(inner, text, next, then)),
        }
    }

    fn run_sequence(
        parts: &[Pattern],
        text: &[char],
        at: usize,
        then: &dyn Fn(usize) -> bool,
    ) -> bool {
        match parts.split_first() {
            None => then(at),
            Some((first, rest)) => {
                first.run(text, at, &|next| Self::run_sequence(rest, text, next, then))
            }
        }
    }

    fn run_any(inner: &Pattern, text: &[char], at: usize, then: &dyn Fn(usize) -> bool) -> bool {
        let longer = inner.run(text, at, &|next| {
            next > at && Self::run_any(inner, text, next, then)
        });
        longer || then(at)
    }

    /// `re.match(r'^(?:…)$', text)`: Python's `$` also matches before a newline that ends the text.
    fn matches(&self, text: &[char]) -> bool {
        let end = text.len();
        self.run(text, 0, &|at| {
            at == end || (at + 1 == end && text[at] == '\n')
        })
    }
}

/// `Resolver`'s implicit resolvers in registration order, each with the first characters it is registered under (`""` stands for the empty text).
fn resolvers() -> Vec<(Tag, &'static str, Pattern)> {
    let exponent = || {
        optional(Sequence(vec![
            Class(|letter| letter == 'e' || letter == 'E'),
            sign(),
            some(digit()),
        ]))
    };
    let space_or_tab = || Class(|letter| letter == ' ' || letter == '\t');
    vec![
        (
            Tag::Bool,
            "yYnNtTfFoO",
            words(&[
                "yes", "Yes", "YES", "no", "No", "NO", "true", "True", "TRUE", "false", "False",
                "FALSE", "on", "On", "ON", "off", "Off", "OFF",
            ]),
        ),
        (
            Tag::Float,
            "-+0123456789.",
            Either(vec![
                Sequence(vec![
                    optional(sign()),
                    digit(),
                    any(digit_or_underscore()),
                    Literal("."),
                    any(digit_or_underscore()),
                    exponent(),
                ]),
                Sequence(vec![
                    Literal("."),
                    digit(),
                    any(digit_or_underscore()),
                    exponent(),
                ]),
                Sequence(vec![
                    optional(sign()),
                    digit(),
                    any(digit_or_underscore()),
                    sexagesimal(),
                    Literal("."),
                    any(digit_or_underscore()),
                ]),
                Sequence(vec![
                    optional(sign()),
                    Literal("."),
                    words(&["inf", "Inf", "INF"]),
                ]),
                Sequence(vec![Literal("."), words(&["nan", "NaN", "NAN"])]),
            ]),
        ),
        (
            Tag::Int,
            "-+0123456789",
            Either(vec![
                Sequence(vec![
                    optional(sign()),
                    Literal("0b"),
                    some(Class(|letter| matches!(letter, '0' | '1' | '_'))),
                ]),
                Sequence(vec![
                    optional(sign()),
                    Literal("0"),
                    some(Class(|letter| matches!(letter, '0'..='7' | '_'))),
                ]),
                Sequence(vec![
                    optional(sign()),
                    Either(vec![
                        Literal("0"),
                        Sequence(vec![
                            Class(|letter| matches!(letter, '1'..='9')),
                            any(digit_or_underscore()),
                        ]),
                    ]),
                ]),
                Sequence(vec![
                    optional(sign()),
                    Literal("0x"),
                    some(Class(|letter| letter.is_ascii_hexdigit() || letter == '_')),
                ]),
                Sequence(vec![
                    optional(sign()),
                    Class(|letter| matches!(letter, '1'..='9')),
                    any(digit_or_underscore()),
                    sexagesimal(),
                ]),
            ]),
        ),
        (Tag::Merge, "<", Literal("<<")),
        (Tag::Null, "~nN", words(&["~", "null", "Null", "NULL", ""])),
        (
            Tag::Timestamp,
            "0123456789",
            Either(vec![
                Sequence(vec![
                    digit(),
                    digit(),
                    digit(),
                    digit(),
                    Literal("-"),
                    digit(),
                    digit(),
                    Literal("-"),
                    digit(),
                    digit(),
                ]),
                Sequence(vec![
                    digit(),
                    digit(),
                    digit(),
                    digit(),
                    Literal("-"),
                    digit(),
                    optional(digit()),
                    Literal("-"),
                    digit(),
                    optional(digit()),
                    Either(vec![
                        Class(|letter| letter == 'T' || letter == 't'),
                        some(space_or_tab()),
                    ]),
                    digit(),
                    optional(digit()),
                    Literal(":"),
                    digit(),
                    digit(),
                    Literal(":"),
                    digit(),
                    digit(),
                    optional(Sequence(vec![Literal("."), any(digit())])),
                    optional(Sequence(vec![
                        any(space_or_tab()),
                        Either(vec![
                            Literal("Z"),
                            Sequence(vec![
                                sign(),
                                digit(),
                                optional(digit()),
                                optional(Sequence(vec![Literal(":"), digit(), digit()])),
                            ]),
                        ]),
                    ])),
                ]),
            ]),
        ),
        (Tag::Value, "=", Literal("=")),
        (Tag::Yaml, "!&*", words(&["!", "&", "*"])),
    ]
}

/// The tag `Resolver.resolve` gives a plain scalar with this text: the first implicit resolver registered under its first character (the null resolver alone for the empty text) whose pattern matches, else `str`.
fn resolve(text: &[char]) -> Tag {
    static RESOLVERS: LazyLock<Vec<(Tag, &'static str, Pattern)>> = LazyLock::new(resolvers);
    for (tag, first, pattern) in RESOLVERS.iter() {
        let registered = match text.first() {
            None => *tag == Tag::Null,
            Some(letter) => first.contains(*letter),
        };
        if registered && pattern.matches(text) {
            return *tag;
        }
    }
    Tag::Str
}

// --- the emitter ---------------------------------------------------------------------------------

/// `Emitter.analyze_scalar`'s verdict on a non-empty scalar, or on the empty one.
struct Analysis {
    empty: bool,
    multiline: bool,
    allow_flow_plain: bool,
    allow_block_plain: bool,
    allow_single_quoted: bool,
}

const LINE_BREAKS: [char; 4] = ['\n', '\u{85}', '\u{2028}', '\u{2029}'];
const BREAKS_AND_BLANKS: [char; 8] = [
    '\0', ' ', '\t', '\r', '\n', '\u{85}', '\u{2028}', '\u{2029}',
];

fn is_break(letter: char) -> bool {
    LINE_BREAKS.contains(&letter)
}

fn is_blank_or_break(letter: char) -> bool {
    BREAKS_AND_BLANKS.contains(&letter)
}

fn analyze(scalar: &[char]) -> Analysis {
    if scalar.is_empty() {
        return Analysis {
            empty: true,
            multiline: false,
            allow_flow_plain: false,
            allow_block_plain: true,
            allow_single_quoted: true,
        };
    }
    let mut block_indicators = false;
    let mut flow_indicators = false;
    let mut line_breaks = false;
    let mut special_characters = false;
    let (mut leading_space, mut leading_break) = (false, false);
    let (mut trailing_space, mut trailing_break) = (false, false);
    let (mut break_space, mut space_break) = (false, false);
    if scalar.starts_with(&['-', '-', '-']) || scalar.starts_with(&['.', '.', '.']) {
        block_indicators = true;
        flow_indicators = true;
    }
    let mut preceded_by_whitespace = true;
    let mut followed_by_whitespace = scalar.len() == 1 || is_blank_or_break(scalar[1]);
    let (mut previous_space, mut previous_break) = (false, false);
    let last = scalar.len() - 1;
    for (index, &letter) in scalar.iter().enumerate() {
        if index == 0 {
            if "#,[]{}&*!|>'\"%@`".contains(letter) {
                flow_indicators = true;
                block_indicators = true;
            }
            if letter == '?' || letter == ':' {
                flow_indicators = true;
                if followed_by_whitespace {
                    block_indicators = true;
                }
            }
            if letter == '-' && followed_by_whitespace {
                flow_indicators = true;
                block_indicators = true;
            }
        } else {
            if ",?[]{}".contains(letter) {
                flow_indicators = true;
            }
            if letter == ':' {
                flow_indicators = true;
                if followed_by_whitespace {
                    block_indicators = true;
                }
            }
            if letter == '#' && preceded_by_whitespace {
                flow_indicators = true;
                block_indicators = true;
            }
        }
        if is_break(letter) {
            line_breaks = true;
        }
        if !(letter == '\n' || ('\u{20}'..='\u{7e}').contains(&letter)) {
            let unicode = (letter == '\u{85}'
                || ('\u{a0}'..='\u{d7ff}').contains(&letter)
                || ('\u{e000}'..='\u{fffd}').contains(&letter)
                || ('\u{10000}'..'\u{10ffff}').contains(&letter))
                && letter != '\u{feff}';
            if !unicode {
                special_characters = true;
            }
        }
        if letter == ' ' {
            if index == 0 {
                leading_space = true;
            }
            if index == last {
                trailing_space = true;
            }
            if previous_break {
                break_space = true;
            }
            previous_space = true;
            previous_break = false;
        } else if is_break(letter) {
            if index == 0 {
                leading_break = true;
            }
            if index == last {
                trailing_break = true;
            }
            if previous_space {
                space_break = true;
            }
            previous_space = false;
            previous_break = true;
        } else {
            previous_space = false;
            previous_break = false;
        }
        preceded_by_whitespace = is_blank_or_break(letter);
        followed_by_whitespace = index + 2 >= scalar.len() || is_blank_or_break(scalar[index + 2]);
    }
    let mut allow_flow_plain = true;
    let mut allow_block_plain = true;
    let mut allow_single_quoted = true;
    if leading_space || leading_break || trailing_space || trailing_break {
        allow_flow_plain = false;
        allow_block_plain = false;
    }
    if break_space {
        allow_flow_plain = false;
        allow_block_plain = false;
        allow_single_quoted = false;
    }
    if space_break || special_characters {
        allow_flow_plain = false;
        allow_block_plain = false;
        allow_single_quoted = false;
    }
    if line_breaks {
        allow_flow_plain = false;
        allow_block_plain = false;
    }
    if flow_indicators {
        allow_flow_plain = false;
    }
    if block_indicators {
        allow_block_plain = false;
    }
    Analysis {
        empty: false,
        multiline: line_breaks,
        allow_flow_plain,
        allow_block_plain,
        allow_single_quoted,
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Style {
    Plain,
    Single,
    Double,
}

/// `expect_node`'s context flags for the node being written.
#[derive(Clone, Copy, Default)]
struct Context {
    root: bool,
    simple_key: bool,
}

/// The emitter's writing state: the text so far, and what PyYAML's `Emitter` tracks while writing it.
struct Emitter {
    out: String,
    indents: Vec<Option<usize>>,
    indent: Option<usize>,
    flow_level: usize,
    column: usize,
    whitespace: bool,
    indention: bool,
    open_ended: bool,
}

impl Default for Emitter {
    fn default() -> Self {
        Self {
            out: String::new(),
            indents: Vec::new(),
            indent: None,
            flow_level: 0,
            column: 0,
            whitespace: true,
            indention: true,
            open_ended: false,
        }
    }
}

impl Emitter {
    /// One document holding `node`, as `dump` writes it: an implicit start, the root node, the document end and the stream end.
    fn document(&mut self, node: &Node) -> Result<(), PyFmtError> {
        self.node(
            node,
            Context {
                root: true,
                simple_key: false,
            },
        )?;
        self.write_indent();
        if self.open_ended {
            self.write_indicator("...", true, false, false);
            self.write_indent();
        }
        Ok(())
    }

    fn node(&mut self, node: &Node, context: Context) -> Result<(), PyFmtError> {
        match node {
            Node::Scalar(tag, text) => self.scalar(*tag, text, context),
            Node::Sequence(items) => {
                self.write_indicator("[", true, true, false);
                self.flow_level += 1;
                self.increase_indent();
                for (index, item) in items.iter().enumerate() {
                    if index > 0 {
                        self.write_indicator(",", false, false, false);
                    }
                    if self.column > BEST_WIDTH {
                        self.write_indent();
                    }
                    self.node(item, Context::default())?;
                }
                self.indent = self.indents.pop().flatten();
                self.flow_level -= 1;
                self.write_indicator("]", false, false, false);
                Ok(())
            }
            Node::Mapping(entries) => {
                self.write_indicator("{", true, true, false);
                self.flow_level += 1;
                self.increase_indent();
                for (index, (key, item)) in entries.iter().enumerate() {
                    if index > 0 {
                        self.write_indicator(",", false, false, false);
                    }
                    if self.column > BEST_WIDTH {
                        self.write_indent();
                    }
                    if is_simple_key(key) {
                        self.node(
                            key,
                            Context {
                                root: false,
                                simple_key: true,
                            },
                        )?;
                        self.write_indicator(":", false, false, false);
                    } else {
                        self.write_indicator("?", true, false, false);
                        self.node(key, Context::default())?;
                        if self.column > BEST_WIDTH {
                            self.write_indent();
                        }
                        self.write_indicator(":", true, false, false);
                    }
                    self.node(item, Context::default())?;
                }
                self.indent = self.indents.pop().flatten();
                self.flow_level -= 1;
                self.write_indicator("}", false, false, false);
                Ok(())
            }
        }
    }

    /// `process_tag`, `expect_scalar` and `process_scalar` for one scalar. A scalar whose style would need its tag written is refused; no value a [`PyValue`] holds needs one.
    fn scalar(&mut self, tag: Tag, text: &[char], context: Context) -> Result<(), PyFmtError> {
        let analysis = analyze(text);
        let detected = resolve(text) == tag;
        let style = self.choose_style(&analysis, detected, context);
        let implicit = match style {
            Style::Plain => detected,
            Style::Single | Style::Double => tag == Tag::Str,
        };
        if !implicit {
            let text: String = text.iter().collect();
            return Err(PyFmtError(format!(
                "the flow dump would tag the scalar {text:?}"
            )));
        }
        self.increase_indent();
        let split = !context.simple_key;
        match style {
            Style::Plain => self.write_plain(text, split, context.root),
            Style::Single => self.write_single_quoted(text, split),
            Style::Double => self.write_double_quoted(text, split),
        }
        self.indent = self.indents.pop().flatten();
        Ok(())
    }

    fn choose_style(&self, analysis: &Analysis, implicit: bool, context: Context) -> Style {
        if implicit
            && !(context.simple_key && (analysis.empty || analysis.multiline))
            && (self.flow_level > 0 && analysis.allow_flow_plain
                || (self.flow_level == 0 && analysis.allow_block_plain))
        {
            return Style::Plain;
        }
        if analysis.allow_single_quoted && !(context.simple_key && analysis.multiline) {
            return Style::Single;
        }
        Style::Double
    }

    /// `increase_indent(flow=True)`, the only form a flow dump calls.
    fn increase_indent(&mut self) {
        self.indents.push(self.indent);
        self.indent = Some(
            self.indent
                .map_or(BEST_INDENT, |indent| indent + BEST_INDENT),
        );
    }

    fn write(&mut self, text: &[char]) {
        self.out.extend(text);
        self.column += text.len();
    }

    fn write_indicator(
        &mut self,
        indicator: &str,
        need_whitespace: bool,
        whitespace: bool,
        indention: bool,
    ) {
        if !(self.whitespace || !need_whitespace) {
            self.out.push(' ');
            self.column += 1;
        }
        self.out.push_str(indicator);
        self.column += indicator.chars().count();
        self.whitespace = whitespace;
        self.indention = self.indention && indention;
        self.open_ended = false;
    }

    fn write_indent(&mut self) {
        let indent = self.indent.unwrap_or(0);
        if !self.indention || self.column > indent || (self.column == indent && !self.whitespace) {
            self.write_line_break(None);
        }
        if self.column < indent {
            self.whitespace = true;
            self.out
                .extend(std::iter::repeat_n(' ', indent - self.column));
            self.column = indent;
        }
    }

    fn write_line_break(&mut self, letter: Option<char>) {
        self.whitespace = true;
        self.indention = true;
        self.column = 0;
        self.out.push(letter.unwrap_or('\n'));
    }

    /// The line breaks of `text[start..end]` as the quoted and plain writers write them: a `\n` that leads them adds a line break of its own, and every other break is written as itself.
    fn write_breaks(&mut self, breaks: &[char]) {
        if breaks.first() == Some(&'\n') {
            self.write_line_break(None);
        }
        for &letter in breaks {
            self.write_line_break(if letter == '\n' { None } else { Some(letter) });
        }
        self.write_indent();
    }

    fn write_plain(&mut self, text: &[char], split: bool, root: bool) {
        if root {
            self.open_ended = true;
        }
        if text.is_empty() {
            return;
        }
        if !self.whitespace {
            self.write(&[' ']);
        }
        self.whitespace = false;
        self.indention = false;
        let (mut spaces, mut breaks) = (false, false);
        let (mut start, mut end) = (0, 0);
        while end <= text.len() {
            let letter = text.get(end).copied();
            if spaces {
                if letter != Some(' ') {
                    if start + 1 == end && self.column > BEST_WIDTH && split {
                        self.write_indent();
                        self.whitespace = false;
                        self.indention = false;
                    } else {
                        self.write(&text[start..end]);
                    }
                    start = end;
                }
            } else if breaks {
                if !letter.is_some_and(is_break) {
                    self.write_breaks(&text[start..end]);
                    self.whitespace = false;
                    self.indention = false;
                    start = end;
                }
            } else if letter.is_none_or(|letter| letter == ' ' || is_break(letter)) {
                self.write(&text[start..end]);
                start = end;
            }
            if let Some(letter) = letter {
                spaces = letter == ' ';
                breaks = is_break(letter);
            }
            end += 1;
        }
    }

    fn write_single_quoted(&mut self, text: &[char], split: bool) {
        self.write_indicator("'", true, false, false);
        let (mut spaces, mut breaks) = (false, false);
        let (mut start, mut end) = (0, 0);
        while end <= text.len() {
            let letter = text.get(end).copied();
            if spaces {
                if letter != Some(' ') {
                    if start + 1 == end
                        && self.column > BEST_WIDTH
                        && split
                        && start != 0
                        && end != text.len()
                    {
                        self.write_indent();
                    } else {
                        self.write(&text[start..end]);
                    }
                    start = end;
                }
            } else if breaks {
                if !letter.is_some_and(is_break) {
                    self.write_breaks(&text[start..end]);
                    start = end;
                }
            } else if letter
                .is_none_or(|letter| letter == ' ' || is_break(letter) || letter == '\'')
                && start < end
            {
                self.write(&text[start..end]);
                start = end;
            }
            if letter == Some('\'') {
                self.write(&['\'', '\'']);
                start = end + 1;
            }
            if let Some(letter) = letter {
                spaces = letter == ' ';
                breaks = is_break(letter);
            }
            end += 1;
        }
        self.write_indicator("'", false, false, false);
    }

    fn write_double_quoted(&mut self, text: &[char], split: bool) {
        self.write_indicator("\"", true, false, false);
        let (mut start, mut end) = (0, 0);
        while end <= text.len() {
            let letter = text.get(end).copied();
            let escaped = letter.is_none_or(|letter| {
                "\"\\\u{85}\u{2028}\u{2029}\u{feff}".contains(letter)
                    || !(('\u{20}'..='\u{7e}').contains(&letter)
                        || ('\u{a0}'..='\u{d7ff}').contains(&letter)
                        || ('\u{e000}'..='\u{fffd}').contains(&letter))
            });
            if escaped {
                if start < end {
                    self.write(&text[start..end]);
                    start = end;
                }
                if let Some(letter) = letter {
                    let escape = escape(letter);
                    let escape: Vec<char> = escape.chars().collect();
                    self.write(&escape);
                    start = end + 1;
                }
            }
            if 0 < end
                && end + 1 < text.len()
                && (letter == Some(' ') || start >= end)
                && (self.column + end).saturating_sub(start) > BEST_WIDTH
                && split
            {
                let mut data: Vec<char> = text[start.min(end)..end].to_vec();
                data.push('\\');
                if start < end {
                    start = end;
                }
                self.write(&data);
                self.write_indent();
                self.whitespace = false;
                self.indention = false;
                if text[start] == ' ' {
                    self.write(&['\\']);
                }
            }
            end += 1;
        }
        self.write_indicator("\"", false, false, false);
    }
}

/// `check_simple_key` for a mapping key: a scalar that is neither empty nor multiline, or an empty collection, whose prepared tag and text together stay under the limit.
fn is_simple_key(key: &Node) -> bool {
    match key {
        Node::Scalar(tag, text) => {
            let analysis = analyze(text);
            tag.prepared_length() + text.len() < SIMPLE_KEY_LIMIT
                && !analysis.empty
                && !analysis.multiline
        }
        Node::Sequence(items) => items.is_empty(),
        Node::Mapping(entries) => entries.is_empty(),
    }
}

/// One character in a double-quoted scalar as `write_double_quoted` escapes it: `ESCAPE_REPLACEMENTS`, else `\xXX`, `\uXXXX` or `\UXXXXXXXX` in uppercase hex.
fn escape(letter: char) -> String {
    let short = match letter {
        '\0' => '0',
        '\u{7}' => 'a',
        '\u{8}' => 'b',
        '\t' => 't',
        '\n' => 'n',
        '\u{b}' => 'v',
        '\u{c}' => 'f',
        '\r' => 'r',
        '\u{1b}' => 'e',
        '"' => '"',
        '\\' => '\\',
        '\u{85}' => 'N',
        '\u{a0}' => '_',
        '\u{2028}' => 'L',
        '\u{2029}' => 'P',
        _ => {
            let point = u32::from(letter);
            return if point <= 0xff {
                format!("\\x{point:02X}")
            } else if point <= 0xffff {
                format!("\\u{point:04X}")
            } else {
                format!("\\U{point:08X}")
            };
        }
    };
    format!("\\{short}")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn text(value: &str) -> PyValue {
        PyValue::str(value)
    }

    fn dump(value: &PyValue) -> String {
        flow_dump(value).expect("dump")
    }

    #[test]
    fn a_policy_record_is_a_flow_mapping_with_its_quoted_why() {
        let record = PyValue::Dict(vec![
            (text("exit"), text("x-height")),
            (
                text("when"),
                PyValue::Dict(vec![(
                    text("right"),
                    PyValue::Dict(vec![(text("family"), PyValue::List(vec![text("qsIt")]))]),
                )]),
            ),
            (text("by"), PyValue::Int(-1)),
            (
                text("why"),
                text("Reviewer rejected E665:E670 (\u{b7}May\u{b7}It)"),
            ),
        ]);
        assert_eq!(
            dump(&record),
            "{exit: x-height, when: {right: {family: [qsIt]}}, by: -1, why: 'Reviewer rejected E665:E670 (\u{b7}May\u{b7}It)'}"
        );
    }

    #[test]
    fn a_string_that_would_read_back_as_another_type_is_quoted() {
        for (value, expected) in [
            ("yes", "'yes'"),
            ("null", "'null'"),
            ("", "''"),
            ("0x1F", "'0x1F'"),
            ("1:30", "'1:30'"),
            ("1.5", "'1.5'"),
            ("1e5", "1e5"),
            ("2001-12-14", "'2001-12-14'"),
            ("<<", "'<<'"),
            ("none", "none"),
        ] {
            let wrapped = PyValue::List(vec![text(value)]);
            assert_eq!(dump(&wrapped), format!("[{expected}]"), "{value:?}");
        }
    }

    #[test]
    fn a_root_plain_scalar_ends_the_document_explicitly() {
        assert_eq!(dump(&text("a")), "a\n...");
        assert_eq!(dump(&PyValue::None), "null\n...");
        assert_eq!(dump(&text("a b'c")), "a b'c\n...");
        assert_eq!(dump(&text("it's: x")), "'it''s: x'");
    }

    #[test]
    fn multiline_scalars_and_complex_keys_follow_the_emitter() {
        let value = PyValue::Dict(vec![
            (text("a"), text("x\ny")),
            (
                text("k"),
                PyValue::List(vec![
                    text("p\n q"),
                    PyValue::Dict(vec![(text("b"), text("a\n\nb"))]),
                ]),
            ),
            (text(""), PyValue::Int(2)),
            (
                PyValue::Tuple(vec![PyValue::Int(1), PyValue::Int(2)]),
                PyValue::Int(3),
            ),
            (PyValue::Tuple(vec![]), PyValue::Bool(true)),
        ]);
        assert_eq!(
            dump(&value),
            "{a: 'x\n\n    y', k: [\"p\\n q\", {b: 'a\n\n\n        b'}], ? '' : 2, ? [1, 2] : 3, []: true}"
        );
    }

    #[test]
    fn special_characters_are_escaped_in_double_quotes() {
        assert_eq!(
            dump(&PyValue::List(vec![text(
                "a\tb\u{feff}\u{1f600}\u{e650}\u{85}"
            )])),
            "[\"a\\tb\\uFEFF\\U0001F600\u{e650}\\N\"]"
        );
    }

    #[test]
    fn bytes_sets_and_records_are_refused() {
        for value in [
            PyValue::Bytes(vec![1]),
            PyValue::Set(vec![]),
            PyValue::Record("Unit".into(), vec![]),
        ] {
            assert!(flow_dump(&PyValue::List(vec![value])).is_err());
        }
    }
}
