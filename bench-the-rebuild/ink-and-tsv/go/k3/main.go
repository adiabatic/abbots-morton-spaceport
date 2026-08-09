// K3 — placed-ink layer, Go port of rebuild/review/ink.py. Same contract as the Rust port: reproduce
// CPython's repr() text exactly so signature_digest / delta_digest come out byte-identical, and fold
// every per-row digest into one printed sha256 so nothing can be optimized away.
package main

import (
	"crypto/sha1"
	"crypto/sha256"
	"encoding/binary"
	"fmt"
	"os"
	"runtime"
	"sort"
	"strconv"
	"sync"
	"time"
)

type Point struct {
	IsNone bool
	X, Y   int32
}

type Contour struct {
	Op  uint32
	Pts []Point
}

type Outline []Contour

type Glyph struct {
	Name             string
	Xoff, Yoff, Xadv int32
}

type Row struct {
	Unit, Config  string
	Before, After []Glyph
}

type Input struct {
	Ops    []string
	Tables []map[string]Outline
	Rows   []Row
}

type reader struct {
	buf []byte
	pos int
}

func (r *reader) u32() uint32 {
	v := binary.LittleEndian.Uint32(r.buf[r.pos:])
	r.pos += 4
	return v
}
func (r *reader) i32() int32 { return int32(r.u32()) }
func (r *reader) u8() byte   { v := r.buf[r.pos]; r.pos++; return v }
func (r *reader) str() string {
	n := int(r.u32())
	s := string(r.buf[r.pos : r.pos+n])
	r.pos += n
	return s
}

func load(path string) *Input {
	raw, err := os.ReadFile(path)
	if err != nil {
		panic(err)
	}
	if string(raw[0:4]) != "K3B1" {
		panic("bad magic")
	}
	r := &reader{raw, 4}
	nops := int(r.u32())
	ops := make([]string, nops)
	for i := range ops {
		ops[i] = r.str()
	}
	ntables := int(r.u32())
	tables := make([]map[string]Outline, ntables)
	for t := 0; t < ntables; t++ {
		n := int(r.u32())
		table := make(map[string]Outline, n*2)
		for i := 0; i < n; i++ {
			name := r.str()
			nc := int(r.u32())
			outline := make(Outline, nc)
			for c := 0; c < nc; c++ {
				op := r.u32()
				np := int(r.u32())
				pts := make([]Point, np)
				for p := 0; p < np; p++ {
					isNone := r.u8() == 1
					x := r.i32()
					y := r.i32()
					pts[p] = Point{isNone, x, y}
				}
				outline[c] = Contour{op, pts}
			}
			table[name] = outline
		}
		tables[t] = table
	}
	nrows := int(r.u32())
	rows := make([]Row, nrows)
	for i := 0; i < nrows; i++ {
		unit := r.str()
		config := r.str()
		_ = r.str()
		sides := make([][]Glyph, 2)
		for s := 0; s < 2; s++ {
			n := int(r.u32())
			run := make([]Glyph, n)
			for g := 0; g < n; g++ {
				run[g] = Glyph{r.str(), r.i32(), r.i32(), r.i32()}
			}
			sides[s] = run
		}
		rows[i] = Row{unit, config, sides[0], sides[1]}
	}
	return &Input{ops, tables, rows}
}

func translateOutline(v Outline, dx, dy int32) Outline {
	out := make(Outline, len(v))
	for i, c := range v {
		pts := make([]Point, len(c.Pts))
		for j, p := range c.Pts {
			if p.IsNone {
				pts[j] = p
			} else {
				pts[j] = Point{false, p.X + dx, p.Y + dy}
			}
		}
		out[i] = Contour{c.Op, pts}
	}
	return out
}

