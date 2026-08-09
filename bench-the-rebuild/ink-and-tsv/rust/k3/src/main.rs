// K3 — placed-ink layer, Rust port of rebuild/review/ink.py.
//
// Reproduces byte-for-byte what the Python produces: the same placed-outline arithmetic, the same
// prefix/suffix stripping and multiset subtraction in config_diff, and — critically — the same
// CPython repr() text feeding sha256/sha1, so signature_digest and delta_digest come out identical.
//
// Dead-code elimination is defeated structurally: every per-row digest is folded into one running
// sha256 whose hex is printed, so no loop's result is unused.

use sha1::Sha1;
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::time::Instant;

type Point = Option<(i32, i32)>;
type Contour = (u32, Vec<Point>);
type Outline = Vec<Contour>;

struct Reader<'a> {
    buf: &'a [u8],
    pos: usize,
}

impl<'a> Reader<'a> {
    fn u32(&mut self) -> u32 {
        let v = u32::from_le_bytes(self.buf[self.pos..self.pos + 4].try_into().unwrap());
        self.pos += 4;
        v
    }
    fn i32(&mut self) -> i32 {
        let v = i32::from_le_bytes(self.buf[self.pos..self.pos + 4].try_into().unwrap());
        self.pos += 4;
        v
    }
    fn u8(&mut self) -> u8 {
        let v = self.buf[self.pos];
        self.pos += 1;
        v
    }
    fn str(&mut self) -> String {
        let n = self.u32() as usize;
        let s = std::str::from_utf8(&self.buf[self.pos..self.pos + n]).unwrap().to_string();
        self.pos += n;
        s
    }
}

struct Glyph {
    name: String,
    xoff: i32,
    yoff: i32,
    xadv: i32,
}

struct Row {
    unit: String,
    config: String,
    before: Vec<Glyph>,
    after: Vec<Glyph>,
}

struct Input {
    ops: Vec<String>,
    tables: Vec<HashMap<String, Outline>>,
    rows: Vec<Row>,
}

fn load(path: &str) -> Input {
    let raw = std::fs::read(path).expect("input");
    assert_eq!(&raw[0..4], b"K3B1");
    let mut r = Reader { buf: &raw, pos: 4 };
    let nops = r.u32() as usize;
    let ops: Vec<String> = (0..nops).map(|_| r.str()).collect();
    let ntables = r.u32() as usize;
    let mut tables = Vec::with_capacity(ntables);
    for _ in 0..ntables {
        let nglyphs = r.u32() as usize;
        let mut table: HashMap<String, Outline> = HashMap::with_capacity(nglyphs * 2);
        for _ in 0..nglyphs {
            let name = r.str();
            let ncont = r.u32() as usize;
            let mut outline: Outline = Vec::with_capacity(ncont);
            for _ in 0..ncont {
                let op = r.u32();
                let npts = r.u32() as usize;
                let mut pts: Vec<Point> = Vec::with_capacity(npts);
                for _ in 0..npts {
                    let is_none = r.u8();
                    let x = r.i32();
                    let y = r.i32();
                    pts.push(if is_none == 1 { None } else { Some((x, y)) });
                }
                outline.push((op, pts));
            }
            table.insert(name, outline);
        }
        tables.push(table);
    }
    let nrows = r.u32() as usize;
    let mut rows = Vec::with_capacity(nrows);
    for _ in 0..nrows {
        let unit = r.str();
        let config = r.str();
        let _text = r.str();
        let mut sides: Vec<Vec<Glyph>> = Vec::with_capacity(2);
        for _ in 0..2 {
            let n = r.u32() as usize;
            let mut run = Vec::with_capacity(n);
            for _ in 0..n {
                let name = r.str();
                let xoff = r.i32();
                let yoff = r.i32();
                let xadv = r.i32();
                run.push(Glyph { name, xoff, yoff, xadv });
            }
            sides.push(run);
        }
        let after = sides.pop().unwrap();
        let before = sides.pop().unwrap();
        rows.push(Row { unit, config, before, after });
    }
    Input { ops, tables, rows }
}

// ---- ink.translate_outline -------------------------------------------------------------------

#[inline]
fn translate_outline(value: &Outline, dx: i32, dy: i32) -> Outline {
    value
        .iter()
        .map(|(op, pts)| {
            (
                *op,
                pts.iter().map(|p| p.map(|(x, y)| (x + dx, y + dy))).collect::<Vec<Point>>(),
            )
        })
        .collect()
}

