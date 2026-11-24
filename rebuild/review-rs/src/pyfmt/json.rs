//! CPython's `json.dumps` and `json.loads` over [`PyValue`].
//!
//! The writers cover every form the build writes: `json.dumps(value)` ([`dumps`]), with `sort_keys=True` ([`dumps_sorted`], the carry projection whose sha256 is a unit's content key, and `manifest_sha256`), with `indent=1` ([`dumps_indent`], `manifest.json` and the row-delta heads), with `ensure_ascii=False` ([`dumps_unicode`], the index and app lines), with `separators=(",", ":")` ([`dumps_compact`], `review-facts.json`), and a fragment's shard bytes, `json.dumps([fragment], indent=1, ensure_ascii=True)[1:-2]` ([`fragment_text`]). A tuple is written as a list, as `json` writes one. A dict key must be a `str`: one `json` would stringify (an int, a bool, None) is refused, as are bytes, a set and a record, which `json` refuses; there is no float to write.
//!
//! The reader ([`loads`] for a `str`, [`loads_bytes`] for `bytes`, and [`raw_decode`] for one value inside a `str`, as a shard part's walk reads its elements) reads what `json.loads` reads into the value it gives: an object as a dict whose repeated key keeps its first place and its last value, an array as a list, and an int as an int. It refuses what `json.loads` refuses, and what `json.loads` reads but the value model cannot hold: a float, `NaN` and `Infinity` among them, an int beyond 64 bits, a lone surrogate, escaped or in the bytes, and text `json.detect_encoding` takes for UTF-16 or UTF-32. Such a refusal names [`UNHELD`], so [`unheld`] tells it from one `json.loads` makes too: the reader reads on past a value it cannot hold and refuses it only when the rest of the text reads, so a text `json.loads` refuses anywhere is never taken for one it reads. [`Cursor`] walks a text's bytes forward without building a value, for a caller that reads a few fields of a document and leaves the rest to the reader.
//!
//! The writers write into a [`JsonSink`]: a `String`, or a digest that hashes the text as it is written. [`write_fragment_text`] appends a fragment's shard bytes to a buffer the caller keeps, and [`write_sorted_dict`] writes [`dumps_sorted`]'s text for a dict given as borrowed entries.
//!
//! The writers write into a [`JsonSink`]: a `String`, or a digest that hashes the text as it is written. [`write_fragment_text`] appends a fragment's shard bytes to a buffer the caller keeps, and [`write_sorted_dict`] writes [`dumps_sorted`]'s text for a dict given as borrowed entries.

use super::{
    DECIMAL_WIDTH, INT_MAX_STR_DIGITS, LOWER_HEX_DIGITS, PyFmtError, PyValue, decimal,
    int_digits_exceeded,
};

/// `json.dumps(value)`: `", "` between items, `": "` after a key, ASCII, every dict in its insertion order.
pub fn dumps(value: &PyValue) -> Result<String, PyFmtError> {
    Encoder::INLINE.dumps(value)
}

/// `json.dumps(value, sort_keys=True)`: `", "` between items, `": "` after a key, and every dict's keys sorted by code point.
pub fn dumps_sorted(value: &PyValue) -> Result<String, PyFmtError> {
    Encoder {
        sort_keys: true,
        ..Encoder::INLINE
    }
    .dumps(value)
}

/// `json.dumps(value, indent=1)`: each item on its own line, indented one space a level, `","` at a line's end and `": "` after a key, ASCII, every dict in its insertion order.
pub fn dumps_indent(value: &PyValue) -> Result<String, PyFmtError> {
    Encoder::INDENTED.dumps(value)
}

/// `json.dumps(value, ensure_ascii=False)`: [`dumps`]' separators and order, with every string written as [`write_unicode_string`] writes it.
pub fn dumps_unicode(value: &PyValue) -> Result<String, PyFmtError> {
    Encoder {
        ensure_ascii: false,
        ..Encoder::INLINE
    }
    .dumps(value)
}

/// `json.dumps(value, separators=(",", ":"))`: nothing between the items and a key's colon, ASCII, every dict in its insertion order.
pub fn dumps_compact(value: &PyValue) -> Result<String, PyFmtError> {
    Encoder {
        item_separator: ",",
        key_separator: ":",
        ..Encoder::INLINE
    }
    .dumps(value)
}

/// `json.dumps([value], indent=1, ensure_ascii=True)[1:-2]`, a fragment's shard bytes: the value one level deep in a one-element list, with the list's brackets and the newline before its `]` cut away, so the text starts with a newline and one space. Dict keys keep their insertion order.
pub fn fragment_text(value: &PyValue) -> Result<String, PyFmtError> {
    let mut out = String::new();
    write_fragment_text(&mut out, value)?;
    Ok(out)
}

/// Append [`fragment_text`]'s text for `value` to `out`. On a refusal `out` holds what was written before the refused value.
pub fn write_fragment_text(out: &mut String, value: &PyValue) -> Result<(), PyFmtError> {
    let encoder = Encoder::INDENTED;
    encoder.newline(out, 1);
    encoder.write(out, value, 1)
}

/// Write [`dumps_sorted`]'s text for a dict of `entries` (a dict holding those entries in that order) to `out`, with the same refusals.
pub fn write_sorted_dict<W: JsonSink>(
    out: &mut W,
    entries: &[&(PyValue, PyValue)],
) -> Result<(), PyFmtError> {
    let encoder = Encoder {
        sort_keys: true,
        ..Encoder::INLINE
    };
    if entries.is_empty() {
        out.push_text("{}");
        return Ok(());
    }
    let mut keyed = Vec::with_capacity(entries.len());
    for (key, item) in entries.iter().copied() {
        keyed.push((dict_key(key)?, item));
    }
    keyed.sort_by(|left, right| left.0.cmp(right.0));
    encoder.write_entries(out, &keyed, 0)
}

/// Where a JSON writer puts its text: a `String`, or a digest that hashes the bytes as they come.
pub trait JsonSink {
    /// Append `text`.
    fn push_text(&mut self, text: &str);
    /// Append one ASCII byte.
    fn push_ascii(&mut self, byte: u8);
}

impl JsonSink for String {
    fn push_text(&mut self, text: &str) {
        self.push_str(text);
    }

    fn push_ascii(&mut self, byte: u8) {
        self.push(char::from(byte));
    }
}

/// The escape `json`'s ASCII encoder writes for an ASCII byte, or None for a byte it writes as itself: the two-character escapes, and `\u00xx` for every other C0 control and DEL.
fn ascii_escape(byte: u8) -> Option<&'static str> {
    const CONTROLS: [&str; 32] = [
        "\\u0000", "\\u0001", "\\u0002", "\\u0003", "\\u0004", "\\u0005", "\\u0006", "\\u0007",
        "\\b", "\\t", "\\n", "\\u000b", "\\f", "\\r", "\\u000e", "\\u000f", "\\u0010", "\\u0011",
        "\\u0012", "\\u0013", "\\u0014", "\\u0015", "\\u0016", "\\u0017", "\\u0018", "\\u0019",
        "\\u001a", "\\u001b", "\\u001c", "\\u001d", "\\u001e", "\\u001f",
    ];
    match byte {
        b'"' => Some("\\\""),
        b'\\' => Some("\\\\"),
        0x7f => Some("\\u007f"),
        0..=0x1f => Some(CONTROLS[usize::from(byte)]),
        _ => None,
    }
}

