//! The rune-schema check the drafter runs on each policy record it drafts: `spec_load._load_schema` over `rebuild/schema/rune.schema.json`, `_SchemaValidator`'s closed vocabulary and root-local references, and `for_definition(...).check` as the drafter builds its three checkers ([`RuneSchema`], [`Checker`]).
//!
//! Only the verdict reaches the drafter's output: a record the schema rejects raises `DraftError`, and a shipped draft says `schema_valid: true`. So a [`Checker`] answers whether `check` finds no problem, and the problem text is not ported. The schema is compiled once into an arena of nodes when it is read, and what `_SchemaValidator` would refuse is refused then: a keyword outside the vocabulary, a `type` outside its types, `$id` or `$schema` below the root or another dialect, a keyword value of a shape the 2020-12 metaschema rejects, and a `$ref` that is not root-local or names no schema. A `pattern` outside the syntax [`Pattern`] reads, a float and an integer beyond 64 bits are refused too, since nothing the drafter writes reaches them.

use std::collections::HashMap;
use std::fmt;
use std::path::Path;

use serde_json::{Map, Value};

use crate::pyfmt::{PyValue, decimal_value, is_space};

/// A schema the check refuses.
#[derive(Debug)]
pub struct SchemaError(pub String);

impl fmt::Display for SchemaError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for SchemaError {}

type Result<T> = std::result::Result<T, SchemaError>;

fn refuse<T>(message: impl Into<String>) -> Result<T> {
    Err(SchemaError(message.into()))
}

/// `_SchemaValidator._KEYWORDS`, the keywords the check applies.
const KEYWORDS: &[&str] = &[
    "type",
    "enum",
    "const",
    "pattern",
    "minimum",
    "items",
    "minItems",
    "maxItems",
    "minProperties",
    "properties",
    "patternProperties",
    "additionalProperties",
    "required",
    "propertyNames",
    "allOf",
    "anyOf",
    "oneOf",
    "not",
    "if",
    "then",
    "$ref",
    "$defs",
];

/// `_SchemaValidator._ANNOTATIONS`, the keywords the check reads past.
const ANNOTATIONS: &[&str] = &["$schema", "$id", "title", "description"];

/// The dialect `$schema` must name, `Draft202012Validator.META_SCHEMA["$id"]`.
const DIALECT: &str = "https://json-schema.org/draft/2020-12/schema";

/// One step of a schema's place in the document: an object key or an array index.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
enum Part {
    Key(String),
    Index(usize),
}

type NodeId = usize;

/// A compiled schema: a boolean schema, or an object's keywords in the document's order.
enum Node {
    Bool(bool),
    Keywords(Vec<Keyword>),
}

/// `_SchemaValidator._TYPES`, as the strict validator's type checker reads each.
#[derive(Clone, Copy)]
enum Kind {
    Object,
    Array,
    String,
    Integer,
    Boolean,
}

impl Kind {
    fn named(name: &str) -> Option<Self> {
        Some(match name {
            "object" => Self::Object,
            "array" => Self::Array,
            "string" => Self::String,
            "integer" => Self::Integer,
            "boolean" => Self::Boolean,
            _ => return None,
        })
    }

    /// `is_type`: a dict, a list (never a tuple), a str, an int that is not a bool, a bool.
    fn admits(self, value: &PyValue) -> bool {
        matches!(
            (self, value),
            (Self::Object, PyValue::Dict(_))
                | (Self::Array, PyValue::List(_))
                | (Self::String, PyValue::Str(_))
                | (Self::Integer, PyValue::Int(_))
                | (Self::Boolean, PyValue::Bool(_))
        )
    }
}

enum Keyword {
    Type(Kind),
    Enum(Vec<Value>),
    Const(Value),
    Pattern(Pattern),
    Minimum(i64),
    Items(NodeId),
    MinItems(usize),
    MaxItems(usize),
    MinProperties(usize),
    Properties(Vec<(String, NodeId)>),
    PatternProperties(Vec<(Pattern, NodeId)>),
    AdditionalProperties {
        schema: NodeId,
        known: Vec<String>,
        patterns: Vec<Pattern>,
    },
    Required(Vec<String>),
    PropertyNames(NodeId),
    AllOf(Vec<NodeId>),
    AnyOf(Vec<NodeId>),
    OneOf(Vec<NodeId>),
    Not(NodeId),
    If {
        condition: NodeId,
        then: Option<NodeId>,
    },
    Ref(NodeId),
}

/// The rune schema, compiled: what `spec_load._load_schema` gives the drafter.
pub struct RuneSchema {
    nodes: Vec<Node>,
    definitions: HashMap<String, NodeId>,
}

/// One definition's checker, `_SchemaValidator.for_definition(name)`.
#[derive(Clone, Copy)]
pub struct Checker<'a> {
    schema: &'a RuneSchema,
    node: NodeId,
}

impl RuneSchema {
    /// Read and compile the schema file at `path`, which `json.loads` reads as Python reads it.
    pub fn read(path: &Path) -> Result<Self> {
        let text = std::fs::read_to_string(path).map_err(|failure| {
            SchemaError(format!(
                "cannot read the rune schema {}: {failure}",
                path.display()
            ))
        })?;
        let name = path.file_name().map_or_else(
            || path.display().to_string(),
            |name| name.to_string_lossy().into_owned(),
        );
        let schema: Value = serde_json::from_str(&text)
            .map_err(|failure| SchemaError(format!("{name}: not JSON: {failure}")))?;
        Self::compile(&schema, &name)
    }