// ---- CPython repr() reproduction -------------------------------------------------------------

#[inline]
fn push_i32(out: &mut Vec<u8>, mut v: i32) {
    if v == 0 {
        out.push(b'0');
        return;
    }
    if v < 0 {
        out.push(b'-');
    }
    let mut tmp = [0u8; 12];
    let mut n = 0;
    let neg = v < 0;
    while v != 0 {
        let d = (v % 10).abs() as u8;
        tmp[n] = b'0' + d;
        n += 1;
        v /= 10;
    }
    let _ = neg;
    while n > 0 {
        n -= 1;
        out.push(tmp[n]);
    }
}

fn push_points(out: &mut Vec<u8>, pts: &[Point]) {
    out.push(b'(');
    for (i, p) in pts.iter().enumerate() {
        if i > 0 {
            out.extend_from_slice(b", ");
        }
        match p {
            None => out.extend_from_slice(b"None"),
            Some((x, y)) => {
                out.push(b'(');
                push_i32(out, *x);
                out.extend_from_slice(b", ");
                push_i32(out, *y);
                out.push(b')');
            }
        }
    }
    if pts.len() == 1 {
        out.push(b',');
    }
    out.push(b')');
}

fn push_piece(out: &mut Vec<u8>, piece: &Outline, ops: &[String]) {
    out.push(b'(');
    for (i, (op, pts)) in piece.iter().enumerate() {
        if i > 0 {
            out.extend_from_slice(b", ");
        }
        out.extend_from_slice(b"('");
        out.extend_from_slice(ops[*op as usize].as_bytes());
        out.extend_from_slice(b"', ");
        push_points(out, pts);
        out.push(b')');
    }
    if piece.len() == 1 {
        out.push(b',');
    }
    out.push(b')');
}

fn push_pieces(out: &mut Vec<u8>, pieces: &[Outline], ops: &[String]) {
    out.push(b'(');
    for (i, piece) in pieces.iter().enumerate() {
        if i > 0 {
            out.extend_from_slice(b", ");
        }
        push_piece(out, piece, ops);
    }
    if pieces.len() == 1 {
        out.push(b',');
    }
    out.push(b')');
}

// ---- InkComparator.ink_pieces / run_ink / config_diff ------------------------------------------

fn ink_pieces(run: &[Glyph], table: &HashMap<String, Outline>) -> Vec<Outline> {
    let mut pieces: Vec<Outline> = Vec::with_capacity(run.len());
    let mut pen_x = 0i32;
    for g in run {
        let outline = &table[&g.name];
        if !outline.is_empty() {
            pieces.push(translate_outline(outline, pen_x + g.xoff, g.yoff));
        }
        pen_x += g.xadv;
    }
    pieces.sort();
    pieces
}

fn run_ink(run: &[Glyph], table: &HashMap<String, Outline>) -> Vec<(Outline, i32)> {
    let mut pieces: Vec<(Outline, i32)> = Vec::with_capacity(run.len());
    let mut pen_x = 0i32;
    for g in run {
        let outline = &table[&g.name];
        if !outline.is_empty() {
            pieces.push((translate_outline(outline, 0, g.yoff), pen_x + g.xoff));
        }
        pen_x += g.xadv;
    }
    pieces
}

