package main

// The settlement kernel: the same algorithm as model.py and the Rust port, on packed structs and interned ids.

import (
	"fmt"
	"strings"
	"sync"
)

type CellId struct {
	Rune   uint8
	Stance uint8
	Entry  int8
	Exit   int8
	Adj    uint16
}

const AdjLocked uint16 = 1

type Settled struct {
	Cell      CellId
	Seam      int8
	Extension int8
}

type LeftCtx struct {
	Kind    uint8
	Has     bool
	Settled Settled
}

func boundaryLeft(kind uint8) LeftCtx { return LeftCtx{Kind: kind} }

func (l *LeftCtx) seam() int8 {
	if l.Kind == KLetter && l.Has {
		return l.Settled.Seam
	}
	return NoneH
}

type Candidate struct {
	Stance     uint8
	Entry      int8
	Seam       int8
	OrderIndex uint8
	ExitIndex  int8
}

type Trace struct {
	Settled Settled
	Joint   bool
	Prosp   int32
	NNotes  uint32
}

type Fail uint8

const (
	FailNone Fail = iota
	FailStranded
	FailNoCandidates
	FailRaised
)

type MemoKey struct {
	LeftKind uint8
	LRune    int8
	LStance  int8
	LSeam    int8
	LExt     int8
	Token    uint8
	R1       uint8
	R2       uint8
	R3       uint8
	R4       uint8
}

type ClosureKey struct {
	Rune   uint8
	Stance uint8
	Entry  int8
	Seam   int8
	R1     uint8
	R2     uint8
}

type ProspectKey struct {
	Rune   uint8
	Stance uint8
	Entry  int8
	Seam   int8
	R1     uint8
	R2     uint8
	R3     uint8
	R4     uint8
}

type ShareKind uint8

const (
	ShareNone ShareKind = iota
	ShareLocal
	ShareShared
)

type Share struct {
	Kind   ShareKind
	Local  map[MemoKey]Trace
	Shared map[MemoKey]Trace
	Mu     *sync.RWMutex
}

type Engine struct {
	spec          *Spec
	features      uint8
	traceCache    map[MemoKey]Trace
	closureCache  map[ClosureKey]bool
	prospectCache map[ProspectKey]int32
	share         Share
	shareDelta    uint8
	fired         []bool
	firedCount    uint32
	firedLog      []uint32
	captureStarts []int
	traceFired    map[MemoKey][]uint32
	closureFired  map[ClosureKey][]uint32
	prospectFired map[ProspectKey][]uint32
	nCandidates   uint64
	nProspect     uint64
	nTrace        uint64
	nFavors       uint64
	// Every elimination and note string the Python baseline formats is folded in here and printed, so the
	// compiler cannot delete the formatting work.
	textSink uint64
}

func NewEngine(spec *Spec, features uint8, share Share, shareDelta uint8) *Engine {
	return &Engine{
		spec:          spec,
		features:      features,
		traceCache:    make(map[MemoKey]Trace),
		closureCache:  make(map[ClosureKey]bool),
		prospectCache: make(map[ProspectKey]int32),
		share:         share,
		shareDelta:    shareDelta,
		fired:         make([]bool, spec.ProvCount),
		traceFired:    make(map[MemoKey][]uint32),
		closureFired:  make(map[ClosureKey][]uint32),
		prospectFired: make(map[ProspectKey][]uint32),
		textSink:      0xcbf29ce484222325,
	}
}

func fnvStr(h uint64, s string) uint64 {
	for i := 0; i < len(s); i++ {
		h = (h ^ uint64(s[i])) * 0x100000001b3
	}
	return h
}

// --- fired-pointer journal --------------------------------------------------

func (e *Engine) recordFired(prov uint32) {
	if len(e.captureStarts) > 0 {
		e.firedLog = append(e.firedLog, prov)
	}
	if !e.fired[prov] {
		e.fired[prov] = true
		e.firedCount++
	}
}

func (e *Engine) replayFired(delta []uint32) {
	if len(delta) == 0 {
		return
	}
	for _, p := range delta {
		if !e.fired[p] {
			e.fired[p] = true
			e.firedCount++
		}
	}
	if len(e.captureStarts) > 0 {
		e.firedLog = append(e.firedLog, delta...)
	}
}

func (e *Engine) beginCapture() { e.captureStarts = append(e.captureStarts, len(e.firedLog)) }

func (e *Engine) endCapture() []uint32 {
	start := e.captureStarts[len(e.captureStarts)-1]
	e.captureStarts = e.captureStarts[:len(e.captureStarts)-1]
	var delta []uint32
	for _, p := range e.firedLog[start:] {
		found := false
		for _, q := range delta {
			if q == p {
				found = true
				break
			}
		}
		if !found {
			delta = append(delta, p)
		}
	}
	if len(e.captureStarts) == 0 {
		e.firedLog = e.firedLog[:0]
	}
	return delta
}