    /// Compile a schema document, refusing what `_SchemaValidator` refuses (the module doc). `name` names the schema in a refusal.
    pub fn compile(schema: &Value, name: &str) -> Result<Self> {
        if !schema.is_object() {
            return refuse(format!("{name}: the schema is not a JSON object"));
        }
        let mut compiler = Compiler {
            name,
            nodes: Vec::new(),
            paths: HashMap::new(),
            references: Vec::new(),
        };
        compiler.node(schema, &mut Vec::new())?;
        for (node, index, reference) in std::mem::take(&mut compiler.references) {
            let target = compiler.resolve(schema, &reference)?;
            if let Node::Keywords(keywords) = &mut compiler.nodes[node] {
                keywords[index] = Keyword::Ref(target);
            }
        }
        let definitions = schema
            .get("$defs")
            .and_then(Value::as_object)
            .map(|defs| {
                defs.keys()
                    .map(|definition| {
                        let path = [Part::Key("$defs".to_owned()), Part::Key(definition.clone())];
                        (definition.clone(), compiler.paths[path.as_slice()])
                    })
                    .collect()
            })
            .unwrap_or_default();
        Ok(Self {
            nodes: compiler.nodes,
            definitions,
        })
    }

    /// `for_definition`: the checker of one `$defs` entry, or a refusal naming an unknown one.
    pub fn for_definition(&self, name: &str) -> Result<Checker<'_>> {
        match self.definitions.get(name) {
            Some(&node) => Ok(Checker { schema: self, node }),
            None => refuse(format!("unknown schema definition {name:?}")),
        }
    }

    fn valid(&self, node: NodeId, value: &PyValue) -> bool {
        match &self.nodes[node] {
            Node::Bool(verdict) => *verdict,
            Node::Keywords(keywords) => keywords.iter().all(|keyword| self.holds(keyword, value)),
        }
    }

    /// Whether one keyword passes, as the jsonschema validator of the same name decides it: each applies only to the kinds of value it names and passes any other.
    fn holds(&self, keyword: &Keyword, value: &PyValue) -> bool {
        match keyword {
            Keyword::Type(kind) => kind.admits(value),
            Keyword::Enum(options) => options.iter().any(|option| equal(option, value)),
            Keyword::Const(option) => equal(option, value),
            Keyword::Pattern(pattern) => match value {
                PyValue::Str(text) => pattern.search(text),
                _ => true,
            },
            Keyword::Minimum(minimum) => match value {
                PyValue::Int(number) => number >= minimum,
                _ => true,
            },
            Keyword::Items(schema) => match value {
                PyValue::List(items) => items.iter().all(|item| self.valid(*schema, item)),
                _ => true,
            },
            Keyword::MinItems(least) => match value {
                PyValue::List(items) => items.len() >= *least,
                _ => true,
            },
            Keyword::MaxItems(most) => match value {
                PyValue::List(items) => items.len() <= *most,
                _ => true,
            },
            Keyword::MinProperties(least) => match value {
                PyValue::Dict(entries) => entries.len() >= *least,
                _ => true,
            },
            Keyword::Properties(properties) => match value {
                PyValue::Dict(entries) => properties.iter().all(|(name, schema)| {
                    entry(entries, name).is_none_or(|child| self.valid(*schema, child))
                }),
                _ => true,
            },
            Keyword::PatternProperties(properties) => match value {
                PyValue::Dict(entries) => properties.iter().all(|(pattern, schema)| {
                    entries.iter().all(|(key, child)| match key {
                        PyValue::Str(name) if pattern.search(name) => self.valid(*schema, child),
                        _ => true,
                    })
                }),
                _ => true,
            },
            Keyword::AdditionalProperties {
                schema,
                known,
                patterns,
            } => match value {
                PyValue::Dict(entries) => entries
                    .iter()
                    .filter(|(key, _)| match key {
                        PyValue::Str(name) => {
                            !known.contains(name)
                                && !patterns.iter().any(|pattern| pattern.search(name))
                        }
                        _ => true,
                    })
                    .all(|(_, child)| self.valid(*schema, child)),
                _ => true,
            },
            Keyword::Required(names) => match value {
                PyValue::Dict(entries) => names.iter().all(|name| entry(entries, name).is_some()),
                _ => true,
            },
            Keyword::PropertyNames(schema) => match value {
                PyValue::Dict(entries) => entries.iter().all(|(key, _)| self.valid(*schema, key)),
                _ => true,
            },
            Keyword::AllOf(schemas) => schemas.iter().all(|schema| self.valid(*schema, value)),
            Keyword::AnyOf(schemas) => schemas.iter().any(|schema| self.valid(*schema, value)),
            Keyword::OneOf(schemas) => {
                schemas
                    .iter()
                    .filter(|schema| self.valid(**schema, value))
                    .take(2)
                    .count()
                    == 1
            }
            Keyword::Not(schema) => !self.valid(*schema, value),
            Keyword::If { condition, then } => {
                !self.valid(*condition, value) || then.is_none_or(|then| self.valid(then, value))
            }
            Keyword::Ref(schema) => self.valid(*schema, value),
        }
    }
}