// cmpOutline reproduces Python's lexicographic tuple comparison over (operator, points) pairs. The
// operator index is assigned in lexicographic name order by the exporter, so comparing indices is
// comparing the operator strings.
func cmpOutline(a, b Outline) int {
	n := len(a)
	if len(b) < n {
		n = len(b)
	}
	for i := 0; i < n; i++ {
		if a[i].Op != b[i].Op {
			if a[i].Op < b[i].Op {
				return -1
			}
			return 1
		}
		pa, pb := a[i].Pts, b[i].Pts
		m := len(pa)
		if len(pb) < m {
			m = len(pb)
		}
		for j := 0; j < m; j++ {
			if pa[j] != pb[j] {
				if pa[j].IsNone != pb[j].IsNone {
					if pa[j].IsNone {
						return -1
					}
					return 1
				}
				if pa[j].X != pb[j].X {
					if pa[j].X < pb[j].X {
						return -1
					}
					return 1
				}
				if pa[j].Y < pb[j].Y {
					return -1
				}
				return 1
			}
		}
		if len(pa) != len(pb) {
			if len(pa) < len(pb) {
				return -1
			}
			return 1
		}
	}
	if len(a) != len(b) {
		if len(a) < len(b) {
			return -1
		}
		return 1
	}
	return 0
}

func equalOutline(a, b Outline) bool { return cmpOutline(a, b) == 0 }

func pushInt(out []byte, v int32) []byte { return strconv.AppendInt(out, int64(v), 10) }

func pushPoints(out []byte, pts []Point) []byte {
	out = append(out, '(')
	for i, p := range pts {
		if i > 0 {
			out = append(out, ',', ' ')
		}
		if p.IsNone {
			out = append(out, "None"...)
		} else {
			out = append(out, '(')
			out = pushInt(out, p.X)
			out = append(out, ',', ' ')
			out = pushInt(out, p.Y)
			out = append(out, ')')
		}
	}
	if len(pts) == 1 {
		out = append(out, ',')
	}
	return append(out, ')')
}

func pushPiece(out []byte, piece Outline, ops []string) []byte {
	out = append(out, '(')
	for i, c := range piece {
		if i > 0 {
			out = append(out, ',', ' ')
		}
		out = append(out, '(', '\'')
		out = append(out, ops[c.Op]...)
		out = append(out, '\'', ',', ' ')
		out = pushPoints(out, c.Pts)
		out = append(out, ')')
	}
	if len(piece) == 1 {
		out = append(out, ',')
	}
	return append(out, ')')
}

func pushPieces(out []byte, pieces []Outline, ops []string) []byte {
	out = append(out, '(')
	for i, piece := range pieces {
		if i > 0 {
			out = append(out, ',', ' ')
		}
		out = pushPiece(out, piece, ops)
	}
	if len(pieces) == 1 {
		out = append(out, ',')
	}
	return append(out, ')')
}

func packPiece(out []byte, piece Outline) []byte {
	var scratch [4]byte
	binary.LittleEndian.PutUint32(scratch[:], uint32(len(piece)))
	out = append(out, scratch[:]...)
	for _, c := range piece {
		binary.LittleEndian.PutUint32(scratch[:], c.Op)
		out = append(out, scratch[:]...)
		binary.LittleEndian.PutUint32(scratch[:], uint32(len(c.Pts)))
		out = append(out, scratch[:]...)
		for _, p := range c.Pts {
			if p.IsNone {
				out = append(out, 1, 0, 0, 0, 0, 0, 0, 0, 0)
			} else {
				out = append(out, 0)
				binary.LittleEndian.PutUint32(scratch[:], uint32(p.X))
				out = append(out, scratch[:]...)
				binary.LittleEndian.PutUint32(scratch[:], uint32(p.Y))
				out = append(out, scratch[:]...)
			}
		}
	}
	return out
}

func inkPieces(run []Glyph, table map[string]Outline) []Outline {
	pieces := make([]Outline, 0, len(run))
	var penX int32
	for _, g := range run {
		outline := table[g.Name]
		if len(outline) > 0 {
			pieces = append(pieces, translateOutline(outline, penX+g.Xoff, g.Yoff))
		}
		penX += g.Xadv
	}
	sort.Slice(pieces, func(i, j int) bool { return cmpOutline(pieces[i], pieces[j]) < 0 })
	return pieces
}

type placed struct {
	outline Outline
	pen     int32
}