func (e *Engine) abortCapture() {
	e.captureStarts = e.captureStarts[:len(e.captureStarts)-1]
	if len(e.captureStarts) == 0 {
		e.firedLog = e.firedLog[:0]
	}
}

// --- condition matching -----------------------------------------------------

func (e *Engine) leftExitStroke(left *LeftCtx) int8 {
	if left.Kind != KLetter || !left.Has {
		return -1
	}
	return e.spec.Runes[left.Settled.Cell.Rune].MinStroke
}

func (e *Engine) condMatchesLeft(cond *Condition, left *LeftCtx, seam int8) bool {
	if cond.IsToken >= 0 {
		if cond.IsToken == 0 {
			if left.Kind == KLetter {
				return false
			}
		} else if left.Kind != uint8(cond.IsToken) {
			return false
		}
	}
	needsLetter := cond.HasFamily || len(cond.Klass) > 0 || cond.StanceM != 0 ||
		cond.JoinedAt != UnsetH || cond.Stroke >= 0
	if needsLetter {
		if left.Kind != KLetter || !left.Has {
			return false
		}
		cell := &left.Settled.Cell
		if cond.HasFamily && (cond.Family>>uint(cell.Rune))&1 == 0 {
			return false
		}
		for _, k := range cond.Klass {
			if (e.spec.Classes[k]>>uint(cell.Rune))&1 == 0 {
				return false
			}
		}
		if cond.StanceM != 0 && (cond.StanceM>>uint(cell.Stance))&1 == 0 {
			return false
		}
		if cond.JoinedAt != UnsetH && cond.JoinedAt != seam {
			return false
		}
		if cond.Stroke >= 0 && e.leftExitStroke(left) != cond.Stroke {
			return false
		}
	}
	for i := range cond.Except {
		if e.condMatchesLeft(&cond.Except[i], left, seam) {
			return false
		}
	}
	return true
}

// Tri-state: 1 definite match, 0 definite non-match, -1 = a beyond-window slot decided it.
func (e *Engine) condMatchesRight(cond *Condition, tokens *[4]uint8, at int) int8 {
	var token uint8 = TokUnknown
	if at < 4 {
		token = tokens[at]
	}
	unknown := false
	if cond.IsToken >= 0 {
		if token == TokUnknown {
			unknown = true
		} else if cond.IsToken == 0 {
			if tokIsLetter(token) {
				return 0
			}
		} else if tokKind(token) != uint8(cond.IsToken) {
			return 0
		}
	}
	needsLetter := cond.HasFamily || len(cond.Klass) > 0 || cond.Stroke >= 0
	if needsLetter {
		if token == TokUnknown {
			unknown = true
		} else if !tokIsLetter(token) {
			return 0
		} else {
			if cond.HasFamily && (cond.Family>>uint(token))&1 == 0 {
				return 0
			}
			for _, k := range cond.Klass {
				if (e.spec.Classes[k]>>uint(token))&1 == 0 {
					return 0
				}
			}
			if cond.Stroke >= 0 && (e.spec.Runes[token].EntryStrokes>>uint(cond.Stroke))&1 == 0 {
				return 0
			}
		}
	}
	for i := range cond.Except {
		switch e.condMatchesRight(&cond.Except[i], tokens, at) {
		case 1:
			return 0
		case -1:
			unknown = true
		}
	}
	if cond.Then != nil {
		switch e.condMatchesRight(cond.Then, tokens, at+1) {
		case 0:
			return 0
		case -1:
			unknown = true
		}
	}
	if unknown {
		return -1
	}
	return 1
}

func (e *Engine) whenMatches(when *When, left *LeftCtx, entry, seam int8, tokens *[4]uint8) int8 {
	if when.Feature >= 0 && (e.features>>uint(when.Feature))&1 == 0 {
		return 0
	}
	if when.SelfEntry >= 0 {
		live := int8(0)
		if entry != NoneH {
			live = 1
		}
		if when.SelfEntry != live {
			return 0
		}
	}
	if when.SelfExit >= 0 {
		live := int8(0)
		if seam != NoneH {
			live = 1
		}
		if when.SelfExit != live {
			return 0
		}
	}
	unknown := false
	if when.Word >= 0 {
		p := wordPosition(left.Kind, tokKind(tokens[0]), tokens[0])
		if p == -1 {
			unknown = true
		} else if p != when.Word {
			return 0
		}
	}
	if when.Left != nil && !e.condMatchesLeft(when.Left, left, entry) {
		return 0
	}
	if when.Right != nil {
		switch e.condMatchesRight(when.Right, tokens, 0) {
		case 0:
			return 0
		case -1:
			unknown = true
		}
	}
	if unknown {
		return -1
	}
	return 1
}

// --- capability -------------------------------------------------------------