impl Checker<'_> {
    /// Whether `check` finds no problem with `record`: every dict key reached through dicts and lists is a `str`, and the definition validates it.
    pub fn check(&self, record: &PyValue) -> bool {
        keys_are_strings(record) && self.schema.valid(self.node, record)
    }
}

/// `check`'s key pass, which reports any key that is not a `str` before the schema runs; it walks dicts and lists, never tuples.
fn keys_are_strings(value: &PyValue) -> bool {
    match value {
        PyValue::Dict(entries) => entries
            .iter()
            .all(|(key, child)| matches!(key, PyValue::Str(_)) && keys_are_strings(child)),
        PyValue::List(items) => items.iter().all(keys_are_strings),
        _ => true,
    }
}

/// The value under the `str` key `name`, as `name in instance` finds it.
fn entry<'v>(entries: &'v [(PyValue, PyValue)], name: &str) -> Option<&'v PyValue> {
    entries.iter().find_map(|(key, child)| match key {
        PyValue::Str(key) if key == name => Some(child),
        _ => None,
    })
}

/// jsonschema's `_utils.equal` between a schema's JSON value and a Python value: strings only equal strings, sequences (a tuple, a list, bytes) compare item by item, mappings key by key, and otherwise `==` with `True` and `False` kept apart from `1` and `0`. A dataclass, a named tuple and a set equal no JSON value.
fn equal(schema: &Value, value: &PyValue) -> bool {
    match (schema, value) {
        (Value::String(text), PyValue::Str(other)) => text == other,
        (Value::String(_), _) | (_, PyValue::Str(_)) => false,
        (Value::Array(items), PyValue::Tuple(others) | PyValue::List(others)) => {
            items.len() == others.len()
                && items
                    .iter()
                    .zip(others)
                    .all(|(item, other)| equal(item, other))
        }
        (Value::Array(items), PyValue::Bytes(bytes)) => {
            items.len() == bytes.len()
                && items
                    .iter()
                    .zip(bytes)
                    .all(|(item, byte)| equal(item, &PyValue::Int(i64::from(*byte))))
        }
        (Value::Object(map), PyValue::Dict(entries)) => {
            map.len() == entries.len()
                && map.iter().all(|(name, item)| {
                    entry(entries, name).is_some_and(|other| equal(item, other))
                })
        }
        (Value::Null, PyValue::None) => true,
        (Value::Bool(flag), PyValue::Bool(other)) => flag == other,
        (Value::Number(number), PyValue::Int(other)) => number.as_i64() == Some(*other),
        _ => false,
    }
}

struct Compiler<'a> {
    name: &'a str,
    nodes: Vec<Node>,
    paths: HashMap<Vec<Part>, NodeId>,
    references: Vec<(NodeId, usize, String)>,
}

