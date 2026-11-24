//! The baseline subset pack `rebuild/review/subset_pack.py` writes, read through one read-only mapping (`ffi::Mapping`) every thread shares, refused when its header disagrees with what the init says to check, and each lookup a binary search of one configuration's keys that builds one [`SubsetRow`] from the record at that index.
//!
//! The layout is the packer's: an 8-byte little-endian header length, the header's sort-keys JSON, padding to 8 bytes, then each configuration's section at the header's offsets from there, a sorted array of 8-byte keys in the header's byte order and a fixed-width little-endian record array, `<BBB{g}H{c}B{j}H` (the three counts, then the glyph ids, the cluster starts and the junction ids, each padded to its slot count). The packer's code digest in the header, which the init's must equal, stands for that layout.

use std::fmt;
use std::path::PathBuf;

use serde_json::Value;

use crate::ffi::Mapping;
use crate::pyfmt::repr::repr_str;
use crate::pyfmt::{decimal_value, is_space};
use crate::wire::SubsetPackInit;

/// `KEY_SLOTS`: the longest window a key packs.
pub const KEY_SLOTS: usize = 4;
const SLOT_MAX: u64 = 0xFFFF;
const KEY_BYTES: usize = 8;
const ALIGN: usize = 8;
const LENGTH_BYTES: usize = 8;

/// A pack the engine refuses, or a lookup it cannot answer.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SubsetPackError(pub String);

impl fmt::Display for SubsetPackError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for SubsetPackError {}

type Result<T> = std::result::Result<T, SubsetPackError>;

fn error<T>(message: impl Into<String>) -> Result<T> {
    Err(SubsetPackError(message.into()))
}

/// `SubsetRow`: one window's baseline glyph names, cluster starts and junction tokens, the strings borrowed from the pack.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SubsetRow<'a> {
    pub glyphs: Vec<&'a str>,
    pub clusters: Vec<usize>,
    pub junctions: Vec<&'a str>,
}

/// Python's `int(part, 16)` over the ASCII text `_PyUnicode_TransformDecimalAndSpaceToASCII` makes of `part`: a non-ASCII space becomes a space and a decimal digit of any script its ASCII digit. The text is optional whitespace, an optional sign, an optional `0x` with one underscore allowed after it, hex digits with single underscores between them, and optional whitespace, where whitespace is the six ASCII characters `Py_ISSPACE` accepts. Returns the sign and the magnitude, which saturates past `u64`, or None for text `int` refuses.
pub(crate) fn python_hex_int(part: &str) -> Option<(bool, u64)> {
    let mut ascii = Vec::with_capacity(part.len());
    for letter in part.chars() {
        if u32::from(letter) < 127 {
            ascii.push(letter as u8);
        } else if is_space(letter) {
            ascii.push(b' ');
        } else {
            ascii.push(b'0' + u8::try_from(decimal_value(letter)?).ok()?);
        }
    }
    let ascii_space = |byte: &u8| matches!(byte, b' ' | b'\t' | b'\n' | b'\r' | 0x0b | 0x0c);
    let start = ascii.iter().position(|byte| !ascii_space(byte))?;
    let end = ascii.iter().rposition(|byte| !ascii_space(byte))? + 1;
    let (negative, digits) = match &ascii[start..end] {
        [b'-', rest @ ..] => (true, rest),
        [b'+', rest @ ..] => (false, rest),
        text => (false, text),
    };
    python_hex_digits(digits).map(|magnitude| (negative, magnitude))
}

fn python_hex_digits(mut text: &[u8]) -> Option<u64> {
    if let [b'0', b'x' | b'X', rest @ ..] = text {
        text = rest.strip_prefix(b"_").unwrap_or(rest);
    }
    if text.first() == Some(&b'_') || text.last() == Some(&b'_') || text.is_empty() {
        return None;
    }
    let mut magnitude: u64 = 0;
    let mut previous_underscore = false;
    for &byte in text {
        if byte == b'_' {
            if previous_underscore {
                return None;
            }
            previous_underscore = true;
            continue;
        }
        previous_underscore = false;
        let digit = char::from(byte).to_digit(16)?;
        magnitude = magnitude
            .saturating_mul(16)
            .saturating_add(u64::from(digit));
    }
    Some(magnitude)
}

