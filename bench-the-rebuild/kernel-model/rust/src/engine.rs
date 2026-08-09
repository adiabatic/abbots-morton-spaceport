// The settlement kernel: a line-for-line port of model.py's Engine, on packed structs and interned ids.
use crate::fx::FxMap;
use crate::spec::*;

pub const STAGE_REFUSE: u8 = 5;

#[derive(Clone, Copy, PartialEq, Eq, Hash, Debug, Default)]
pub struct CellId {
    pub rune: u8,
    pub stance: u8,
    pub entry: i8,
    pub exit: i8,
    pub adj: u16,
}

// adjustments bit layout: 0 = locked, 1..2 = en-ext by, 3..4 = en-con by, 5..6 = ex-ext by, 7..8 = ex-con by
pub const ADJ_LOCKED: u16 = 1;
#[inline(always)]
pub fn adj_set(adj: u16, shift: u32, by: i8) -> u16 {
    adj | ((by as u16 & 3) << shift)
}

#[derive(Clone, Copy, PartialEq, Eq, Hash, Debug, Default)]
pub struct Settled {
    pub cell: CellId,
    pub seam: i8,
    pub extension: i8,
}

#[derive(Clone, Copy, Debug)]
pub struct LeftCtx {
    pub kind: u8,
    pub has: bool,
    pub settled: Settled,
}

impl LeftCtx {
    #[inline(always)]
    pub fn boundary(kind: u8) -> Self {
        LeftCtx { kind, has: false, settled: Settled::default() }
    }
    #[inline(always)]
    pub fn seam(&self) -> i8 {
        if self.kind == K_LETTER && self.has {
            self.settled.seam
        } else {
            NONE_H
        }
    }
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub struct Candidate {
    pub stance: u8,
    pub entry: i8,
    pub seam: i8,
    pub order_index: u8,
    pub exit_index: i8,
}

#[derive(Clone, Copy, Debug)]
pub struct Ranked {
    pub join_count: i32,
    pub prospect: i32,
}

#[derive(Clone, Copy, Debug)]
pub struct Trace {
    pub settled: Settled,
    pub joint_floor: bool,
    pub prospect: i32,
    pub n_notes: u32,
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Fail {
    Stranded,
    NoCandidates,
    Raised,
}

pub type Res<T> = Result<T, Fail>;

#[derive(Clone, Copy, PartialEq, Eq, Hash, Debug)]
pub struct MemoKey {
    pub left_kind: u8,
    pub lrune: i8,
    pub lstance: i8,
    pub lseam: i8,
    pub lext: i8,
    pub token: u8,
    pub r1: u8,
    pub r2: u8,
    pub r3: u8,
    pub r4: u8,
}

#[derive(Clone, Copy, PartialEq, Eq, Hash, Debug)]
pub struct ClosureKey {
    pub rune: u8,
    pub stance: u8,
    pub entry: i8,
    pub seam: i8,
    pub r1: u8,
    pub r2: u8,
}

#[derive(Clone, Copy, PartialEq, Eq, Hash, Debug)]
pub struct ProspectKey {
    pub rune: u8,
    pub stance: u8,
    pub entry: i8,
    pub seam: i8,
    pub r1: u8,
    pub r2: u8,
    pub r3: u8,
    pub r4: u8,
}

pub enum Share<'a> {
    None,
    Local(&'a FxMap<MemoKey, Trace>),
    Shared(&'a std::sync::RwLock<FxMap<MemoKey, Trace>>),
}

pub struct Engine<'a> {
    pub spec: &'a Spec,
    pub features: u8,
    pub trace_cache: FxMap<MemoKey, Trace>,
    pub closure_cache: FxMap<ClosureKey, bool>,
    pub prospect_cache: FxMap<ProspectKey, i32>,
    pub share: Share<'a>,
    pub share_delta: u8,
    pub fired: Vec<bool>,
    pub fired_count: u32,
    fired_log: Vec<u32>,
    capture_starts: Vec<usize>,
    trace_fired: FxMap<MemoKey, Box<[u32]>>,
    closure_fired: FxMap<ClosureKey, Box<[u32]>>,
    prospect_fired: FxMap<ProspectKey, Box<[u32]>>,
    pub n_candidates: u64,
    pub n_prospect: u64,
    pub n_trace: u64,
    pub n_favors: u64,
    // Every generated elimination and note string is folded in here and printed, so nothing the Python
    // baseline formats can be dead-code-eliminated out of the port.
    pub text_sink: u64,
    scratch_order: Vec<u8>,
    scratch_notes: Vec<String>,
}

#[inline(always)]
fn fnv_str(mut h: u64, s: &str) -> u64 {
    for b in s.as_bytes() {
        h = (h ^ (*b as u64)).wrapping_mul(0x100000001b3);
    }
    h
}

impl<'a> Engine<'a> {
    pub fn new(spec: &'a Spec, features: u8, share: Share<'a>, share_delta: u8) -> Self {
        Engine {
            spec,
            features,
            trace_cache: FxMap::default(),
            closure_cache: FxMap::default(),
            prospect_cache: FxMap::default(),
            share,
            share_delta,
            fired: vec![false; spec.prov_names.len()],
            fired_count: 0,
            fired_log: Vec::new(),
            capture_starts: Vec::new(),
            trace_fired: FxMap::default(),
            closure_fired: FxMap::default(),
            prospect_fired: FxMap::default(),
            n_candidates: 0,
            n_prospect: 0,
            n_trace: 0,
            n_favors: 0,
            text_sink: 0xcbf29ce484222325,
            scratch_order: Vec::new(),
            scratch_notes: Vec::new(),
        }
    }