impl Compiler<'_> {
    fn refuse<T>(&self, message: impl fmt::Display) -> Result<T> {
        refuse(format!("{}: {message}", self.name))
    }

    /// Compile the schema at `path`, recording the path as a schema's place for `$ref`.
    fn node(&mut self, schema: &Value, path: &mut Vec<Part>) -> Result<NodeId> {
        let id = self.nodes.len();
        self.nodes.push(Node::Bool(true));
        self.paths.insert(path.clone(), id);
        self.nodes[id] = match schema {
            Value::Bool(verdict) => Node::Bool(*verdict),
            Value::Object(map) => Node::Keywords(self.keywords(id, map, path)?),
            _ => return self.refuse(format!("{} is not a schema", pointer(path))),
        };
        Ok(id)
    }

    fn child(&mut self, schema: &Value, path: &mut Vec<Part>, parts: &[Part]) -> Result<NodeId> {
        let depth = path.len();
        path.extend_from_slice(parts);
        let id = self.node(schema, path);
        path.truncate(depth);
        id
    }

    fn children(
        &mut self,
        keyword: &str,
        value: &Value,
        path: &mut Vec<Part>,
    ) -> Result<Vec<NodeId>> {
        match value.as_array() {
            Some(schemas) if !schemas.is_empty() => schemas
                .iter()
                .enumerate()
                .map(|(index, schema)| {
                    self.child(
                        schema,
                        path,
                        &[Part::Key(keyword.to_owned()), Part::Index(index)],
                    )
                })
                .collect(),
            _ => self.refuse(format!(
                "{keyword} at {} is not a non-empty array of schemas",
                pointer(path)
            )),
        }
    }

    fn named_children(
        &mut self,
        keyword: &str,
        value: &Value,
        path: &mut Vec<Part>,
    ) -> Result<Vec<(String, NodeId)>> {
        let Some(map) = value.as_object() else {
            return self.refuse(format!(
                "{keyword} at {} is not an object of schemas",
                pointer(path)
            ));
        };
        map.iter()
            .map(|(name, schema)| {
                let parts = [Part::Key(keyword.to_owned()), Part::Key(name.clone())];
                Ok((name.clone(), self.child(schema, path, &parts)?))
            })
            .collect()
    }

    fn count(&self, keyword: &str, value: &Value, path: &[Part]) -> Result<usize> {
        match value.as_u64().and_then(|count| usize::try_from(count).ok()) {
            Some(count) => Ok(count),
            None => self.refuse(format!(
                "{keyword} at {} is not a non-negative integer",
                pointer(path)
            )),
        }
    }

    fn pattern(&self, source: &str, path: &[Part]) -> Result<Pattern> {
        Pattern::compile(source).map_err(|failure| {
            SchemaError(format!(
                "{}: the pattern {source:?} at {}: {failure}",
                self.name,
                pointer(path)
            ))
        })
    }

    fn keywords(
        &mut self,
        id: NodeId,
        map: &Map<String, Value>,
        path: &mut Vec<Part>,
    ) -> Result<Vec<Keyword>> {
        let mut keywords = Vec::new();
        let (mut condition, mut then) = (None, None);
        for (keyword, value) in map {
            if !KEYWORDS.contains(&keyword.as_str()) && !ANNOTATIONS.contains(&keyword.as_str()) {
                return self.refuse(format!("unsupported JSON Schema keyword {keyword:?}"));
            }
            match keyword.as_str() {
                "$schema" | "$id" if !path.is_empty() => {
                    return self.refuse(format!("{keyword} is supported only at the schema root"));
                }
                "$schema" if value.as_str() != Some(DIALECT) => {
                    return self.refuse("$schema must name JSON Schema Draft 2020-12");
                }
                "$schema" => {}
                "$id" | "title" | "description" if !value.is_string() => {
                    return self.refuse(format!("{keyword} at {} is not a string", pointer(path)));
                }
                "$id" | "title" | "description" => {}
                "type" => match value.as_str().and_then(Kind::named) {
                    Some(kind) => keywords.push(Keyword::Type(kind)),
                    None => return self.refuse(format!("unsupported type {value}")),
                },
                "enum" => match value.as_array() {
                    Some(options) => {
                        self.integral(value, path)?;
                        keywords.push(Keyword::Enum(options.clone()));
                    }
                    None => {
                        return self.refuse(format!("enum at {} is not an array", pointer(path)));
                    }
                },
                "const" => {
                    self.integral(value, path)?;
                    keywords.push(Keyword::Const(value.clone()));
                }
                "pattern" => match value.as_str() {
                    Some(source) => keywords.push(Keyword::Pattern(self.pattern(source, path)?)),
                    None => {
                        return self
                            .refuse(format!("pattern at {} is not a string", pointer(path)));
                    }
                },
                "minimum" => match value.as_i64() {
                    Some(minimum) => keywords.push(Keyword::Minimum(minimum)),
                    None => {
                        return self
                            .refuse(format!("minimum at {} is not an integer", pointer(path)));
                    }
                },
                "minItems" => keywords.push(Keyword::MinItems(self.count(keyword, value, path)?)),
                "maxItems" => keywords.push(Keyword::MaxItems(self.count(keyword, value, path)?)),
                "minProperties" => {
                    keywords.push(Keyword::MinProperties(self.count(keyword, value, path)?));
                }
                "required" => {
                    let names: Option<Vec<String>> = value.as_array().and_then(|names| {
                        names
                            .iter()
                            .map(|name| name.as_str().map(str::to_owned))
                            .collect()
                    });
                    match names {
                        Some(names)
                            if names
                                .iter()
                                .enumerate()
                                .all(|(index, name)| !names[..index].contains(name)) =>
                        {
                            keywords.push(Keyword::Required(names));
                        }
                        _ => {
                            return self.refuse(format!(
                                "required at {} is not an array of distinct strings",
                                pointer(path)
                            ));
                        }
                    }
                }
                "items" | "additionalProperties" | "propertyNames" | "not" | "if" | "then" => {
                    let schema = self.child(value, path, &[Part::Key(keyword.clone())])?;
                    match keyword.as_str() {
                        "items" => keywords.push(Keyword::Items(schema)),
                        "additionalProperties" => {
                            let known = map
                                .get("properties")
                                .and_then(Value::as_object)
                                .map(|properties| properties.keys().cloned().collect())
                                .unwrap_or_default();
                            let patterns =
                                match map.get("patternProperties").and_then(Value::as_object) {
                                    Some(properties) => properties
                                        .keys()
                                        .map(|source| self.pattern(source, path))
                                        .collect::<Result<_>>()?,
                                    None => Vec::new(),
                                };
                            keywords.push(Keyword::AdditionalProperties {
                                schema,
                                known,
                                patterns,
                            });
                        }
                        "propertyNames" => keywords.push(Keyword::PropertyNames(schema)),
                        "not" => keywords.push(Keyword::Not(schema)),
                        "if" => condition = Some(schema),
                        _ => then = Some(schema),
                    }
                }
                "properties" => {
                    let properties = self.named_children(keyword, value, path)?;
                    keywords.push(Keyword::Properties(properties));
                }
                "patternProperties" => {
                    let properties = self
                        .named_children(keyword, value, path)?
                        .into_iter()
                        .map(|(source, schema)| Ok((self.pattern(&source, path)?, schema)))
                        .collect::<Result<_>>()?;
                    keywords.push(Keyword::PatternProperties(properties));
                }
                "$defs" => {
                    self.named_children(keyword, value, path)?;
                }
                "allOf" => keywords.push(Keyword::AllOf(self.children(keyword, value, path)?)),
                "anyOf" => keywords.push(Keyword::AnyOf(self.children(keyword, value, path)?)),
                "oneOf" => keywords.push(Keyword::OneOf(self.children(keyword, value, path)?)),
                _ => match value.as_str() {
                    Some(reference) => {
                        self.references
                            .push((id, keywords.len(), reference.to_owned()));
                        keywords.push(Keyword::Ref(id));
                    }
                    None => {
                        return self.refuse(format!("$ref at {} is not a string", pointer(path)));
                    }
                },
            }
        }
        if let Some(condition) = condition {
            keywords.push(Keyword::If { condition, then });
        }
        Ok(keywords)
    }

    /// Refuse a float or an integer beyond 64 bits anywhere in an `enum` or `const` value: no Python value the check meets is a float, and `equal` would let `1.0` equal `1`.
    fn integral(&self, value: &Value, path: &[Part]) -> Result<()> {
        match value {
            Value::Number(number) if number.as_i64().is_none() => self.refuse(format!(
                "the number {number} at {} is not a 64-bit integer",
                pointer(path)
            )),
            Value::Array(items) => items.iter().try_for_each(|item| self.integral(item, path)),
            Value::Object(map) => map.values().try_for_each(|item| self.integral(item, path)),
            _ => Ok(()),
        }
    }

    /// The schema a `$ref` names, resolved as `_check_schema_contract` resolves it: `#` or a percent-encoded JSON Pointer after `#/`, each token an object key or, in an array, a decimal index inside it, ending on a schema's place.
    fn resolve(&self, root: &Value, reference: &str) -> Result<NodeId> {
        if reference != "#" && !reference.starts_with("#/") {
            return self.refuse(format!(
                "only root-local JSON Pointer $refs are supported: {reference:?}"
            ));
        }
        let fragment = percent_decoded(&reference[1..]);
        let mut target = root;
        let mut path = Vec::new();
        if let Some(tokens) = fragment.strip_prefix('/') {
            for token in tokens.split('/') {
                let token = token.replace("~1", "/").replace("~0", "~");
                let step = match target {
                    Value::Object(map) => map.get(&token).map(|child| (child, Part::Key(token))),
                    Value::Array(items) => decimal(&token).and_then(|index| {
                        items.get(index).map(|child| (child, Part::Index(index)))
                    }),
                    _ => None,
                };
                let Some((child, part)) = step else {
                    return self.refuse(format!("dangling $ref {reference:?}"));
                };
                target = child;
                path.push(part);
            }
        }
        match self.paths.get(&path) {
            Some(&node) => Ok(node),
            None => self.refuse(format!("$ref {reference:?} does not name a schema")),
        }
    }
}