/// Append `\u` and one UTF-16 code unit as four lowercase hex digits, as `format!("\\u{unit:04x}")` writes it.
fn push_unit_escape<W: JsonSink>(out: &mut W, unit: u16) {
    let mut escape = *b"\\u0000";
    for (slot, shift) in escape[2..].iter_mut().zip([12, 8, 4, 0]) {
        *slot = LOWER_HEX_DIGITS[usize::from((unit >> shift) & 0xf)];
    }
    out.push_text(std::str::from_utf8(&escape).expect("an escape is ASCII"));
}

/// [`write_string`] into any [`JsonSink`]: each run of printable ASCII other than `"` and `\` is appended at once.
fn push_ascii_string<W: JsonSink>(out: &mut W, text: &str) {
    out.push_ascii(b'"');
    let bytes = text.as_bytes();
    let mut run = 0;
    let mut at = 0;
    while at < bytes.len() {
        let byte = bytes[at];
        if (b' '..=b'~').contains(&byte) && byte != b'"' && byte != b'\\' {
            at += 1;
            continue;
        }
        out.push_text(&text[run..at]);
        if let Some(escape) = ascii_escape(byte) {
            out.push_text(escape);
            at += 1;
        } else {
            let letter = text[at..].chars().next().expect("a character starts here");
            let mut units = [0u16; 2];
            for &unit in letter.encode_utf16(&mut units).iter() {
                push_unit_escape(out, unit);
            }
            at += letter.len_utf8();
        }
        run = at;
    }
    out.push_text(&text[run..]);
    out.push_ascii(b'"');
}

/// [`write_unicode_string`] into any [`JsonSink`]: each run of characters written as themselves is appended at once.
fn push_unicode_string<W: JsonSink>(out: &mut W, text: &str) {
    out.push_ascii(b'"');
    let bytes = text.as_bytes();
    let mut run = 0;
    for (at, &byte) in bytes.iter().enumerate() {
        if byte >= 0x20 && byte != b'"' && byte != b'\\' {
            continue;
        }
        out.push_text(&text[run..at]);
        out.push_text(ascii_escape(byte).expect("a quote, a backslash or a C0 control"));
        run = at + 1;
    }
    out.push_text(&text[run..]);
    out.push_ascii(b'"');
}

/// Append one JSON string as `json`'s ASCII encoder writes it: `\"`, `\\`, `\n`, `\r`, `\t`, `\b` and `\f`; the printable ASCII characters as themselves; and every other character as `\uXXXX` in lowercase hex, beyond the BMP as a surrogate pair.
pub fn write_string(out: &mut String, text: &str) {
    push_ascii_string(out, text);
}

/// Append one JSON string as `json` writes it with `ensure_ascii=False`: `\"`, `\\`, `\n`, `\r`, `\t`, `\b` and `\f`, every other C0 control as `\u00xx` in lowercase hex, and every other character, DEL and the non-ASCII ones among them, as itself.
pub fn write_unicode_string(out: &mut String, text: &str) {
    push_unicode_string(out, text);
}

/// A dict key as the writers take it, refusing a key that is not a `str` as `json` does.
fn dict_key(key: &PyValue) -> Result<&str, PyFmtError> {
    match key {
        PyValue::Str(text) => Ok(text.as_str()),
        other => Err(PyFmtError(format!(
            "a JSON object key must be a str, not {}",
            other.kind()
        ))),
    }
}

/// The most entries a dict's sorted keys are held for on the stack while it is written.
const STACK_ENTRIES: usize = 16;

/// The spaces a newline's indent is written from, up to their length at a time.
const SPACES: &str = "                                ";

struct Encoder {
    indent: Option<usize>,
    sort_keys: bool,
    ensure_ascii: bool,
    item_separator: &'static str,
    key_separator: &'static str,
}

impl Encoder {
    const INLINE: Self = Self {
        indent: None,
        sort_keys: false,
        ensure_ascii: true,
        item_separator: ", ",
        key_separator: ": ",
    };

    const INDENTED: Self = Self {
        indent: Some(1),
        item_separator: ",",
        ..Self::INLINE
    };

    fn dumps(&self, value: &PyValue) -> Result<String, PyFmtError> {
        let mut out = String::new();
        self.write(&mut out, value, 0)?;
        Ok(out)
    }

    fn newline<W: JsonSink>(&self, out: &mut W, level: usize) {
        if let Some(indent) = self.indent {
            out.push_ascii(b'\n');
            let mut width = indent * level;
            while width > 0 {
                let piece = width.min(SPACES.len());
                out.push_text(&SPACES[..piece]);
                width -= piece;
            }
        }
    }

    fn string<W: JsonSink>(&self, out: &mut W, text: &str) {
        if self.ensure_ascii {
            push_ascii_string(out, text);
        } else {
            push_unicode_string(out, text);
        }
    }

    fn write<W: JsonSink>(
        &self,
        out: &mut W,
        value: &PyValue,
        level: usize,
    ) -> Result<(), PyFmtError> {
        match value {
            PyValue::None => out.push_text("null"),
            PyValue::Bool(true) => out.push_text("true"),
            PyValue::Bool(false) => out.push_text("false"),
            PyValue::Int(number) => out.push_text(decimal(*number, &mut [0; DECIMAL_WIDTH])),
            PyValue::Str(text) => self.string(out, text),
            PyValue::Tuple(items) | PyValue::List(items) => {
                if items.is_empty() {
                    out.push_text("[]");
                    return Ok(());
                }
                out.push_ascii(b'[');
                for (index, item) in items.iter().enumerate() {
                    if index > 0 {
                        out.push_text(self.item_separator);
                    }
                    self.newline(out, level + 1);
                    self.write(out, item, level + 1)?;
                }
                self.newline(out, level);
                out.push_ascii(b']');
            }
            PyValue::Dict(entries) => {
                if entries.is_empty() {
                    out.push_text("{}");
                    return Ok(());
                }
                if !self.sort_keys {
                    for (key, _) in entries {
                        dict_key(key)?;
                    }
                    out.push_ascii(b'{');
                    for (index, (key, item)) in entries.iter().enumerate() {
                        self.write_entry(out, index, dict_key(key)?, item, level)?;
                    }
                    self.newline(out, level);
                    out.push_ascii(b'}');
                } else if entries.len() <= STACK_ENTRIES {
                    let mut keyed: [(&str, &PyValue); STACK_ENTRIES] =
                        [("", &PyValue::None); STACK_ENTRIES];
                    for (slot, (key, item)) in keyed.iter_mut().zip(entries) {
                        *slot = (dict_key(key)?, item);
                    }
                    let keyed = &mut keyed[..entries.len()];
                    keyed.sort_by(|left, right| left.0.cmp(right.0));
                    self.write_entries(out, keyed, level)?;
                } else {
                    let mut keyed = Vec::with_capacity(entries.len());
                    for (key, item) in entries {
                        keyed.push((dict_key(key)?, item));
                    }
                    keyed.sort_by(|left, right| left.0.cmp(right.0));
                    self.write_entries(out, &keyed, level)?;
                }
            }
            PyValue::Bytes(_) | PyValue::Set(_) | PyValue::Record(..) => {
                return Err(PyFmtError(format!(
                    "{} is not JSON serializable",
                    value.kind()
                )));
            }
        }
        Ok(())
    }