    // --- fired-pointer journal (settle.Engine._record_fired and friends) ----

    #[inline(always)]
    fn record_fired(&mut self, prov: u32) {
        if !self.capture_starts.is_empty() {
            self.fired_log.push(prov);
        }
        if !self.fired[prov as usize] {
            self.fired[prov as usize] = true;
            self.fired_count += 1;
        }
    }

    #[inline(always)]
    fn replay_fired(&mut self, delta: &[u32]) {
        if delta.is_empty() {
            return;
        }
        for &p in delta {
            if !self.fired[p as usize] {
                self.fired[p as usize] = true;
                self.fired_count += 1;
            }
        }
        if !self.capture_starts.is_empty() {
            self.fired_log.extend_from_slice(delta);
        }
    }

    #[inline(always)]
    fn begin_capture(&mut self) {
        self.capture_starts.push(self.fired_log.len());
    }

    fn end_capture(&mut self) -> Box<[u32]> {
        let start = self.capture_starts.pop().unwrap();
        let mut delta: Vec<u32> = Vec::new();
        for &p in &self.fired_log[start..] {
            if !delta.contains(&p) {
                delta.push(p);
            }
        }
        if self.capture_starts.is_empty() {
            self.fired_log.clear();
        }
        delta.into_boxed_slice()
    }

    fn abort_capture(&mut self) {
        self.capture_starts.pop();
        if self.capture_starts.is_empty() {
            self.fired_log.clear();
        }
    }

    // --- condition matching -------------------------------------------------

    fn left_exit_stroke(&self, left: &LeftCtx) -> i8 {
        if left.kind != K_LETTER || !left.has {
            return -1;
        }
        self.spec.runes[left.settled.cell.rune as usize].min_stroke
    }

    pub fn cond_matches_left(&self, cond: &Condition, left: &LeftCtx, seam: i8) -> bool {
        if cond.is_token >= 0 {
            if cond.is_token == 0 {
                if left.kind == K_LETTER {
                    return false;
                }
            } else if left.kind != cond.is_token as u8 {
                return false;
            }
        }
        let needs_letter = cond.has_family
            || !cond.klass.is_empty()
            || cond.stance_mask != 0
            || cond.joined_at != UNSET_H
            || cond.stroke >= 0;
        if needs_letter {
            if left.kind != K_LETTER || !left.has {
                return false;
            }
            let cell = &left.settled.cell;
            if cond.has_family && (cond.family >> cell.rune) & 1 == 0 {
                return false;
            }
            for &k in &cond.klass {
                if (self.spec.classes[k as usize] >> cell.rune) & 1 == 0 {
                    return false;
                }
            }
            if cond.stance_mask != 0 && (cond.stance_mask >> cell.stance) & 1 == 0 {
                return false;
            }
            if cond.joined_at != UNSET_H && cond.joined_at != seam {
                return false;
            }
            if cond.stroke >= 0 && self.left_exit_stroke(left) != cond.stroke {
                return false;
            }
        }
        for ex in &cond.except_ {
            if self.cond_matches_left(ex, left, seam) {
                return false;
            }
        }
        true
    }

    // Tri-state: Some(true) definite match, Some(false) definite non-match, None = a beyond-window slot decided it.
    pub fn cond_matches_right(&self, cond: &Condition, tokens: &[u8; 4], at: usize) -> Option<bool> {
        let token = if at < 4 { tokens[at] } else { TOK_UNKNOWN };
        let mut unknown = false;
        if cond.is_token >= 0 {
            if token == TOK_UNKNOWN {
                unknown = true;
            } else if cond.is_token == 0 {
                if tok_is_letter(token) {
                    return Some(false);
                }
            } else if tok_kind(token) != cond.is_token as u8 {
                return Some(false);
            }
        }
        let needs_letter = cond.has_family || !cond.klass.is_empty() || cond.stroke >= 0;
        if needs_letter {
            if token == TOK_UNKNOWN {
                unknown = true;
            } else if !tok_is_letter(token) {
                return Some(false);
            } else {
                let rune = token;
                if cond.has_family && (cond.family >> rune) & 1 == 0 {
                    return Some(false);
                }
                for &k in &cond.klass {
                    if (self.spec.classes[k as usize] >> rune) & 1 == 0 {
                        return Some(false);
                    }
                }
                if cond.stroke >= 0
                    && (self.spec.runes[rune as usize].entry_strokes >> cond.stroke) & 1 == 0
                {
                    return Some(false);
                }
            }
        }
        for ex in &cond.except_ {
            match self.cond_matches_right(ex, tokens, at) {
                Some(true) => return Some(false),
                None => unknown = true,
                _ => {}
            }
        }
        if let Some(then) = &cond.then {
            match self.cond_matches_right(then, tokens, at + 1) {
                Some(false) => return Some(false),
                None => unknown = true,
                _ => {}
            }
        }
        if unknown {
            None
        } else {
            Some(true)
        }
    }