func (e *Engine) entryAvailable(runeIdx uint8, stanceIdx int, height int8, left *LeftCtx, tokens *[4]uint8) bool {
	st := &e.spec.Runes[runeIdx].Stances[stanceIdx]
	hitProv := int64(-1)
	for i := range st.Entries {
		row := &st.Entries[i]
		if row.Height != height {
			continue
		}
		if !row.Selectable {
			break
		}
		if len(row.Scope) == 0 {
			return true
		}
		for j := range row.Scope {
			if e.condMatchesLeft(&row.Scope[j], left, height) {
				hitProv = int64(row.Prov)
				break
			}
		}
		break
	}
	if hitProv >= 0 {
		e.recordFired(uint32(hitProv))
		return true
	}
	for i := range st.Unlocks {
		u := &st.Unlocks[i]
		if u.Entry != height || (e.features>>uint(u.Feature))&1 == 0 {
			continue
		}
		if u.When == nil {
			e.recordFired(u.Prov)
			return true
		}
		if e.whenMatches(u.When, left, height, NoneH, tokens) != 0 {
			e.recordFired(u.Prov)
			return true
		}
	}
	return false
}

type exitSource struct {
	height    int8
	rowIndex  int32
	exitIndex int8
}

func (e *Engine) exitSources(runeIdx uint8, stanceIdx int, out []exitSource) []exitSource {
	st := &e.spec.Runes[runeIdx].Stances[stanceIdx]
	out = out[:0]
	for i := range st.Exits {
		out = append(out, exitSource{st.Exits[i].Height, int32(i), int8(i)})
	}
	offset := int8(len(out))
	for i := range st.Unlocks {
		u := &st.Unlocks[i]
		if u.Exit >= 0 && (e.features>>uint(u.Feature))&1 == 1 {
			declared := false
			for j := range st.Exits {
				if st.Exits[j].Height == u.Exit {
					declared = true
					break
				}
			}
			if !declared {
				e.recordFired(u.Prov)
				out = append(out, exitSource{u.Exit, -1, offset})
				offset++
			}
		}
	}
	return out
}

func (e *Engine) activePairingUnlocks(runeIdx uint8, stanceIdx int, left *LeftCtx, entry int8, tokens *[4]uint8, out [][2]int8) [][2]int8 {
	st := &e.spec.Runes[runeIdx].Stances[stanceIdx]
	out = out[:0]
	for i := range st.Unlocks {
		u := &st.Unlocks[i]
		if !u.HasPairing || (e.features>>uint(u.Feature))&1 == 0 {
			continue
		}
		if u.When != nil && e.whenMatches(u.When, left, entry, NoneH, tokens) == 0 {
			continue
		}
		e.recordFired(u.Prov)
		out = append(out, u.Pairing)
	}
	return out
}

func pairingAllowed(st *Stance, entryState, exitState int8, unlocked [][2]int8) bool {
	pair := [2]int8{entryState, exitState}
	for _, p := range unlocked {
		if p == pair {
			return true
		}
	}
	for _, p := range st.Never {
		if p == pair {
			return false
		}
	}
	if st.HasOnly {
		for _, p := range st.Only {
			if p == pair {
				return true
			}
		}
		return false
	}
	return true
}

func (e *Engine) refusalHit(runeIdx uint8, c *Candidate, left *LeftCtx, tokens *[4]uint8) int64 {
	pool := e.spec.Runes[runeIdx].Refuse
	for i := range pool {
		rec := &pool[i]
		if rec.Stance >= 0 && uint8(rec.Stance) != c.Stance {
			continue
		}
		if rec.HasEntry && rec.Entry != c.Entry {
			continue
		}
		if rec.HasExit && rec.Exit != c.Seam {
			continue
		}
		if rec.Stance < 0 && !rec.HasEntry && !rec.HasExit && c.Seam == NoneH {
			continue
		}
		if e.whenMatches(&rec.When, left, c.Entry, c.Seam, tokens) == 1 {
			e.recordFired(rec.Prov)
			return int64(rec.Ident)
		}
	}
	return -1
}

// --- candidate enumeration --------------------------------------------------

