// k1-micro: primitive-operation benchmark, Go side. Stdlib only. Built with a
// normal `go build` -- no non-default flags, GC left on at the default GOGC.
//
// Every kernel mirrors bench.py's loop skeleton: iterate a prebuilt slice, store
// the product into a preallocated heap buffer, and fold a cheap portable integer
// into an accumulator that is printed and checked against Python's. Dead-code
// elimination is defeated by the escaping store into a package-level sink plus
// the printed accumulator; Go's escape analysis cannot prove a package-level
// store dead.
//
// Strong checksums come from separate, untimed verify passes using the same
// 64-bit mixer as bench.py: h = (h ^ v) * 0x100000001b3.
package main

import (
	"fmt"
	"hash/maphash"
	"os"
	"runtime"
	"slices"
	"sort"
	"strconv"
	"strings"
	"time"
)

const (
	prime      = 0x100000001b3
	fnvOffset  = 0xcbf29ce484222325
	M1         = 400_000
	M8         = 700_000
	NPROBE     = 1_000_000
	NALLOC     = 10_000_000
	NLISTS     = 60_000
	NLEGACY    = 500_000
	REPS       = 5
	MEDREPS    = 3
	HEAVYREPS  = 2
	WARMUP     = 1
	VERIFYCAP  = 200_000
	PROBEMUL   = 2654435761
	NULLSTRING = "" // no real symbol is empty; gen_data.py asserts it
)

func mix(h, v uint64) uint64 { return (h ^ v) * prime }

type R8 struct {
	a, b, c, d, e  string
	n1, n2, n3     int32
}

type Cand5 struct {
	stance, entry, seam    string
	orderIndex, exitIndex  int32
}

type Key10 [10]string

type Cand struct {
	st             uint8
	sy, ei, oi, jc int32
}

// package-level sinks: an escaping store the compiler cannot elide
var (
	sinkR8    R8
	sinkPtr   *R8
	sinkU64   uint64
	sinkSlice []R8
	sinkMapLen int
)

type row struct {
	op      string
	ops     int
	raw     float64
	minv    float64
	maxv    float64
	spread  float64
	control float64
	net     float64
	reps    int
	acc     uint64
	extra   [][2]string
}

var rows []row

func bench(name string, ops, reps int, f func() uint64, ctl func() uint64, extra [][2]string) {
	var acc uint64
	for i := 0; i < WARMUP; i++ {
		acc = f()
	}
	sinkU64 ^= acc
	raw := make([]int64, 0, reps)
	for i := 0; i < reps; i++ {
		t := time.Now()
		acc = f()
		raw = append(raw, time.Since(t).Nanoseconds())
		sinkU64 ^= acc
	}
	var cn []int64
	if ctl != nil {
		for i := 0; i < WARMUP; i++ {
			sinkU64 ^= ctl()
		}
		for i := 0; i < reps; i++ {
			t := time.Now()
			sinkU64 ^= ctl()
			cn = append(cn, time.Since(t).Nanoseconds())
		}
	}
	slices.Sort(raw)
	med := float64(raw[len(raw)/2])
	cmed, net := 0.0, med
	if len(cn) > 0 {
		slices.Sort(cn)
		cmed = float64(cn[len(cn)/2])
		net = med - cmed
	}
	n := float64(ops)
	r := row{name, ops, med / n, float64(raw[0]) / n, float64(raw[len(raw)-1]) / n,
		100 * float64(raw[len(raw)-1]-raw[0]) / med, cmed / n, net / n, reps, acc, extra}
	fmt.Fprintf(os.Stderr, "  %-44s net %9.2f  raw %9.2f ns/op\n", r.op, r.net, r.raw)
	rows = append(rows, r)
}

func ex(kv ...string) [][2]string {
	out := make([][2]string, 0, len(kv)/2)
	for i := 0; i+1 < len(kv); i += 2 {
		out = append(out, [2]string{kv[i], kv[i+1]})
	}
	return out
}