    pub fn when_matches(
        &self,
        when: &When,
        left: &LeftCtx,
        entry: i8,
        seam: i8,
        tokens: &[u8; 4],
    ) -> Option<bool> {
        if when.feature >= 0 && (self.features >> when.feature) & 1 == 0 {
            return Some(false);
        }
        if when.self_entry >= 0 {
            let live = if entry != NONE_H { 1 } else { 0 };
            if when.self_entry != live {
                return Some(false);
            }
        }
        if when.self_exit >= 0 {
            let live = if seam != NONE_H { 1 } else { 0 };
            if when.self_exit != live {
                return Some(false);
            }
        }
        let mut unknown = false;
        if when.word >= 0 {
            let position = word_position(left.kind, tok_kind(tokens[0]), tokens[0]);
            match position {
                -1 => unknown = true,
                p if p != when.word => return Some(false),
                _ => {}
            }
        }
        if let Some(c) = &when.left {
            if !self.cond_matches_left(c, left, entry) {
                return Some(false);
            }
        }
        if let Some(c) = &when.right {
            match self.cond_matches_right(c, tokens, 0) {
                Some(false) => return Some(false),
                None => unknown = true,
                _ => {}
            }
        }
        if unknown {
            None
        } else {
            Some(true)
        }
    }

    // --- capability ---------------------------------------------------------

    fn entry_available(
        &mut self,
        rune_idx: u8,
        stance_idx: usize,
        height: i8,
        left: &LeftCtx,
        tokens: &[u8; 4],
    ) -> bool {
        let rune = &self.spec.runes[rune_idx as usize];
        let stance = &rune.stances[stance_idx];
        let mut hit_prov: Option<u32> = None;
        for row in &stance.entries {
            if row.height != height {
                continue;
            }
            if !row.selectable {
                break;
            }
            if row.scope.is_empty() {
                return true;
            }
            for cond in &row.scope {
                if self.cond_matches_left(cond, left, height) {
                    hit_prov = Some(row.prov);
                    break;
                }
            }
            break;
        }
        if let Some(p) = hit_prov {
            self.record_fired(p);
            return true;
        }
        let n = stance.unlocks.len();
        for i in 0..n {
            let u = &self.spec.runes[rune_idx as usize].stances[stance_idx].unlocks[i];
            if u.entry != height || (self.features >> u.feature) & 1 == 0 {
                continue;
            }
            let prov = u.prov;
            match &u.when {
                None => {
                    self.record_fired(prov);
                    return true;
                }
                Some(w) => {
                    let w = w.clone();
                    if self.when_matches(&w, left, height, NONE_H, tokens) != Some(false) {
                        self.record_fired(prov);
                        return true;
                    }
                }
            }
        }
        false
    }

    fn exit_sources(&mut self, rune_idx: u8, stance_idx: usize) -> Vec<(i8, i32, i8)> {
        // (height, scope-row index or -1 for an unlock-born exit, exit_index)
        let stance = &self.spec.runes[rune_idx as usize].stances[stance_idx];
        let mut sources: Vec<(i8, i32, i8)> = Vec::with_capacity(stance.exits.len() + 1);
        for (index, row) in stance.exits.iter().enumerate() {
            sources.push((row.height, index as i32, index as i8));
        }
        let mut offset = sources.len() as i8;
        let mut to_fire: Vec<u32> = Vec::new();
        for u in &stance.unlocks {
            if u.exit >= 0 && (self.features >> u.feature) & 1 == 1 {
                if stance.exits.iter().all(|r| r.height != u.exit) {
                    to_fire.push(u.prov);
                    sources.push((u.exit, -1, offset));
                    offset += 1;
                }
            }
        }
        for p in to_fire {
            self.record_fired(p);
        }
        sources
    }

    fn active_pairing_unlocks(
        &mut self,
        rune_idx: u8,
        stance_idx: usize,
        left: &LeftCtx,
        entry: i8,
        tokens: &[u8; 4],
    ) -> Vec<(i8, i8)> {
        let n = self.spec.runes[rune_idx as usize].stances[stance_idx].unlocks.len();
        let mut active: Vec<(i8, i8)> = Vec::new();
        for i in 0..n {
            let u = &self.spec.runes[rune_idx as usize].stances[stance_idx].unlocks[i];
            let pairing = match u.pairing {
                Some(p) => p,
                None => continue,
            };
            if (self.features >> u.feature) & 1 == 0 {
                continue;
            }
            let prov = u.prov;
            if let Some(w) = u.when.clone() {
                if self.when_matches(&w, left, entry, NONE_H, tokens) == Some(false) {
                    continue;
                }
            }
            self.record_fired(prov);
            active.push(pairing);
        }
        active
    }

    fn pairing_allowed(
        stance: &Stance,
        entry_state: i8,
        exit_state: i8,
        unlocked: &[(i8, i8)],
    ) -> bool {
        let pair = (entry_state, exit_state);
        if unlocked.contains(&pair) {
            return true;
        }
        if stance.never.contains(&pair) {
            return false;
        }
        match &stance.only {
            Some(only) => only.contains(&pair),
            None => true,
        }
    }

    fn refusal_hit(
        &mut self,
        rune_idx: u8,
        candidate: &Candidate,
        left: &LeftCtx,
        tokens: &[u8; 4],
    ) -> Option<u32> {
        let n = self.spec.runes[rune_idx as usize].refuse.len();
        for i in 0..n {
            let record = &self.spec.runes[rune_idx as usize].refuse[i];
            if record.stance >= 0 && record.stance as u8 != candidate.stance {
                continue;
            }
            if record.has_entry && record.entry != candidate.entry {
                continue;
            }
            if record.has_exit && record.exit != candidate.seam {
                continue;
            }
            if record.stance < 0 && !record.has_entry && !record.has_exit && candidate.seam == NONE_H {
                continue;
            }
            let prov = record.prov;
            let ident = record.ident;
            let when = record.when.clone();
            if self.when_matches(&when, left, candidate.entry, candidate.seam, tokens) == Some(true) {
                self.record_fired(prov);
                return Some(ident);
            }
        }
        None
    }