    /// A nonempty dict's entries, keyed by their `str` keys in the order given, inside its braces.
    fn write_entries<W: JsonSink>(
        &self,
        out: &mut W,
        keyed: &[(&str, &PyValue)],
        level: usize,
    ) -> Result<(), PyFmtError> {
        out.push_ascii(b'{');
        for (index, &(key, item)) in keyed.iter().enumerate() {
            self.write_entry(out, index, key, item, level)?;
        }
        self.newline(out, level);
        out.push_ascii(b'}');
        Ok(())
    }

    /// The `index`th entry of a dict at `level`, after the separator that comes before every entry but the first.
    fn write_entry<W: JsonSink>(
        &self,
        out: &mut W,
        index: usize,
        key: &str,
        item: &PyValue,
        level: usize,
    ) -> Result<(), PyFmtError> {
        if index > 0 {
            out.push_text(self.item_separator);
        }
        self.newline(out, level + 1);
        self.string(out, key);
        out.push_text(self.key_separator);
        self.write(out, item, level + 1)
    }
}

/// What a refusal of [`loads`] or [`loads_bytes`] says when `json.loads` reads the text into a value the model cannot hold.
pub const UNHELD: &str = "which the reader does not hold";

/// Whether a refusal of [`loads`] or [`loads_bytes`] is of a text `json.loads` reads, into a value the model cannot hold or from UTF-16 or UTF-32, rather than one Python refuses too.
pub fn unheld(failure: &PyFmtError) -> bool {
    failure.0.contains(UNHELD)
}

/// `json.loads(text)` for a `str`: the one value `text` holds, with only JSON's whitespace around it. A `str` that starts with a byte order mark is refused, as `json.loads` refuses one.
pub fn loads(text: &str) -> Result<PyValue, PyFmtError> {
    if text.starts_with('\u{feff}') {
        return Err(PyFmtError(
            "Unexpected UTF-8 BOM (decode using utf-8-sig): line 1 column 1 (char 0)".into(),
        ));
    }
    Reader::new(text).document()
}

/// `json.loads(data)` for `bytes`: the text decoded as `json.loads` decodes it, then read as [`loads`] reads a `str`. A UTF-8 byte order mark is dropped. Text `json.detect_encoding` takes for UTF-16 or UTF-32, and a lone surrogate `surrogatepass` lets through, are refused as [`UNHELD`] where Python reads the text, and as Python refuses them where it does not.
pub fn loads_bytes(data: &[u8]) -> Result<PyValue, PyFmtError> {
    let encoding = detect_encoding(data);
    let (text, surrogate) = decode(data, encoding)?;
    let mut reader = Reader::new(&text);
    if encoding != "utf-8" {
        reader.unheld = Some(format!("the JSON text is {encoding}, {UNHELD}"));
    } else if surrogate {
        reader.unheld = Some(format!("the JSON text holds a lone surrogate, {UNHELD}"));
    }
    reader.document()
}

/// `json.JSONDecoder().raw_decode(text, at)`: the one value that starts at byte `at` of `text`, with no whitespace skipped before it, and the byte it ends at; what follows it is not read.
pub fn raw_decode(text: &str, at: usize) -> Result<(PyValue, usize), PyFmtError> {
    let mut reader = Reader::new(text);
    reader.at = at;
    let value = reader.value()?;
    match reader.unheld.take() {
        Some(unheld) => Err(PyFmtError(unheld)),
        None => Ok((value, reader.at)),
    }
}

/// `data.decode(encoding, "surrogatepass")` for the encoding `json.detect_encoding` names, with U+FFFD standing in for each lone surrogate `surrogatepass` lets through, and whether there was one. Bytes the codec refuses are refused, as Python refuses them.
fn decode(data: &[u8], encoding: &str) -> Result<(String, bool), PyFmtError> {
    let refuse =
        |encoding: &str| PyFmtError(format!("the JSON text does not decode as {encoding}"));
    let mut text = String::with_capacity(data.len());
    let mut surrogate = false;
    let mut point = |unit: u32, text: &mut String| match char::from_u32(unit) {
        Some(letter) => text.push(letter),
        None => {
            surrogate = true;
            text.push(char::REPLACEMENT_CHARACTER);
        }
    };
    match encoding {
        "utf-8" => {
            let mut rest = data.strip_prefix(b"\xef\xbb\xbf").unwrap_or(data);
            loop {
                match std::str::from_utf8(rest) {
                    Ok(valid) => {
                        text.push_str(valid);
                        break;
                    }
                    Err(failure) => {
                        let at = failure.valid_up_to();
                        text.push_str(std::str::from_utf8(&rest[..at]).expect("valid up to"));
                        match rest[at..] {
                            [0xed, second @ 0xa0..=0xbf, third @ 0x80..=0xbf, ..] => {
                                let unit = 0xd000
                                    | (u32::from(second & 0x3f) << 6)
                                    | u32::from(third & 0x3f);
                                point(unit, &mut text);
                                rest = &rest[at + 3..];
                            }
                            _ => return Err(refuse(encoding)),
                        }
                    }
                }
            }
        }
        "utf-16" | "utf-16-le" | "utf-16-be" => {
            let (body, big) = match encoding {
                "utf-16" => (&data[2..], data.starts_with(b"\xfe\xff")),
                other => (data, other == "utf-16-be"),
            };
            if body.len() % 2 != 0 {
                return Err(refuse(encoding));
            }
            let units: Vec<u16> = body
                .chunks(2)
                .map(|pair| {
                    if big {
                        u16::from_be_bytes([pair[0], pair[1]])
                    } else {
                        u16::from_le_bytes([pair[0], pair[1]])
                    }
                })
                .collect();
            for unit in char::decode_utf16(units) {
                match unit {
                    Ok(letter) => text.push(letter),
                    Err(lone) => point(u32::from(lone.unpaired_surrogate()), &mut text),
                }
            }
        }
        _ => {
            let (body, big) = match encoding {
                "utf-32" => (&data[4..], data.starts_with(b"\0\0\xfe\xff")),
                other => (data, other == "utf-32-be"),
            };
            if body.len() % 4 != 0 {
                return Err(refuse(encoding));
            }
            for word in body.chunks(4) {
                let word = [word[0], word[1], word[2], word[3]];
                let unit = if big {
                    u32::from_be_bytes(word)
                } else {
                    u32::from_le_bytes(word)
                };
                if unit > 0x10_ffff {
                    return Err(refuse(encoding));
                }
                point(unit, &mut text);
            }
        }
    }
    Ok((text, surrogate))
}

