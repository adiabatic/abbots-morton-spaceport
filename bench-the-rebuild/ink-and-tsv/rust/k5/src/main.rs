// K5 — TSV parsing, Rust port of rebuild/validation/rowmodel.py's Row.from_tsv,
// rebuild/review/audit.py's load_audit, and rebuild/pipeline/baseline_subset.py's filter_table.
//
// Equivalence: every variant is verified by re-serializing the parsed rows to the same canonical TSV
// the Python checksum uses and hashing that, so the printed checksum is a round trip through the
// parsed values and cannot be satisfied by echoing the input. The checksum pass runs outside the
// timer but consumes the whole parsed table, which is also what defeats dead-code elimination.

use sha2::{Digest, Sha256};
use std::time::Instant;

// ---- canonical re-serialization (must match k5_python.canon_row / canon_audit) -----------------

fn push_u32_hex4(out: &mut Vec<u8>, v: u32) {
    let s = format!("{:04X}", v);
    out.extend_from_slice(s.as_bytes());
}

fn push_i64(out: &mut Vec<u8>, v: i64) {
    let mut buf = itoa(v);
    out.append(&mut buf);
}

fn itoa(mut v: i64) -> Vec<u8> {
    if v == 0 {
        return vec![b'0'];
    }
    let neg = v < 0;
    let mut tmp = Vec::with_capacity(20);
    while v != 0 {
        tmp.push(b'0' + (v % 10).unsigned_abs() as u8);
        v /= 10;
    }
    if neg {
        tmp.push(b'-');
    }
    tmp.reverse();
    tmp
}

// ---- Row ---------------------------------------------------------------------------------------

struct RowOwned {
    codepoints: Vec<u32>,
    glyphs: Vec<String>,
    clusters: Vec<i64>,
    seams: Vec<String>,
    positions: Vec<(i64, i64, i64)>,
}

struct RowBorrowed<'a> {
    codepoints: Vec<u32>,
    glyphs: Vec<&'a str>,
    clusters: Vec<i64>,
    seams: Vec<&'a str>,
    positions: Vec<(i64, i64, i64)>,
}

#[inline]
fn parse_hex(s: &str) -> u32 {
    let mut v = 0u32;
    for b in s.as_bytes() {
        v = v * 16
            + match b {
                b'0'..=b'9' => (b - b'0') as u32,
                b'a'..=b'f' => (b - b'a' + 10) as u32,
                _ => (b - b'A' + 10) as u32,
            };
    }
    v
}

#[inline]
fn parse_i64(s: &str) -> i64 {
    let bytes = s.as_bytes();
    let (neg, bytes) = if bytes[0] == b'-' { (true, &bytes[1..]) } else { (false, bytes) };
    let mut v = 0i64;
    for b in bytes {
        v = v * 10 + (b - b'0') as i64;
    }
    if neg {
        -v
    } else {
        v
    }
}

fn parse_rows_owned(text: &str) -> Vec<RowOwned> {
    let mut out = Vec::with_capacity(64 * 1024);
    for line in text.split('\n') {
        if line.is_empty() || line.as_bytes()[0] == b'#' {
            continue;
        }
        let mut it = line.splitn(5, '\t');
        let cps = it.next().unwrap();
        let glyphs = it.next().unwrap();
        let clusters = it.next().unwrap();
        let seams = it.next().unwrap();
        let positions = it.next().unwrap();
        out.push(RowOwned {
            codepoints: cps.split(':').map(parse_hex).collect(),
            glyphs: glyphs.split('|').map(|s| s.to_string()).collect(),
            clusters: clusters.split(',').map(parse_i64).collect(),
            seams: if seams.is_empty() {
                Vec::new()
            } else {
                seams.split(',').map(|s| s.to_string()).collect()
            },
            positions: positions
                .split('|')
                .map(|t| {
                    let mut p = t.splitn(3, ',');
                    (
                        parse_i64(p.next().unwrap()),
                        parse_i64(p.next().unwrap()),
                        parse_i64(p.next().unwrap()),
                    )
                })
                .collect(),
        });
    }
    out
}