    // --- candidate enumeration ---------------------------------------------

    pub fn candidates(
        &mut self,
        left: &LeftCtx,
        rune_idx: u8,
        tokens: &[u8; 4],
        out: &mut Vec<Candidate>,
        eliminations: bool,
    ) {
        self.n_candidates += 1;
        out.clear();
        let committed = left.seam();
        let n_stances = self.spec.runes[rune_idx as usize].stances.len();
        let mut order: Vec<u8> = std::mem::take(&mut self.scratch_order);
        order.clear();
        order.extend_from_slice(&self.spec.runes[rune_idx as usize].order);
        for s in 0..n_stances {
            let name = self.spec.runes[rune_idx as usize].stances[s].name;
            if !order.contains(&name) {
                order.push(name);
            }
        }
        let right1 = tokens[0];
        let right1_is_letter = tok_is_letter(right1);
        for s in 0..n_stances {
            let sname = self.spec.runes[rune_idx as usize].stances[s].name;
            let order_index = order.iter().position(|&x| x == sname).unwrap() as u8;
            let mut entry = NONE_H;
            if committed != NONE_H {
                if !self.entry_available(rune_idx, s, committed, left, tokens) {
                    if eliminations {
                        let msg = format!(
                            "qs{:02}.st{}: no available entry row at {} against the committed seam",
                            rune_idx,
                            sname,
                            height_name(committed)
                        );
                        self.text_sink = fnv_str(self.text_sink, &msg);
                    }
                    continue;
                }
                entry = committed;
            }
            let st = &self.spec.runes[rune_idx as usize].stances[s];
            if st.require_entry && entry == NONE_H {
                if eliminations {
                    let msg = format!("qs{:02}.st{}: requires a live entry", rune_idx, sname);
                    self.text_sink = fnv_str(self.text_sink, &msg);
                }
                continue;
            }
            let unlocked = self.active_pairing_unlocks(rune_idx, s, left, entry, tokens);
            let entry_state = entry;
            if right1_is_letter {
                let sources = self.exit_sources(rune_idx, s);
                for (height, row_index, exit_index) in sources {
                    let candidate = Candidate {
                        stance: sname,
                        entry,
                        seam: height,
                        order_index,
                        exit_index,
                    };
                    {
                        let st = &self.spec.runes[rune_idx as usize].stances[s];
                        if !Self::pairing_allowed(st, entry_state, height, &unlocked) {
                            if eliminations {
                                let msg = format!(
                                    "qs{:02}.st{}: pairing ({}, {}) not allowed",
                                    rune_idx,
                                    sname,
                                    height_name(entry_state),
                                    height_name(height)
                                );
                                self.text_sink = fnv_str(self.text_sink, &msg);
                            }
                            continue;
                        }
                    }
                    if row_index >= 0 {
                        let (has_scope, prov) = {
                            let row = &self.spec.runes[rune_idx as usize].stances[s].exits
                                [row_index as usize];
                            (!row.scope.is_empty(), row.prov)
                        };
                        if has_scope {
                            let mut scoped = false;
                            let mut fire = false;
                            let n = self.spec.runes[rune_idx as usize].stances[s].exits
                                [row_index as usize]
                                .scope
                                .len();
                            for c in 0..n {
                                let verdict = {
                                    let cond = &self.spec.runes[rune_idx as usize].stances[s].exits
                                        [row_index as usize]
                                        .scope[c];
                                    self.cond_matches_right(cond, &[right1, tokens[1], TOK_UNKNOWN, TOK_UNKNOWN], 0)
                                };
                                if verdict == Some(true) {
                                    fire = true;
                                }
                                if verdict != Some(false) {
                                    scoped = true;
                                    break;
                                }
                            }
                            if fire {
                                self.record_fired(prov);
                            }
                            if !scoped {
                                if eliminations {
                                    let msg = format!(
                                        "qs{:02}.st{}: exit {} toward-scope does not admit {}",
                                        rune_idx,
                                        sname,
                                        height_name(height),
                                        right1
                                    );
                                    self.text_sink = fnv_str(self.text_sink, &msg);
                                }
                                continue;
                            }
                        }
                    }
                    if !self.acceptor_exists(&candidate, rune_idx, right1, tokens[1]) {
                        if eliminations {
                            let msg = format!(
                                "qs{:02}.st{}: exit {} has no refusal-aware acceptor cell on {}",
                                rune_idx,
                                sname,
                                height_name(height),
                                right1
                            );
                            self.text_sink = fnv_str(self.text_sink, &msg);
                        }
                        continue;
                    }
                    if let Some(ident) = self.refusal_hit(rune_idx, &candidate, left, tokens) {
                        if eliminations {
                            let msg = format!(
                                "qs{:02}.st{}: exit {} refused by #{}",
                                rune_idx,
                                sname,
                                height_name(height),
                                ident
                            );
                            self.text_sink = fnv_str(self.text_sink, &msg);
                        }
                        continue;
                    }
                    out.push(candidate);
                }
            }
            {
                let st = &self.spec.runes[rune_idx as usize].stances[s];
                if st.require_exit {
                    continue;
                }
            }
            let non_joining = Candidate {
                stance: sname,
                entry,
                seam: NONE_H,
                order_index,
                exit_index: -1,
            };
            {
                let st = &self.spec.runes[rune_idx as usize].stances[s];
                if !Self::pairing_allowed(st, entry_state, NONE_H, &unlocked) {
                    if eliminations {
                        let msg = format!(
                            "qs{:02}.st{}: pairing ({}, none) not allowed",
                            rune_idx,
                            sname,
                            height_name(entry_state)
                        );
                        self.text_sink = fnv_str(self.text_sink, &msg);
                    }
                    continue;
                }
            }
            if self.refusal_hit(rune_idx, &non_joining, left, tokens).is_some() {
                if eliminations {
                    let msg = format!("qs{:02}.st{}: non-joining cell refused", rune_idx, sname);
                    self.text_sink = fnv_str(self.text_sink, &msg);
                }
                continue;
            }
            out.push(non_joining);
        }
        self.scratch_order = order;
    }

