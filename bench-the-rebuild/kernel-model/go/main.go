package main

// The deep-slot liveness probes and the worklist fixpoint: table._ProspectLiveness and table.build_tables.

import (
	"fmt"
	"os"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

const (
	seatRaised      uint64 = ^uint64(0)
	seatUnreachable uint64 = ^uint64(0) - 1
)

type trip struct{ a, b, c uint8 }
type quad struct{ a, b, c, d uint8 }
type sigKey struct {
	follower, family, stance uint8
	seam                     int8
}
type pv3Key struct {
	r1, r2 uint8
	sig    uint32
}
type pv4Key struct {
	r1, r2, r3 uint8
	sig        uint32
}

type Builder struct {
	eng            *Engine
	thirdSeat      map[trip]bool
	thirdJoint     map[trip]bool
	pv3            map[pv3Key]bool
	vv3            map[pv3Key]bool
	fourthSeat     map[quad]bool
	pv4            map[pv4Key]bool
	vv4            map[pv4Key]bool
	sigs           map[sigKey]uint32
	sigIntern      map[string]uint32
	shapes         [][]([2]int8)
	shapesSet      []bool
	leftConds      [][]Condition
	leftCondsSet   []bool
	leftClasses    [][]LeftCtx
	leftClassesSet []bool
	probes         []uint8
	chains3        [][]Condition
	chains4        [][]Condition
	thirdVerdicts  map[trip]bool
	fourthVerdicts map[quad]bool
}

func chainReach(c *Condition) int {
	reach := 0
	if c.Then != nil {
		if v := 1 + chainReach(c.Then); v > reach {
			reach = v
		}
	}
	for i := range c.Except {
		if v := chainReach(&c.Except[i]); v > reach {
			reach = v
		}
	}
	return reach
}

func NewBuilder(spec *Spec, features uint8, share Share, shareDelta uint8) *Builder {
	n := len(spec.Runes)
	probes := make([]uint8, spec.NLetters)
	for i := range probes {
		probes[i] = uint8(i)
	}
	chains3 := make([][]Condition, n)
	chains4 := make([][]Condition, n)
	for i := 0; i < n; i++ {
		for j := range spec.Runes[i].Prefer {
			right := spec.Runes[i].Prefer[j].When.Right
			if right == nil {
				continue
			}
			reach := chainReach(right)
			if reach >= 2 {
				chains3[i] = append(chains3[i], *right)
			}
			if reach >= 3 {
				chains4[i] = append(chains4[i], *right)
			}
		}
	}
	return &Builder{
		eng:            NewEngine(spec, features, share, shareDelta),
		thirdSeat:      map[trip]bool{},
		thirdJoint:     map[trip]bool{},
		pv3:            map[pv3Key]bool{},
		vv3:            map[pv3Key]bool{},
		fourthSeat:     map[quad]bool{},
		pv4:            map[pv4Key]bool{},
		vv4:            map[pv4Key]bool{},
		sigs:           map[sigKey]uint32{},
		sigIntern:      map[string]uint32{},
		shapes:         make([][]([2]int8), n),
		shapesSet:      make([]bool, n),
		leftConds:      make([][]Condition, n),
		leftCondsSet:   make([]bool, n),
		leftClasses:    make([][]LeftCtx, n),
		leftClassesSet: make([]bool, n),
		probes:         probes,
		chains3:        chains3,
		chains4:        chains4,
		thirdVerdicts:  map[trip]bool{},
		fourthVerdicts: map[quad]bool{},
	}
}

func (b *Builder) inputShapes(family uint8) [][2]int8 {
	if b.shapesSet[family] {
		return b.shapes[family]
	}
	var out [][2]int8
	for s := range b.eng.spec.Runes[family].Stances {
		st := &b.eng.spec.Runes[family].Stances[s]
		seams := []int8{NoneH}
		for i := range st.Exits {
			seams = append(seams, st.Exits[i].Height)
		}
		for i := range st.Unlocks {
			if st.Unlocks[i].Exit >= 0 {
				seams = append(seams, st.Unlocks[i].Exit)
			}
		}
		var seen []int8
		for _, sm := range seams {
			dup := false
			for _, q := range seen {
				if q == sm {
					dup = true
					break
				}
			}
			if !dup {
				seen = append(seen, sm)
				out = append(out, [2]int8{int8(st.Name), sm})
			}
		}
	}
	b.shapes[family] = out
	b.shapesSet[family] = true
	return out
}

func (b *Builder) leftConditions(follower uint8) []Condition {
	if b.leftCondsSet[follower] {
		return b.leftConds[follower]
	}
	var out []Condition
	rn := &b.eng.spec.Runes[follower]
	for s := range rn.Stances {
		for i := range rn.Stances[s].Entries {
			out = append(out, rn.Stances[s].Entries[i].Scope...)
		}
	}
	for _, pool := range [][]PolicyRecord{rn.Refuse, rn.Prefer} {
		for i := range pool {
			if pool[i].When.Left != nil {
				out = append(out, *pool[i].When.Left)
			}
		}
	}
	b.leftConds[follower] = out
	b.leftCondsSet[follower] = true
	return out
}

func virtualLeft(family, stance uint8, seam int8) LeftCtx {
	return LeftCtx{Kind: KLetter, Has: true, Settled: Settled{
		Cell: CellId{Rune: family, Stance: stance, Entry: NoneH, Exit: seam}, Seam: seam,
	}}
}

func (b *Builder) signature(follower, family, stance uint8, seam int8) uint32 {
	key := sigKey{follower, family, stance, seam}
	if v, ok := b.sigs[key]; ok {
		return v
	}
	vl := virtualLeft(family, stance, seam)
	conds := b.leftConditions(follower)
	var sb strings.Builder
	sb.WriteByte(byte(seam + 2))
	for i := range conds {
		if b.eng.condMatchesLeft(&conds[i], &vl, seam) {
			sb.WriteByte('1')
		} else {
			sb.WriteByte('0')
		}
	}
	s := sb.String()
	id, ok := b.sigIntern[s]
	if !ok {
		id = uint32(len(b.sigIntern))
		b.sigIntern[s] = id
	}
	b.sigs[key] = id
	return id
}

func (b *Builder) thirdLive(family, r1, r2 uint8) bool {
	stageOne := b.prospectVariesThird(family, r1, r2) || b.voteVariesThird(family, r1, r2)
	key := trip{family, r1, r2}
	if stageOne {
		verdict, ok := b.thirdSeat[key]
		if !ok {
			verdict = b.seatVaries(family, r1, r2, NoTok)
			b.thirdSeat[key] = verdict
		}
		if verdict {
			return true
		}
	}
	if v, ok := b.thirdJoint[key]; ok {
		return v
	}
	verdict := false
	for _, t := range b.probes {
		if b.fourthLive(family, r1, r2, t) {
			verdict = true
			break
		}
	}
	b.thirdJoint[key] = verdict
	return verdict
}

func (b *Builder) prospectVariesThird(family, r1, r2 uint8) bool {
	for _, sh := range b.inputShapes(family) {
		stance, seam := uint8(sh[0]), sh[1]
		sig := b.signature(r1, family, stance, seam)
		key := pv3Key{r1, r2, sig}
		verdict, ok := b.pv3[key]
		if !ok {
			verdict = b.thirdClassLive(family, stance, seam, r1, r2)
			b.pv3[key] = verdict
		}
		if verdict {
			return true
		}
	}
	return false
}

func (b *Builder) thirdClassLive(family, stance uint8, seam int8, r1, r2 uint8) bool {
	c := Candidate{Stance: stance, Entry: NoneH, Seam: seam, ExitIndex: -1}
	base := [4]uint8{r1, r2, TokEdge, TokEdge}
	baseline := b.eng.prospect(family, &c, &base)
	for _, t := range b.probes {
		t3 := [4]uint8{r1, r2, t, TokEdge}
		edge4 := b.eng.prospect(family, &c, &t3)
		if edge4 != baseline {
			return true
		}
		t4 := [4]uint8{r1, r2, t, TokUnknown}
		if b.eng.prospect(family, &c, &t4) != edge4 {
			return true
		}
	}
	return false
}

func (b *Builder) fourthLive(family, r1, r2, r3 uint8) bool {
	if !(b.prospectVariesFourth(family, r1, r2, r3) || b.voteVariesFourth(family, r1, r2, r3)) {
		return false
	}
	key := quad{family, r1, r2, r3}
	if v, ok := b.fourthSeat[key]; ok {
		return v
	}
	v := b.seatVaries(family, r1, r2, r3)
	b.fourthSeat[key] = v
	return v
}

func (b *Builder) prospectVariesFourth(family, r1, r2, r3 uint8) bool {
	for _, sh := range b.inputShapes(family) {
		stance, seam := uint8(sh[0]), sh[1]
		sig := b.signature(r1, family, stance, seam)
		key := pv4Key{r1, r2, r3, sig}
		verdict, ok := b.pv4[key]
		if !ok {
			verdict = b.fourthClassLive(family, stance, seam, r1, r2, r3)
			b.pv4[key] = verdict
		}
		if verdict {
			return true
		}
	}
	return false
}

func (b *Builder) fourthClassLive(family, stance uint8, seam int8, r1, r2, r3 uint8) bool {
	c := Candidate{Stance: stance, Entry: NoneH, Seam: seam, ExitIndex: -1}
	base := [4]uint8{r1, r2, r3, TokEdge}
	baseline := b.eng.prospect(family, &c, &base)
	for _, t := range b.probes {
		tk := [4]uint8{r1, r2, r3, t}
		if b.eng.prospect(family, &c, &tk) != baseline {
			return true
		}
	}
	return false
}

func (b *Builder) voteVariesThird(family, r1, r2 uint8) bool {
	if r1 == family || len(b.eng.spec.Runes[r1].Prefer) == 0 {
		return false
	}
	for _, sh := range b.inputShapes(family) {
		stance, seam := uint8(sh[0]), sh[1]
		sig := b.signature(r1, family, stance, seam)
		key := pv3Key{r1, r2, sig}
		verdict, ok := b.vv3[key]
		if !ok {
			verdict = b.voteClassLive(family, stance, seam, r1, r2, NoTok)
			b.vv3[key] = verdict
		}
		if verdict {
			return true
		}
	}
	return false
}

func (b *Builder) voteVariesFourth(family, r1, r2, r3 uint8) bool {
	if r1 == family || len(b.eng.spec.Runes[r1].Prefer) == 0 {
		return false
	}
	for _, sh := range b.inputShapes(family) {
		stance, seam := uint8(sh[0]), sh[1]
		sig := b.signature(r1, family, stance, seam)
		key := pv4Key{r1, r2, r3, sig}
		verdict, ok := b.vv4[key]
		if !ok {
			verdict = b.voteClassLive(family, stance, seam, r1, r2, r3)
			b.vv4[key] = verdict
		}
		if verdict {
			return true
		}
	}
	return false
}

func (b *Builder) voteClassLive(family, stance uint8, seam int8, r1, r2, r3 uint8) bool {
	c := Candidate{Stance: stance, Entry: NoneH, Seam: seam, ExitIndex: -1}
	owner := r1
	edgeLeft := boundaryLeft(KEdge)
	n := len(b.eng.spec.Runes[owner].Prefer)
	for ri := 0; ri < n; ri++ {
		if r3 == NoTok {
			base := [4]uint8{r1, r2, TokEdge, TokEdge}
			baseline := b.eng.preferFavors(owner, ri, family, &c, &edgeLeft, &base)
			for _, t := range b.probes {
				t3 := [4]uint8{r1, r2, t, TokEdge}
				edge4 := b.eng.preferFavors(owner, ri, family, &c, &edgeLeft, &t3)
				if edge4 != baseline {
					return true
				}
				t4 := [4]uint8{r1, r2, t, TokUnknown}
				if b.eng.preferFavors(owner, ri, family, &c, &edgeLeft, &t4) != edge4 {
					return true
				}
			}
		} else {
			base := [4]uint8{r1, r2, r3, TokEdge}
			baseline := b.eng.preferFavors(owner, ri, family, &c, &edgeLeft, &base)
			for _, t := range b.probes {
				tk := [4]uint8{r1, r2, r3, t}
				if b.eng.preferFavors(owner, ri, family, &c, &edgeLeft, &tk) != baseline {
					return true
				}
			}
		}
	}
	return false
}

func (b *Builder) seatLeftClasses(family uint8) []LeftCtx {
	if b.leftClassesSet[family] {
		return b.leftClasses[family]
	}
	out := []LeftCtx{boundaryLeft(KEdge), boundaryLeft(KSpace), boundaryLeft(KZwnj), boundaryLeft(KNamer)}
	seen := map[uint32]bool{}
	for _, leftFamily := range b.eng.spec.Order {
		for _, sh := range b.inputShapes(leftFamily) {
			stance, seam := uint8(sh[0]), sh[1]
			sig := b.signature(family, leftFamily, stance, seam)
			if seen[sig] {
				continue
			}
			seen[sig] = true
			out = append(out, virtualLeft(leftFamily, stance, seam))
		}
	}
	b.leftClasses[family] = out
	b.leftClassesSet[family] = true
	return out
}

func (b *Builder) seatOutcome(left *LeftCtx, token uint8, tokens *[4]uint8) uint64 {
	trace, fail := b.eng.transitionTrace(left, token, tokens)
	switch fail {
	case FailNone:
		c := trace.Settled.Cell
		return uint64(c.Rune) | uint64(c.Stance)<<8 |
			uint64(uint16(int16(c.Entry)+2))<<16 | uint64(uint16(int16(c.Exit)+2))<<24 |
			uint64(c.Adj)<<32
	case FailRaised:
		return seatRaised
	}
	return seatUnreachable
}

func (b *Builder) seatVaries(family, r1, r2, r3 uint8) bool {
	token := family
	classes := b.seatLeftClasses(family)
	for i := range classes {
		left := classes[i]
		var baseline uint64
		if r3 == NoTok {
			tk := [4]uint8{r1, r2, TokEdge, TokEdge}
			baseline = b.seatOutcome(&left, token, &tk)
		} else {
			tk := [4]uint8{r1, r2, r3, TokEdge}
			baseline = b.seatOutcome(&left, token, &tk)
		}
		if baseline == seatRaised {
			return true
		}
		if baseline == seatUnreachable {
			continue
		}
		for _, t := range b.probes {
			if r3 == NoTok {
				tk := [4]uint8{r1, r2, t, TokEdge}
				edge4 := b.seatOutcome(&left, token, &tk)
				if edge4 == seatRaised || edge4 == seatUnreachable || edge4 != baseline {
					return true
				}
				tk2 := [4]uint8{r1, r2, t, TokUnknown}
				unk := b.seatOutcome(&left, token, &tk2)
				if unk == seatRaised || unk == seatUnreachable || unk != edge4 {
					return true
				}
			} else {
				tk := [4]uint8{r1, r2, r3, t}
				v := b.seatOutcome(&left, token, &tk)
				if v == seatRaised || v == seatUnreachable || v != baseline {
					return true
				}
			}
		}
	}
	return false
}

func (b *Builder) thirdMatters(family, r1, r2 uint8) bool {
	key := trip{family, r1, r2}
	if v, ok := b.thirdVerdicts[key]; ok {
		return v
	}
	tokens := [4]uint8{r1, r2, TokUnknown, TokUnknown}
	verdict := false
	for i := range b.chains3[family] {
		if b.eng.condMatchesRight(&b.chains3[family][i], &tokens, 0) == -1 {
			verdict = true
			break
		}
	}
	if !verdict {
		verdict = b.thirdLive(family, r1, r2)
	}
	b.thirdVerdicts[key] = verdict
	return verdict
}

func (b *Builder) fourthMatters(family, r1, r2, r3 uint8) bool {
	key := quad{family, r1, r2, r3}
	if v, ok := b.fourthVerdicts[key]; ok {
		return v
	}
	tokens := [4]uint8{r1, r2, r3, TokUnknown}
	verdict := false
	for i := range b.chains4[family] {
		if b.eng.condMatchesRight(&b.chains4[family][i], &tokens, 0) == -1 {
			verdict = true
			break
		}
	}
	if !verdict {
		verdict = b.fourthLive(family, r1, r2, r3)
	}
	b.fourthVerdicts[key] = verdict
	return verdict
}

// --- the fixpoint -----------------------------------------------------------

type seenKey struct {
	lkind, lrune, lstance uint8
	lhas                  bool
	lentry, lexit         int8
	ladj                  uint16
	lseam, lext           int8
	irune, r1c, r2a, r3a  uint8
}

type winKey struct {
	irune          uint8
	locked         bool
	lkind          uint8
	lrune, lstance uint8
	lentry, lexit  int8
	ladj           uint16
	r1, r2, r3, r4 uint8
}

type row struct {
	settled Settled
	joint   bool
	prosp   int32
}

type BuildResult struct {
	Config     string
	Windows    int
	Cells      int
	Checksum   uint64
	NoCand     uint64
	Stranded   uint64
	Raised     uint64
	Candidates uint64
	Prospect   uint64
	Trace      uint64
	Favors     uint64
	MemoEnt    int
	Fired      uint32
	TextSink   uint64
}

type wlItem struct {
	left            LeftCtx
	rune_           uint8
	r1c, r2a, r3a   uint8
}

func buildTables(spec *Spec, features uint8, share Share, shareDelta uint8) (BuildResult, map[MemoKey]Trace) {
	b := NewBuilder(spec, features, share, shareDelta)
	rightOptions := []uint8{TokEdge, TokSpace, TokZwnj, TokNamer}
	for i := 0; i < spec.NLetters; i++ {
		rightOptions = append(rightOptions, uint8(i))
	}
	var formationPairs [][2]uint8
	for _, i := range spec.Order {
		if spec.Runes[i].HasSeq {
			formationPairs = append(formationPairs, spec.Runes[i].Seq)
		}
	}
	isFormation := func(a, c uint8) bool {
		for _, p := range formationPairs {
			if p[0] == a && p[1] == c {
				return true
			}
		}
		return false
	}

	transitions := map[winKey]row{}
	seen := map[seenKey]bool{}
	var worklist []wlItem
	for _, kind := range []uint8{KEdge, KSpace, KZwnj, KNamer} {
		for _, name := range spec.Order {
			worklist = append(worklist, wlItem{boundaryLeft(kind), name, NoTok, NoTok, NoTok})
		}
	}
	var nocand, stranded, raised uint64

	for len(worklist) > 0 {
		it := worklist[len(worklist)-1]
		worklist = worklist[:len(worklist)-1]
		left := it.left
		sk := seenKey{
			lkind: left.Kind, lhas: left.Has,
			lrune: left.Settled.Cell.Rune, lstance: left.Settled.Cell.Stance,
			lentry: left.Settled.Cell.Entry, lexit: left.Settled.Cell.Exit, ladj: left.Settled.Cell.Adj,
			lseam: left.Settled.Seam, lext: left.Settled.Extension,
			irune: it.rune_, r1c: it.r1c, r2a: it.r2a, r3a: it.r3a,
		}
		if seen[sk] {
			continue
		}
		seen[sk] = true
		locked := left.Kind == KZwnj && spec.Runes[it.rune_].EntryBearing
		hasSeq := spec.Runes[it.rune_].HasSeq
		seq := spec.Runes[it.rune_].Seq

		var r1Options []uint8
		if it.r1c != NoTok {
			r1Options = []uint8{it.r1c}
		} else {
			r1Options = rightOptions
		}
		for _, right1 := range r1Options {
			var right2Options []uint8
			if tokIsLetter(right1) {
				for _, r := range rightOptions {
					if tokIsLetter(r) && isFormation(right1, r) {
						continue
					}
					if it.r2a != NoTok && r != it.r2a {
						continue
					}
					if hasSeq && tokIsLetter(r) && r == seq[1] {
						continue
					}
					right2Options = append(right2Options, r)
				}
			} else {
				right2Options = []uint8{TokEdge}
			}
			for _, right2 := range right2Options {
				var right3Slots []uint8
				if tokIsLetter(right1) && tokIsLetter(right2) && b.thirdMatters(it.rune_, right1, right2) {
					for _, r := range rightOptions {
						if tokIsLetter(r) && isFormation(right2, r) {
							continue
						}
						if it.r3a != NoTok && r != it.r3a {
							continue
						}
						right3Slots = append(right3Slots, r)
					}
				} else {
					right3Slots = []uint8{NoTok}
				}
				for _, right3 := range right3Slots {
					var right4Slots []uint8
					if right3 != NoTok && tokIsLetter(right3) && b.fourthMatters(it.rune_, right1, right2, right3) {
						for _, r := range rightOptions {
							if tokIsLetter(r) && isFormation(right3, r) {
								continue
							}
							right4Slots = append(right4Slots, r)
						}
					} else {
						right4Slots = []uint8{NoTok}
					}
					for _, right4 := range right4Slots {
						wk := winKey{irune: it.rune_, locked: locked, lkind: left.Kind,
							r1: right1, r3: right3, r4: right4}
						if tokIsLetter(right1) {
							wk.r2 = right2
						} else {
							wk.r2 = NoTok
						}
						if left.Has {
							wk.lrune = left.Settled.Cell.Rune
							wk.lstance = left.Settled.Cell.Stance
							wk.lentry = left.Settled.Cell.Entry
							wk.lexit = left.Settled.Cell.Exit
							wk.ladj = left.Settled.Cell.Adj
						}
						var settled Settled
						if existing, ok := transitions[wk]; ok {
							settled = existing.settled
						} else {
							r3t := right3
							if r3t == NoTok {
								r3t = TokEdge
							}
							r4t := right4
							if r4t == NoTok {
								r4t = TokEdge
							}
							tokens := [4]uint8{right1, right2, r3t, r4t}
							trace, fail := b.eng.transitionTrace(&left, it.rune_, &tokens)
							if fail != FailNone {
								switch fail {
								case FailStranded:
									stranded++
								case FailNoCandidates:
									nocand++
								default:
									raised++
								}
								continue
							}
							settled = trace.Settled
							transitions[wk] = row{trace.Settled, trace.Joint, trace.Prosp}
						}
						if tokIsLetter(right1) {
							successorAllowed := it.r3a
							if right3 != NoTok {
								successorAllowed = right3
							}
							worklist = append(worklist, wlItem{
								LeftCtx{Kind: KLetter, Has: true, Settled: settled},
								right1, right2, successorAllowed, right4,
							})
						}
					}
				}
			}
		}
	}

	lines := make([]string, 0, len(transitions))
	cellSet := map[CellId]bool{}
	for wk, r := range transitions {
		cellSet[r.settled.Cell] = true
		inputLabel := fmt.Sprintf("qs%02d", wk.irune)
		if wk.locked {
			inputLabel += ".noentry"
		}
		var leftLabel string
		if wk.lkind == KLetter {
			c := CellId{Rune: wk.lrune, Stance: wk.lstance, Entry: wk.lentry, Exit: wk.lexit, Adj: wk.ladj}
			leftLabel = cellLabel(&c)
		} else {
			switch wk.lkind {
			case KEdge:
				leftLabel = "edge"
			case KSpace:
				leftLabel = "space"
			case KZwnj:
				leftLabel = "zwnj"
			default:
				leftLabel = "namer-dot"
			}
		}
		na := func(t uint8) string {
			if t == NoTok {
				return "#NA"
			}
			return tokenLabel(t)
		}
		joint := "0"
		if r.joint {
			joint = "1"
		}
		lines = append(lines, strings.Join([]string{
			inputLabel, leftLabel, tokenLabel(wk.r1), na(wk.r2), na(wk.r3), na(wk.r4),
			cellLabel(&r.settled.Cell), joint,
			strconv.Itoa(int(r.prosp)), strconv.Itoa(int(r.settled.Extension)),
		}, "\t"))
	}
	sort.Strings(lines)
	checksum := uint64(0xcbf29ce484222325)
	for _, line := range lines {
		for i := 0; i < len(line); i++ {
			checksum = (checksum ^ uint64(line[i])) * 0x100000001b3
		}
		checksum = (checksum ^ 10) * 0x100000001b3
	}

	res := BuildResult{
		Windows: len(lines), Cells: len(cellSet), Checksum: checksum,
		NoCand: nocand, Stranded: stranded, Raised: raised,
		Candidates: b.eng.nCandidates, Prospect: b.eng.nProspect,
		Trace: b.eng.nTrace, Favors: b.eng.nFavors,
		MemoEnt: len(b.eng.traceCache), Fired: b.eng.firedCount, TextSink: b.eng.textSink,
	}
	return res, b.eng.traceCache
}

var configFeatures = [6]uint8{0, 1, 2, 4, 1 | 4, 8}
var configNames = [6]string{"default", "ss03", "ss04", "ss05", "ss03+ss05", "ss10"}

func main() {
	if os.Args[1] == "memo" {
		runMemoBench()
		return
	}
	specPath := os.Args[1]
	mode := "one"
	if len(os.Args) > 2 {
		mode = os.Args[2]
	}
	letters := 15
	if len(os.Args) > 3 {
		letters, _ = strconv.Atoi(os.Args[3])
	}
	spec := LoadSpec(specPath, letters)
	t0 := time.Now()
	var results []BuildResult
	switch mode {
	case "one":
		r, _ := buildTables(spec, configFeatures[0], Share{}, 0)
		r.Config = "default"
		results = append(results, r)
	case "six":
		r0, donor := buildTables(spec, configFeatures[0], Share{}, 0)
		r0.Config = configNames[0]
		results = append(results, r0)
		for i := 1; i < 6; i++ {
			r, _ := buildTables(spec, configFeatures[i], Share{Kind: ShareLocal, Local: donor}, configFeatures[i])
			r.Config = configNames[i]
			results = append(results, r)
		}
	case "six-noshare":
		for i := 0; i < 6; i++ {
			r, _ := buildTables(spec, configFeatures[i], Share{}, 0)
			r.Config = configNames[i]
			results = append(results, r)
		}
	case "six-par":
		r0, donor := buildTables(spec, configFeatures[0], Share{}, 0)
		r0.Config = configNames[0]
		var mu sync.RWMutex
		out := make([]BuildResult, 6)
		out[0] = r0
		var wg sync.WaitGroup
		for i := 1; i < 6; i++ {
			wg.Add(1)
			go func(i int) {
				defer wg.Done()
				r, _ := buildTables(spec, configFeatures[i],
					Share{Kind: ShareShared, Shared: donor, Mu: &mu}, configFeatures[i])
				r.Config = configNames[i]
				out[i] = r
			}(i)
		}
		wg.Wait()
		results = out
	case "six-par-noshare":
		out := make([]BuildResult, 6)
		var wg sync.WaitGroup
		for i := 0; i < 6; i++ {
			wg.Add(1)
			go func(i int) {
				defer wg.Done()
				r, _ := buildTables(spec, configFeatures[i], Share{}, 0)
				r.Config = configNames[i]
				out[i] = r
			}(i)
		}
		wg.Wait()
		results = out
	default:
		panic("unknown mode")
	}
	wall := time.Since(t0).Seconds()
	var sb strings.Builder
	fmt.Fprintf(&sb, "{\"impl\":\"go\",\"mode\":\"%s\",\"letters\":%d,\"wall_seconds\":%.6f,\"configs\":[", mode, letters, wall)
	for i, r := range results {
		if i > 0 {
			sb.WriteString(",")
		}
		fmt.Fprintf(&sb, "{\"config\":\"%s\",\"windows\":%d,\"cells\":%d,\"checksum\":%d,\"nocand\":%d,\"stranded\":%d,\"raised\":%d,\"candidates\":%d,\"prospect\":%d,\"trace\":%d,\"favors\":%d,\"memo_entries\":%d,\"fired\":%d,\"text_sink\":%d}",
			r.Config, r.Windows, r.Cells, r.Checksum, r.NoCand, r.Stranded, r.Raised,
			r.Candidates, r.Prospect, r.Trace, r.Favors, r.MemoEnt, r.Fired, r.TextSink)
	}
	sb.WriteString("]}")
	fmt.Println(sb.String())
}