/// A JSON Pointer token as an index, read as `token.isdigit()` and `int(token)` read it; a digit `int` cannot read is no index either.
fn decimal(token: &str) -> Option<usize> {
    if token.is_empty() {
        return None;
    }
    token.chars().try_fold(0usize, |index, letter| {
        let digit = usize::try_from(decimal_value(letter)?).ok()?;
        index.checked_mul(10)?.checked_add(digit)
    })
}

/// `urllib.parse.unquote`: each `%` and two hex digits as a byte, the bytes read as UTF-8 with replacement.
fn percent_decoded(text: &str) -> String {
    let bytes = text.as_bytes();
    let mut decoded = Vec::with_capacity(bytes.len());
    let mut at = 0;
    while at < bytes.len() {
        let hex = |offset: usize| {
            bytes
                .get(at + offset)
                .and_then(|&byte| char::from(byte).to_digit(16))
        };
        match (bytes[at], hex(1), hex(2)) {
            (b'%', Some(high), Some(low)) => {
                decoded.push(u8::try_from(high * 16 + low).unwrap_or_default());
                at += 3;
            }
            (byte, _, _) => {
                decoded.push(byte);
                at += 1;
            }
        }
    }
    String::from_utf8_lossy(&decoded).into_owned()
}

fn pointer(path: &[Part]) -> String {
    let mut text = "#".to_owned();
    for part in path {
        match part {
            Part::Key(key) => text += &format!("/{key}"),
            Part::Index(index) => text += &format!("/{index}"),
        }
    }
    text
}

/// A `pattern` as Python's `re.search` reads it, in the syntax the rune schema's patterns use: literals and escaped punctuation, `.`, classes with ranges and negation, `\s`, `\S`, `\d` and `\D` with Python's Unicode meanings, the anchors `^`, `$` (which also matches before a final newline), `\A` and `\Z`, groups and non-capturing groups, alternation, and greedy or lazy `*`, `+`, `?` and `{m,n}`. Anything else is refused when the pattern compiles. Only whether a match exists is read, which the order the alternatives are tried in does not change.
pub struct Pattern {
    alternatives: Vec<Vec<Piece>>,
}

struct Piece {
    atom: Atom,
    min: usize,
    max: Option<usize>,
}

enum Atom {
    Set(CharSet),
    Group(Vec<Vec<Piece>>),
    Start,
    End,
    AbsoluteEnd,
}

enum CharSet {
    Literal(char),
    Any,
    Class {
        items: Vec<ClassItem>,
        negated: bool,
    },
}

enum ClassItem {
    Range(char, char),
    Space(bool),
    Decimal(bool),
}

