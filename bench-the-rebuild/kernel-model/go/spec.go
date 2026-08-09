package main

// Spec loading: the same flat integer text file model.py and the Rust port read, into packed structs.

import (
	"fmt"
	"os"
	"strconv"
	"strings"
)

const (
	NoneH  int8 = -1
	UnsetH int8 = -2

	KLetter uint8 = 0
	KEdge   uint8 = 1
	KSpace  uint8 = 2
	KZwnj   uint8 = 3
	KNamer  uint8 = 4

	TokEdge    uint8 = 20
	TokSpace   uint8 = 21
	TokZwnj    uint8 = 22
	TokNamer   uint8 = 23
	TokUnknown uint8 = 24
	NoTok      uint8 = 255
)

func tokKind(t uint8) uint8 {
	if t < 20 {
		return KLetter
	}
	return t - 19
}

func tokIsLetter(t uint8) bool { return t < 20 }

type Condition struct {
	Family    uint32
	HasFamily bool
	Klass     []uint8
	StanceM   uint8
	JoinedAt  int8
	Stroke    int8
	IsToken   int8
	Except    []Condition
	Then      *Condition
}

type When struct {
	Left      *Condition
	Right     *Condition
	SelfEntry int8
	SelfExit  int8
	Word      int8
	Feature   int8
}

type SurfaceRow struct {
	Height     int8
	Selectable bool
	Scope      []Condition
	Prov       uint32
}

type Unlock struct {
	Feature    int8
	Entry      int8
	Exit       int8
	HasPairing bool
	Pairing    [2]int8
	When       *When
	Prov       uint32
}

type Stance struct {
	Name         uint8
	Entries      []SurfaceRow
	Exits        []SurfaceRow
	Never        [][2]int8
	Only         [][2]int8
	HasOnly      bool
	Unlocks      []Unlock
	RequireEntry bool
	RequireExit  bool
}

type PolicyRecord struct {
	When     When
	Stance   int8
	Entry    int8
	Exit     int8
	HasEntry bool
	HasExit  bool
	HasCell  bool
	Cell     [2]int8
	HasOver  bool
	Over     [2]int8
	Absolute bool
	By       int8
	Ident    uint32
	Weight   int32
	Prov     uint32
}

type Rune_ struct {
	Index        uint8
	HasSeq       bool
	Seq          [2]uint8
	Stances      []Stance
	Order        []uint8
	Refuse       []PolicyRecord
	Prefer       []PolicyRecord
	Extend       []PolicyRecord
	Contract     []PolicyRecord
	EntryStrokes uint8
	EntryBearing bool
	MinStroke    int8
	FeatureMask  uint8
}

type Spec struct {
	NLetters  int
	Runes     []Rune_
	Order     []uint8
	Classes   []uint32
	ProvCount int
}

func heightName(h int8) string {
	switch h {
	case 0:
		return "baseline"
	case 1:
		return "x-height"
	case 2:
		return "y6"
	case 3:
		return "top"
	}
	return "none"
}

func heightY(h int8) int32 {
	switch h {
	case 0:
		return 0
	case 1:
		return 5
	case 2:
		return 6
	case 3:
		return 8
	}
	return 1000000
}

type toks struct {
	parts []string
	i     int
}

func (t *toks) nx() int64 {
	v, err := strconv.ParseInt(t.parts[t.i], 10, 64)
	if err != nil {
		panic(err)
	}
	t.i++
	return v
}