/// `json.detect_encoding`, with `utf-8-sig` reported as `utf-8`.
fn detect_encoding(data: &[u8]) -> &'static str {
    if data.starts_with(b"\0\0\xfe\xff") || data.starts_with(b"\xff\xfe\0\0") {
        return "utf-32";
    }
    if data.starts_with(b"\xfe\xff") || data.starts_with(b"\xff\xfe") {
        return "utf-16";
    }
    if data.starts_with(b"\xef\xbb\xbf") {
        return "utf-8";
    }
    match *data {
        [0, second, _, _, ..] => {
            if second != 0 {
                "utf-16-be"
            } else {
                "utf-32-be"
            }
        }
        [_, 0, third, fourth, ..] => {
            if third != 0 || fourth != 0 {
                "utf-16-le"
            } else {
                "utf-32-le"
            }
        }
        [0, _] => "utf-16-be",
        [_, 0] => "utf-16-le",
        _ => "utf-8",
    }
}

/// A scan of one JSON text as `json`'s C scanner walks it, holding the first value it read but cannot hold.
struct Reader<'a> {
    text: &'a str,
    bytes: &'a [u8],
    at: usize,
    unheld: Option<String>,
}

impl<'a> Reader<'a> {
    fn new(text: &'a str) -> Self {
        Self {
            text,
            bytes: text.as_bytes(),
            at: 0,
            unheld: None,
        }
    }

    /// Note a value `json.loads` reads that the model cannot hold, and stand None in for it.
    fn unheld(&mut self, what: &str, start: usize) -> PyValue {
        if self.unheld.is_none() {
            self.unheld = Some(format!("{what}, {UNHELD} (byte {start})"));
        }
        PyValue::None
    }

    fn fail(&self, message: &str) -> PyFmtError {
        PyFmtError(format!("{message} (byte {})", self.at))
    }

    fn peek(&self) -> Option<u8> {
        self.bytes.get(self.at).copied()
    }

    fn whitespace(&mut self) {
        while let Some(b' ' | b'\t' | b'\n' | b'\r') = self.peek() {
            self.at += 1;
        }
    }

    fn document(mut self) -> Result<PyValue, PyFmtError> {
        self.whitespace();
        let value = self.value()?;
        self.whitespace();
        if self.at != self.bytes.len() {
            return Err(self.fail("Extra data"));
        }
        match self.unheld.take() {
            Some(unheld) => Err(PyFmtError(unheld)),
            None => Ok(value),
        }
    }

    fn literal(&mut self, word: &str, value: PyValue) -> Result<PyValue, PyFmtError> {
        if self.bytes[self.at..].starts_with(word.as_bytes()) {
            self.at += word.len();
            Ok(value)
        } else {
            Err(self.fail("Expecting value"))
        }
    }

    fn value(&mut self) -> Result<PyValue, PyFmtError> {
        match self.peek() {
            Some(b'"') => {
                self.at += 1;
                self.string().map(PyValue::Str)
            }
            Some(b'{') => {
                self.at += 1;
                self.object()
            }
            Some(b'[') => {
                self.at += 1;
                self.array()
            }
            Some(b'n') => self.literal("null", PyValue::None),
            Some(b't') => self.literal("true", PyValue::Bool(true)),
            Some(b'f') => self.literal("false", PyValue::Bool(false)),
            Some(b'-' | b'0'..=b'9') => self.number(),
            Some(b'N') => self.constant("NaN"),
            Some(b'I') => self.constant("Infinity"),
            _ => Err(self.fail("Expecting value")),
        }
    }

    /// `NaN`, `Infinity` or `-Infinity` at the scan, a float `json.loads` reads.
    fn constant(&mut self, word: &str) -> Result<PyValue, PyFmtError> {
        let start = self.at;
        if !self.bytes[self.at..].starts_with(word.as_bytes()) {
            return Err(self.fail("Expecting value"));
        }
        self.at += word.len();
        Ok(self.unheld(&format!("{word} is a float"), start))
    }

    fn digits(&mut self) -> usize {
        let start = self.at;
        while let Some(b'0'..=b'9') = self.peek() {
            self.at += 1;
        }
        self.at - start
    }

    /// A number as `json`'s `NUMBER_RE` matches it: an int, then a fraction only where a digit follows the point and an exponent only where a digit follows its sign, so an int followed by anything else is the int, and what follows it is the caller's to refuse.
    fn number(&mut self) -> Result<PyValue, PyFmtError> {
        let start = self.at;
        if self.peek() == Some(b'-') {
            self.at += 1;
        }
        match self.peek() {
            Some(b'1'..=b'9') => {
                self.digits();
            }
            Some(b'0') => self.at += 1,
            Some(b'I') => {
                self.at = start;
                return self.constant("-Infinity");
            }
            _ => {
                self.at = start;
                return Err(self.fail("Expecting value"));
            }
        }
        let int_end = self.at;
        let mut float = false;
        if self.peek() == Some(b'.') {
            self.at += 1;
            if self.digits() > 0 {
                float = true;
            } else {
                self.at = int_end;
            }
        }
        let exponent = self.at;
        if let Some(b'e' | b'E') = self.peek() {
            self.at += 1;
            if let Some(b'+' | b'-') = self.peek() {
                self.at += 1;
            }
            if self.digits() > 0 {
                float = true;
            } else {
                self.at = exponent;
            }
        }
        if float {
            return Ok(self.unheld("a float", start));
        }
        let digits = &self.text[start..int_end];
        let count = digits.trim_start_matches('-').len();
        if count > INT_MAX_STR_DIGITS {
            return Err(PyFmtError(int_digits_exceeded(count)));
        }
        Ok(match digits.parse::<i64>() {
            Ok(number) => PyValue::Int(number),
            Err(_) => self.unheld(&format!("the int {digits} does not fit 64 bits"), start),
        })
    }

    fn string(&mut self) -> Result<String, PyFmtError> {
        let mut out = String::new();
        loop {
            let run = self.at;
            while let Some(byte) = self.peek() {
                if byte == b'"' || byte == b'\\' || byte < 0x20 {
                    break;
                }
                self.at += 1;
            }
            out.push_str(&self.text[run..self.at]);
            match self.peek() {
                None => return Err(self.fail("Unterminated string starting at")),
                Some(b'"') => {
                    self.at += 1;
                    return Ok(out);
                }
                Some(b'\\') => {
                    self.at += 1;
                    let escape = self
                        .peek()
                        .ok_or_else(|| self.fail("Unterminated string starting at"))?;
                    self.at += 1;
                    match escape {
                        b'"' => out.push('"'),
                        b'\\' => out.push('\\'),
                        b'/' => out.push('/'),
                        b'b' => out.push('\u{8}'),
                        b'f' => out.push('\u{c}'),
                        b'n' => out.push('\n'),
                        b'r' => out.push('\r'),
                        b't' => out.push('\t'),
                        b'u' => out.push(self.escaped_character()?),
                        _ => {
                            self.at -= 2;
                            return Err(self.fail("Invalid \\escape"));
                        }
                    }
                }
                Some(_) => return Err(self.fail("Invalid control character at")),
            }
        }
    }

    fn hex_unit(&mut self) -> Result<u32, PyFmtError> {
        let digits = self
            .bytes
            .get(self.at..self.at + 4)
            .filter(|digits| digits.iter().all(u8::is_ascii_hexdigit))
            .ok_or_else(|| self.fail("Invalid \\uXXXX escape"))?;
        let unit = digits.iter().fold(0, |unit, &digit| {
            (unit << 4) | char::from(digit).to_digit(16).unwrap_or(0)
        });
        self.at += 4;
        Ok(unit)
    }