impl ClassItem {
    fn matches(&self, letter: char) -> bool {
        match self {
            Self::Range(low, high) => (*low..=*high).contains(&letter),
            Self::Space(wanted) => is_space(letter) == *wanted,
            Self::Decimal(wanted) => decimal_value(letter).is_some() == *wanted,
        }
    }
}

impl CharSet {
    fn matches(&self, letter: char) -> bool {
        match self {
            Self::Literal(literal) => letter == *literal,
            Self::Any => letter != '\n',
            Self::Class { items, negated } => {
                items.iter().any(|item| item.matches(letter)) != *negated
            }
        }
    }
}

impl Pattern {
    /// Compile `source`, or say why it is refused.
    pub fn compile(source: &str) -> std::result::Result<Self, String> {
        let mut parser = PatternParser {
            letters: source.chars().collect(),
            at: 0,
        };
        let alternatives = parser.alternatives()?;
        if parser.at < parser.letters.len() {
            return Err("unbalanced parenthesis".to_owned());
        }
        Ok(Self { alternatives })
    }

    /// `re.search(pattern, text) is not None`.
    pub fn search(&self, text: &str) -> bool {
        let letters: Vec<char> = text.chars().collect();
        (0..=letters.len())
            .any(|start| alternatives(&self.alternatives, &letters, start, &mut |_| true))
    }
}

fn alternatives(
    choices: &[Vec<Piece>],
    text: &[char],
    at: usize,
    rest: &mut dyn FnMut(usize) -> bool,
) -> bool {
    choices
        .iter()
        .any(|sequence| pieces(sequence, text, at, rest))
}

fn pieces(
    sequence: &[Piece],
    text: &[char],
    at: usize,
    rest: &mut dyn FnMut(usize) -> bool,
) -> bool {
    match sequence.split_first() {
        None => rest(at),
        Some((piece, tail)) => repeat(piece, 0, text, at, &mut |next| {
            pieces(tail, text, next, rest)
        }),
    }
}

/// Match `piece` at least its minimum and at most its maximum more times from `count`, then `rest`. A repetition that consumes nothing past the minimum is not taken again, so an empty-matching group cannot loop.
fn repeat(
    piece: &Piece,
    count: usize,
    text: &[char],
    at: usize,
    rest: &mut dyn FnMut(usize) -> bool,
) -> bool {
    let again = piece.max.is_none_or(|max| count < max)
        && atom(&piece.atom, text, at, &mut |next| {
            (next != at || count < piece.min) && repeat(piece, count + 1, text, next, rest)
        });
    again || (count >= piece.min && rest(at))
}

fn atom(atom: &Atom, text: &[char], at: usize, rest: &mut dyn FnMut(usize) -> bool) -> bool {
    match atom {
        Atom::Set(set) => text.get(at).is_some_and(|&letter| set.matches(letter)) && rest(at + 1),
        Atom::Group(choices) => alternatives(choices, text, at, rest),
        Atom::Start => at == 0 && rest(at),
        Atom::End => (at == text.len() || (at + 1 == text.len() && text[at] == '\n')) && rest(at),
        Atom::AbsoluteEnd => at == text.len() && rest(at),
    }
}

struct PatternParser {
    letters: Vec<char>,
    at: usize,
}

impl PatternParser {
    fn peek(&self) -> Option<char> {
        self.letters.get(self.at).copied()
    }

    fn take(&mut self) -> Option<char> {
        let letter = self.peek();
        self.at += usize::from(letter.is_some());
        letter
    }

    fn alternatives(&mut self) -> std::result::Result<Vec<Vec<Piece>>, String> {
        let mut choices = vec![self.sequence()?];
        while self.peek() == Some('|') {
            self.at += 1;
            choices.push(self.sequence()?);
        }
        Ok(choices)
    }

    fn sequence(&mut self) -> std::result::Result<Vec<Piece>, String> {
        let mut sequence = Vec::new();
        while let Some(letter) = self.peek() {
            if letter == '|' || letter == ')' {
                break;
            }
            let atom = self.atom()?;
            let (min, max) = match self.quantifier()? {
                Some(_) if matches!(atom, Atom::Start | Atom::End | Atom::AbsoluteEnd) => {
                    return Err("nothing to repeat".to_owned());
                }
                Some(bounds) => bounds,
                None => (1, Some(1)),
            };
            sequence.push(Piece { atom, min, max });
        }
        Ok(sequence)
    }

    fn atom(&mut self) -> std::result::Result<Atom, String> {
        match self.take() {
            Some('(') => {
                if self.peek() == Some('?') {
                    if self.letters.get(self.at + 1) != Some(&':') {
                        return Err("an extension group other than (?:...)".to_owned());
                    }
                    self.at += 2;
                }
                let choices = self.alternatives()?;
                if self.take() != Some(')') {
                    return Err("missing ), unterminated subpattern".to_owned());
                }
                Ok(Atom::Group(choices))
            }
            Some('[') => self.class(),
            Some('.') => Ok(Atom::Set(CharSet::Any)),
            Some('^') => Ok(Atom::Start),
            Some('$') => Ok(Atom::End),
            Some('\\') => match self.take() {
                Some('A') => Ok(Atom::Start),
                Some('Z') => Ok(Atom::AbsoluteEnd),
                Some(letter) => Ok(Atom::Set(match escape(letter)? {
                    ClassItem::Range(literal, _) => CharSet::Literal(literal),
                    item => CharSet::Class {
                        items: vec![item],
                        negated: false,
                    },
                })),
                None => Err("bad escape (end of pattern)".to_owned()),
            },
            Some('*' | '+' | '?' | '{') => Err("nothing to repeat, or a bare {".to_owned()),
            Some(letter) => Ok(Atom::Set(CharSet::Literal(letter))),
            None => Err("end of pattern".to_owned()),
        }
    }

