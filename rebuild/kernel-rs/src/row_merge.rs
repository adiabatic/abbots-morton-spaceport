//! The windows rows' keyed merge: one configuration's previous rows, held as a base and segments in the segment format of `rebuild/pipeline/row_delta.py`, read as one stream of live rows in key order. A table build's finish merges its own rows against that stream and writes only the rows that differ ([`crate::artifacts::write_windows`]), and the shipped-order walk reads the merged stream as it reads a whole payload ([`MergedPayload`], [`crate::shipped_order::Walk::walk`]).
//!
//! The parts reach the crate decompressed, each on a pipe of its own (`/dev/fd/N`, `kernel_exec._InheritedPipes`), with their header lines checked and stripped by the caller: a base's rows are window rows, one a line, and a segment's rows are `+\t<window row>` for an upsert and `-\t<six key fields>` for a tombstone (`row_delta.UPSERT`, `row_delta.TOMBSTONE`). A row's key is its first six fields, compared field by field as `stream::TransitionRow::key` compares them ([`compare_keys`]). Every part holds at most one row a key, in strictly ascending key order, and at each key the row of the latest part that has one is live unless it is a tombstone. The merge checks both: a part whose keys do not strictly ascend, or whose rows end short of or run past the count its head lists, is refused, naming the part.
//!
//! Each part is read once, front to back, through a buffer of [`BUFFER_BYTES`], and the merge holds two lines a part beside it, so a merge over a base and `n` segments holds `n + 1` buffers whatever the parts' sizes. The base's rows are compared with the least key among the segments' current rows, which is found again only when a segment moves, so a base row costs one key comparison however many segments there are.

use std::cmp::Ordering;
use std::io::{BufRead, BufReader, Read};

/// A segment row's first byte for an upsert, `row_delta.UPSERT`.
pub const UPSERT: u8 = b'+';

/// A segment row's first byte for a tombstone, `row_delta.TOMBSTONE`.
pub const TOMBSTONE: u8 = b'-';

/// The read buffer each part's stream takes.
pub const BUFFER_BYTES: usize = 1 << 20;

/// How many leading fields of a window row key it.
const KEY_FIELDS: usize = 6;

/// One part of a configuration's previous rows as a command line names it: the path it is read from and the rows its head lists.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PriorPart {
    pub path: String,
    pub rows: u64,
}

/// Parses a part list as `build-tables --previous=` and `replay-emitted --segments=` take it, `<path>:<rows>` items joined by commas, or `None` for any other spelling: an empty path, or a count that is not ASCII digits.
pub fn parse_parts(list: &str) -> Option<Vec<PriorPart>> {
    list.split(',')
        .map(|item| {
            let (path, rows) = item.rsplit_once(':')?;
            if path.is_empty() || rows.is_empty() || !rows.bytes().all(|byte| byte.is_ascii_digit())
            {
                return None;
            }
            Some(PriorPart {
                path: path.to_owned(),
                rows: rows.parse().ok()?,
            })
        })
        .collect()
}

/// Compares two keys, each six tab-separated fields, field by field: at the first byte where they differ, the key whose field ends there, at a tab or at the key's end, is the lesser, and otherwise the bytes decide. That is the order `[&str; 6]` gives the same fields, which `stream::TransitionRow::key` and `table.Window.key` sort by.
pub fn compare_keys(left: &[u8], right: &[u8]) -> Ordering {
    let common = left
        .iter()
        .zip(right)
        .take_while(|(here, there)| here == there)
        .count();
    match (left.get(common), right.get(common)) {
        (None, None) => Ordering::Equal,
        (None, Some(_)) | (Some(b'\t'), Some(_)) => Ordering::Less,
        (Some(_), None) | (Some(_), Some(b'\t')) => Ordering::Greater,
        (Some(here), Some(there)) => here.cmp(there),
    }
}

/// The end of a window row's key: the offset of its sixth tab, or `None` for a row with fewer than seven fields.
fn key_end(row: &[u8]) -> Option<usize> {
    row.iter()
        .enumerate()
        .filter(|(_, byte)| **byte == b'\t')
        .nth(KEY_FIELDS - 1)
        .map(|(at, _)| at)
}

/// A key spelled for a refusal: its fields joined by `, `.
fn spell_key(key: &[u8]) -> String {
    String::from_utf8_lossy(key).replace('\t', ", ")
}