    /// The character a `\u` escape names, read past the `u`: a high surrogate joins the low surrogate a second escape names, as `json` joins them, and a surrogate left alone, which `json.loads` reads, is noted as unheld, with U+FFFD standing in for it.
    fn escaped_character(&mut self) -> Result<char, PyFmtError> {
        let start = self.at - 2;
        let unit = self.hex_unit()?;
        if (0xd800..0xdc00).contains(&unit) && self.bytes[self.at..].starts_with(b"\\u") {
            let second = self.at;
            self.at += 2;
            let low = self.hex_unit()?;
            if (0xdc00..0xe000).contains(&low) {
                let point = 0x10000 + ((unit - 0xd800) << 10) + (low - 0xdc00);
                return Ok(char::from_u32(point).unwrap_or(char::REPLACEMENT_CHARACTER));
            }
            self.at = second;
        }
        Ok(char::from_u32(unit).unwrap_or_else(|| {
            self.unheld("a lone surrogate", start);
            char::REPLACEMENT_CHARACTER
        }))
    }

    fn array(&mut self) -> Result<PyValue, PyFmtError> {
        let mut items = Vec::new();
        self.whitespace();
        if self.peek() == Some(b']') {
            self.at += 1;
            return Ok(PyValue::List(items));
        }
        loop {
            items.push(self.value()?);
            self.whitespace();
            match self.peek() {
                Some(b']') => {
                    self.at += 1;
                    return Ok(PyValue::List(items));
                }
                Some(b',') => {
                    self.at += 1;
                    self.whitespace();
                    if self.peek() == Some(b']') {
                        return Err(self.fail("Illegal trailing comma before end of array"));
                    }
                }
                _ => return Err(self.fail("Expecting ',' delimiter")),
            }
        }
    }

    fn object(&mut self) -> Result<PyValue, PyFmtError> {
        let mut entries: Vec<(PyValue, PyValue)> = Vec::new();
        self.whitespace();
        if self.peek() == Some(b'}') {
            self.at += 1;
            return Ok(PyValue::Dict(entries));
        }
        loop {
            if self.peek() != Some(b'"') {
                return Err(self.fail("Expecting property name enclosed in double quotes"));
            }
            self.at += 1;
            let key = self.string()?;
            self.whitespace();
            if self.peek() != Some(b':') {
                return Err(self.fail("Expecting ':' delimiter"));
            }
            self.at += 1;
            self.whitespace();
            let item = self.value()?;
            match entries
                .iter_mut()
                .find(|(held, _)| matches!(held, PyValue::Str(text) if *text == key))
            {
                Some(entry) => entry.1 = item,
                None => entries.push((PyValue::Str(key), item)),
            }
            self.whitespace();
            match self.peek() {
                Some(b'}') => {
                    self.at += 1;
                    return Ok(PyValue::Dict(entries));
                }
                Some(b',') => {
                    self.at += 1;
                    self.whitespace();
                    if self.peek() == Some(b'}') {
                        return Err(self.fail("Illegal trailing comma before end of object"));
                    }
                }
                _ => return Err(self.fail("Expecting ',' delimiter")),
            }
        }
    }
}

/// The most digits an int [`Cursor::int`] reads has, the most an `i64` holds.
const CURSOR_INT_DIGITS: usize = 19;

/// Whether a JSON string runs on over the byte: anything but `"`, `\` and the C0 controls.
fn plain(byte: u8) -> bool {
    byte >= 0x20 && byte != b'"' && byte != b'\\'
}

/// The bytes of `word`, read little-endian, that a JSON string stops at (`"`, `\` and the C0 controls), each as its high bit: the lowest bit set is the first such byte, and none is set when there is none. A bit above the lowest can be set for a byte that is not one, from the borrow out of the byte below it.
fn stops_in(word: u64) -> u64 {
    const ONES: u64 = 0x0101_0101_0101_0101;
    const HIGHS: u64 = 0x8080_8080_8080_8080;
    let zeros = |word: u64| word.wrapping_sub(ONES) & !word & HIGHS;
    (word.wrapping_sub(0x20 * ONES) & !word & HIGHS)
        | zeros(word ^ (u64::from(b'"') * ONES))
        | zeros(word ^ (u64::from(b'\\') * ONES))
}

/// A forward read over one JSON text's bytes, as [`loads_bytes`] reads them, that allocates nothing of its own. Each step gives None where [`loads_bytes`] refuses the text or reads a value the model cannot hold, and also where the text takes a form the cursor does not read: an int followed by `.`, `e` or `E`, an int of more than nineteen digits, a `\u` escape of a surrogate, and a key with an escape where [`Cursor::key`] matches keys. A caller that gets None reads the text with [`loads_bytes`] instead, so a text the cursor reads to its end is one [`loads_bytes`] reads to the same values. It reads a `str`: a caller starts it only on bytes that are UTF-8 and whose first byte is `{`, which `json.detect_encoding` takes for UTF-8 with no byte order mark and [`loads_bytes`] decodes to the same text.
#[derive(Clone, Debug)]
pub struct Cursor<'a> {
    text: &'a str,
    bytes: &'a [u8],
    at: usize,
}

impl<'a> Cursor<'a> {
    /// A cursor at the start of `text`.
    pub fn new(text: &'a str) -> Self {
        Self {
            text,
            bytes: text.as_bytes(),
            at: 0,
        }
    }

    /// The byte at the cursor.
    pub fn peek(&self) -> Option<u8> {
        self.bytes.get(self.at).copied()
    }

    fn whitespace(&mut self) {
        while let Some(b' ' | b'\t' | b'\n' | b'\r') = self.peek() {
            self.at += 1;
        }
    }

    /// The end of the text, past JSON's whitespace, as `json.loads` requires after the one value.
    pub fn end(&mut self) -> Option<()> {
        self.whitespace();
        (self.at == self.bytes.len()).then_some(())
    }

    fn word(&mut self, word: &[u8]) -> Option<()> {
        self.bytes[self.at..].starts_with(word).then(|| {
            self.at += word.len();
        })
    }

    /// `null` at the cursor.
    pub fn null(&mut self) -> Option<()> {
        self.word(b"null")
    }

    /// `true` or `false` at the cursor.
    pub fn bool(&mut self) -> Option<bool> {
        match self.peek()? {
            b't' => self.word(b"true").map(|()| true),
            b'f' => self.word(b"false").map(|()| false),
            _ => None,
        }
    }

    /// An int at the cursor, as `json`'s `NUMBER_RE` reads one with no fraction or exponent.
    pub fn int(&mut self) -> Option<i64> {
        let negative = self.peek() == Some(b'-');
        if negative {
            self.at += 1;
        }
        let start = self.at;
        match self.peek()? {
            b'0' => self.at += 1,
            b'1'..=b'9' => {
                while let Some(b'0'..=b'9') = self.peek() {
                    self.at += 1;
                }
            }
            _ => return None,
        }
        let digits = &self.bytes[start..self.at];
        if digits.len() > CURSOR_INT_DIGITS || matches!(self.peek(), Some(b'.' | b'e' | b'E')) {
            return None;
        }
        let magnitude = digits
            .iter()
            .fold(0i128, |value, &digit| value * 10 + i128::from(digit - b'0'));
        i64::try_from(if negative { -magnitude } else { magnitude }).ok()
    }