    #[inline(always)]
    fn virtual_left(rune_idx: u8, candidate: &Candidate) -> LeftCtx {
        LeftCtx {
            kind: K_LETTER,
            has: true,
            settled: Settled {
                cell: CellId {
                    rune: rune_idx,
                    stance: candidate.stance,
                    entry: candidate.entry,
                    exit: candidate.seam,
                    adj: 0,
                },
                seam: candidate.seam,
                extension: 0,
            },
        }
    }

    fn acceptor_exists(&mut self, candidate: &Candidate, rune_idx: u8, r1: u8, r2: u8) -> bool {
        if !tok_is_letter(r1) {
            return false;
        }
        let key = ClosureKey {
            rune: rune_idx,
            stance: candidate.stance,
            entry: candidate.entry,
            seam: candidate.seam,
            r1,
            r2,
        };
        if let Some(&cached) = self.closure_cache.get(&key) {
            if let Some(delta) = self.closure_fired.get(&key) {
                let d = delta.clone();
                self.replay_fired(&d);
            }
            return cached;
        }
        self.begin_capture();
        let virtual_left = Self::virtual_left(rune_idx, candidate);
        let mut buf = Vec::new();
        self.candidates(&virtual_left, r1, &[r2, TOK_UNKNOWN, TOK_UNKNOWN, TOK_UNKNOWN], &mut buf, false);
        let result = !buf.is_empty();
        let delta = self.end_capture();
        self.closure_fired.insert(key, delta);
        self.closure_cache.insert(key, result);
        result
    }

    // --- prospect -----------------------------------------------------------

    pub fn prospect(&mut self, rune_idx: u8, candidate: &Candidate, tokens: &[u8; 4]) -> i32 {
        self.n_prospect += 1;
        if !tok_is_letter(tokens[0]) || !tok_is_letter(tokens[1]) {
            return 0;
        }
        let key = ProspectKey {
            rune: rune_idx,
            stance: candidate.stance,
            entry: candidate.entry,
            seam: candidate.seam,
            r1: tokens[0],
            r2: tokens[1],
            r3: tokens[2],
            r4: tokens[3],
        };
        if let Some(&cached) = self.prospect_cache.get(&key) {
            if let Some(delta) = self.prospect_fired.get(&key) {
                let d = delta.clone();
                self.replay_fired(&d);
            }
            return cached;
        }
        self.begin_capture();
        let virtual_left = Self::virtual_left(rune_idx, candidate);
        let shifted = [tokens[1], tokens[2], tokens[3], TOK_UNKNOWN];
        let result = match self.transition_trace(&virtual_left, tokens[0], &shifted) {
            Ok(trace) => {
                if trace.settled.seam != NONE_H {
                    1
                } else {
                    0
                }
            }
            Err(_) => {
                let mut buf = Vec::new();
                self.candidates(
                    &virtual_left,
                    tokens[0],
                    &[tokens[1], TOK_UNKNOWN, TOK_UNKNOWN, TOK_UNKNOWN],
                    &mut buf,
                    false,
                );
                if buf.iter().any(|c| c.seam != NONE_H) {
                    1
                } else {
                    0
                }
            }
        };
        let delta = self.end_capture();
        self.prospect_fired.insert(key, delta);
        self.prospect_cache.insert(key, result);
        result
    }

    // --- prefers ------------------------------------------------------------

    fn cell_pattern_matches(pattern: (i8, i8), candidate: &Candidate) -> bool {
        pattern.0 == candidate.entry && pattern.1 == candidate.seam
    }

    pub fn prefer_favors(
        &mut self,
        owner: u8,
        rec_kind: usize,
        rec_index: usize,
        rune_idx: u8,
        candidate: &Candidate,
        left: &LeftCtx,
        tokens: &[u8; 4],
    ) -> Option<bool> {
        self.n_favors += 1;
        let _ = rec_kind;
        let (when, r_stance, r_cell, r_over) = {
            let r = &self.spec.runes[owner as usize].prefer[rec_index];
            (r.when.clone(), r.stance, r.cell, r.over)
        };
        if owner == rune_idx {
            let verdict = self.when_matches(&when, left, candidate.entry, candidate.seam, tokens);
            if verdict == Some(false) {
                return None;
            }
            if let Some(cell) = r_cell {
                let favored = Self::cell_pattern_matches(cell, candidate);
                if let Some(over) = r_over {
                    if !favored && !Self::cell_pattern_matches(over, candidate) {
                        return None;
                    }
                }
                return Some(favored);
            }
            if r_stance >= 0 {
                return Some(candidate.stance == r_stance as u8);
            }
            return None;
        }
        if !tok_is_letter(tokens[0]) || tokens[0] != owner {
            return None;
        }
        let virtual_left = Self::virtual_left(rune_idx, candidate);
        let vote_right2 = tokens[2];
        let vote_right3 = tokens[3];
        let mut buf = Vec::new();
        self.candidates(
            &virtual_left,
            owner,
            &[tokens[1], vote_right2, TOK_UNKNOWN, TOK_UNKNOWN],
            &mut buf,
            false,
        );
        let vote_tokens = [tokens[1], vote_right2, vote_right3, TOK_UNKNOWN];
        let mut relevant = false;
        for cell in &buf {
            let verdict = self.when_matches(&when, &virtual_left, cell.entry, cell.seam, &vote_tokens);
            if verdict == Some(false) {
                continue;
            }
            relevant = true;
            if r_stance >= 0 && cell.stance == r_stance as u8 {
                return Some(true);
            }
            if let Some(pat) = r_cell {
                if Self::cell_pattern_matches(pat, cell) {
                    return Some(true);
                }
            }
        }
        if relevant {
            Some(false)
        } else {
            None
        }
    }