/// One part's rows as the merge reads them: the current line and the one before it, the offsets of the current row and its key within the line, and how many rows it has read against how many its head lists.
struct Stream<'a> {
    label: String,
    reader: Box<dyn BufRead + 'a>,
    segment: bool,
    rows: u64,
    read: u64,
    line: Vec<u8>,
    previous: Vec<u8>,
    previous_key: (usize, usize),
    row: usize,
    key: (usize, usize),
    done: bool,
}

impl<'a> Stream<'a> {
    fn new(label: String, reader: Box<dyn BufRead + 'a>, rows: u64, segment: bool) -> Self {
        Self {
            label,
            reader,
            segment,
            rows,
            read: 0,
            line: Vec::new(),
            previous: Vec::new(),
            previous_key: (0, 0),
            row: 0,
            key: (0, 0),
            done: false,
        }
    }

    fn key(&self) -> &[u8] {
        &self.line[self.key.0..self.key.1]
    }

    fn op(&self) -> u8 {
        if self.segment { self.line[0] } else { UPSERT }
    }

    fn row(&self) -> &[u8] {
        &self.line[self.row..]
    }

    /// Reads the next row, checking that it parses, that its key follows the previous row's, and that the part holds no more rows than its head lists; at the end, that it held exactly that many.
    fn advance(&mut self) -> Result<(), String> {
        std::mem::swap(&mut self.line, &mut self.previous);
        self.previous_key = self.key;
        self.line.clear();
        let read = self
            .reader
            .read_until(b'\n', &mut self.line)
            .map_err(|error| format!("{}: {error}", self.label))?;
        if read == 0 {
            if self.read != self.rows {
                return Err(format!(
                    "{} ends after {} rows where its head lists {}",
                    self.label, self.read, self.rows
                ));
            }
            self.done = true;
            return Ok(());
        }
        self.read += 1;
        if self.read > self.rows {
            return Err(format!(
                "{} holds more than the {} rows its head lists",
                self.label, self.rows
            ));
        }
        if self.line.last() != Some(&b'\n') {
            return Err(format!("{}: row {} has no line end", self.label, self.read));
        }
        let malformed = || {
            format!(
                "{}: row {} is not a {}: {:?}",
                self.label,
                self.read,
                if self.segment {
                    "segment row"
                } else {
                    "window row"
                },
                String::from_utf8_lossy(&self.line)
            )
        };
        let body = self.line.len() - 1;
        if self.segment {
            if self.line.len() < 3 || self.line[1] != b'\t' {
                return Err(malformed());
            }
            self.row = 2;
            self.key = match self.line[0] {
                UPSERT => {
                    let end = key_end(&self.line[2..]).ok_or_else(malformed)?;
                    (2, 2 + end)
                }
                TOMBSTONE => {
                    let tabs = self.line[2..body]
                        .iter()
                        .filter(|byte| **byte == b'\t')
                        .count();
                    if tabs != KEY_FIELDS - 1 {
                        return Err(malformed());
                    }
                    (2, body)
                }
                _ => return Err(malformed()),
            };
        } else {
            self.row = 0;
            self.key = (0, key_end(&self.line).ok_or_else(malformed)?);
        }
        if self.read > 1 {
            let previous = &self.previous[self.previous_key.0..self.previous_key.1];
            if compare_keys(previous, self.key()) != Ordering::Less {
                return Err(format!(
                    "{} is out of key order: row {} ({}) does not follow ({})",
                    self.label,
                    self.read,
                    spell_key(self.key()),
                    spell_key(previous)
                ));
            }
        }
        Ok(())
    }
}

/// A base and its segments merged by key into the live rows, in key order, each yielded as the window row line it is ([`KeyedMerge::next_into`]).
pub struct KeyedMerge<'a> {
    base: Stream<'a>,
    segments: Vec<Stream<'a>>,
    least: Option<usize>,
    stale: bool,
    scratch: Vec<u8>,
}