func LoadSpec(path string, nLetters int) *Spec {
	raw, err := os.ReadFile(path)
	if err != nil {
		panic(err)
	}
	var conds []Condition
	var whens []When
	var classes []uint32
	var runes []Rune_
	var orderList []uint8
	nStances := make([]int, 64)
	var ident uint32
	provCount := 0
	var letterMask uint32
	if nLetters >= 32 {
		letterMask = ^uint32(0)
	} else {
		letterMask = (uint32(1) << uint(nLetters)) - 1
	}
	prov := func(string) uint32 {
		provCount++
		return uint32(provCount - 1)
	}
	condOf := func(i int64) *Condition {
		if i >= 0 {
			c := conds[i]
			return &c
		}
		return nil
	}
	ensure := func(idx int) {
		for len(runes) <= idx {
			runes = append(runes, Rune_{Index: uint8(len(runes)), MinStroke: -1})
		}
	}

	for _, line := range strings.Split(string(raw), "\n") {
		parts := strings.Fields(line)
		if len(parts) == 0 {
			continue
		}
		key := parts[0]
		t := &toks{parts: parts, i: 1}
		switch key {
		case "header":
			t.nx()
		case "class":
			t.nx()
			classes = append(classes, uint32(t.nx())&letterMask)
		case "rune":
			idx := int(t.nx())
			isliga := t.nx()
			a := t.nx()
			b := t.nx()
			n := int(t.nx())
			ensure(idx)
			if isliga != 0 {
				runes[idx].HasSeq = true
				runes[idx].Seq = [2]uint8{uint8(a), uint8(b)}
			}
			nStances[idx] = n
			orderList = append(orderList, uint8(idx))
		case "order":
			idx := int(t.nx())
			o := make([]uint8, 0, nStances[idx])
			for k := 0; k < nStances[idx]; k++ {
				o = append(o, uint8(t.nx()))
			}
			runes[idx].Order = o
		case "strokes":
			idx := int(t.nx())
			m := uint8(t.nx())
			runes[idx].EntryStrokes = m
			runes[idx].MinStroke = -1
			for b := 0; b < 8; b++ {
				if (m>>uint(b))&1 == 1 {
					runes[idx].MinStroke = int8(b)
					break
				}
			}
		case "cond":
			t.nx()
			famRaw := uint32(t.nx())
			nk := int(t.nx())
			klass := make([]uint8, 0, nk)
			for k := 0; k < nk; k++ {
				klass = append(klass, uint8(t.nx()))
			}
			smask := uint8(t.nx())
			ja := int8(t.nx())
			st := int8(t.nx())
			it := int8(t.nx())
			ne := int(t.nx())
			ex := make([]Condition, 0, ne)
			for k := 0; k < ne; k++ {
				ex = append(ex, conds[t.nx()])
			}
			th := t.nx()
			fam := famRaw & letterMask
			conds = append(conds, Condition{
				Family: fam, HasFamily: fam != 0, Klass: klass, StanceM: smask,
				JoinedAt: ja, Stroke: st, IsToken: it, Except: ex, Then: condOf(th),
			})
		case "when":
			t.nx()
			left := t.nx()
			right := t.nx()
			whens = append(whens, When{
				Left: condOf(left), Right: condOf(right),
				SelfEntry: int8(t.nx()), SelfExit: int8(t.nx()), Word: int8(t.nx()), Feature: int8(t.nx()),
			})
		case "stance":
			r := int(t.nx())
			sname := uint8(t.nx())
			reqE := t.nx()
			reqX := t.nx()
			ne := int(t.nx())
			entries := make([]SurfaceRow, 0, ne)
			for k := 0; k < ne; k++ {
				h := int8(t.nx())
				sel := t.nx()
				ns := int(t.nx())
				scope := make([]Condition, 0, ns)
				for j := 0; j < ns; j++ {
					scope = append(scope, conds[t.nx()])
				}
				pid := prov(fmt.Sprintf("qs%02d.yaml:stances.st%d.entries.%s", r, sname, heightName(h)))
				entries = append(entries, SurfaceRow{Height: h, Selectable: sel != 0, Scope: scope, Prov: pid})
			}
			nx := int(t.nx())
			exits := make([]SurfaceRow, 0, nx)
			for k := 0; k < nx; k++ {
				h := int8(t.nx())
				ns := int(t.nx())
				scope := make([]Condition, 0, ns)
				for j := 0; j < ns; j++ {
					scope = append(scope, conds[t.nx()])
				}
				pid := prov(fmt.Sprintf("qs%02d.yaml:stances.st%d.exits.%s", r, sname, heightName(h)))
				exits = append(exits, SurfaceRow{Height: h, Selectable: true, Scope: scope, Prov: pid})
			}
			nn := int(t.nx())
			never := make([][2]int8, 0, nn)
			for k := 0; k < nn; k++ {
				never = append(never, [2]int8{int8(t.nx()), int8(t.nx())})
			}
			hasOnly := t.nx()
			no := int(t.nx())
			only := make([][2]int8, 0, no)
			for k := 0; k < no; k++ {
				only = append(only, [2]int8{int8(t.nx()), int8(t.nx())})
			}
			nu := int(t.nx())
			unlocks := make([]Unlock, 0, nu)
			for k := 0; k < nu; k++ {
				feat := int8(t.nx())
				en := int8(t.nx())
				exx := int8(t.nx())
				hp := t.nx()
				pe := int8(t.nx())
				px := int8(t.nx())
				w := t.nx()
				pid := prov(fmt.Sprintf("qs%02d.yaml:stances.st%d.unlocks[%d]", r, sname, len(unlocks)))
				u := Unlock{Feature: feat, Entry: en, Exit: exx, HasPairing: hp != 0,
					Pairing: [2]int8{pe, px}, Prov: pid}
				if w >= 0 {
					ww := whens[w]
					u.When = &ww
				}
				unlocks = append(unlocks, u)
			}
			runes[r].Stances = append(runes[r].Stances, Stance{
				Name: sname, Entries: entries, Exits: exits, Never: never,
				Only: only, HasOnly: hasOnly != 0, Unlocks: unlocks,
				RequireEntry: reqE != 0, RequireExit: reqX != 0,
			})
		case "record":
			r := int(t.nx())
			kind := int(t.nx())
			w := whens[t.nx()]
			s := int8(t.nx())
			entryRaw := int8(t.nx())
			exitRaw := int8(t.nx())
			hc := t.nx()
			ce := int8(t.nx())
			cx := int8(t.nx())
			ho := t.nx()
			oe := int8(t.nx())
			ox := int8(t.nx())
			absolute := t.nx()
			by := int8(t.nx())
			var weight int32
			if w.Left != nil {
				weight += 2 + int32(popcount(w.Left.Family))
			}
			if w.Right != nil {
				weight += 2 + int32(popcount(w.Right.Family))
			}
			for _, f := range []int8{w.SelfEntry, w.SelfExit, w.Word, w.Feature} {
				if f >= 0 {
					weight++
				}
			}
			if s >= 0 {
				weight++
			}
			if hc != 0 {
				weight++
			}
			kname := []string{"refuse", "prefer", "extend", "contract"}[kind]
			nExisting := 0
			switch kind {
			case 0:
				nExisting = len(runes[r].Refuse)
			case 1:
				nExisting = len(runes[r].Prefer)
			case 2:
				nExisting = len(runes[r].Extend)
			default:
				nExisting = len(runes[r].Contract)
			}
			pid := prov(fmt.Sprintf("qs%02d.yaml:policy.%s[%d]", r, kname, nExisting))
			rec := PolicyRecord{
				When: w, Stance: s, Entry: entryRaw, Exit: exitRaw,
				HasEntry: entryRaw != UnsetH, HasExit: exitRaw != UnsetH,
				HasCell: hc != 0, Cell: [2]int8{ce, cx},
				HasOver: ho != 0, Over: [2]int8{oe, ox},
				Absolute: absolute != 0, By: by, Ident: ident, Weight: weight, Prov: pid,
			}
			ident++
			switch kind {
			case 0:
				runes[r].Refuse = append(runes[r].Refuse, rec)
			case 1:
				runes[r].Prefer = append(runes[r].Prefer, rec)
			case 2:
				runes[r].Extend = append(runes[r].Extend, rec)
			default:
				runes[r].Contract = append(runes[r].Contract, rec)
			}
		}
	}

	var live []uint8
	for _, i := range orderList {
		r := &runes[i]
		keep := int(i) < nLetters
		if !keep && r.HasSeq {
			keep = int(r.Seq[0]) < nLetters && int(r.Seq[1]) < nLetters
		}
		if keep {
			live = append(live, i)
		}
	}
	for i := range runes {
		bearing := false
		for _, s := range runes[i].Stances {
			for _, row := range s.Entries {
				if row.Selectable {
					bearing = true
				}
			}
			for _, u := range s.Unlocks {
				if u.Entry >= 0 {
					bearing = true
				}
			}
		}
		runes[i].EntryBearing = bearing
		var fmask uint8
		for _, s := range runes[i].Stances {
			for _, u := range s.Unlocks {
				if u.Feature >= 0 {
					fmask |= 1 << uint(u.Feature)
				}
			}
		}
		for _, pool := range [][]PolicyRecord{runes[i].Refuse, runes[i].Prefer, runes[i].Extend, runes[i].Contract} {
			for _, rec := range pool {
				if rec.When.Feature >= 0 {
					fmask |= 1 << uint(rec.When.Feature)
				}
			}
		}
		runes[i].FeatureMask = fmask
	}
	return &Spec{NLetters: nLetters, Runes: runes, Order: live, Classes: classes, ProvCount: provCount}
}

func popcount(x uint32) int {
	n := 0
	for x != 0 {
		x &= x - 1
		n++
	}
	return n
}