    fn apply_prefers(
        &mut self,
        mode_absolute: bool,
        rune_idx: u8,
        survivors: &mut Vec<Candidate>,
        left: &LeftCtx,
        tokens: &[u8; 4],
    ) -> Res<()> {
        if survivors.len() <= 1 {
            return Ok(());
        }
        let mut gathered: Vec<(u8, usize)> = Vec::new();
        let mut owners: Vec<u8> = vec![rune_idx];
        if tok_is_letter(tokens[0]) && tokens[0] != rune_idx {
            owners.push(tokens[0]);
        }
        for &owner in &owners {
            for (i, record) in self.spec.runes[owner as usize].prefer.iter().enumerate() {
                if record.absolute != mode_absolute {
                    continue;
                }
                gathered.push((owner, i));
            }
        }
        if gathered.is_empty() {
            return Ok(());
        }
        let mut applicable: Vec<(u8, usize, Vec<Candidate>)> = Vec::new();
        for &(owner, ri) in &gathered {
            let mut favored: Vec<Candidate> = Vec::new();
            let mut relevant = false;
            for idx in 0..survivors.len() {
                let candidate = survivors[idx];
                let vote = self.prefer_favors(owner, 1, ri, rune_idx, &candidate, left, tokens);
                match vote {
                    None => continue,
                    Some(v) => {
                        relevant = true;
                        if v {
                            favored.push(candidate);
                        }
                    }
                }
            }
            if relevant && !favored.is_empty() && favored.len() < survivors.len() {
                applicable.push((owner, ri, favored));
            }
        }
        if applicable.is_empty() {
            return Ok(());
        }
        let mut order: Vec<usize> = (0..applicable.len()).collect();
        let outranked: Vec<usize> = (0..applicable.len())
            .map(|i| {
                (0..applicable.len())
                    .filter(|&j| {
                        j != i
                            && self.outranks(
                                applicable[j].0,
                                applicable[j].1,
                                applicable[i].0,
                                applicable[i].1,
                            )
                    })
                    .count()
            })
            .collect();
        order.sort_by_key(|&i| outranked[i]);

        let mut current: Vec<Candidate> = survivors.clone();
        let mut applied: Vec<(u8, usize)> = Vec::new();
        for &index in &order {
            let (owner, ri, ref favored) = applicable[index];
            let narrowed: Vec<Candidate> =
                current.iter().copied().filter(|c| favored.contains(c)).collect();
            if !narrowed.is_empty() {
                current = narrowed;
                applied.push((owner, ri));
                let prov = self.spec.runes[owner as usize].prefer[ri].prov;
                self.record_fired(prov);
                continue;
            }
            for &(prev_owner, prev_ri) in &applied {
                if self.outranks(prev_owner, prev_ri, owner, ri)
                    || self.outranks(owner, ri, prev_owner, prev_ri)
                {
                    continue;
                }
                return Err(Fail::Raised);
            }
        }
        *survivors = current;
        Ok(())
    }

    #[inline(always)]
    fn outranks(&self, a_owner: u8, a_ri: usize, b_owner: u8, b_ri: usize) -> bool {
        let a = &self.spec.runes[a_owner as usize].prefer[a_ri];
        let b = &self.spec.runes[b_owner as usize].prefer[b_ri];
        if a.weight != b.weight {
            return a.weight > b.weight;
        }
        a_owner < b_owner
    }

    // --- the memoized kernel ------------------------------------------------

    pub fn transition_trace(&mut self, left: &LeftCtx, token: u8, tokens: &[u8; 4]) -> Res<Trace> {
        self.n_trace += 1;
        if !tok_is_letter(token) {
            return Ok(Trace {
                settled: Settled {
                    cell: CellId { rune: 255, stance: 255, entry: NONE_H, exit: NONE_H, adj: 0 },
                    seam: NONE_H,
                    extension: 0,
                },
                joint_floor: false,
                prospect: 0,
                n_notes: 0,
            });
        }
        let key = MemoKey {
            left_kind: left.kind,
            lrune: if left.has { left.settled.cell.rune as i8 } else { -1 },
            lstance: if left.has { left.settled.cell.stance as i8 } else { -1 },
            lseam: if left.has { left.settled.seam } else { -2 },
            lext: if left.has { left.settled.extension } else { 0 },
            token,
            r1: tokens[0],
            r2: tokens[1],
            r3: tokens[2],
            r4: tokens[3],
        };
        if let Some(&trace) = self.trace_cache.get(&key) {
            if let Some(delta) = self.trace_fired.get(&key) {
                let d = delta.clone();
                self.replay_fired(&d);
            }
            return Ok(trace);
        }
        if self.share_blind(left, token, tokens) {
        match &self.share {
            Share::None => {}
            Share::Local(map) => {
                if let Some(&trace) = map.get(&key) {
                    return Ok(trace);
                }
            }
            Share::Shared(lock) => {
                let guard = lock.read().unwrap();
                if let Some(&trace) = guard.get(&key) {
                    return Ok(trace);
                }
            }
        }
        }
        self.begin_capture();
        match self.transition_trace_uncached(left, token, tokens) {
            Ok(trace) => {
                let delta = self.end_capture();
                self.trace_fired.insert(key, delta);
                self.trace_cache.insert(key, trace);
                Ok(trace)
            }
            Err(e) => {
                self.abort_capture();
                Err(e)
            }
        }
    }