func runInk(run []Glyph, table map[string]Outline) []placed {
	pieces := make([]placed, 0, len(run))
	var penX int32
	for _, g := range run {
		outline := table[g.Name]
		if len(outline) > 0 {
			pieces = append(pieces, placed{translateOutline(outline, 0, g.Yoff), penX + g.Xoff})
		}
		penX += g.Xadv
	}
	return pieces
}

func configDiff(before, after []placed) ([]Outline, []Outline, int32) {
	start := 0
	for start < len(before) && start < len(after) &&
		before[start].pen == after[start].pen && equalOutline(before[start].outline, after[start].outline) {
		start++
	}
	stripped := 0
	haveShift := false
	var shift int32
	for {
		if len(before)-1-stripped < start || len(after)-1-stripped < start {
			break
		}
		b := before[len(before)-1-stripped]
		a := after[len(after)-1-stripped]
		if !equalOutline(b.outline, a.outline) {
			break
		}
		dx := a.pen - b.pen
		if !haveShift {
			shift = dx
			haveShift = true
		}
		if dx != shift {
			break
		}
		stripped++
	}

	count := func(items []placed) (map[string]int, map[string]Outline) {
		counts := make(map[string]int)
		reps := make(map[string]Outline)
		for _, item := range items {
			piece := translateOutline(item.outline, item.pen, 0)
			key := string(packPiece(nil, piece))
			counts[key]++
			if _, ok := reps[key]; !ok {
				reps[key] = piece
			}
		}
		return counts, reps
	}
	mb, rb := count(before[start : len(before)-stripped])
	ma, ra := count(after[start : len(after)-stripped])
	var beforeOnly, afterOnly []Outline
	for k, v := range mb {
		for i := 0; i < v-ma[k]; i++ {
			beforeOnly = append(beforeOnly, rb[k])
		}
	}
	for k, v := range ma {
		for i := 0; i < v-mb[k]; i++ {
			afterOnly = append(afterOnly, ra[k])
		}
	}
	haveX := false
	var x0 int32
	for _, group := range [][]Outline{beforeOnly, afterOnly} {
		for _, piece := range group {
			for _, c := range piece {
				for _, p := range c.Pts {
					if p.IsNone {
						continue
					}
					if !haveX || p.X < x0 {
						x0 = p.X
						haveX = true
					}
				}
			}
		}
	}
	if !haveX {
		return nil, nil, shift
	}
	normalize := func(pieces []Outline) []Outline {
		out := make([]Outline, len(pieces))
		for i, piece := range pieces {
			out[i] = translateOutline(piece, -x0, 0)
		}
		sort.Slice(out, func(i, j int) bool { return cmpOutline(out[i], out[j]) < 0 })
		return out
	}
	return normalize(beforeOnly), normalize(afterOnly), shift
}

func process(rows []Row, in *Input, binaryDigest bool) []string {
	beforeTable, afterTable := in.Tables[0], in.Tables[1]
	out := make([]string, len(rows))
	buf := make([]byte, 0, 1<<16)
	for i, row := range rows {
		bp := inkPieces(row.Before, beforeTable)
		ap := inkPieces(row.After, afterTable)
		buf = buf[:0]
		if binaryDigest {
			for _, piece := range bp {
				buf = packPiece(buf, piece)
			}
			for _, piece := range ap {
				buf = packPiece(buf, piece)
			}
		} else {
			buf = append(buf, '(')
			buf = pushPieces(buf, bp, in.Ops)
			buf = append(buf, ',', ' ')
			buf = pushPieces(buf, ap, in.Ops)
			buf = append(buf, ')')
		}
		sd := sha256.Sum256(buf)

		br := runInk(row.Before, beforeTable)
		ar := runInk(row.After, afterTable)
		bo, ao, shift := configDiff(br, ar)
		buf = buf[:0]
		buf = append(buf, '(')
		buf = pushPieces(buf, bo, in.Ops)
		buf = append(buf, ',', ' ')
		buf = pushPieces(buf, ao, in.Ops)
		buf = append(buf, ',', ' ')
		buf = pushInt(buf, shift)
		buf = append(buf, ')')
		dd := sha1.Sum(buf)

		out[i] = fmt.Sprintf("%s\t%s\t%x\td-%x\n", row.Unit, row.Config, sd, dd[:6])
	}
	return out
}