func main() {
	dir := "../data"
	out := "../out/go.json"
	if len(os.Args) > 1 {
		dir = os.Args[1]
	}
	if len(os.Args) > 2 {
		out = os.Args[2]
	}

	symText, err := os.ReadFile(dir + "/symbols.txt")
	must(err)
	syms := strings.Split(strings.TrimRight(string(symText), "\n"), "\n")
	if syms[0] != "-" {
		panic("symbol 0 must be the null marker")
	}
	symID := map[string]uint64{}
	for i, s := range syms {
		symID[s] = uint64(i)
	}
	symN := make([]string, len(syms))
	copy(symN, syms)
	symN[0] = NULLSTRING

	gbuf, err := os.ReadFile(dir + "/keys-global.u8")
	must(err)
	pbuf, err := os.ReadFile(dir + "/keys-packed.u64")
	must(err)
	n := len(gbuf) / 10
	fmt.Fprintf(os.Stderr, "go: N=%d symbols=%d\n", n, len(syms))

	packed := make([]uint64, n)
	for i := range packed {
		var v uint64
		for k := 0; k < 8; k++ {
			v |= uint64(pbuf[i*8+k]) << (8 * k)
		}
		packed[i] = v
	}
	keys := make([]Key10, n)
	ids10 := make([][10]byte, n)
	for i := 0; i < n; i++ {
		for s := 0; s < 10; s++ {
			keys[i][s] = symN[gbuf[i*10+s]]
			ids10[i][s] = gbuf[i*10+s]
		}
	}

	mkR8 := func(i int) R8 {
		k := keys[i]
		n1, e := strconv.Atoi(k[4])
		must(e)
		return R8{a: k[0], b: k[5], c: k[1], d: k[2], e: k[3],
			n1: int32(n1), n2: int32(i % 97), n3: int32(i % 13)}
	}
	fields := make([]R8, M1)
	for i := range fields {
		fields[i] = mkR8(i)
	}

	verify := func(buf []R8) uint64 {
		h := uint64(fnvOffset)
		lim := VERIFYCAP
		if len(buf) < lim {
			lim = len(buf)
		}
		for _, r := range buf[:lim] {
			for _, v := range []string{r.a, r.b, r.c, r.d, r.e} {
				h = mix(h, symID[nz(v)])
			}
			h = mix(h, uint64(int64(r.n1)))
			h = mix(h, uint64(int64(r.n2)))
			h = mix(h, uint64(int64(r.n3)))
		}
		return h
	}

	// ------------------------------------------------ B1 construct 8 fields
	fmt.Fprintln(os.Stderr, "B1 construct 8-field record")
	{
		buf := make([]R8, M1)
		run := func() uint64 {
			var acc uint64
			for j := range fields {
				r := &fields[j]
				buf[j] = R8{a: r.a, b: r.b, c: r.c, d: r.d, e: r.e, n1: r.n1, n2: r.n2, n3: r.n3}
				acc ^= uint64(r.n2)
			}
			sinkR8 = buf[M1-1]
			return acc
		}
		run()
		cksum := verify(buf)
		buf2 := make([]R8, M1)
		ctl := func() uint64 {
			var acc uint64
			for j := range fields {
				r := &fields[j]
				buf2[j].a = r.a
				acc ^= uint64(r.n2)
			}
			sinkR8 = buf2[M1-1]
			return acc
		}
		bench("construct8/struct-copy", M1, REPS, run, ctl,
			ex("checksum", fmt.Sprint(cksum)))
	}

	// --------------------------------------- B1x legacy 5-field Candidate
	fmt.Fprintln(os.Stderr, "B1x legacy 5-field Candidate")
	var candRows [][]string
	{
		ctext, err := os.ReadFile(dir + "/candidates.tsv")
		must(err)
		for _, l := range strings.Split(strings.TrimRight(string(ctext), "\n"), "\n")[1:] {
			if l != "" {
				candRows = append(candRows, strings.Split(l, "\t"))
			}
		}
		cf := make([]Cand5, len(candRows))
		for i, r := range candRows {
			cf[i] = Cand5{r[1], dashNull(r[2]), dashNull(r[3]), int32(i), int32(i + 1)}
		}
		repsIn := NLEGACY / len(cf)
		nops := repsIn * len(cf)
		buf := make([]Cand5, len(cf))
		buf2 := make([]Cand5, len(cf))
		run := func() uint64 {
			var acc uint64
			for r := 0; r < repsIn; r++ {
				for j := range cf {
					c := &cf[j]
					buf[j] = Cand5{c.stance, c.entry, c.seam, c.orderIndex, c.exitIndex}
					acc ^= uint64(c.orderIndex)
				}
			}
			sinkU64 ^= uint64(buf[0].orderIndex)
			return acc
		}
		ctl := func() uint64 {
			var acc uint64
			for r := 0; r < repsIn; r++ {
				for j := range cf {
					c := &cf[j]
					buf2[j].stance = c.stance
					acc ^= uint64(c.orderIndex)
				}
			}
			sinkU64 ^= uint64(buf2[0].orderIndex)
			return acc
		}
		bench("legacy5/struct-construct", nops, MEDREPS, run, ctl, nil)

		seed := maphash.MakeSeed()
		pre := make([]uint64, len(cf))
		for i := range cf {
			pre[i] = maphash.Comparable(seed, cf[i])
		}
		run = func() uint64 {
			var acc uint64
			for r := 0; r < repsIn; r++ {
				for j := range cf {
					acc ^= maphash.Comparable(seed, cf[j])
				}
			}
			return acc
		}
		ctl = func() uint64 {
			var acc uint64
			for r := 0; r < repsIn; r++ {
				for _, h := range pre {
					acc ^= h
				}
			}
			return acc
		}
		bench("legacy5/struct-hash-maphash", nops, MEDREPS, run, ctl, nil)
	}

	// ---------------------------------------------------- B2 hash 8-field
	fmt.Fprintln(os.Stderr, "B2 hash 8-field record")
	{
		seed := maphash.MakeSeed()
		pre := make([]uint64, M1)
		for i := range fields {
			pre[i] = maphash.Comparable(seed, fields[i])
		}
		seen := map[uint64]struct{}{}
		for _, h := range pre {
			seen[h] = struct{}{}
		}
		run := func() uint64 {
			var acc uint64
			for i := range fields {
				acc ^= maphash.Comparable(seed, fields[i])
			}
			return acc
		}
		ctl := func() uint64 {
			var acc uint64
			for _, h := range pre {
				acc ^= h
			}
			return acc
		}
		bench("hash8/struct-maphash", M1, REPS, run, ctl,
			ex("distinct_hash_values", fmt.Sprint(len(seen))))
	}

	// ------------------------------------------------------- B3 equality
	fmt.Fprintln(os.Stderr, "B3 equality compare")
	{
		y := make([]R8, M1)
		copy(y, fields)
		z := make([]R8, M1)
		copy(z, fields[1:])
		z[M1-1] = fields[0]
		run := func() uint64 {
			var c uint64
			for j := 0; j < M1; j++ {
				if fields[j] == y[j] {
					c++
				}
			}
			return c
		}
		ctl := func() uint64 {
			var c uint64
			for j := 0; j < M1; j++ {
				if fields[j].n2 == y[j].n2 {
					c++
				}
			}
			return c
		}
		bench("eq8/struct-equal", M1, REPS, run, ctl, nil)
		run = func() uint64 {
			var c uint64
			for j := 0; j < M1; j++ {
				if fields[j] == z[j] {
					c++
				}
			}
			return c
		}
		ctl = func() uint64 {
			var c uint64
			for j := 0; j < M1; j++ {
				if fields[j].n2 == z[j].n2 {
					c++
				}
			}
			return c
		}
		bench("eq8/struct-unequal", M1, REPS, run, ctl, nil)
	}

	// -------------------------------------- B5 strings vs u8 symbol ids
	fmt.Fprintln(os.Stderr, "B5 interned strings vs u8 symbol ids")
	{
		seed := maphash.MakeSeed()
		a := keys[:M1]
		b := make([]Key10, M1)
		copy(b, a)
		ia := ids10[:M1]
		ib := make([][10]byte, M1)
		copy(ib, ia)
		pa := packed[:M1]
		pb := make([]uint64, M1)
		copy(pb, pa)

		bench("sym/eq-10str-tuple", M1, REPS, func() uint64 {
			var c uint64
			for j := 0; j < M1; j++ {
				if a[j] == b[j] {
					c++
				}
			}
			return c
		}, func() uint64 {
			var c uint64
			for j := 0; j < M1; j++ {
				if a[j][0] == b[j][0] {
					c++
				}
			}
			return c
		}, nil)
		bench("sym/eq-10u8-bytes", M1, REPS, func() uint64 {
			var c uint64
			for j := 0; j < M1; j++ {
				if ia[j] == ib[j] {
					c++
				}
			}
			return c
		}, func() uint64 {
			var c uint64
			for j := 0; j < M1; j++ {
				if ia[j][0] == ib[j][0] {
					c++
				}
			}
			return c
		}, nil)
		bench("sym/eq-packed-u64", M1, REPS, func() uint64 {
			var c uint64
			for j := 0; j < M1; j++ {
				if pa[j] == pb[j] {
					c++
				}
			}
			return c
		}, func() uint64 {
			var c uint64
			for j := 0; j < M1; j++ {
				if pa[j]&0xff == pb[j]&0xff {
					c++
				}
			}
			return c
		}, nil)

		pre := make([]uint64, M1)
		for i := range a {
			pre[i] = maphash.Comparable(seed, a[i])
		}
		hctl := func() uint64 {
			var acc uint64
			for _, h := range pre {
				acc ^= h
			}
			return acc
		}
		bench("sym/hash-10str-tuple", M1, REPS, func() uint64 {
			var acc uint64
			for i := range a {
				acc ^= maphash.Comparable(seed, a[i])
			}
			return acc
		}, hctl, nil)
		bench("sym/hash-10u8-bytes", M1, REPS, func() uint64 {
			var acc uint64
			for i := range ia {
				acc ^= maphash.Comparable(seed, ia[i])
			}
			return acc
		}, hctl, nil)
		bench("sym/hash-packed-u64", M1, REPS, func() uint64 {
			var acc uint64
			for i := range pa {
				acc ^= maphash.Comparable(seed, pa[i])
			}
			return acc
		}, hctl, nil)
	}

	// ---------------------------------------------------------- B6 ranking
	fmt.Fprintln(os.Stderr, "B6 rank a 3-8 candidate list")
	{
		stances := []string{}
		for _, r := range candRows {
			stances = append(stances, r[1])
		}
		sort.Strings(stances)
		stances = slices.Compact(stances)
		type triple struct{ st uint8; sy, cnt int32 }
		base := make([]triple, len(candRows))
		for i, r := range candRows {
			st := slices.Index(stances, r[1])
			var sy int32
			switch r[3] {
			case "-":
				sy = -1
			case "ex-y0":
				sy = 0
			case "ex-y5":
				sy = 5
			case "ex-y6":
				sy = 6
			default:
				panic("seam " + r[3])
			}
			c, e := strconv.Atoi(r[4])
			must(e)
			base[i] = triple{uint8(st), sy, int32(c)}
		}
		nc := len(base)
		lists := make([][]Cand, NLISTS)
		for j := 0; j < NLISTS; j++ {
			l := 3 + j%6
			item := make([]Cand, l)
			for k := 0; k < l; k++ {
				t := base[(j*7+k*13)%nc]
				item[k] = Cand{t.st, t.sy, int32(k), int32((j + k) % 11), t.cnt % 5}
			}
			lists[j] = item
		}
		floorCmp := func(x, y Cand) int {
			xj, yj := b2i(x.sy < 0), b2i(y.sy < 0)
			if xj != yj {
				return xj - yj
			}
			xs, ys := x.sy, y.sy
			if xs < 0 {
				xs = 1_000_000
			}
			if ys < 0 {
				ys = 1_000_000
			}
			if xs != ys {
				return int(xs - ys)
			}
			return int(x.ei - y.ei)
		}
		rankCmp := func(x, y Cand) int {
			if x.jc != y.jc {
				return int(y.jc - x.jc)
			}
			if x.oi != y.oi {
				return int(x.oi - y.oi)
			}
			return int(x.ei - y.ei)
		}
		h := uint64(fnvOffset)
		var sc [8]Cand
		for _, item := range lists {
			l := len(item)
			copy(sc[:l], item)
			slices.SortStableFunc(sc[:l], floorCmp)
			o0, o1 := sc[0], sc[1]
			copy(sc[:l], item)
			slices.SortStableFunc(sc[:l], rankCmp)
			r0 := sc[0]
			h = mix(h, uint64(o0.st))
			h = mix(h, uint64(int64(o1.ei)))
			h = mix(h, uint64(r0.st))
			h = mix(h, uint64(int64(r0.oi)))
		}
		run := func() uint64 {
			var acc uint64
			var s [8]Cand
			for _, item := range lists {
				l := len(item)
				copy(s[:l], item)
				slices.SortStableFunc(s[:l], floorCmp)
				acc ^= uint64(s[0].ei*3 + s[1].ei*5)
				copy(s[:l], item)
				slices.SortStableFunc(s[:l], rankCmp)
				acc ^= uint64(s[0].oi * 7)
			}
			sinkU64 ^= uint64(s[0].ei)
			return acc
		}
		ctl := func() uint64 {
			var acc uint64
			for _, item := range lists {
				acc ^= uint64(item[0].ei*3 + item[1].ei*5)
				acc ^= uint64(item[0].oi * 7)
			}
			return acc
		}
		bench("rank/two-stable-sorts-per-list", NLISTS, REPS, run, ctl,
			ex("checksum", fmt.Sprint(h), "lists", fmt.Sprint(NLISTS)))
	}

	// ----------------------------------------------------------- B8 filter
	fmt.Fprintln(os.Stderr, "B8 filter a 700k-row table")
	{
		table := make([]R8, M8)
		for i := range table {
			table[i] = mkR8(i)
		}
		keep := func(r *R8) bool {
			return r.c != NULLSTRING && r.d != NULLSTRING &&
				(r.b == "qsNo" || r.b == "qsMay" || r.b == "qsPea") &&
				r.n1 >= 0 && r.a != "space"
		}
		var matched uint64
		h := uint64(fnvOffset)
		for i := range table {
			r := &table[i]
			if keep(r) {
				matched++
				for _, v := range []string{r.a, r.b, r.c, r.d, r.e} {
					h = mix(h, symID[nz(v)])
				}
				h = mix(h, uint64(int64(r.n1)))
				h = mix(h, uint64(int64(r.n2)))
				h = mix(h, uint64(int64(r.n3)))
			}
		}
		run := func() uint64 {
			var c, acc uint64
			for i := range table {
				r := &table[i]
				if keep(r) {
					c++
					acc ^= uint64(r.n2)
				}
			}
			return (c << 8) ^ acc
		}
		ctl := func() uint64 {
			var c uint64
			for i := range table {
				sinkPtr = &table[i]
				c++
			}
			return c << 8
		}
		bench("filter700k/struct-slice", M8, MEDREPS, run, ctl,
			ex("matched", fmt.Sprint(matched), "checksum", fmt.Sprint(h)))
	}

	// -------------------------------------------------------------- B4 map
	fmt.Fprintln(os.Stderr, "B4 map with a 10-slot optional-string key")
	{
		bench("map10str/insert", n, HEAVYREPS, func() uint64 {
			m := map[Key10]uint32{}
			for i := range keys {
				m[keys[i]] = uint32(i)
			}
			sinkMapLen = len(m)
			return uint64(len(m))
		}, nil, nil)
		bench("map10str/insert-presized", n, HEAVYREPS, func() uint64 {
			m := make(map[Key10]uint32, n)
			for i := range keys {
				m[keys[i]] = uint32(i)
			}
			sinkMapLen = len(m)
			return uint64(len(m))
		}, nil, nil)

		store := make(map[Key10]uint32, n)
		for i := range keys {
			store[keys[i]] = uint32(i)
		}
		probes := make([]Key10, NPROBE)
		for p := 0; p < NPROBE; p++ {
			k := keys[(uint64(p)*PROBEMUL)%uint64(n)]
			if p%4 == 3 {
				k[9] = "MISS"
			}
			probes[p] = k
		}
		var hits, sum uint64
		for _, k := range probes {
			if v, ok := store[k]; ok {
				hits++
				sum += uint64(v)
			}
		}
		bench("map10str/lookup", NPROBE, MEDREPS, func() uint64 {
			var acc, hh uint64
			for i := range probes {
				if v, ok := store[probes[i]]; ok {
					hh++
					acc += uint64(v)
				}
			}
			return (acc << 1) ^ hh
		}, func() uint64 {
			var acc uint64
			for i := range probes {
				sinkU64 ^= uint64(len(probes[i][0]))
				acc ^= 1
			}
			return acc
		}, ex("hits", fmt.Sprint(hits), "checksum", fmt.Sprint(sum)))

		bench("mapU64/insert", n, HEAVYREPS, func() uint64 {
			m := map[uint64]uint32{}
			for i, k := range packed {
				m[k] = uint32(i)
			}
			sinkMapLen = len(m)
			return uint64(len(m))
		}, nil, nil)
		bench("mapU64/insert-presized", n, HEAVYREPS, func() uint64 {
			m := make(map[uint64]uint32, n)
			for i, k := range packed {
				m[k] = uint32(i)
			}
			sinkMapLen = len(m)
			return uint64(len(m))
		}, nil, nil)

		storep := make(map[uint64]uint32, n)
		for i, k := range packed {
			storep[k] = uint32(i)
		}
		probesp := make([]uint64, NPROBE)
		for p := 0; p < NPROBE; p++ {
			k := packed[(uint64(p)*PROBEMUL)%uint64(n)]
			if p%4 == 3 {
				k = (k &^ (31 << 45)) | (31 << 45)
			}
			probesp[p] = k
		}
		var hitsp, sump uint64
		for _, k := range probesp {
			if v, ok := storep[k]; ok {
				hitsp++
				sump += uint64(v)
			}
		}
		bench("mapU64/lookup", NPROBE, MEDREPS, func() uint64 {
			var acc, hh uint64
			for _, k := range probesp {
				if v, ok := storep[k]; ok {
					hh++
					acc += uint64(v)
				}
			}
			return (acc << 1) ^ hh
		}, func() uint64 {
			var acc uint64
			for _, k := range probesp {
				sinkU64 ^= k & 1
				acc ^= 1
			}
			return acc
		}, ex("hits", fmt.Sprint(hitsp), "checksum", fmt.Sprint(sump)))
	}

	// ------------------------------------------------------------ B7 alloc
	fmt.Fprintln(os.Stderr, "B7 allocate and drop 10M small objects")
	{
		f0 := fields[0]
		var ptrs [8]*R8
		bench("alloc10M/pointer-gc", NALLOC, HEAVYREPS, func() uint64 {
			var acc uint64
			for i := 0; i < NALLOC; i++ {
				n2 := int32(i % 97)
				r := f0
				r.n2, r.n3 = n2, int32(i%13)
				ptrs[i&7] = &r
				acc ^= uint64(n2)
			}
			sinkPtr = ptrs[0]
			return acc
		}, func() uint64 {
			var acc uint64
			var slots [8]R8
			for i := 0; i < NALLOC; i++ {
				n2 := int32(i % 97)
				slots[i&7].n2 = n2
				acc ^= uint64(n2)
			}
			sinkR8 = slots[0]
			return acc
		}, nil)

		bench("alloc10M/by-value-no-alloc", NALLOC, HEAVYREPS, func() uint64 {
			var acc uint64
			var slots [8]R8
			for i := 0; i < NALLOC; i++ {
				n2 := int32(i % 97)
				r := f0
				r.n2, r.n3 = n2, int32(i%13)
				slots[i&7] = r
				acc ^= uint64(n2)
			}
			sinkR8 = slots[0]
			return acc
		}, func() uint64 {
			var acc uint64
			var slots [8]R8
			for i := 0; i < NALLOC; i++ {
				n2 := int32(i % 97)
				slots[i&7].n2 = n2
				acc ^= uint64(n2)
			}
			sinkR8 = slots[0]
			return acc
		}, nil)

		bench("alloc10M/arena-append", NALLOC, HEAVYREPS, func() uint64 {
			arena := make([]R8, 0, NALLOC)
			var acc uint64
			for i := 0; i < NALLOC; i++ {
				n2 := int32(i % 97)
				r := f0
				r.n2, r.n3 = n2, int32(i%13)
				arena = append(arena, r)
				acc ^= uint64(n2)
			}
			sinkSlice = arena
			sinkR8 = arena[0]
			return acc
		}, nil, nil)
	}

	runtime.KeepAlive(sinkSlice)
	var sb strings.Builder
	sb.WriteString("{\n \"lang\": \"go\",\n \"runtime\": \"" + runtime.Version() + "\",\n")
	fmt.Fprintf(&sb, " \"n_keys\": %d,\n \"results\": [\n", n)
	for i, r := range rows {
		fmt.Fprintf(&sb, "  {\"op\": \"%s\", \"lang\": \"go\", \"ops\": %d, \"raw_ns_per_op\": %.4f, \"min_ns_per_op\": %.4f, \"max_ns_per_op\": %.4f, \"spread_pct\": %.2f, \"control_ns_per_op\": %.4f, \"net_ns_per_op\": %.4f, \"reps\": %d, \"acc\": \"%d\"",
			r.op, r.ops, r.raw, r.minv, r.maxv, r.spread, r.control, r.net, r.reps, r.acc)
		for _, kv := range r.extra {
			fmt.Fprintf(&sb, ", \"%s\": \"%s\"", kv[0], kv[1])
		}
		sb.WriteString("}")
		if i+1 < len(rows) {
			sb.WriteString(",")
		}
		sb.WriteString("\n")
	}
	sb.WriteString(" ]\n}\n")
	must(os.WriteFile(out, []byte(sb.String()), 0o644))
	fmt.Fprintln(os.Stderr, "go bench written to "+out)
}

func nz(s string) string {
	if s == NULLSTRING {
		return "-"
	}
	return s
}

func dashNull(s string) string {
	if s == "-" {
		return NULLSTRING
	}
	return s
}

func b2i(b bool) int {
	if b {
		return 1
	}
	return 0
}

func must(err error) {
	if err != nil {
		panic(err)
	}
}