/// `pack_key`: the pack's integer key for a colon-joined codepoint string, each part read as `int(part, 16)` reads it into a right-aligned 16-bit slot. A part `int` refuses, a codepoint outside the slot width, or more than [`KEY_SLOTS`] parts is refused with Python's message, the first failing part first.
pub fn pack_key(codepoints: &str) -> Result<u64> {
    let mut key: u64 = 0;
    let mut slots = 0usize;
    for part in codepoints.split(':') {
        let Some((negative, magnitude)) = python_hex_int(part) else {
            return error(format!(
                "invalid literal for int() with base 16: {}",
                repr_str(part)
            ));
        };
        if magnitude > SLOT_MAX || (negative && magnitude != 0) {
            return error(format!(
                "the codepoint {part} is outside the pack's {SLOT_MAX:04X} slot width: {codepoints}"
            ));
        }
        key = (key << 16) | magnitude;
        slots += 1;
    }
    if slots > KEY_SLOTS {
        return error(format!(
            "a window of {slots} codepoints outruns the pack's {KEY_SLOTS} key slots: {codepoints}"
        ));
    }
    Ok(key)
}

fn aligned(offset: usize) -> usize {
    offset.div_ceil(ALIGN) * ALIGN
}

/// One configuration's section: its row count and the file offsets of its keys and its records.
#[derive(Clone, Debug, PartialEq, Eq)]
struct Section {
    rows: usize,
    keys_at: usize,
    records_at: usize,
}

/// One mapped pack, shared read-only by every thread.
pub struct SubsetPack {
    path: PathBuf,
    mapping: Mapping,
    strings: Vec<String>,
    big_endian_keys: bool,
    glyph_slots: usize,
    cluster_slots: usize,
    record_size: usize,
    sections: Vec<(String, Section)>,
}

fn header_count(value: &Value, what: &str) -> Result<usize> {
    value
        .as_u64()
        .and_then(|count| usize::try_from(count).ok())
        .ok_or_else(|| SubsetPackError(format!("the pack header's {what} is not a count")))
}

/// The reason `_header_disagreement` gives for a header the init's values disagree with, or None.
fn header_disagreement(
    header: &serde_json::Map<String, Value>,
    init: &SubsetPackInit,
) -> Option<String> {
    if header.get("packer").and_then(Value::as_str) != Some(init.packer.as_str()) {
        return Some("written by other packer code".to_string());
    }
    let byteorder = header.get("byteorder");
    if byteorder.and_then(Value::as_str) != Some(init.byteorder.as_str()) {
        let shown = match byteorder {
            Some(Value::String(text)) => text.clone(),
            None | Some(Value::Null) => "None".to_string(),
            Some(other) => other.to_string(),
        };
        return Some(format!("written on a {shown}-endian host"));
    }
    let same_digests = match header.get("digests") {
        Some(Value::Object(recorded)) => {
            recorded.len() == init.digests.len()
                && init.digests.iter().all(|(config, digest)| {
                    recorded.get(config).and_then(Value::as_str) == Some(digest)
                })
        }
        _ => false,
    };
    if !same_digests {
        return Some("written over other tables".to_string());
    }
    None
}