    /// A string at the cursor, its text appended to `out` when there is one; whether it holds an escape.
    fn scan_string(&mut self, mut out: Option<&mut String>) -> Option<bool> {
        if self.peek()? != b'"' {
            return None;
        }
        self.at += 1;
        let mut escaped = false;
        loop {
            let run = self.at;
            while let Some(word) = self.bytes.get(self.at..self.at + 8) {
                let stops = stops_in(u64::from_le_bytes(word.try_into().expect("eight bytes")));
                if stops != 0 {
                    self.at += (stops.trailing_zeros() / 8) as usize;
                    break;
                }
                self.at += 8;
            }
            while self.peek().is_some_and(plain) {
                self.at += 1;
            }
            if let Some(out) = out.as_deref_mut() {
                out.push_str(&self.text[run..self.at]);
            }
            match self.peek()? {
                b'"' => {
                    self.at += 1;
                    return Some(escaped);
                }
                b'\\' => {
                    escaped = true;
                    let (letter, length) = match *self.bytes.get(self.at + 1)? {
                        b'"' => ('"', 2),
                        b'\\' => ('\\', 2),
                        b'/' => ('/', 2),
                        b'b' => ('\u{8}', 2),
                        b'f' => ('\u{c}', 2),
                        b'n' => ('\n', 2),
                        b'r' => ('\r', 2),
                        b't' => ('\t', 2),
                        b'u' => {
                            let digits = self.bytes.get(self.at + 2..self.at + 6)?;
                            let unit = digits.iter().try_fold(0u32, |unit, &digit| {
                                Some((unit << 4) | char::from(digit).to_digit(16)?)
                            })?;
                            (char::from_u32(unit)?, 6)
                        }
                        _ => return None,
                    };
                    self.at += length;
                    if let Some(out) = out.as_deref_mut() {
                        out.push(letter);
                    }
                }
                _ => return None,
            }
        }
    }

    /// A string at the cursor, its text appended to `out`.
    pub fn string(&mut self, out: &mut String) -> Option<()> {
        self.scan_string(Some(out)).map(drop)
    }

    /// Past the `{` at the cursor and the whitespace after it.
    pub fn open_object(&mut self) -> Option<()> {
        if self.peek()? != b'{' {
            return None;
        }
        self.at += 1;
        self.whitespace();
        Some(())
    }

