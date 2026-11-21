//! CPython's `json.dumps` and `json.loads` over [`PyValue`].
//!
//! The writers cover every form the worker and the corpus build's parent write (`rebuild/CORPUS-PARENT-PORT-PLAN.md` § JSON forms): `json.dumps(value)` ([`dumps`]), with `sort_keys=True` ([`dumps_sorted`], the carry projection whose sha256 is a unit's content key, and `manifest_sha256`), with `indent=1` ([`dumps_indent`], `manifest.json` and the row-delta heads), with `ensure_ascii=False` ([`dumps_unicode`], the index and app lines), with `separators=(",", ":")` ([`dumps_compact`], `review-facts.json`), and a fragment's shard bytes, `json.dumps([fragment], indent=1, ensure_ascii=True)[1:-2]` ([`fragment_text`]). A tuple is written as a list, as `json` writes one. A dict key must be a `str`: one `json` would stringify (an int, a bool, None) is refused, as are bytes, a set and a record, which `json` refuses; there is no float to write.
//!
//! The reader ([`loads`] for a `str`, [`loads_bytes`] for `bytes`, and [`raw_decode`] for one value inside a `str`, as a shard part's walk reads its elements) reads what `json.loads` reads into the value it gives: an object as a dict whose repeated key keeps its first place and its last value, an array as a list, and an int as an int. It refuses what `json.loads` refuses, and what `json.loads` reads but the value model cannot hold or the plan's § Reading what Python wrote has the Rust build refuse: a float, `NaN` and `Infinity` among them, an int beyond 64 bits, a lone surrogate, escaped or in the bytes, and text `json.detect_encoding` takes for UTF-16 or UTF-32. Such a refusal names [`UNHELD`], so [`unheld`] tells it from one Python makes too: the reader reads on past a value it cannot hold and refuses it only when the rest of the text reads, so a text Python refuses anywhere is never taken for one it reads.

use super::{INT_MAX_STR_DIGITS, PyFmtError, PyValue, int_digits_exceeded};

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
    let encoder = Encoder::INDENTED;
    let mut out = String::new();
    encoder.newline(&mut out, 1);
    encoder.write(&mut out, value, 1)?;
    Ok(out)
}

/// Append one JSON string as `json`'s ASCII encoder writes it: `\"`, `\\`, `\n`, `\r`, `\t`, `\b` and `\f`; the printable ASCII characters as themselves; and every other character as `\uXXXX` in lowercase hex, beyond the BMP as a surrogate pair.
pub fn write_string(out: &mut String, text: &str) {
    out.push('"');
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
}

/// Append one JSON string as `json` writes it with `ensure_ascii=False`: `\"`, `\\`, `\n`, `\r`, `\t`, `\b` and `\f`, every other C0 control as `\u00xx` in lowercase hex, and every other character, DEL and the non-ASCII ones among them, as itself.
pub fn write_unicode_string(out: &mut String, text: &str) {
    out.push('"');
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
}

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

    fn newline(&self, out: &mut String, level: usize) {
        if let Some(indent) = self.indent {
            out.push('\n');
            out.extend(std::iter::repeat_n(' ', indent * level));
        }
    }

    fn string(&self, out: &mut String, text: &str) {
        if self.ensure_ascii {
            write_string(out, text);
        } else {
            write_unicode_string(out, text);
        }
    }

    fn write(&self, out: &mut String, value: &PyValue, level: usize) -> Result<(), PyFmtError> {
        match value {
            PyValue::None => out.push_str("null"),
            PyValue::Bool(true) => out.push_str("true"),
            PyValue::Bool(false) => out.push_str("false"),
            PyValue::Int(number) => out.push_str(&number.to_string()),
            PyValue::Str(text) => self.string(out, text),
            PyValue::Tuple(items) | PyValue::List(items) => {
                if items.is_empty() {
                    out.push_str("[]");
                    return Ok(());
                }
                out.push('[');
                for (index, item) in items.iter().enumerate() {
                    if index > 0 {
                        out.push_str(self.item_separator);
                    }
                    self.newline(out, level + 1);
                    self.write(out, item, level + 1)?;
                }
                self.newline(out, level);
                out.push(']');
            }
            PyValue::Dict(entries) => {
                if entries.is_empty() {
                    out.push_str("{}");
                    return Ok(());
                }
                let mut keyed = entries
                    .iter()
                    .map(|(key, item)| match key {
                        PyValue::Str(text) => Ok((text.as_str(), item)),
                        other => Err(PyFmtError(format!(
                            "a JSON object key must be a str, not {}",
                            other.kind()
                        ))),
                    })
                    .collect::<Result<Vec<_>, _>>()?;
                if self.sort_keys {
                    keyed.sort_by(|left, right| left.0.cmp(right.0));
                }
                out.push('{');
                for (index, (key, item)) in keyed.into_iter().enumerate() {
                    if index > 0 {
                        out.push_str(self.item_separator);
                    }
                    self.newline(out, level + 1);
                    self.string(out, key);
                    out.push_str(self.key_separator);
                    self.write(out, item, level + 1)?;
                }
                self.newline(out, level);
                out.push('}');
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