    fn class(&mut self) -> std::result::Result<Atom, String> {
        let negated = self.peek() == Some('^');
        self.at += usize::from(negated);
        let start = self.at;
        let mut items = Vec::new();
        loop {
            let item = match self.take() {
                None => return Err("unterminated character set".to_owned()),
                Some(']') if self.at != start + 1 => break,
                Some('\\') => match self.take() {
                    Some(letter) => escape(letter)?,
                    None => return Err("unterminated character set".to_owned()),
                },
                Some(letter) => ClassItem::Range(letter, letter),
            };
            if self.peek() == Some('-')
                && self
                    .letters
                    .get(self.at + 1)
                    .is_some_and(|&next| next != ']')
            {
                self.at += 1;
                let high = match self.take() {
                    Some('\\') => match self.take() {
                        Some(letter) => escape(letter)?,
                        None => return Err("unterminated character set".to_owned()),
                    },
                    Some(letter) => ClassItem::Range(letter, letter),
                    None => return Err("unterminated character set".to_owned()),
                };
                match (item, high) {
                    (ClassItem::Range(low, _), ClassItem::Range(high, _)) if low <= high => {
                        items.push(ClassItem::Range(low, high));
                    }
                    _ => return Err("bad character range".to_owned()),
                }
            } else {
                items.push(item);
            }
        }
        Ok(Atom::Set(CharSet::Class { items, negated }))
    }

    /// A quantifier after an atom, as `(min, max)`, with a lazy `?` after it read past; a possessive or a second quantifier is refused.
    fn quantifier(&mut self) -> std::result::Result<Option<(usize, Option<usize>)>, String> {
        let bounds = match self.peek() {
            Some('*') => (0, None),
            Some('+') => (1, None),
            Some('?') => (0, Some(1)),
            Some('{') => {
                self.at += 1;
                let low = self.digits();
                let high = if self.peek() == Some(',') {
                    self.at += 1;
                    self.digits()
                } else {
                    Some(low.ok_or("a { that is not a quantifier")?)
                };
                if self.peek() != Some('}') {
                    return Err("a { that is not a quantifier".to_owned());
                }
                let low = low.unwrap_or(0);
                if high.is_some_and(|high| high < low) {
                    return Err("min repeat greater than max repeat".to_owned());
                }
                (low, high)
            }
            _ => return Ok(None),
        };
        self.at += 1;
        if self.peek() == Some('?') {
            self.at += 1;
        }
        if matches!(self.peek(), Some('*' | '+' | '?' | '{')) {
            return Err("multiple repeat".to_owned());
        }
        Ok(Some(bounds))
    }

    fn digits(&mut self) -> Option<usize> {
        let start = self.at;
        while self.peek().is_some_and(|letter| letter.is_ascii_digit()) {
            self.at += 1;
        }
        self.letters[start..self.at]
            .iter()
            .collect::<String>()
            .parse()
            .ok()
    }
}