    #[inline(always)]
    fn share_blind(&self, left: &LeftCtx, token: u8, tokens: &[u8; 4]) -> bool {
        let delta = self.share_delta;
        if delta == 0 {
            return true;
        }
        if left.kind == K_LETTER
            && left.has
            && self.spec.runes[left.settled.cell.rune as usize].feature_mask & delta != 0
        {
            return false;
        }
        if self.spec.runes[token as usize].feature_mask & delta != 0 {
            return false;
        }
        for &t in tokens {
            if tok_is_letter(t) && self.spec.runes[t as usize].feature_mask & delta != 0 {
                return false;
            }
        }
        true
    }

    fn transition_trace_uncached(&mut self, left: &LeftCtx, token: u8, tokens: &[u8; 4]) -> Res<Trace> {
        let rune_idx = token;
        let committed = left.seam();
        let locked = left.kind == K_ZWNJ && self.spec.runes[rune_idx as usize].entry_bearing;

        let mut notes: Vec<String> = std::mem::take(&mut self.scratch_notes);
        notes.clear();
        let mut survivors: Vec<Candidate> = Vec::new();
        self.candidates(left, rune_idx, tokens, &mut survivors, true);
        if survivors.is_empty() {
            self.scratch_notes = notes;
            if committed != NONE_H {
                return Err(Fail::Stranded);
            }
            return Err(Fail::NoCandidates);
        }

        let n_ranked = survivors.len();
        let mut ranked: Vec<(Candidate, Ranked)> = Vec::with_capacity(n_ranked);
        for i in 0..survivors.len() {
            let c = survivors[i];
            let p = self.prospect(rune_idx, &c, tokens);
            let left_term = if committed != NONE_H { 1 } else { 0 };
            let own_term = if c.seam != NONE_H { 1 } else { 0 };
            let p2 = self.prospect(rune_idx, &c, tokens);
            ranked.push((c, Ranked { join_count: left_term + own_term + p, prospect: p2 }));
        }
        let mut decided_stage = 0u8; // only-candidate

        self.apply_prefers(true, rune_idx, &mut survivors, left, tokens)?;
        if survivors.len() == 1 && decided_stage == 0 && n_ranked > 1 {
            decided_stage = 1;
        }

        if survivors.len() > 1 {
            let best = survivors
                .iter()
                .map(|c| ranked.iter().find(|(k, _)| k == c).unwrap().1.join_count)
                .max()
                .unwrap();
            let narrowed: Vec<Candidate> = survivors
                .iter()
                .copied()
                .filter(|c| ranked.iter().find(|(k, _)| k == c).unwrap().1.join_count == best)
                .collect();
            if narrowed.len() < survivors.len() && narrowed.len() == 1 {
                decided_stage = 2;
            }
            survivors = narrowed;
        }

        if survivors.len() > 1 {
            self.apply_prefers(false, rune_idx, &mut survivors, left, tokens)?;
            if survivors.len() == 1 {
                decided_stage = 3;
            }
        }

        if survivors.len() > 1 {
            let best_order = survivors.iter().map(|c| c.order_index).min().unwrap();
            let narrowed: Vec<Candidate> =
                survivors.iter().copied().filter(|c| c.order_index == best_order).collect();
            if narrowed.len() == 1 {
                decided_stage = 4;
            }
            survivors = narrowed;
        }

        let mut joint_floor = false;
        if survivors.len() > 1 {
            survivors.sort_by_key(|c| {
                (
                    if c.seam != NONE_H { 0 } else { 1 },
                    height_y(c.seam),
                    c.exit_index,
                )
            });
            let _ = decided_stage;
            joint_floor = (survivors[0].seam == NONE_H) != (survivors[1].seam == NONE_H);
            survivors.truncate(1);
        }

        let winner = survivors[0];
        let settled = self.commit(rune_idx, &winner, locked, left, tokens, &mut notes);
        let prospect = ranked.iter().find(|(k, _)| *k == winner).unwrap().1.prospect;
        // The Python baseline sorts and keeps the ranked tuple on the trace; the port pays the sort and folds
        // the notes into the text sink, then stores only what the fixpoint reads.
        ranked.sort_by_key(|(c, r)| (-r.join_count, c.order_index, c.exit_index));
        let mut n_notes = 0u32;
        for note in notes.iter() {
            self.text_sink = fnv_str(self.text_sink, note);
            n_notes += 1;
        }
        self.text_sink ^= (ranked.len() as u64) ^ ((n_notes as u64) << 8) ^ ((decided_stage as u64) << 20);
        self.scratch_notes = notes;
        Ok(Trace { settled, joint_floor, prospect, n_notes })
    }