func (e *Engine) candidates(left *LeftCtx, runeIdx uint8, tokens *[4]uint8, out []Candidate, eliminations bool) []Candidate {
	e.nCandidates++
	out = out[:0]
	committed := left.seam()
	rn := &e.spec.Runes[runeIdx]
	// A fresh order list per call, the way settle.Engine.candidates builds one.
	order := make([]uint8, 0, len(rn.Order)+len(rn.Stances))
	order = append(order, rn.Order...)
	for s := range rn.Stances {
		name := rn.Stances[s].Name
		found := false
		for _, o := range order {
			if o == name {
				found = true
				break
			}
		}
		if !found {
			order = append(order, name)
		}
	}
	right1 := tokens[0]
	right1IsLetter := tokIsLetter(right1)
	var srcBuf []exitSource
	var unlockBuf [][2]int8
	for s := range rn.Stances {
		st := &rn.Stances[s]
		sname := st.Name
		var orderIndex uint8
		for i, o := range order {
			if o == sname {
				orderIndex = uint8(i)
				break
			}
		}
		entry := NoneH
		if committed != NoneH {
			if !e.entryAvailable(runeIdx, s, committed, left, tokens) {
				if eliminations {
					e.textSink = fnvStr(e.textSink, fmt.Sprintf(
						"qs%02d.st%d: no available entry row at %s against the committed seam",
						runeIdx, sname, heightName(committed)))
				}
				continue
			}
			entry = committed
		}
		if st.RequireEntry && entry == NoneH {
			if eliminations {
				e.textSink = fnvStr(e.textSink, fmt.Sprintf("qs%02d.st%d: requires a live entry", runeIdx, sname))
			}
			continue
		}
		unlockBuf = e.activePairingUnlocks(runeIdx, s, left, entry, tokens, unlockBuf)
		entryState := entry
		if right1IsLetter {
			srcBuf = e.exitSources(runeIdx, s, srcBuf)
			for _, src := range srcBuf {
				c := Candidate{Stance: sname, Entry: entry, Seam: src.height, OrderIndex: orderIndex, ExitIndex: src.exitIndex}
				if !pairingAllowed(st, entryState, src.height, unlockBuf) {
					if eliminations {
						e.textSink = fnvStr(e.textSink, fmt.Sprintf("qs%02d.st%d: pairing (%s, %s) not allowed",
							runeIdx, sname, heightName(entryState), heightName(src.height)))
					}
					continue
				}
				if src.rowIndex >= 0 {
					row := &st.Exits[src.rowIndex]
					if len(row.Scope) > 0 {
						scoped := false
						fire := false
						probe := [4]uint8{right1, tokens[1], TokUnknown, TokUnknown}
						for j := range row.Scope {
							v := e.condMatchesRight(&row.Scope[j], &probe, 0)
							if v == 1 {
								fire = true
							}
							if v != 0 {
								scoped = true
								break
							}
						}
						if fire {
							e.recordFired(row.Prov)
						}
						if !scoped {
							if eliminations {
								e.textSink = fnvStr(e.textSink, fmt.Sprintf(
									"qs%02d.st%d: exit %s toward-scope does not admit %d",
									runeIdx, sname, heightName(src.height), right1))
							}
							continue
						}
					}
				}
				if !e.acceptorExists(&c, runeIdx, right1, tokens[1]) {
					if eliminations {
						e.textSink = fnvStr(e.textSink, fmt.Sprintf(
							"qs%02d.st%d: exit %s has no refusal-aware acceptor cell on %d",
							runeIdx, sname, heightName(src.height), right1))
					}
					continue
				}
				if ident := e.refusalHit(runeIdx, &c, left, tokens); ident >= 0 {
					if eliminations {
						e.textSink = fnvStr(e.textSink, fmt.Sprintf("qs%02d.st%d: exit %s refused by #%d",
							runeIdx, sname, heightName(src.height), ident))
					}
					continue
				}
				out = append(out, c)
			}
		}
		if st.RequireExit {
			continue
		}
		nonJoining := Candidate{Stance: sname, Entry: entry, Seam: NoneH, OrderIndex: orderIndex, ExitIndex: -1}
		if !pairingAllowed(st, entryState, NoneH, unlockBuf) {
			if eliminations {
				e.textSink = fnvStr(e.textSink, fmt.Sprintf("qs%02d.st%d: pairing (%s, none) not allowed",
					runeIdx, sname, heightName(entryState)))
			}
			continue
		}
		if e.refusalHit(runeIdx, &nonJoining, left, tokens) >= 0 {
			if eliminations {
				e.textSink = fnvStr(e.textSink, fmt.Sprintf("qs%02d.st%d: non-joining cell refused", runeIdx, sname))
			}
			continue
		}
		out = append(out, nonJoining)
	}
	return out
}

func virtualLeftOf(runeIdx uint8, c *Candidate) LeftCtx {
	return LeftCtx{Kind: KLetter, Has: true, Settled: Settled{
		Cell: CellId{Rune: runeIdx, Stance: c.Stance, Entry: c.Entry, Exit: c.Seam},
		Seam: c.Seam,
	}}
}

func (e *Engine) acceptorExists(c *Candidate, runeIdx, r1, r2 uint8) bool {
	if !tokIsLetter(r1) {
		return false
	}
	key := ClosureKey{runeIdx, c.Stance, c.Entry, c.Seam, r1, r2}
	if cached, ok := e.closureCache[key]; ok {
		if d, ok2 := e.closureFired[key]; ok2 {
			e.replayFired(d)
		}
		return cached
	}
	e.beginCapture()
	vl := virtualLeftOf(runeIdx, c)
	toks := [4]uint8{r2, TokUnknown, TokUnknown, TokUnknown}
	buf := e.candidates(&vl, r1, &toks, nil, false)
	result := len(buf) > 0
	e.closureFired[key] = e.endCapture()
	e.closureCache[key] = result
	return result
}