impl SubsetPack {
    /// Map the pack the init names, refusing a file that is not a pack, a header whose format, packer digest, byte order or table digests differ from the init's, which are what Python checks, and a header whose sections fall outside the file.
    pub fn open(init: &SubsetPackInit) -> Result<Self> {
        let path = PathBuf::from(&init.path);
        let shown = path.display();
        let mapping = Mapping::open(&path).map_err(|failure| {
            SubsetPackError(format!("{shown}: cannot map the subset pack: {failure}"))
        })?;
        let bytes = mapping.bytes();
        let Some(prefix) = bytes.get(..LENGTH_BYTES) else {
            return error(format!("{shown}: not a subset pack"));
        };
        let length = u64::from_le_bytes(prefix.try_into().expect("eight bytes"));
        let Some(encoded) = usize::try_from(length)
            .ok()
            .and_then(|length| bytes.get(LENGTH_BYTES..LENGTH_BYTES.checked_add(length)?))
        else {
            return error(format!("{shown}: truncated subset pack header"));
        };
        let header: Value = serde_json::from_slice(encoded)
            .map_err(|_| SubsetPackError(format!("{shown}: unreadable subset pack header")))?;
        let Value::Object(header) = header else {
            return error(format!("{shown}: not a {} pack", init.format));
        };
        if header.get("format").and_then(Value::as_str) != Some(init.format.as_str()) {
            return error(format!("{shown}: not a {} pack", init.format));
        }
        if let Some(disagreement) = header_disagreement(&header, init) {
            return error(format!(
                "{shown}: the subset pack was {disagreement}; rewrite it over the tables it is read beside"
            ));
        }
        let base = aligned(LENGTH_BYTES + encoded.len());
        let field = |name: &str| {
            header
                .get(name)
                .ok_or_else(|| SubsetPackError(format!("{shown}: the pack header has no {name}")))
        };
        let strings = field("strings")?
            .as_array()
            .and_then(|strings| {
                strings
                    .iter()
                    .map(|text| text.as_str().map(str::to_string))
                    .collect::<Option<Vec<_>>>()
            })
            .ok_or_else(|| {
                SubsetPackError(format!("{shown}: the pack's strings are not a list of str"))
            })?;
        let big_endian_keys = match init.byteorder.as_str() {
            "little" => false,
            "big" => true,
            other => {
                return error(format!(
                    "{shown}: the byte order {other} is neither little nor big"
                ));
            }
        };
        let glyph_slots = header_count(field("glyph_slots")?, "glyph_slots")?;
        let cluster_slots = header_count(field("cluster_slots")?, "cluster_slots")?;
        let junction_slots = header_count(field("junction_slots")?, "junction_slots")?;
        let Some(record_size) = [
            glyph_slots,
            glyph_slots,
            cluster_slots,
            junction_slots,
            junction_slots,
        ]
        .into_iter()
        .try_fold(3usize, usize::checked_add) else {
            return error(format!("{shown}: the pack's slot counts outrun memory"));
        };
        let Value::Object(listed) = field("sections")? else {
            return error(format!(
                "{shown}: the pack's sections are not a JSON object"
            ));
        };
        let mut sections = Vec::with_capacity(listed.len());
        for (config, section) in listed {
            let count =
                |name: &str| header_count(&section[name], &format!("{config} section's {name}"));
            let (rows, keys, records) = (count("rows")?, count("keys")?, count("records")?);
            let span = |at: usize, width: usize| {
                let start = base.checked_add(at)?;
                let end = rows.checked_mul(width)?.checked_add(start)?;
                (rows == 0 || end <= bytes.len()).then_some(start)
            };
            let (Some(keys_at), Some(records_at)) =
                (span(keys, KEY_BYTES), span(records, record_size))
            else {
                return error(format!(
                    "{shown}: the {config} section runs past the end of the pack"
                ));
            };
            sections.push((
                config.clone(),
                Section {
                    rows,
                    keys_at,
                    records_at,
                },
            ));
        }
        Ok(Self {
            path,
            mapping,
            strings,
            big_endian_keys,
            glyph_slots,
            cluster_slots,
            record_size,
            sections,
        })
    }

    fn key_at(&self, section: &Section, index: usize) -> u64 {
        let at = section.keys_at + index * KEY_BYTES;
        let bytes: [u8; KEY_BYTES] = self.mapping.bytes()[at..at + KEY_BYTES]
            .try_into()
            .expect("eight bytes");
        if self.big_endian_keys {
            u64::from_be_bytes(bytes)
        } else {
            u64::from_le_bytes(bytes)
        }
    }