fn parse_rows_borrowed(text: &str) -> Vec<RowBorrowed<'_>> {
    let mut out = Vec::with_capacity(64 * 1024);
    for line in text.split('\n') {
        if line.is_empty() || line.as_bytes()[0] == b'#' {
            continue;
        }
        let mut it = line.splitn(5, '\t');
        let cps = it.next().unwrap();
        let glyphs = it.next().unwrap();
        let clusters = it.next().unwrap();
        let seams = it.next().unwrap();
        let positions = it.next().unwrap();
        out.push(RowBorrowed {
            codepoints: cps.split(':').map(parse_hex).collect(),
            glyphs: glyphs.split('|').collect(),
            clusters: clusters.split(',').map(parse_i64).collect(),
            seams: if seams.is_empty() { Vec::new() } else { seams.split(',').collect() },
            positions: positions
                .split('|')
                .map(|t| {
                    let mut p = t.splitn(3, ',');
                    (
                        parse_i64(p.next().unwrap()),
                        parse_i64(p.next().unwrap()),
                        parse_i64(p.next().unwrap()),
                    )
                })
                .collect(),
        });
    }
    out
}

fn canon_row(
    out: &mut Vec<u8>,
    codepoints: &[u32],
    glyphs: &[&str],
    clusters: &[i64],
    seams: &[&str],
    positions: &[(i64, i64, i64)],
) {
    for (i, cp) in codepoints.iter().enumerate() {
        if i > 0 {
            out.push(b':');
        }
        push_u32_hex4(out, *cp);
    }
    out.push(b'\t');
    for (i, g) in glyphs.iter().enumerate() {
        if i > 0 {
            out.push(b'|');
        }
        out.extend_from_slice(g.as_bytes());
    }
    out.push(b'\t');
    for (i, c) in clusters.iter().enumerate() {
        if i > 0 {
            out.push(b',');
        }
        push_i64(out, *c);
    }
    out.push(b'\t');
    for (i, s) in seams.iter().enumerate() {
        if i > 0 {
            out.push(b',');
        }
        out.extend_from_slice(s.as_bytes());
    }
    out.push(b'\t');
    for (i, (x, y, a)) in positions.iter().enumerate() {
        if i > 0 {
            out.push(b'|');
        }
        push_i64(out, *x);
        out.push(b',');
        push_i64(out, *y);
        out.push(b',');
        push_i64(out, *a);
    }
    out.push(b'\n');
}

fn rows_checksum_owned(rows: &[RowOwned]) -> String {
    let mut h = Sha256::new();
    let mut buf = Vec::with_capacity(4096);
    for r in rows {
        buf.clear();
        let glyphs: Vec<&str> = r.glyphs.iter().map(|s| s.as_str()).collect();
        let seams: Vec<&str> = r.seams.iter().map(|s| s.as_str()).collect();
        canon_row(&mut buf, &r.codepoints, &glyphs, &r.clusters, &seams, &r.positions);
        h.update(&buf);
    }
    hex(&h.finalize())
}

fn rows_checksum_borrowed(rows: &[RowBorrowed]) -> String {
    let mut h = Sha256::new();
    let mut buf = Vec::with_capacity(4096);
    for r in rows {
        buf.clear();
        canon_row(&mut buf, &r.codepoints, &r.glyphs, &r.clusters, &r.seams, &r.positions);
        h.update(&buf);
    }
    hex(&h.finalize())
}

// ---- AuditRow ----------------------------------------------------------------------------------

struct AuditOwned {
    config: String,
    codepoints: String,
    kinds: Vec<String>,
    matched_entry: String,
    baseline: Vec<String>,
    new: Vec<String>,
}

fn parse_audit_owned(text: &str) -> Vec<AuditOwned> {
    let mut out = Vec::with_capacity(300_000);
    for (index, line) in text.split('\n').enumerate() {
        if index == 0 || line.is_empty() {
            continue;
        }
        let mut it = line.splitn(6, '\t');
        out.push(AuditOwned {
            config: it.next().unwrap().to_string(),
            codepoints: it.next().unwrap().to_string(),
            kinds: it.next().unwrap().split(',').map(|s| s.to_string()).collect(),
            matched_entry: it.next().unwrap().to_string(),
            baseline: it.next().unwrap().split('|').map(|s| s.to_string()).collect(),
            new: it.next().unwrap().split('|').map(|s| s.to_string()).collect(),
        });
    }
    out
}