/// An escape's meaning, in a class or out of one: `\s`, `\S`, `\d` and `\D`, the control escapes, and any character but an ASCII letter or digit as itself, as a one-character range.
fn escape(letter: char) -> std::result::Result<ClassItem, String> {
    let literal = |letter| Ok(ClassItem::Range(letter, letter));
    match letter {
        's' => Ok(ClassItem::Space(true)),
        'S' => Ok(ClassItem::Space(false)),
        'd' => Ok(ClassItem::Decimal(true)),
        'D' => Ok(ClassItem::Decimal(false)),
        't' => literal('\t'),
        'n' => literal('\n'),
        'r' => literal('\r'),
        'f' => literal('\u{c}'),
        'v' => literal('\u{b}'),
        letter if !letter.is_ascii_alphanumeric() => literal(letter),
        letter => Err(format!("an escape the check does not read: \\{letter}")),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn schema(text: &str) -> Result<RuneSchema> {
        RuneSchema::compile(&serde_json::from_str(text).expect("JSON"), "test.json")
    }

    fn dict(entries: &[(&str, PyValue)]) -> PyValue {
        PyValue::Dict(
            entries
                .iter()
                .map(|(key, value)| (PyValue::str(*key), value.clone()))
                .collect(),
        )
    }

    #[test]
    fn a_keyword_outside_the_vocabulary_and_a_ref_that_names_no_schema_are_refused() {
        assert!(schema(r##"{"$defs": {"a": {"format": "uri"}}}"##).is_err());
        assert!(schema(r##"{"$defs": {"a": {"$ref": "#/$defs/b"}}}"##).is_err());
        assert!(schema(r##"{"$defs": {"a": {"$ref": "#/$defs"}}}"##).is_err());
        assert!(schema(r##"{"$defs": {"a": {"$ref": "other.json#/$defs/a"}}}"##).is_err());
        assert!(schema(r##"{"$defs": {"a": {"$id": "x"}}}"##).is_err());
        assert!(schema(r##"{"$defs": {"a": {"type": "number"}}}"##).is_err());
        assert!(schema(r##"{"$defs": {"a": {"$ref": "#/$defs/b%7E1c"}, "b~c": true}}"##).is_err());
        assert!(schema(r##"{"$defs": {"a": {"$ref": "#/$defs/b~1c"}, "b/c": true}}"##).is_ok());
    }

    #[test]
    fn types_are_strict_a_tuple_is_no_array_and_a_bool_no_integer() {
        let schema = schema(
            r#"{"$defs": {"a": {"type": "array", "items": {"type": "integer", "minimum": 1}}}}"#,
        )
        .expect("compiles");
        let checker = schema.for_definition("a").expect("defined");
        assert!(checker.check(&PyValue::List(vec![PyValue::Int(1)])));
        assert!(!checker.check(&PyValue::Tuple(vec![PyValue::Int(1)])));
        assert!(!checker.check(&PyValue::List(vec![PyValue::Bool(true)])));
        assert!(!checker.check(&PyValue::List(vec![PyValue::Int(0)])));
        assert!(schema.for_definition("b").is_err());
    }

    #[test]
    fn a_key_that_is_not_a_str_fails_through_dicts_and_lists_but_not_tuples() {
        let schema = schema(r#"{"$defs": {"a": true}}"#).expect("compiles");
        let checker = schema.for_definition("a").expect("defined");
        let odd = PyValue::Dict(vec![(PyValue::Int(1), PyValue::None)]);
        assert!(!checker.check(&dict(&[("x", PyValue::List(vec![odd.clone()]))])));
        assert!(checker.check(&dict(&[("x", PyValue::Tuple(vec![odd]))])));
    }

    #[test]
    fn closed_records_with_one_of_and_if_then() {
        let schema = schema(
            r##"{"$defs": {"r": {"type": "object", "additionalProperties": false, "required": ["when"],
                "properties": {"when": {"$ref": "#/$defs/w"}, "mode": {"enum": ["absolute", "soft"]}, "why": {"type": "string"}},
                "allOf": [{"if": {"properties": {"mode": {"const": "absolute"}}, "required": ["mode"]}, "then": {"required": ["why"]}}]},
                "w": {"oneOf": [{"type": "string"}, {"type": "array", "minItems": 1}]}}}"##,
        )
        .expect("compiles");
        let checker = schema.for_definition("r").expect("defined");
        let when = PyValue::str("x");
        assert!(checker.check(&dict(&[("when", when.clone())])));
        assert!(!checker.check(&dict(&[("when", when.clone()), ("zz", PyValue::None)])));
        assert!(!checker.check(&dict(&[("when", PyValue::List(Vec::new()))])));
        assert!(!checker.check(&dict(&[
            ("when", when.clone()),
            ("mode", PyValue::str("absolute"))
        ])));
        assert!(checker.check(&dict(&[
            ("when", when.clone()),
            ("mode", PyValue::str("absolute")),
            ("why", PyValue::str("y")),
        ])));
        assert!(checker.check(&dict(&[("when", when), ("mode", PyValue::str("soft"))])));
    }

    #[test]
    fn patterns_search_as_python_re_search_does() {
        let family = Pattern::compile("^qs[A-Z][A-Za-z]*(_qs[A-Z][A-Za-z]*)*$").expect("compiles");
        assert!(family.search("qsPea"));
        assert!(family.search("qsDay_qsUtter"));
        assert!(family.search("qsPea\n"));
        assert!(!family.search("qsPea\n\n"));
        assert!(!family.search("qsPea_"));
        assert!(!family.search(" qsPea"));
        let forbidden =
            Pattern::compile("(before|after|noentry|noexit|nonjoining|ss[0-9])").expect("compiles");
        assert!(forbidden.search("half-after"));
        assert!(forbidden.search("xss1"));
        assert!(!forbidden.search("half"));
        let inked = Pattern::compile("\\S").expect("compiles");
        assert!(!inked.search(" \t\u{3000}\u{85}"));
        assert!(inked.search(" x"));
        let slot = Pattern::compile("^policy\\.(refuse|prefer)\\[[0-9]+\\]$").expect("compiles");
        assert!(slot.search("policy.prefer[12]"));
        assert!(!slot.search("policyxprefer[12]"));
        assert!(!slot.search("policy.prefer[]"));
        let counted = Pattern::compile("^ss[0-9]{2}$").expect("compiles");
        assert!(counted.search("ss01"));
        assert!(!counted.search("ss1"));
        assert!(
            Pattern::compile("[a-z][a-z0-9-]*")
                .expect("compiles")
                .search("-a")
        );
        assert!(Pattern::compile("(a*)*b").expect("compiles").search("aab"));
    }

    #[test]
    fn patterns_outside_the_syntax_are_refused() {
        for source in [
            "\\w", "(?=a)", "a**", "*a", "^*", "[z-a]", "(a", "a)", "a{2,1}", "a*+", "\\1",
        ] {
            assert!(Pattern::compile(source).is_err(), "{source}");
        }
    }
}