    /// The record at `index`, unpacked whole and sliced as Python slices its fields, each slice clamped to the record.
    fn materialize(&self, section: &Section, index: usize) -> Result<SubsetRow<'_>> {
        let at = section.records_at + index * self.record_size;
        let record = &self.mapping.bytes()[at..at + self.record_size];
        let wide = |bytes: &[u8]| {
            bytes
                .as_chunks::<2>()
                .0
                .iter()
                .map(|&pair| usize::from(u16::from_le_bytes(pair)))
                .collect::<Vec<_>>()
        };
        let glyphs_end = 3 + 2 * self.glyph_slots;
        let clusters_end = glyphs_end + self.cluster_slots;
        let mut fields: Vec<usize> = record[..3]
            .iter()
            .map(|&count| usize::from(count))
            .collect();
        fields.extend(wide(&record[3..glyphs_end]));
        fields.extend(
            record[glyphs_end..clusters_end]
                .iter()
                .map(|&cluster| usize::from(cluster)),
        );
        fields.extend(wide(&record[clusters_end..]));
        let slice = |from: usize, count: usize| {
            &fields[from.min(fields.len())..(from + count).min(fields.len())]
        };
        let glyphs_at = 3;
        let clusters_at = glyphs_at + self.glyph_slots;
        let junctions_at = clusters_at + self.cluster_slots;
        let names = |ids: &[usize]| {
            ids.iter()
                .map(|&id| {
                    self.strings.get(id).map(String::as_str).ok_or_else(|| {
                        SubsetPackError(format!(
                            "{}: a record names string {id} of {}",
                            self.path.display(),
                            self.strings.len()
                        ))
                    })
                })
                .collect::<Result<Vec<&str>>>()
        };
        Ok(SubsetRow {
            glyphs: names(slice(glyphs_at, fields[0]))?,
            clusters: slice(clusters_at, fields[1]).to_vec(),
            junctions: names(slice(junctions_at, fields[2]))?,
        })
    }

    /// The row for `codepoints` under `config`, or None when the pack has no such configuration or window, or when [`pack_key`] refuses the codepoints.
    pub fn row(&self, config: &str, codepoints: &str) -> Result<Option<SubsetRow<'_>>> {
        let Some((_, section)) = self.sections.iter().find(|(held, _)| held == config) else {
            return Ok(None);
        };
        let Ok(key) = pack_key(codepoints) else {
            return Ok(None);
        };
        let mut low = 0;
        let mut high = section.rows;
        while low < high {
            let middle = low + (high - low) / 2;
            if self.key_at(section, middle) < key {
                low = middle + 1;
            } else {
                high = middle;
            }
        }
        if low >= section.rows || self.key_at(section, low) != key {
            return Ok(None);
        }
        self.materialize(section, low).map(Some)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::path::Path;

    const PACKER: &str = "packer-digest";

    fn init(path: &Path) -> SubsetPackInit {
        SubsetPackInit {
            path: path.display().to_string(),
            format: "ams-subset-pack/1".to_string(),
            packer: PACKER.to_string(),
            byteorder: "little".to_string(),
            digests: vec![
                ("default".to_string(), "d0".to_string()),
                ("ss10".to_string(), "d1".to_string()),
            ],
        }
    }

    /// A pack laid out as `write_pack_in_memory` lays one out: two glyph slots, two cluster slots and one junction slot, `default` with two rows and `ss10` with none, over the strings `break`, `qsPea`, `qsTea` and `y6`.
    fn pack_bytes() -> Vec<u8> {
        let record_size = 3 + 2 * 2 + 2 + 2;
        let rows: [(u64, &[u8]); 2] = [
            (0xE650, &[1, 1, 0, 1, 0, 0, 0, 0, 0, 0, 0]),
            (0xE650_E652, &[2, 2, 1, 1, 0, 2, 0, 0, 1, 3, 0]),
        ];
        let keys_at = 0;
        let records_at = aligned(rows.len() * KEY_BYTES);
        let end = records_at + rows.len() * record_size;
        let header = json!({
            "byteorder": "little",
            "cluster_slots": 2,
            "configs": ["default", "ss10"],
            "digests": {"default": "d0", "ss10": "d1"},
            "format": "ams-subset-pack/1",
            "glyph_slots": 2,
            "junction_slots": 1,
            "packer": PACKER,
            "sections": {
                "default": {"keys": keys_at, "records": records_at, "rows": rows.len()},
                "ss10": {"keys": aligned(end), "records": aligned(end), "rows": 0},
            },
            "strings": ["break", "qsPea", "qsTea", "y6"],
        })
        .to_string();
        let base = aligned(LENGTH_BYTES + header.len());
        let mut bytes = (header.len() as u64).to_le_bytes().to_vec();
        bytes.extend(header.as_bytes());
        bytes.resize(base, 0);
        for (key, _) in rows {
            bytes.extend(key.to_le_bytes());
        }
        bytes.resize(base + records_at, 0);
        for (_, record) in rows {
            assert_eq!(record.len(), record_size);
            bytes.extend(record);
        }
        bytes
    }

    /// A file under the system's temporary directory, named for this process and the test, removed when dropped.
    struct Scratch(PathBuf);

    impl Scratch {
        fn new(name: &str, bytes: &[u8]) -> Self {
            let path = std::env::temp_dir().join(format!(
                "ams-review-subset-pack-{}-{name}",
                std::process::id()
            ));
            std::fs::write(&path, bytes).expect("the scratch pack is written");
            Self(path)
        }
    }

    impl Drop for Scratch {
        fn drop(&mut self) {
            let _ = std::fs::remove_file(&self.0);
        }
    }

    fn refusal(init: &SubsetPackInit) -> String {
        match SubsetPack::open(init) {
            Ok(_) => panic!("the pack is opened"),
            Err(SubsetPackError(message)) => message,
        }
    }

    #[test]
    fn a_key_packs_each_codepoint_into_a_right_aligned_slot_as_int_reads_it() {
        assert_eq!(pack_key("E650"), Ok(0xE650));
        assert_eq!(pack_key("0020:E650:E652"), Ok(0x0020_E650_E652));
        for spelling in [
            "e650",
            "0xE650",
            "0X_e650",
            "+E650",
            "E6_50",
            " \tE650\u{2003}\u{85}",
            "0000:E650",
            "E\u{666}\u{665}\u{660}",
            "E\u{ff16}\u{ff15}\u{ff10}",
        ] {
            assert_eq!(pack_key(spelling), Ok(0xE650), "{spelling:?}");
        }
        assert_eq!(pack_key("-0"), Ok(0));
    }

    #[test]
    fn a_key_refuses_what_int_refuses_a_codepoint_past_the_slot_and_a_fifth_slot() {
        for refused in [
            "",
            "+",
            "0x",
            "0x__1",
            "+-1",
            "_E650",
            "E650_",
            "E6__50",
            "E6 50",
            "\x1cE650",
            "E650\x00",
            "E650\x7f",
            "G650",
            ":E650",
            "E650:",
            "E650::E650",
        ] {
            assert_eq!(
                pack_key(refused),
                error(format!(
                    "invalid literal for int() with base 16: {}",
                    repr_str(
                        refused
                            .split(':')
                            .find(|part| python_hex_int(part).is_none())
                            .expect("a refused part")
                    )
                )),
                "{refused:?}"
            );
        }
        assert_eq!(
            pack_key("1E650"),
            error("the codepoint 1E650 is outside the pack's FFFF slot width: 1E650")
        );
        assert_eq!(
            pack_key("-1"),
            error("the codepoint -1 is outside the pack's FFFF slot width: -1")
        );
        assert!(pack_key("10000000000000000E650").is_err());
        assert_eq!(
            pack_key("0000:0000:0000:0000:E650"),
            error(
                "a window of 5 codepoints outruns the pack's 4 key slots: 0000:0000:0000:0000:E650"
            )
        );
    }

    #[test]
    fn a_row_is_the_record_at_its_key_and_none_for_a_missing_window_or_configuration() {
        let scratch = Scratch::new("rows", &pack_bytes());
        let pack = SubsetPack::open(&init(&scratch.0)).expect("the pack opens");
        let row = pack
            .row("default", "E650:E652")
            .expect("a row")
            .expect("a hit");
        assert_eq!(
            (row.glyphs, row.clusters, row.junctions),
            (vec!["qsPea", "qsTea"], vec![0, 1], vec!["y6"])
        );
        let single = pack.row("default", "e650").expect("a row").expect("a hit");
        assert_eq!(
            (single.glyphs, single.clusters, single.junctions),
            (vec!["qsPea"], vec![0], vec![])
        );
        for (config, codepoints) in [
            ("default", "E651"),
            ("default", "0000"),
            ("default", "FFFF:FFFF:FFFF:FFFF"),
            ("default", "1E650"),
            ("ss10", "E650"),
            ("ss03", "E650"),
        ] {
            assert_eq!(
                pack.row(config, codepoints),
                Ok(None),
                "{config} {codepoints}"
            );
        }
    }

    #[test]
    fn a_pack_whose_header_disagrees_with_the_init_is_refused() {
        let scratch = Scratch::new("refusals", &pack_bytes());
        let base = init(&scratch.0);
        let mut format = base.clone();
        format.format = "ams-subset-pack/2".to_string();
        assert!(refusal(&format).ends_with("not a ams-subset-pack/2 pack"));
        let mut packer = base.clone();
        packer.packer = "other".to_string();
        assert!(refusal(&packer).ends_with("the subset pack was written by other packer code; rewrite it over the tables it is read beside"));
        let mut byteorder = base.clone();
        byteorder.byteorder = "big".to_string();
        assert!(refusal(&byteorder).contains("written on a little-endian host"));
        let mut digest = base.clone();
        digest.digests[1].1 = "other".to_string();
        let mut fewer = base.clone();
        fewer.digests.pop();
        let mut more = base.clone();
        more.digests.push(("ss03".to_string(), "d2".to_string()));
        for digests in [digest, fewer, more] {
            assert!(refusal(&digests).contains("written over other tables"));
        }
        let mut reordered = base.clone();
        reordered.digests.reverse();
        assert!(SubsetPack::open(&reordered).is_ok());
    }

    #[test]
    fn a_file_that_is_not_a_whole_pack_is_refused() {
        let bytes = pack_bytes();
        let short = Scratch::new("short", &bytes[..4]);
        assert!(refusal(&init(&short.0)).ends_with("not a subset pack"));
        let truncated = Scratch::new("truncated", &bytes[..20]);
        assert!(refusal(&init(&truncated.0)).ends_with("truncated subset pack header"));
        let cut = Scratch::new("cut", &bytes[..bytes.len() - 1]);
        assert!(
            refusal(&init(&cut.0)).ends_with("the default section runs past the end of the pack")
        );
    }

    #[test]
    fn every_thread_can_share_one_pack() {
        fn shared<T: Send + Sync>() {}
        shared::<SubsetPack>();
    }
}