fn audit_checksum(rows: &[AuditOwned]) -> String {
    let mut h = Sha256::new();
    let mut buf: Vec<u8> = Vec::with_capacity(1024);
    for r in rows {
        buf.clear();
        buf.extend_from_slice(r.config.as_bytes());
        buf.push(b'\t');
        buf.extend_from_slice(r.codepoints.as_bytes());
        buf.push(b'\t');
        for (i, k) in r.kinds.iter().enumerate() {
            if i > 0 {
                buf.push(b',');
            }
            buf.extend_from_slice(k.as_bytes());
        }
        buf.push(b'\t');
        buf.extend_from_slice(r.matched_entry.as_bytes());
        buf.push(b'\t');
        for (i, b) in r.baseline.iter().enumerate() {
            if i > 0 {
                buf.push(b'|');
            }
            buf.extend_from_slice(b.as_bytes());
        }
        buf.push(b'\t');
        for (i, n) in r.new.iter().enumerate() {
            if i > 0 {
                buf.push(b'|');
            }
            buf.extend_from_slice(n.as_bytes());
        }
        buf.push(b'\n');
        h.update(&buf);
    }
    hex(&h.finalize())
}

// ---- filter_table ------------------------------------------------------------------------------

const ALPHABET: [u32; 18] = [
    0x0020, 0x00B7, 0x200C, 0xE650, 0xE652, 0xE653, 0xE658, 0xE65A, 0xE665, 0xE666, 0xE667, 0xE668,
    0xE670, 0xE672, 0xE675, 0xE676, 0xE679, 0xE67A,
];

#[inline]
fn in_alphabet(token: &[u8]) -> bool {
    let mut v = 0u32;
    for b in token {
        let d = match b {
            b'0'..=b'9' => (b - b'0') as u32,
            b'a'..=b'f' => (b - b'a' + 10) as u32,
            b'A'..=b'F' => (b - b'A' + 10) as u32,
            _ => return false,
        };
        v = v.wrapping_mul(16).wrapping_add(d);
    }
    ALPHABET.contains(&v)
}

#[inline]
fn keep_line(line: &[u8]) -> bool {
    let end = match line.iter().position(|&c| c == b'\t') {
        Some(i) => i,
        None => line.len(),
    };
    let field = &line[..end];
    for token in field.split(|&c| c == b':') {
        if !in_alphabet(token) {
            return false;
        }
    }
    true
}

fn filter_chunk(data: &[u8], out: &mut Vec<u8>) -> usize {
    let mut kept = 0;
    for line in data.split(|&c| c == b'\n') {
        if line.is_empty() {
            continue;
        }
        if line[0] == b'#' {
            out.extend_from_slice(line);
            out.push(b'\n');
            continue;
        }
        if keep_line(line) {
            out.extend_from_slice(line);
            out.push(b'\n');
            kept += 1;
        }
    }
    kept
}

fn split_at_lines(data: &[u8], parts: usize) -> Vec<&[u8]> {
    let mut bounds = Vec::with_capacity(parts + 1);
    bounds.push(0usize);
    for i in 1..parts {
        let mut p = data.len() * i / parts;
        while p < data.len() && data[p] != b'\n' {
            p += 1;
        }
        if p < data.len() {
            p += 1;
        }
        bounds.push(p.min(data.len()));
    }
    bounds.push(data.len());
    bounds.dedup();
    (0..bounds.len() - 1).map(|i| &data[bounds[i]..bounds[i + 1]]).collect()
}

fn hex(bytes: &[u8]) -> String {
    let mut s = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        s.push(char::from_digit((b >> 4) as u32, 16).unwrap());
        s.push(char::from_digit((b & 15) as u32, 16).unwrap());
    }
    s
}