// --- prospect ---------------------------------------------------------------

func (e *Engine) prospect(runeIdx uint8, c *Candidate, tokens *[4]uint8) int32 {
	e.nProspect++
	if !tokIsLetter(tokens[0]) || !tokIsLetter(tokens[1]) {
		return 0
	}
	key := ProspectKey{runeIdx, c.Stance, c.Entry, c.Seam, tokens[0], tokens[1], tokens[2], tokens[3]}
	if cached, ok := e.prospectCache[key]; ok {
		if d, ok2 := e.prospectFired[key]; ok2 {
			e.replayFired(d)
		}
		return cached
	}
	e.beginCapture()
	vl := virtualLeftOf(runeIdx, c)
	shifted := [4]uint8{tokens[1], tokens[2], tokens[3], TokUnknown}
	var result int32
	trace, fail := e.transitionTrace(&vl, tokens[0], &shifted)
	if fail == FailNone {
		if trace.Settled.Seam != NoneH {
			result = 1
		}
	} else {
		toks := [4]uint8{tokens[1], TokUnknown, TokUnknown, TokUnknown}
		buf := e.candidates(&vl, tokens[0], &toks, nil, false)
		for _, cc := range buf {
			if cc.Seam != NoneH {
				result = 1
				break
			}
		}
	}
	e.prospectFired[key] = e.endCapture()
	e.prospectCache[key] = result
	return result
}

// --- prefers ----------------------------------------------------------------

func cellPatternMatches(pattern [2]int8, c *Candidate) bool {
	return pattern[0] == c.Entry && pattern[1] == c.Seam
}

// Returns 1 favored, 0 not favored, -1 "record does not speak here" (Python's None).
func (e *Engine) preferFavors(owner uint8, recIndex int, runeIdx uint8, c *Candidate, left *LeftCtx, tokens *[4]uint8) int8 {
	e.nFavors++
	rec := &e.spec.Runes[owner].Prefer[recIndex]
	if owner == runeIdx {
		v := e.whenMatches(&rec.When, left, c.Entry, c.Seam, tokens)
		if v == 0 {
			return -1
		}
		if rec.HasCell {
			favored := cellPatternMatches(rec.Cell, c)
			if rec.HasOver && !favored && !cellPatternMatches(rec.Over, c) {
				return -1
			}
			if favored {
				return 1
			}
			return 0
		}
		if rec.Stance >= 0 {
			if c.Stance == uint8(rec.Stance) {
				return 1
			}
			return 0
		}
		return -1
	}
	if !tokIsLetter(tokens[0]) || tokens[0] != owner {
		return -1
	}
	vl := virtualLeftOf(runeIdx, c)
	voteR2 := tokens[2]
	voteR3 := tokens[3]
	toks := [4]uint8{tokens[1], voteR2, TokUnknown, TokUnknown}
	buf := e.candidates(&vl, owner, &toks, nil, false)
	voteTokens := [4]uint8{tokens[1], voteR2, voteR3, TokUnknown}
	relevant := false
	for i := range buf {
		cell := &buf[i]
		if e.whenMatches(&rec.When, &vl, cell.Entry, cell.Seam, &voteTokens) == 0 {
			continue
		}
		relevant = true
		if rec.Stance >= 0 && cell.Stance == uint8(rec.Stance) {
			return 1
		}
		if rec.HasCell && cellPatternMatches(rec.Cell, cell) {
			return 1
		}
	}
	if relevant {
		return 0
	}
	return -1
}

func (e *Engine) outranks(aOwner uint8, aRi int, bOwner uint8, bRi int) bool {
	a := &e.spec.Runes[aOwner].Prefer[aRi]
	b := &e.spec.Runes[bOwner].Prefer[bRi]
	if a.Weight != b.Weight {
		return a.Weight > b.Weight
	}
	return aOwner < bOwner
}

type ownedRecord struct {
	owner uint8
	ri    int
}