fn config_diff(
    before: &[(Outline, i32)],
    after: &[(Outline, i32)],
) -> (Vec<Outline>, Vec<Outline>, i32) {
    let mut start = 0usize;
    while start < before.len() && start < after.len() && before[start] == after[start] {
        start += 1;
    }
    let mut stripped = 0usize;
    let mut shift: Option<i32> = None;
    loop {
        if before.len() < stripped + 1 || after.len() < stripped + 1 {
            break;
        }
        let bi = before.len() - 1 - stripped;
        let ai = after.len() - 1 - stripped;
        if bi < start || ai < start {
            break;
        }
        if before[bi].0 != after[ai].0 {
            break;
        }
        let dx = after[ai].1 - before[bi].1;
        if shift.is_none() {
            shift = Some(dx);
        }
        if dx != shift.unwrap() {
            break;
        }
        stripped += 1;
    }
    let shift = shift.unwrap_or(0);

    let mut mb: HashMap<Outline, i64> = HashMap::new();
    for (outline, pen) in &before[start..before.len() - stripped] {
        *mb.entry(translate_outline(outline, *pen, 0)).or_insert(0) += 1;
    }
    let mut ma: HashMap<Outline, i64> = HashMap::new();
    for (outline, pen) in &after[start..after.len() - stripped] {
        *ma.entry(translate_outline(outline, *pen, 0)).or_insert(0) += 1;
    }
    let mut before_only: Vec<Outline> = Vec::new();
    for (k, v) in &mb {
        let rest = v - ma.get(k).copied().unwrap_or(0);
        for _ in 0..rest.max(0) {
            before_only.push(k.clone());
        }
    }
    let mut after_only: Vec<Outline> = Vec::new();
    for (k, v) in &ma {
        let rest = v - mb.get(k).copied().unwrap_or(0);
        for _ in 0..rest.max(0) {
            after_only.push(k.clone());
        }
    }
    let mut x0: Option<i32> = None;
    for piece in before_only.iter().chain(after_only.iter()) {
        for (_op, pts) in piece {
            for p in pts {
                if let Some((x, _)) = p {
                    x0 = Some(match x0 {
                        None => *x,
                        Some(m) => m.min(*x),
                    });
                }
            }
        }
    }
    let Some(x0) = x0 else {
        return (Vec::new(), Vec::new(), shift);
    };
    let normalize = |pieces: Vec<Outline>| -> Vec<Outline> {
        let mut out: Vec<Outline> = pieces
            .into_iter()
            .map(|piece| {
                piece
                    .into_iter()
                    .map(|(op, pts)| {
                        (op, pts.into_iter().map(|p| p.map(|(x, y)| (x - x0, y))).collect())
                    })
                    .collect()
            })
            .collect();
        out.sort();
        out
    };
    (normalize(before_only), normalize(after_only), shift)
}

// ---- driver -----------------------------------------------------------------------------------

struct RowOut {
    line: String,
}

fn process(rows: &[Row], input: &Input, binary_digest: bool) -> Vec<RowOut> {
    let before_table = &input.tables[0];
    let after_table = &input.tables[1];
    let mut out = Vec::with_capacity(rows.len());
    let mut buf: Vec<u8> = Vec::with_capacity(1 << 16);
    for row in rows {
        // signature
        let bp = ink_pieces(&row.before, before_table);
        let ap = ink_pieces(&row.after, after_table);
        buf.clear();
        if binary_digest {
            pack_pieces(&mut buf, &bp);
            pack_pieces(&mut buf, &ap);
        } else {
            buf.push(b'(');
            push_pieces(&mut buf, &bp, &input.ops);
            buf.extend_from_slice(b", ");
            push_pieces(&mut buf, &ap, &input.ops);
            buf.push(b')');
        }
        let sd = hex(&Sha256::digest(&buf));

        // config_diff
        let br = run_ink(&row.before, before_table);
        let ar = run_ink(&row.after, after_table);
        let (bo, ao, shift) = config_diff(&br, &ar);
        buf.clear();
        buf.push(b'(');
        push_pieces(&mut buf, &bo, &input.ops);
        buf.extend_from_slice(b", ");
        push_pieces(&mut buf, &ao, &input.ops);
        buf.extend_from_slice(b", ");
        push_i32(&mut buf, shift);
        buf.push(b')');
        let dd = format!("d-{}", &hex(&Sha1::digest(&buf))[..12]);

        out.push(RowOut { line: format!("{}\t{}\t{}\t{}\n", row.unit, row.config, sd, dd) });
    }
    out
}

fn pack_pieces(out: &mut Vec<u8>, pieces: &[Outline]) {
    out.extend_from_slice(&(pieces.len() as u32).to_le_bytes());
    for piece in pieces {
        out.extend_from_slice(&(piece.len() as u32).to_le_bytes());
        for (op, pts) in piece {
            out.extend_from_slice(&op.to_le_bytes());
            out.extend_from_slice(&(pts.len() as u32).to_le_bytes());
            for p in pts {
                match p {
                    None => out.extend_from_slice(&[1u8, 0, 0, 0, 0, 0, 0, 0, 0]),
                    Some((x, y)) => {
                        out.push(0);
                        out.extend_from_slice(&x.to_le_bytes());
                        out.extend_from_slice(&y.to_le_bytes());
                    }
                }
            }
        }
    }
}