impl<'a> KeyedMerge<'a> {
    /// Merges `base`, a reader of a base's rows with the rows its head lists, and `segments`, each a segment's reader with its rows, oldest first. Each stream's first row is read here, so a part that is empty against a nonzero count, or out of order at its first rows, is refused before anything is merged. `name` prefixes each part's label in a refusal.
    pub fn new(
        name: &str,
        base: (Box<dyn BufRead + 'a>, u64),
        segments: Vec<(Box<dyn BufRead + 'a>, u64)>,
    ) -> Result<Self, String> {
        Self::of_streams(
            Stream::new(format!("{name}: the base"), base.0, base.1, false),
            segments
                .into_iter()
                .enumerate()
                .map(|(at, (reader, rows))| {
                    Stream::new(format!("{name}: segment {}", at + 1), reader, rows, true)
                })
                .collect(),
        )
    }

    /// Opens every part `parts` names, the base first and then each segment in order, each through a buffer of [`BUFFER_BYTES`], and merges them. A part's path is in its label, so a refusal names the pipe or file it came from.
    pub fn open(name: &str, parts: &[PriorPart]) -> Result<Self, String> {
        let (base, segments) = parts
            .split_first()
            .ok_or_else(|| format!("{name}: a merge needs a base"))?;
        let open = |label: String, part: &PriorPart, segment: bool| -> Result<Stream<'a>, String> {
            let file = std::fs::File::open(&part.path)
                .map_err(|error| format!("{name}: {}: {error}", part.path))?;
            Ok(Stream::new(
                format!("{label} ({})", part.path),
                Box::new(BufReader::with_capacity(BUFFER_BYTES, file)),
                part.rows,
                segment,
            ))
        };
        let base = open(format!("{name}: the base"), base, false)?;
        let segments = segments
            .iter()
            .enumerate()
            .map(|(at, part)| open(format!("{name}: segment {}", at + 1), part, true))
            .collect::<Result<Vec<_>, _>>()?;
        Self::of_streams(base, segments)
    }

    fn of_streams(base: Stream<'a>, segments: Vec<Stream<'a>>) -> Result<Self, String> {
        let mut merge = Self {
            base,
            segments,
            least: None,
            stale: true,
            scratch: Vec::new(),
        };
        merge.base.advance()?;
        for segment in &mut merge.segments {
            segment.advance()?;
        }
        Ok(merge)
    }

    /// Writes the next live row, with its newline, into `out` in place of what it held, and returns whether there was one.
    pub fn next_into(&mut self, out: &mut Vec<u8>) -> Result<bool, String> {
        loop {
            if self.stale {
                self.least = self.least_segment();
                self.stale = false;
            }
            let order = match (self.base.done, self.least) {
                (true, None) => return Ok(false),
                (false, None) => Ordering::Less,
                (true, Some(_)) => Ordering::Greater,
                (false, Some(at)) => compare_keys(self.base.key(), self.segments[at].key()),
            };
            if order == Ordering::Less {
                out.clear();
                out.extend_from_slice(self.base.row());
                self.base.advance()?;
                return Ok(true);
            }
            let winner = self.least.expect("a segment row decides this key");
            let upsert = self.segments[winner].op() == UPSERT;
            if upsert {
                out.clear();
                out.extend_from_slice(self.segments[winner].row());
            }
            self.scratch.clear();
            self.scratch.extend_from_slice(self.segments[winner].key());
            for segment in &mut self.segments {
                if !segment.done && segment.key() == self.scratch.as_slice() {
                    segment.advance()?;
                }
            }
            self.stale = true;
            if order == Ordering::Equal {
                self.base.advance()?;
            }
            if upsert {
                return Ok(true);
            }
        }
    }

    /// The segment whose current row has the least key, the latest of them when several share it, or `None` once every segment has ended.
    fn least_segment(&self) -> Option<usize> {
        let mut least: Option<usize> = None;
        for (at, segment) in self.segments.iter().enumerate() {
            if segment.done {
                continue;
            }
            least = match least {
                Some(held)
                    if compare_keys(segment.key(), self.segments[held].key())
                        == Ordering::Greater =>
                {
                    Some(held)
                }
                _ => Some(at),
            };
        }
        least
    }
}

/// A whole windows payload read from a base and its segments: the base's own head line and column line as they stand, then the merged rows ([`KeyedMerge`]), so a reader of a payload, the shipped-order walk, reads it as it reads one file. A refusal of the merge reads as an I/O error naming the part.
///
/// The two header lines, megabytes with the head line's JSON, are held in a buffer of their own that is freed once they are read, so the buffer the rows pass through holds one row's length and the walk holds the head line only in its own copy.
pub struct MergedPayload<'a> {
    merge: KeyedMerge<'a>,
    header: Vec<u8>,
    line: Vec<u8>,
    at: usize,
}