func checksum(lines []string) string {
	h := sha256.New()
	for _, l := range lines {
		h.Write([]byte(l))
	}
	return fmt.Sprintf("%x", h.Sum(nil))
}

func main() {
	path := "k3-input.bin"
	if len(os.Args) > 1 {
		path = os.Args[1]
	}
	reps := 3
	if len(os.Args) > 2 {
		reps, _ = strconv.Atoi(os.Args[2])
	}
	threads := 1
	if len(os.Args) > 3 {
		threads, _ = strconv.Atoi(os.Args[3])
	}
	in := load(path)

	blob := make([]byte, 8<<20)
	shaBest := 1e30
	shaSink := 0
	for i := 0; i < 5; i++ {
		runtime.GC()
		t := time.Now()
		d := sha256.Sum256(blob)
		e := time.Since(t).Seconds()
		shaSink += int(d[0])
		if e < shaBest {
			shaBest = e
		}
	}
	shaMBs := (float64(len(blob)) / (1 << 20)) / shaBest

	best := 1e30
	var sum string
	for r := 0; r < reps; r++ {
		runtime.GC()
		t := time.Now()
		var lines []string
		if threads <= 1 {
			lines = process(in.Rows, in, false)
		} else {
			chunk := (len(in.Rows) + threads - 1) / threads
			parts := make([][]string, threads)
			var wg sync.WaitGroup
			for w := 0; w < threads; w++ {
				lo := w * chunk
				if lo >= len(in.Rows) {
					break
				}
				hi := lo + chunk
				if hi > len(in.Rows) {
					hi = len(in.Rows)
				}
				wg.Add(1)
				go func(w, lo, hi int) {
					defer wg.Done()
					parts[w] = process(in.Rows[lo:hi], in, false)
				}(w, lo, hi)
			}
			wg.Wait()
			for _, p := range parts {
				lines = append(lines, p...)
			}
		}
		e := time.Since(t).Seconds()
		sum = checksum(lines)
		if e < best {
			best = e
		}
	}

	bestBin := 1e30
	var sumBin string
	for r := 0; r < reps; r++ {
		runtime.GC()
		t := time.Now()
		lines := process(in.Rows, in, true)
		e := time.Since(t).Seconds()
		sumBin = checksum(lines)
		if e < bestBin {
			bestBin = e
		}
	}

	var outlines []Outline
	points := 0
	for _, v := range in.Tables[0] {
		if len(v) > 0 {
			outlines = append(outlines, v)
			for _, c := range v {
				points += len(c.Pts)
			}
		}
	}
	microReps := 400000 / points
	if microReps < 1 {
		microReps = 1
	}
	sink := 0
	micro := 1e30
	for i := 0; i < 5; i++ {
		runtime.GC()
		t := time.Now()
		for r := 0; r < microReps; r++ {
			for _, v := range outlines {
				sink += len(translateOutline(v, 3, 5))
			}
		}
		if e := time.Since(t).Seconds(); e < micro {
			micro = e
		}
	}
	calls := len(outlines) * microReps

	fmt.Printf("{\"rows\":%d,\"threads\":%d,\"seconds\":%.6f,\"us_per_row\":%.4f,\"checksum\":\"%s\","+
		"\"binary_digest_seconds\":%.6f,\"binary_digest_checksum\":\"%s\","+
		"\"translate_outline_us_per_call\":%.4f,\"translate_outline_ns_per_point\":%.3f,"+
		"\"translate_sink\":%d,\"sha256_mb_per_s\":%.1f,\"sha_sink\":%d}\n",
		len(in.Rows), threads, best, best/float64(len(in.Rows))*1e6, sum,
		bestBin, sumBin,
		micro/float64(calls)*1e6, micro/float64(points*microReps)*1e9, sink, shaMBs, shaSink)
}