fn hex(bytes: &[u8]) -> String {
    let mut s = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        s.push(char::from_digit((b >> 4) as u32, 16).unwrap());
        s.push(char::from_digit((b & 15) as u32, 16).unwrap());
    }
    s
}

fn checksum(outs: &[RowOut]) -> String {
    let mut h = Sha256::new();
    for o in outs {
        h.update(o.line.as_bytes());
    }
    hex(&h.finalize())
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let path = args.get(1).map(|s| s.as_str()).unwrap_or("k3-input.bin");
    let reps: usize = args.get(2).and_then(|s| s.parse().ok()).unwrap_or(3);
    let threads: usize = args.get(3).and_then(|s| s.parse().ok()).unwrap_or(1);
    let input = load(path);
    let nrows = input.rows.len();

    // sha256 throughput micro, so the report can say whether the port got hardware SHA.
    let blob = vec![0x5au8; 8 << 20];
    let mut sha_best = f64::INFINITY;
    let mut sha_sink = 0u32;
    for _ in 0..5 {
        let t = Instant::now();
        let d = Sha256::digest(&blob);
        let e = t.elapsed().as_secs_f64();
        sha_sink = sha_sink.wrapping_add(d[0] as u32);
        if e < sha_best {
            sha_best = e;
        }
    }
    let sha_mbs = (blob.len() as f64 / (1 << 20) as f64) / sha_best;

    let mut best = f64::INFINITY;
    let mut sum = String::new();
    for _ in 0..reps {
        let t = Instant::now();
        let outs: Vec<RowOut> = if threads <= 1 {
            process(&input.rows, &input, false)
        } else {
            let chunk = nrows.div_ceil(threads);
            let parts: Vec<Vec<RowOut>> = std::thread::scope(|scope| {
                let handles: Vec<_> = input
                    .rows
                    .chunks(chunk)
                    .map(|slice| scope.spawn(|| process(slice, &input, false)))
                    .collect();
                handles.into_iter().map(|h| h.join().unwrap()).collect()
            });
            parts.into_iter().flatten().collect()
        };
        let e = t.elapsed().as_secs_f64();
        sum = checksum(&outs);
        if e < best {
            best = e;
        }
    }

    // repr-free digest variant: same kernel, signature digest taken over a packed binary encoding.
    let mut best_bin = f64::INFINITY;
    let mut sum_bin = String::new();
    for _ in 0..reps {
        let t = Instant::now();
        let outs = process(&input.rows, &input, true);
        let e = t.elapsed().as_secs_f64();
        sum_bin = checksum(&outs);
        if e < best_bin {
            best_bin = e;
        }
    }

    // translate_outline micro
    let outlines: Vec<&Outline> =
        input.tables[0].values().filter(|v| !v.is_empty()).collect();
    let points: usize = outlines.iter().map(|v| v.iter().map(|(_, p)| p.len()).sum::<usize>()).sum();
    let micro_reps = (400_000 / points.max(1)).max(1);
    let mut sink = 0usize;
    let mut micro = f64::INFINITY;
    for _ in 0..5 {
        let t = Instant::now();
        for _ in 0..micro_reps {
            for v in &outlines {
                sink += translate_outline(v, 3, 5).len();
            }
        }
        let e = t.elapsed().as_secs_f64();
        if e < micro {
            micro = e;
        }
    }
    let calls = outlines.len() * micro_reps;

    println!(
        "{{\"rows\":{},\"threads\":{},\"seconds\":{:.6},\"us_per_row\":{:.4},\"checksum\":\"{}\",\
         \"binary_digest_seconds\":{:.6},\"binary_digest_checksum\":\"{}\",\
         \"translate_outline_us_per_call\":{:.4},\"translate_outline_ns_per_point\":{:.3},\
         \"translate_sink\":{},\"sha256_mb_per_s\":{:.1},\"sha_sink\":{}}}",
        nrows,
        threads,
        best,
        best / nrows as f64 * 1e6,
        sum,
        best_bin,
        sum_bin,
        micro / calls as f64 * 1e6,
        micro / (points * micro_reps) as f64 * 1e9,
        sink,
        sha_mbs,
        sha_sink
    );
}