impl<'a> MergedPayload<'a> {
    /// Reads the base's head line and column line from `base`, then merges the rest of it, `rows` rows, with `segments` as [`KeyedMerge::new`] does.
    pub fn new(
        name: &str,
        mut base: Box<dyn BufRead + 'a>,
        rows: u64,
        segments: Vec<(Box<dyn BufRead + 'a>, u64)>,
    ) -> Result<Self, String> {
        let mut header = Vec::new();
        for _ in 0..2 {
            base.read_until(b'\n', &mut header)
                .map_err(|error| format!("{name}: the base: {error}"))?;
        }
        Ok(Self {
            merge: KeyedMerge::new(name, (base, rows), segments)?,
            header,
            line: Vec::new(),
            at: 0,
        })
    }
}

impl Read for MergedPayload<'_> {
    fn read(&mut self, buffer: &mut [u8]) -> std::io::Result<usize> {
        let available = self.fill_buf()?;
        let count = available.len().min(buffer.len());
        buffer[..count].copy_from_slice(&available[..count]);
        self.consume(count);
        Ok(count)
    }
}

impl BufRead for MergedPayload<'_> {
    fn fill_buf(&mut self) -> std::io::Result<&[u8]> {
        if !self.header.is_empty() {
            if self.at < self.header.len() {
                return Ok(&self.header[self.at..]);
            }
            self.header = Vec::new();
            self.at = 0;
        }
        if self.at >= self.line.len() {
            self.at = 0;
            if !self
                .merge
                .next_into(&mut self.line)
                .map_err(std::io::Error::other)?
            {
                self.line.clear();
            }
        }
        Ok(&self.line[self.at..])
    }

    fn consume(&mut self, count: usize) {
        self.at += count;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn reader(text: &str) -> (Box<dyn BufRead + '_>, u64) {
        let rows = text.lines().count() as u64;
        (Box::new(text.as_bytes()), rows)
    }

    fn merged(base: &str, segments: &[&str]) -> Result<Vec<String>, String> {
        let mut merge = KeyedMerge::new(
            "default",
            reader(base),
            segments.iter().map(|segment| reader(segment)).collect(),
        )?;
        let mut rows = Vec::new();
        let mut line = Vec::new();
        while merge.next_into(&mut line)? {
            rows.push(String::from_utf8(line.clone()).expect("the rows are text"));
        }
        Ok(rows)
    }

    fn row(key: &str, outcome: &str) -> String {
        format!("{}\t{outcome}\n", key.replace(' ', "\t"))
    }

    /// Keys compare field by field, so a field that is a prefix of another sorts first even where a byte below the tab would have sorted it last, and a key's fields decide in order.
    #[test]
    fn keys_compare_field_by_field() {
        let keys = [
            "a\tb\tc\td\te\tf",
            "a\tb\tc\td\te\tff",
            "a\tb\tc\td\tea\tf",
            "ab\t#\t#\t#\t#\t#",
        ];
        for pair in keys.windows(2) {
            assert_eq!(
                compare_keys(pair[0].as_bytes(), pair[1].as_bytes()),
                Ordering::Less
            );
            assert_eq!(
                compare_keys(pair[1].as_bytes(), pair[0].as_bytes()),
                Ordering::Greater
            );
        }
        assert_eq!(compare_keys(b"a\x01\tb", b"a\tb"), Ordering::Greater);
        assert_eq!(
            compare_keys(keys[0].as_bytes(), keys[0].as_bytes()),
            Ordering::Equal
        );
    }

    /// The latest part wins at each key: a later segment's upsert replaces an earlier one's and the base's, a tombstone removes the row the parts before it hold, a key no earlier part holds is added, and a tombstone of a key nothing holds adds nothing.
    #[test]
    fn the_latest_part_wins_at_each_key() {
        let base = [
            row("a a a a a a", "1"),
            row("b b b b b b", "1"),
            row("c c c c c c", "1"),
            row("e e e e e e", "1"),
        ]
        .concat();
        let first = [
            format!("+\t{}", row("a a a a a a", "2")),
            "-\tc\tc\tc\tc\tc\tc\n".to_owned(),
            format!("+\t{}", row("d d d d d d", "2")),
        ]
        .concat();
        let second = [
            format!("+\t{}", row("a a a a a a", "3")),
            format!("+\t{}", row("c c c c c c", "3")),
            "-\td\td\td\td\td\td\n".to_owned(),
            "-\tz\tz\tz\tz\tz\tz\n".to_owned(),
        ]
        .concat();
        assert_eq!(
            merged(&base, &[&first, &second]).expect("the parts merge"),
            [
                row("a a a a a a", "3"),
                row("b b b b b b", "1"),
                row("c c c c c c", "3"),
                row("e e e e e e", "1"),
            ]
        );
        assert_eq!(
            merged(&base, &[&first]).expect("the parts merge"),
            [
                row("a a a a a a", "2"),
                row("b b b b b b", "1"),
                row("d d d d d d", "2"),
                row("e e e e e e", "1"),
            ]
        );
    }

    /// A part whose keys do not strictly ascend is refused, a repeated key among them, and so is one whose rows end short of its head's count or run past it; each refusal names the part.
    #[test]
    fn a_part_out_of_order_or_of_another_count_is_refused_naming_it() {
        let base = [row("a a a a a a", "1"), row("b b b b b b", "1")].concat();
        let backwards = [
            format!("+\t{}", row("b b b b b b", "2")),
            format!("+\t{}", row("a a a a a a", "2")),
        ]
        .concat();
        let error = merged(&base, &[&backwards]).expect_err("a segment out of order");
        assert!(error.contains("segment 1 is out of key order"), "{error}");
        let repeated = [row("a a a a a a", "1"), row("a a a a a a", "2")].concat();
        let error = merged(&repeated, &[]).expect_err("a base that repeats a key");
        assert!(error.contains("the base is out of key order"), "{error}");
        let short = KeyedMerge::new("default", (Box::new(base.as_bytes()), 3), Vec::new())
            .and_then(|mut merge| {
                let mut line = Vec::new();
                while merge.next_into(&mut line)? {}
                Ok(())
            })
            .expect_err("a base shorter than its count");
        assert!(
            short.contains("the base ends after 2 rows where its head lists 3"),
            "{short}"
        );
        let past = KeyedMerge::new("default", (Box::new(base.as_bytes()), 1), Vec::new())
            .and_then(|mut merge| {
                let mut line = Vec::new();
                while merge.next_into(&mut line)? {}
                Ok(())
            })
            .expect_err("a base longer than its count");
        assert!(
            past.contains("the base holds more than the 1 rows"),
            "{past}"
        );
        let error = merged(&base, &["*\tx\n"]).expect_err("a row that is neither");
        assert!(
            error.contains("segment 1: row 1 is not a segment row"),
            "{error}"
        );
    }

    /// A part list names each part's path and its rows, and any other spelling is refused.
    #[test]
    fn a_part_list_is_paths_and_their_counts() {
        assert_eq!(
            parse_parts("/dev/fd/7:12,/dev/fd/9:0"),
            Some(vec![
                PriorPart {
                    path: "/dev/fd/7".to_owned(),
                    rows: 12
                },
                PriorPart {
                    path: "/dev/fd/9".to_owned(),
                    rows: 0
                },
            ])
        );
        for wrong in [
            "",
            "/dev/fd/7",
            ":3",
            "/dev/fd/7:",
            "/dev/fd/7:+3",
            "/dev/fd/7:3,",
        ] {
            assert_eq!(parse_parts(wrong), None, "{wrong}");
        }
    }

    /// A merged payload reads as the whole payload: the base's head and column lines, then the merged rows, through any reads.
    #[test]
    fn a_merged_payload_reads_as_the_whole_payload() {
        let head = "# head\ncolumns\n";
        let base = format!(
            "{head}{}{}",
            row("a a a a a a", "1"),
            row("b b b b b b", "1")
        );
        let segment = format!("+\t{}", row("b b b b b b", "2"));
        let mut payload = MergedPayload::new(
            "default",
            Box::new(base.as_bytes()),
            2,
            vec![reader(&segment)],
        )
        .expect("the parts merge");
        let mut text = String::new();
        payload
            .read_to_string(&mut text)
            .expect("the payload reads");
        assert_eq!(
            text,
            format!(
                "{head}{}{}",
                row("a a a a a a", "1"),
                row("b b b b b b", "2")
            )
        );
    }
}