func (e *Engine) applyPrefers(modeAbsolute bool, runeIdx uint8, survivors []Candidate, left *LeftCtx, tokens *[4]uint8) ([]Candidate, Fail) {
	if len(survivors) <= 1 {
		return survivors, FailNone
	}
	var gathered []ownedRecord
	owners := []uint8{runeIdx}
	if tokIsLetter(tokens[0]) && tokens[0] != runeIdx {
		owners = append(owners, tokens[0])
	}
	for _, owner := range owners {
		for i := range e.spec.Runes[owner].Prefer {
			if e.spec.Runes[owner].Prefer[i].Absolute != modeAbsolute {
				continue
			}
			gathered = append(gathered, ownedRecord{owner, i})
		}
	}
	if len(gathered) == 0 {
		return survivors, FailNone
	}
	type applicableRec struct {
		owner   uint8
		ri      int
		favored []Candidate
	}
	var applicable []applicableRec
	for _, g := range gathered {
		var favored []Candidate
		relevant := false
		for i := range survivors {
			c := survivors[i]
			vote := e.preferFavors(g.owner, g.ri, runeIdx, &c, left, tokens)
			if vote == -1 {
				continue
			}
			relevant = true
			if vote == 1 {
				favored = append(favored, c)
			}
		}
		if relevant && len(favored) > 0 && len(favored) < len(survivors) {
			applicable = append(applicable, applicableRec{g.owner, g.ri, favored})
		}
	}
	if len(applicable) == 0 {
		return survivors, FailNone
	}
	outranked := make([]int, len(applicable))
	for i := range applicable {
		n := 0
		for j := range applicable {
			if i != j && e.outranks(applicable[j].owner, applicable[j].ri, applicable[i].owner, applicable[i].ri) {
				n++
			}
		}
		outranked[i] = n
	}
	order := make([]int, len(applicable))
	for i := range order {
		order[i] = i
	}
	// insertion sort: stable, and matches Python's stable sorted()
	for i := 1; i < len(order); i++ {
		j := i
		for j > 0 && outranked[order[j-1]] > outranked[order[j]] {
			order[j-1], order[j] = order[j], order[j-1]
			j--
		}
	}
	current := append([]Candidate(nil), survivors...)
	var applied []ownedRecord
	for _, index := range order {
		a := &applicable[index]
		var narrowed []Candidate
		for _, c := range current {
			for _, f := range a.favored {
				if f == c {
					narrowed = append(narrowed, c)
					break
				}
			}
		}
		if len(narrowed) > 0 {
			current = narrowed
			applied = append(applied, ownedRecord{a.owner, a.ri})
			e.recordFired(e.spec.Runes[a.owner].Prefer[a.ri].Prov)
			continue
		}
		for _, prev := range applied {
			if e.outranks(prev.owner, prev.ri, a.owner, a.ri) || e.outranks(a.owner, a.ri, prev.owner, prev.ri) {
				continue
			}
			return survivors, FailRaised
		}
	}
	return current, FailNone
}

// --- the memoized kernel ----------------------------------------------------

func (e *Engine) shareBlind(left *LeftCtx, token uint8, tokens *[4]uint8) bool {
	delta := e.shareDelta
	if delta == 0 {
		return true
	}
	if left.Kind == KLetter && left.Has && e.spec.Runes[left.Settled.Cell.Rune].FeatureMask&delta != 0 {
		return false
	}
	if e.spec.Runes[token].FeatureMask&delta != 0 {
		return false
	}
	for _, t := range tokens {
		if tokIsLetter(t) && e.spec.Runes[t].FeatureMask&delta != 0 {
			return false
		}
	}
	return true
}

func (e *Engine) transitionTrace(left *LeftCtx, token uint8, tokens *[4]uint8) (Trace, Fail) {
	e.nTrace++
	if !tokIsLetter(token) {
		return Trace{Settled: Settled{Cell: CellId{Rune: 255, Stance: 255, Entry: NoneH, Exit: NoneH}, Seam: NoneH}}, FailNone
	}
	key := MemoKey{LeftKind: left.Kind, Token: token, R1: tokens[0], R2: tokens[1], R3: tokens[2], R4: tokens[3]}
	if left.Has {
		key.LRune = int8(left.Settled.Cell.Rune)
		key.LStance = int8(left.Settled.Cell.Stance)
		key.LSeam = left.Settled.Seam
		key.LExt = left.Settled.Extension
	} else {
		key.LRune = -1
		key.LStance = -1
		key.LSeam = -2
	}
	if trace, ok := e.traceCache[key]; ok {
		if d, ok2 := e.traceFired[key]; ok2 {
			e.replayFired(d)
		}
		return trace, FailNone
	}
	if e.share.Kind != ShareNone && e.shareBlind(left, token, tokens) {
		if e.share.Kind == ShareLocal {
			if trace, ok := e.share.Local[key]; ok {
				return trace, FailNone
			}
		} else {
			e.share.Mu.RLock()
			trace, ok := e.share.Shared[key]
			e.share.Mu.RUnlock()
			if ok {
				return trace, FailNone
			}
		}
	}
	e.beginCapture()
	trace, fail := e.transitionTraceUncached(left, token, tokens)
	if fail != FailNone {
		e.abortCapture()
		return trace, fail
	}
	e.traceFired[key] = e.endCapture()
	e.traceCache[key] = trace
	return trace, FailNone
}

type rankedEntry struct {
	c         Candidate
	joinCount int32
	prospect  int32
}