    /// The next key of the object the cursor is in, past its `:` and the whitespace after it, its value next; None in the inner option past the object's `}`. `first` says the object was just opened, so no `,` comes before the key. With `plain`, a key with an escape is not read.
    fn entry(&mut self, first: bool, plain: bool) -> Option<Option<&'a str>> {
        if !first {
            self.whitespace();
            match self.peek()? {
                b'}' => {
                    self.at += 1;
                    return Some(None);
                }
                b',' => {
                    self.at += 1;
                    self.whitespace();
                }
                _ => return None,
            }
        } else if self.peek()? == b'}' {
            self.at += 1;
            return Some(None);
        }
        let start = self.at + 1;
        if self.scan_string(None)? && plain {
            return None;
        }
        let key = &self.text[start..self.at - 1];
        self.whitespace();
        if self.peek()? != b':' {
            return None;
        }
        self.at += 1;
        self.whitespace();
        Some(Some(key))
    }

    /// The next key of the object the cursor is in, as [`Cursor::entry`] reads it, a key with an escape not read.
    pub fn key(&mut self, first: bool) -> Option<Option<&'a str>> {
        self.entry(first, true)
    }

    /// Past the `[` at the cursor and the whitespace after it.
    pub fn open_array(&mut self) -> Option<()> {
        if self.peek()? != b'[' {
            return None;
        }
        self.at += 1;
        self.whitespace();
        Some(())
    }

    /// Whether another item of the array the cursor is in follows, the cursor at it; false past the array's `]`. `first` says the array was just opened, so no `,` comes before the item.
    pub fn item(&mut self, first: bool) -> Option<bool> {
        if !first {
            self.whitespace();
            match self.peek()? {
                b']' => {
                    self.at += 1;
                    Some(false)
                }
                b',' => {
                    self.at += 1;
                    self.whitespace();
                    (self.peek() != Some(b']')).then_some(true)
                }
                _ => None,
            }
        } else if self.peek() == Some(b']') {
            self.at += 1;
            Some(false)
        } else {
            Some(true)
        }
    }

    /// Past the value at the cursor, whatever it is.
    pub fn skip(&mut self) -> Option<()> {
        match self.peek()? {
            b'"' => self.scan_string(None).map(drop),
            b'{' => {
                self.open_object()?;
                let mut first = true;
                while self.entry(first, false)?.is_some() {
                    self.skip()?;
                    first = false;
                }
                Some(())
            }
            b'[' => {
                self.open_array()?;
                let mut first = true;
                while self.item(first)? {
                    self.skip()?;
                    first = false;
                }
                Some(())
            }
            b'n' => self.null(),
            b't' | b'f' => self.bool().map(drop),
            _ => self.int().map(drop),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn text(value: &str) -> PyValue {
        PyValue::str(value)
    }

    fn sample() -> PyValue {
        PyValue::Dict(vec![
            (text("\u{1f600}"), PyValue::Int(1)),
            (text("\u{ffff}"), PyValue::Tuple(vec![])),
            (
                text("b"),
                PyValue::List(vec![PyValue::None, PyValue::Dict(vec![])]),
            ),
            (text("a"), text("\u{e650}\"\u{1f600}\u{7f}/")),
        ])
    }

    #[test]
    fn sorted_keys_follow_code_points_and_strings_escape_as_ascii() {
        assert_eq!(
            dumps_sorted(&sample()).expect("json"),
            r#"{"a": "\ue650\"\ud83d\ude00\u007f/", "b": [null, {}], "\uffff": [], "\ud83d\ude00": 1}"#
        );
    }

    #[test]
    fn a_fragment_is_indented_one_space_a_level_inside_its_cut_away_list() {
        assert_eq!(
            fragment_text(&sample()).expect("json"),
            "\n {\n  \"\\ud83d\\ude00\": 1,\n  \"\\uffff\": [],\n  \"b\": [\n   null,\n   {}\n  ],\n  \"a\": \"\\ue650\\\"\\ud83d\\ude00\\u007f/\"\n }"
        );
        assert_eq!(
            fragment_text(&PyValue::Dict(vec![])).expect("json"),
            "\n {}"
        );
    }

    #[test]
    fn each_form_writes_its_separators_and_its_strings() {
        let value = PyValue::Dict(vec![
            (
                text("k\u{e9}"),
                PyValue::Tuple(vec![PyValue::Int(-1), text("\u{1}\u{7f}\u{1f600}")]),
            ),
            (text("e"), PyValue::Dict(vec![])),
        ]);
        assert_eq!(
            dumps(&value).expect("json"),
            r#"{"k\u00e9": [-1, "\u0001\u007f\ud83d\ude00"], "e": {}}"#
        );
        assert_eq!(
            dumps_compact(&value).expect("json"),
            r#"{"k\u00e9":[-1,"\u0001\u007f\ud83d\ude00"],"e":{}}"#
        );
        assert_eq!(
            dumps_unicode(&value).expect("json"),
            "{\"k\u{e9}\": [-1, \"\\u0001\u{7f}\u{1f600}\"], \"e\": {}}"
        );
        assert_eq!(
            dumps_indent(&value).expect("json"),
            "{\n \"k\\u00e9\": [\n  -1,\n  \"\\u0001\\u007f\\ud83d\\ude00\"\n ],\n \"e\": {}\n}"
        );
    }

    /// `json.dumps(text)`'s string as the writer escaped it before the table: one `format!` per escaped code unit.
    fn reference_ascii(text: &str) -> String {
        let mut out = String::from("\"");
        for letter in text.chars() {
            match letter {
                '"' => out.push_str("\\\""),
                '\\' => out.push_str("\\\\"),
                '\n' => out.push_str("\\n"),
                '\r' => out.push_str("\\r"),
                '\t' => out.push_str("\\t"),
                '\u{8}' => out.push_str("\\b"),
                '\u{c}' => out.push_str("\\f"),
                ' '..='~' => out.push(letter),
                _ => {
                    let mut units = [0u16; 2];
                    for unit in letter.encode_utf16(&mut units) {
                        out.push_str(&format!("\\u{unit:04x}"));
                    }
                }
            }
        }
        out.push('"');
        out
    }

    /// `json.dumps(text, ensure_ascii=False)`'s string as the writer escaped it before the table.
    fn reference_unicode(text: &str) -> String {
        let mut out = String::from("\"");
        for letter in text.chars() {
            match letter {
                '"' => out.push_str("\\\""),
                '\\' => out.push_str("\\\\"),
                '\n' => out.push_str("\\n"),
                '\r' => out.push_str("\\r"),
                '\t' => out.push_str("\\t"),
                '\u{8}' => out.push_str("\\b"),
                '\u{c}' => out.push_str("\\f"),
                '\0'..='\u{1f}' => out.push_str(&format!("\\u{:04x}", u32::from(letter))),
                _ => out.push(letter),
            }
        }
        out.push('"');
        out
    }

    #[test]
    fn every_escape_class_is_written_as_cpython_writes_it() {
        let mut every_ascii = String::new();
        for point in 0..0x80u8 {
            every_ascii.push(char::from(point));
        }
        let mut written = String::new();
        write_string(&mut written, &every_ascii);
        assert_eq!(written, reference_ascii(&every_ascii));
        assert!(written.contains(r#"\u0000\u0001"#) && written.contains(r#"\b\t\n\u000b\f\r"#));
        assert!(written.contains(r##"\u001f !\"#$"##) && written.contains(r#"[\\]"#));
        assert!(written.ends_with(r#"}~\u007f""#));
        for (text, ascii, unicode) in [
            ("", r#""""#, r#""""#),
            ("plain", r#""plain""#, r#""plain""#),
            ("\"\\", r#""\"\\""#, r#""\"\\""#),
            (
                "\u{1}\u{1f}\u{7f}",
                r#""\u0001\u001f\u007f""#,
                "\"\\u0001\\u001f\u{7f}\"",
            ),
            ("·Pea ·Tea", "\"\\u00b7Pea \\u00b7Tea\"", "\"·Pea ·Tea\""),
            (
                "\u{e650}\u{ffff}",
                "\"\\ue650\\uffff\"",
                "\"\u{e650}\u{ffff}\"",
            ),
            (
                "a\u{1f600}b\u{10ffff}",
                "\"a\\ud83d\\ude00b\\udbff\\udfff\"",
                "\"a\u{1f600}b\u{10ffff}\"",
            ),
            ("\u{2019}\n", "\"\\u2019\\n\"", "\"\u{2019}\\n\""),
        ] {
            let mut out = String::new();
            write_string(&mut out, text);
            assert_eq!(out, ascii, "{text:?}");
            assert_eq!(out, reference_ascii(text), "{text:?}");
            let mut out = String::new();
            write_unicode_string(&mut out, text);
            assert_eq!(out, unicode, "{text:?}");
            assert_eq!(out, reference_unicode(text), "{text:?}");
        }
        let mut every_unicode = every_ascii.clone();
        every_unicode.push_str("\u{80}\u{a0}\u{7ff}\u{800}\u{d7ff}\u{e000}\u{fffd}\u{10000}");
        let mut out = String::new();
        write_unicode_string(&mut out, &every_unicode);
        assert_eq!(out, reference_unicode(&every_unicode));
        let mut out = String::new();
        write_string(&mut out, &every_unicode);
        assert_eq!(out, reference_ascii(&every_unicode));
    }

    #[test]
    fn a_sorted_dict_written_from_borrowed_entries_is_dumps_sorted() {
        let PyValue::Dict(entries) = sample() else {
            panic!("a dict");
        };
        let borrowed: Vec<&(PyValue, PyValue)> = entries.iter().collect();
        let mut out = String::new();
        write_sorted_dict(&mut out, &borrowed).expect("json");
        assert_eq!(out, dumps_sorted(&sample()).expect("json"));
        let mut out = String::new();
        write_sorted_dict(&mut out, &[]).expect("json");
        assert_eq!(out, "{}");
        let wide = PyValue::Dict(
            (0..40)
                .rev()
                .map(|index| (text(&format!("k{index}")), PyValue::Int(index - 20)))
                .chain([(text("k3"), PyValue::Bool(true))])
                .collect(),
        );
        let narrow = PyValue::Dict(vec![(text("b"), wide.clone()), (text("a"), wide.clone())]);
        let expected = |value: &PyValue| {
            let PyValue::Dict(entries) = value else {
                panic!("a dict");
            };
            let mut keyed: Vec<(String, &PyValue)> = entries
                .iter()
                .map(|(key, item)| match key {
                    PyValue::Str(key) => (key.clone(), item),
                    _ => panic!("a str key"),
                })
                .collect();
            keyed.sort_by(|left, right| left.0.cmp(&right.0));
            keyed
                .iter()
                .map(|(key, item)| format!("\"{key}\": {}", dumps(item).expect("json")))
                .collect::<Vec<_>>()
                .join(", ")
        };
        assert_eq!(
            dumps_sorted(&wide).expect("json"),
            format!("{{{}}}", expected(&wide))
        );
        assert!(
            dumps_sorted(&narrow)
                .expect("json")
                .starts_with(&format!("{{\"a\": {{{}}}", expected(&wide)))
        );
        let mut indented = String::new();
        write_fragment_text(&mut indented, &narrow).expect("json");
        assert_eq!(indented, fragment_text(&narrow).expect("json"));
        let deep = (0..40).fold(PyValue::Int(1), |inner, _| PyValue::List(vec![inner]));
        let mut expected = "\n ".to_owned();
        for level in 1..=40 {
            expected.push_str(&format!("[\n{}", " ".repeat(level + 1)));
        }
        expected.push('1');
        for level in (1..=40).rev() {
            expected.push_str(&format!("\n{}]", " ".repeat(level)));
        }
        assert_eq!(fragment_text(&deep).expect("json"), expected);
    }

    #[test]
    fn a_non_str_key_and_a_set_are_refused() {
        assert!(dumps_sorted(&PyValue::Dict(vec![(PyValue::Int(1), PyValue::None)])).is_err());
        assert!(dumps(&PyValue::Dict(vec![(PyValue::None, PyValue::None)])).is_err());
        assert!(fragment_text(&PyValue::List(vec![PyValue::Set(vec![])])).is_err());
    }

    #[test]
    fn a_repeated_key_keeps_its_first_place_and_its_last_value() {
        assert_eq!(
            loads(r#" {"a": 1, "b": [true, null], "a": "\u00e9\ud83d\ude00"} "#).expect("read"),
            PyValue::Dict(vec![
                (text("a"), text("\u{e9}\u{1f600}")),
                (
                    text("b"),
                    PyValue::List(vec![PyValue::Bool(true), PyValue::None])
                ),
            ])
        );
        assert_eq!(loads("-0").expect("read"), PyValue::Int(0));
        assert_eq!(
            loads("-9223372036854775808").expect("read"),
            PyValue::Int(i64::MIN)
        );
    }

    #[test]
    fn what_python_reads_but_the_value_model_cannot_hold_is_refused() {
        for text in [
            "1.5",
            "1e5",
            "1E-5",
            "-0.0e+1",
            "[1.5, 2]",
            "NaN",
            "Infinity",
            "-Infinity",
            "9223372036854775808",
            r#""\ud800""#,
            r#""\udc00\ud800""#,
            r#""\ud800\u0041""#,
            r#"{"a": 1.5, "b": NaN}"#,
        ] {
            let refused = loads(text).expect_err(text);
            assert!(
                unheld(&refused),
                "{text:?} was refused as Python refuses it"
            );
        }
        for data in [
            b"\"\xed\xa0\x80\"".as_slice(),
            b"1\0",
            b"\xff\xfe1\0",
            b"\0\0\0\x31",
        ] {
            let refused = loads_bytes(data).expect_err("read");
            assert!(
                unheld(&refused),
                "{data:?} was refused as Python refuses it"
            );
        }
        assert_eq!(
            loads_bytes(b"\xef\xbb\xbf[]").expect("read"),
            PyValue::List(vec![])
        );
    }

    #[test]
    fn the_first_stop_in_a_word_is_its_lowest_flagged_byte() {
        for first in 0..8 {
            for stop in [b'"', b'\\', 0, 0x1f] {
                for other in [b' ', b'a', 0x7f, 0x80, 0xa0, 0xff, b'"', 0x01] {
                    let mut bytes = [b'x'; 8];
                    bytes[first] = stop;
                    for byte in &mut bytes[first + 1..] {
                        *byte = other;
                    }
                    let stops = stops_in(u64::from_le_bytes(bytes));
                    assert_eq!(stops.trailing_zeros() / 8, first as u32, "{bytes:?}");
                }
            }
        }
        for byte in (0x20..=0xffu8).filter(|&byte| byte != b'"' && byte != b'\\') {
            assert_eq!(stops_in(u64::from_le_bytes([byte; 8])), 0, "{byte}");
        }
    }

    /// Whether a cursor reads `data` as one value with only whitespace after it.
    fn cursor_reads(data: &[u8]) -> bool {
        let Ok(text) = std::str::from_utf8(data) else {
            return false;
        };
        let mut cursor = Cursor::new(text);
        cursor.skip().and_then(|()| cursor.end()).is_some()
    }

    #[test]
    fn a_cursor_reads_a_text_only_where_loads_reads_it_to_values_it_holds() {
        for text in [
            "{}",
            "[]",
            "-0",
            "-9223372036854775808",
            "\"\\u00e9\\/\\\"\u{e9}\"",
            "{\"a\": [1, -2, true, false, null, \"x\\n\"], \"a\": {}, \"\\u0062\": [[]]}\n",
            "[ 1 ,\t2\r\n]",
        ] {
            assert!(loads_bytes(text.as_bytes()).is_ok(), "{text:?}");
            assert!(cursor_reads(text.as_bytes()), "{text:?}");
        }
        for text in [
            "1.5",
            "1e5",
            "NaN",
            "-Infinity",
            "9223372036854775808",
            r#""\ud800""#,
            r#""\ud83d\ude00""#,
            "",
            "[1,]",
            "{\"a\":1,}",
            "{a:1}",
            "01",
            "-",
            "[1 2]",
            "{} x",
            "\"\t\"",
            "\"\\x\"",
            "\"\\u12\"",
            "tru",
            "1.",
            "1e",
            "[1.]",
            " 1",
        ] {
            assert!(!cursor_reads(text.as_bytes()), "{text:?}");
        }
        for data in [b"\"\xff\"".as_slice(), b"\"\xed\xa0\x80\"", b"\"\xc3\""] {
            assert!(!cursor_reads(data), "{data:?}");
        }
        let mut cursor = Cursor::new(r#"{"k": "v\u00e9", "\u006b": 1}"#);
        let mut out = String::new();
        cursor.open_object().expect("an object");
        assert_eq!(cursor.key(true), Some(Some("k")));
        cursor.string(&mut out).expect("a string");
        assert_eq!(out, "v\u{e9}");
        assert_eq!(cursor.key(false), None);
    }

    #[test]
    fn what_python_refuses_is_refused() {
        for text in [
            "",
            "[1,]",
            "{\"a\":1,}",
            "{a:1}",
            "01",
            "-",
            "+1",
            ".5",
            "[1 2]",
            "{} x",
            "\"\t\"",
            "\"\\x\"",
            "\"\\u12\"",
            "tru",
            "\u{a0}1",
            "1\u{c}",
            "\u{feff}{}",
            "1.",
            "1.e5",
            "1e",
            "1e+",
            "[1.]",
            "Nan",
            "-Inf",
            "[1.5,]",
            "[NaN 1]",
            "1.5 x",
            r#""\ud800\u12""#,
        ] {
            let refused = loads(text).expect_err(text);
            assert!(
                !unheld(&refused),
                "{text:?} was refused as a value Python reads"
            );
        }
        for data in [
            b"\"\xff\"".as_slice(),
            b"\"\xed\xa0\"",
            b"\xff\xfe",
            b"\xff\xfe1",
            b"\xff\xfe[\0",
            b"\0\0\0\x31\0",
            b"\0\0\x11\0",
        ] {
            let refused = loads_bytes(data).expect_err("read");
            assert!(
                !unheld(&refused),
                "{data:?} was refused as a text Python reads"
            );
        }
        let long = "1".repeat(INT_MAX_STR_DIGITS + 1);
        assert_eq!(
            loads(&long[1..]).map_err(|failure| unheld(&failure)),
            Err(true)
        );
        for text in [format!("[1.5, -{long}]"), format!("[{long}, x]")] {
            assert_eq!(
                loads(&text),
                Err(PyFmtError(int_digits_exceeded(INT_MAX_STR_DIGITS + 1)))
            );
        }
    }
}