fn best<F: FnMut() -> R, R>(mut f: F, reps: usize) -> (f64, R) {
    let mut b = f64::INFINITY;
    let mut last = None;
    for _ in 0..reps {
        let t = Instant::now();
        let r = f();
        let e = t.elapsed().as_secs_f64();
        if e < b {
            b = e;
        }
        last = Some(r);
    }
    (b, last.unwrap())
}

fn main() {
    let a: Vec<String> = std::env::args().collect();
    let rows_path = &a[1];
    let audit_path = &a[2];
    let big_path = &a[3];
    let out_dir = &a[4];
    let threads: usize = a.get(5).and_then(|s| s.parse().ok()).unwrap_or(8);

    let rows_text = std::fs::read_to_string(rows_path).unwrap();
    let (t_owned, owned) = best(|| parse_rows_owned(&rows_text), 5);
    let n_rows = owned.len();
    let ck_owned = rows_checksum_owned(&owned);
    drop(owned);
    let (t_borrowed, borrowed) = best(|| parse_rows_borrowed(&rows_text), 5);
    let ck_borrowed = rows_checksum_borrowed(&borrowed);
    drop(borrowed);

    let audit_text = std::fs::read_to_string(audit_path).unwrap();
    let (t_audit, audit) = best(|| parse_audit_owned(&audit_text), 3);
    let n_audit = audit.len();
    let ck_audit = audit_checksum(&audit);
    drop(audit);

    let big = std::fs::read(big_path).unwrap();
    let big_len = big.len();
    let (t_filter, (kept1, out1)) = best(
        || {
            let mut out = Vec::with_capacity(8 << 20);
            let kept = filter_chunk(&big, &mut out);
            (kept, out)
        },
        2,
    );
    let ck_filter = hex(&Sha256::digest(&out1));
    std::fs::write(format!("{}/rust.subset.tsv", out_dir), &out1).unwrap();
    drop(out1);

    let (t_filter_par, (kept2, out2)) = best(
        || {
            let chunks = split_at_lines(&big, threads);
            let parts: Vec<(usize, Vec<u8>)> = std::thread::scope(|scope| {
                let handles: Vec<_> = chunks
                    .iter()
                    .map(|chunk| {
                        scope.spawn(move || {
                            let mut out = Vec::with_capacity(2 << 20);
                            let kept = filter_chunk(chunk, &mut out);
                            (kept, out)
                        })
                    })
                    .collect();
                handles.into_iter().map(|h| h.join().unwrap()).collect()
            });
            let mut kept = 0;
            let mut out = Vec::with_capacity(8 << 20);
            for (k, part) in parts {
                kept += k;
                out.extend_from_slice(&part);
            }
            (kept, out)
        },
        2,
    );
    let ck_filter_par = hex(&Sha256::digest(&out2));
    drop(out2);

    println!(
        "{{\"rows_from_tsv\":{{\"rows\":{},\"rust_owned_seconds\":{:.6},\"rust_owned_ns_per_row\":{:.1},\
        \"rust_owned_checksum\":\"{}\",\"rust_borrowed_seconds\":{:.6},\"rust_borrowed_ns_per_row\":{:.1},\
        \"rust_borrowed_checksum\":\"{}\"}},\
        \"load_audit\":{{\"rows\":{},\"rust_seconds\":{:.6},\"rust_ns_per_row\":{:.1},\"rust_checksum\":\"{}\"}},\
        \"filter_table\":{{\"source_bytes\":{},\"rust_single_seconds\":{:.6},\"rust_single_kept\":{},\
        \"rust_single_checksum\":\"{}\",\"rust_parallel_threads\":{},\"rust_parallel_seconds\":{:.6},\
        \"rust_parallel_kept\":{},\"rust_parallel_checksum\":\"{}\"}}}}",
        n_rows,
        t_owned,
        t_owned / n_rows as f64 * 1e9,
        ck_owned,
        t_borrowed,
        t_borrowed / n_rows as f64 * 1e9,
        ck_borrowed,
        n_audit,
        t_audit,
        t_audit / n_audit as f64 * 1e9,
        ck_audit,
        big_len,
        t_filter,
        kept1,
        ck_filter,
        threads,
        t_filter_par,
        kept2,
        ck_filter_par
    );
}