func (e *Engine) transitionTraceUncached(left *LeftCtx, token uint8, tokens *[4]uint8) (Trace, Fail) {
	runeIdx := token
	committed := left.seam()
	locked := left.Kind == KZwnj && e.spec.Runes[runeIdx].EntryBearing

	var notes []string
	survivors := e.candidates(left, runeIdx, tokens, nil, true)
	if len(survivors) == 0 {
		if committed != NoneH {
			return Trace{}, FailStranded
		}
		return Trace{}, FailNoCandidates
	}
	nRanked := len(survivors)
	ranked := make([]rankedEntry, 0, nRanked)
	for i := range survivors {
		c := survivors[i]
		p := e.prospect(runeIdx, &c, tokens)
		leftTerm := int32(0)
		if committed != NoneH {
			leftTerm = 1
		}
		ownTerm := int32(0)
		if c.Seam != NoneH {
			ownTerm = 1
		}
		p2 := e.prospect(runeIdx, &c, tokens)
		ranked = append(ranked, rankedEntry{c, leftTerm + ownTerm + p, p2})
	}
	decidedStage := 0

	var fail Fail
	survivors, fail = e.applyPrefers(true, runeIdx, survivors, left, tokens)
	if fail != FailNone {
		return Trace{}, fail
	}
	if len(survivors) == 1 && decidedStage == 0 && nRanked > 1 {
		decidedStage = 1
	}
	lookup := func(c Candidate) *rankedEntry {
		for i := range ranked {
			if ranked[i].c == c {
				return &ranked[i]
			}
		}
		return nil
	}
	if len(survivors) > 1 {
		best := int32(-1 << 30)
		for _, c := range survivors {
			if v := lookup(c).joinCount; v > best {
				best = v
			}
		}
		var narrowed []Candidate
		for _, c := range survivors {
			if lookup(c).joinCount == best {
				narrowed = append(narrowed, c)
			}
		}
		if len(narrowed) < len(survivors) && len(narrowed) == 1 {
			decidedStage = 2
		}
		survivors = narrowed
	}
	if len(survivors) > 1 {
		survivors, fail = e.applyPrefers(false, runeIdx, survivors, left, tokens)
		if fail != FailNone {
			return Trace{}, fail
		}
		if len(survivors) == 1 {
			decidedStage = 3
		}
	}
	if len(survivors) > 1 {
		bestOrder := uint8(255)
		for _, c := range survivors {
			if c.OrderIndex < bestOrder {
				bestOrder = c.OrderIndex
			}
		}
		var narrowed []Candidate
		for _, c := range survivors {
			if c.OrderIndex == bestOrder {
				narrowed = append(narrowed, c)
			}
		}
		if len(narrowed) == 1 {
			decidedStage = 4
		}
		survivors = narrowed
	}
	joint := false
	if len(survivors) > 1 {
		// stable insertion sort on the floor key
		for i := 1; i < len(survivors); i++ {
			j := i
			for j > 0 && floorLess(survivors[j], survivors[j-1]) {
				survivors[j-1], survivors[j] = survivors[j], survivors[j-1]
				j--
			}
		}
		joint = (survivors[0].Seam == NoneH) != (survivors[1].Seam == NoneH)
		survivors = survivors[:1]
	}
	winner := survivors[0]
	settled, notes2 := e.commit(runeIdx, &winner, locked, left, tokens, notes)
	prospect := lookup(winner).prospect
	// The Python baseline sorts and keeps the ranked tuple; the port pays the sort and folds the notes into
	// the text sink, then stores only what the fixpoint reads.
	for i := 1; i < len(ranked); i++ {
		j := i
		for j > 0 && rankedLess(ranked[j], ranked[j-1]) {
			ranked[j-1], ranked[j] = ranked[j], ranked[j-1]
			j--
		}
	}
	var nNotes uint32
	for _, n := range notes2 {
		e.textSink = fnvStr(e.textSink, n)
		nNotes++
	}
	e.textSink ^= uint64(len(ranked)) ^ (uint64(nNotes) << 8) ^ (uint64(decidedStage) << 20)
	return Trace{Settled: settled, Joint: joint, Prosp: prospect, NNotes: nNotes}, FailNone
}

func floorLess(a, b Candidate) bool {
	ak := int32(0)
	bk := int32(0)
	if a.Seam == NoneH {
		ak = 1
	}
	if b.Seam == NoneH {
		bk = 1
	}
	if ak != bk {
		return ak < bk
	}
	ay, by := heightY(a.Seam), heightY(b.Seam)
	if ay != by {
		return ay < by
	}
	return a.ExitIndex < b.ExitIndex
}

func rankedLess(a, b rankedEntry) bool {
	if a.joinCount != b.joinCount {
		return a.joinCount > b.joinCount
	}
	if a.c.OrderIndex != b.c.OrderIndex {
		return a.c.OrderIndex < b.c.OrderIndex
	}
	return a.c.ExitIndex < b.c.ExitIndex
}