    fn pick_adjustment(
        &mut self,
        extend: bool,
        rune_idx: u8,
        winner: &Candidate,
        side_entry: bool,
        height: i8,
        left: &LeftCtx,
        tokens: &[u8; 4],
    ) -> Option<usize> {
        let n = if extend {
            self.spec.runes[rune_idx as usize].extend.len()
        } else {
            self.spec.runes[rune_idx as usize].contract.len()
        };
        let mut best: Option<(u32, usize)> = None;
        for i in 0..n {
            let (stance, has_entry, entry, has_exit, exit, ident, when) = {
                let r = if extend {
                    &self.spec.runes[rune_idx as usize].extend[i]
                } else {
                    &self.spec.runes[rune_idx as usize].contract[i]
                };
                (r.stance, r.has_entry, r.entry, r.has_exit, r.exit, r.ident, r.when.clone())
            };
            if stance >= 0 && stance as u8 != winner.stance {
                continue;
            }
            if side_entry {
                if !has_entry || entry != height {
                    continue;
                }
            } else if !has_exit || exit != height {
                continue;
            }
            if self.when_matches(&when, left, winner.entry, winner.seam, tokens) != Some(true) {
                continue;
            }
            if best.is_none() || ident < best.unwrap().0 {
                best = Some((ident, i));
            }
        }
        best.map(|(_, i)| i)
    }

    fn commit(
        &mut self,
        rune_idx: u8,
        winner: &Candidate,
        locked: bool,
        left: &LeftCtx,
        tokens: &[u8; 4],
        notes: &mut Vec<String>,
    ) -> Settled {
        let mut adj: u16 = 0;
        if locked {
            adj |= ADJ_LOCKED;
        }
        if winner.entry != NONE_H {
            let n_stances = self.spec.runes[rune_idx as usize].stances.len();
            for s in 0..n_stances {
                if self.spec.runes[rune_idx as usize].stances[s].name == winner.stance {
                    if self.entry_available(rune_idx, s, winner.entry, left, tokens) {
                        let note = format!("entry live at {}", height_name(winner.entry));
                        if !notes.contains(&note) {
                            notes.push(note);
                        }
                    }
                    break;
                }
            }
            let mut extend = self.pick_adjustment(true, rune_idx, winner, true, winner.entry, left, tokens);
            let contract = self.pick_adjustment(false, rune_idx, winner, true, winner.entry, left, tokens);
            if extend.is_some() && left.has && left.settled.extension > 0 {
                extend = None;
            }
            if let Some(i) = extend {
                let (prov, by) = {
                    let r = &self.spec.runes[rune_idx as usize].extend[i];
                    (r.prov, r.by)
                };
                self.record_fired(prov);
                adj = adj_set(adj, 1, by);
            }
            if let Some(i) = contract {
                let (prov, by) = {
                    let r = &self.spec.runes[rune_idx as usize].contract[i];
                    (r.prov, r.by)
                };
                self.record_fired(prov);
                adj = adj_set(adj, 3, by);
            }
        }
        let mut extension: i8 = 0;
        if winner.seam != NONE_H {
            let extend = self.pick_adjustment(true, rune_idx, winner, false, winner.seam, left, tokens);
            let contract = self.pick_adjustment(false, rune_idx, winner, false, winner.seam, left, tokens);
            if let Some(i) = extend {
                let (prov, by) = {
                    let r = &self.spec.runes[rune_idx as usize].extend[i];
                    (r.prov, r.by)
                };
                self.record_fired(prov);
                extension += by;
                adj = adj_set(adj, 5, by);
            }
            if let Some(i) = contract {
                let (prov, by) = {
                    let r = &self.spec.runes[rune_idx as usize].contract[i];
                    (r.prov, r.by)
                };
                self.record_fired(prov);
                extension -= by;
                adj = adj_set(adj, 7, by);
            }
        }
        Settled {
            cell: CellId {
                rune: rune_idx,
                stance: winner.stance,
                entry: winner.entry,
                exit: winner.seam,
                adj,
            },
            seam: winner.seam,
            extension,
        }
    }
}

pub fn word_position(left_kind: u8, right1_kind: u8, right1: u8) -> i8 {
    let initial = left_kind == K_EDGE || left_kind == K_SPACE || left_kind == K_ZWNJ;
    if right1 == TOK_UNKNOWN {
        return -1;
    }
    let final_ = right1_kind == K_EDGE || right1_kind == K_SPACE || right1_kind == K_ZWNJ;
    if initial && final_ {
        return 3;
    }
    if initial {
        return 0;
    }
    if final_ {
        return 2;
    }
    1
}

pub fn cell_label(cell: &CellId) -> String {
    let mut s = format!("qs{:02}.st{}", cell.rune, cell.stance);
    if cell.entry != NONE_H {
        s.push_str(&format!(".en-y{}", height_y(cell.entry)));
    }
    if cell.exit != NONE_H {
        s.push_str(&format!(".ex-y{}", height_y(cell.exit)));
    }
    if cell.adj & ADJ_LOCKED != 0 {
        s.push_str(".locked");
    }
    for (shift, name) in [(1u32, "en-ext"), (3, "en-con"), (5, "ex-ext"), (7, "ex-con")] {
        let by = (cell.adj >> shift) & 3;
        if by != 0 {
            s.push_str(&format!(".{}-{}", name, by));
        }
    }
    s
}

pub fn token_label(t: u8) -> String {
    if tok_is_letter(t) {
        format!("qs{:02}", t)
    } else {
        match t {
            TOK_EDGE => "edge".to_string(),
            TOK_SPACE => "space".to_string(),
            TOK_ZWNJ => "zwnj".to_string(),
            TOK_NAMER => "namer-dot".to_string(),
            _ => "unknown".to_string(),
        }
    }
}