func (e *Engine) pickAdjustment(extend bool, runeIdx uint8, winner *Candidate, sideEntry bool, height int8, left *LeftCtx, tokens *[4]uint8) int {
	var pool []PolicyRecord
	if extend {
		pool = e.spec.Runes[runeIdx].Extend
	} else {
		pool = e.spec.Runes[runeIdx].Contract
	}
	best := -1
	var bestIdent uint32
	for i := range pool {
		rec := &pool[i]
		if rec.Stance >= 0 && uint8(rec.Stance) != winner.Stance {
			continue
		}
		if sideEntry {
			if !rec.HasEntry || rec.Entry != height {
				continue
			}
		} else if !rec.HasExit || rec.Exit != height {
			continue
		}
		if e.whenMatches(&rec.When, left, winner.Entry, winner.Seam, tokens) != 1 {
			continue
		}
		if best < 0 || rec.Ident < bestIdent {
			best = i
			bestIdent = rec.Ident
		}
	}
	return best
}

func (e *Engine) commit(runeIdx uint8, winner *Candidate, locked bool, left *LeftCtx, tokens *[4]uint8, notes []string) (Settled, []string) {
	var adj uint16
	if locked {
		adj |= AdjLocked
	}
	if winner.Entry != NoneH {
		for s := range e.spec.Runes[runeIdx].Stances {
			if e.spec.Runes[runeIdx].Stances[s].Name == winner.Stance {
				if e.entryAvailable(runeIdx, s, winner.Entry, left, tokens) {
					note := "entry live at " + heightName(winner.Entry)
					found := false
					for _, n := range notes {
						if n == note {
							found = true
							break
						}
					}
					if !found {
						notes = append(notes, note)
					}
				}
				break
			}
		}
		extend := e.pickAdjustment(true, runeIdx, winner, true, winner.Entry, left, tokens)
		contract := e.pickAdjustment(false, runeIdx, winner, true, winner.Entry, left, tokens)
		if extend >= 0 && left.Has && left.Settled.Extension > 0 {
			extend = -1
		}
		if extend >= 0 {
			rec := &e.spec.Runes[runeIdx].Extend[extend]
			e.recordFired(rec.Prov)
			adj |= uint16(rec.By&3) << 1
		}
		if contract >= 0 {
			rec := &e.spec.Runes[runeIdx].Contract[contract]
			e.recordFired(rec.Prov)
			adj |= uint16(rec.By&3) << 3
		}
	}
	var extension int8
	if winner.Seam != NoneH {
		extend := e.pickAdjustment(true, runeIdx, winner, false, winner.Seam, left, tokens)
		contract := e.pickAdjustment(false, runeIdx, winner, false, winner.Seam, left, tokens)
		if extend >= 0 {
			rec := &e.spec.Runes[runeIdx].Extend[extend]
			e.recordFired(rec.Prov)
			extension += rec.By
			adj |= uint16(rec.By&3) << 5
		}
		if contract >= 0 {
			rec := &e.spec.Runes[runeIdx].Contract[contract]
			e.recordFired(rec.Prov)
			extension -= rec.By
			adj |= uint16(rec.By&3) << 7
		}
	}
	return Settled{
		Cell:      CellId{Rune: runeIdx, Stance: winner.Stance, Entry: winner.Entry, Exit: winner.Seam, Adj: adj},
		Seam:      winner.Seam,
		Extension: extension,
	}, notes
}

func wordPosition(leftKind, right1Kind, right1 uint8) int8 {
	initial := leftKind == KEdge || leftKind == KSpace || leftKind == KZwnj
	if right1 == TokUnknown {
		return -1
	}
	final := right1Kind == KEdge || right1Kind == KSpace || right1Kind == KZwnj
	if initial && final {
		return 3
	}
	if initial {
		return 0
	}
	if final {
		return 2
	}
	return 1
}

func cellLabel(cell *CellId) string {
	var b strings.Builder
	fmt.Fprintf(&b, "qs%02d.st%d", cell.Rune, cell.Stance)
	if cell.Entry != NoneH {
		fmt.Fprintf(&b, ".en-y%d", heightY(cell.Entry))
	}
	if cell.Exit != NoneH {
		fmt.Fprintf(&b, ".ex-y%d", heightY(cell.Exit))
	}
	if cell.Adj&AdjLocked != 0 {
		b.WriteString(".locked")
	}
	names := [4]string{"en-ext", "en-con", "ex-ext", "ex-con"}
	shifts := [4]uint{1, 3, 5, 7}
	for i := 0; i < 4; i++ {
		by := (cell.Adj >> shifts[i]) & 3
		if by != 0 {
			fmt.Fprintf(&b, ".%s-%d", names[i], by)
		}
	}
	return b.String()
}

func tokenLabel(t uint8) string {
	if tokIsLetter(t) {
		return fmt.Sprintf("qs%02d", t)
	}
	switch t {
	case TokEdge:
		return "edge"
	case TokSpace:
		return "space"
	case TokZwnj:
		return "zwnj"
	case TokNamer:
		return "namer-dot"
	}
	return "unknown"
}
