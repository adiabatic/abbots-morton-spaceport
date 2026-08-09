mod engine;
mod fx;
mod memo;
mod spec;

use engine::*;
use fx::{FxMap, FxSet};
use spec::*;
use std::sync::{Arc, RwLock};
use std::time::Instant;

// --- deep-slot liveness (table._ProspectLiveness) ----------------------------

const SEAT_RAISED: u64 = u64::MAX;
const SEAT_UNREACHABLE: u64 = u64::MAX - 1;
const NO_TOK: u8 = 255;

struct Builder<'a> {
    eng: Engine<'a>,
    third_seat: FxMap<(u8, u8, u8), bool>,
    third_joint: FxMap<(u8, u8, u8), bool>,
    pv3: FxMap<(u8, u8, u32), bool>,
    vv3: FxMap<(u8, u8, u32), bool>,
    fourth_seat: FxMap<(u8, u8, u8, u8), bool>,
    pv4: FxMap<(u8, u8, u8, u32), bool>,
    vv4: FxMap<(u8, u8, u8, u32), bool>,
    sigs: FxMap<(u8, u8, u8, i8), u32>,
    sig_intern: std::collections::HashMap<(i8, Vec<bool>), u32>,
    shapes: Vec<Option<Vec<(u8, i8)>>>,
    left_conds: Vec<Option<Vec<Condition>>>,
    left_classes: Vec<Option<Vec<LeftCtx>>>,
    probes: Vec<u8>,
    chains3: Vec<Vec<Condition>>,
    chains4: Vec<Vec<Condition>>,
    third_verdicts: FxMap<(u8, u8, u8), bool>,
    fourth_verdicts: FxMap<(u8, u8, u8, u8), bool>,
}

fn chain_reach(c: &Condition) -> i32 {
    let mut reach = 0;
    if let Some(t) = &c.then {
        reach = reach.max(1 + chain_reach(t));
    }
    for e in &c.except_ {
        reach = reach.max(chain_reach(e));
    }
    reach
}

impl<'a> Builder<'a> {
    fn new(spec: &'a Spec, features: u8, share: Share<'a>, share_delta: u8) -> Self {
        let n = spec.runes.len();
        let probes: Vec<u8> = (0..spec.n_letters as u8).collect();
        let mut chains3 = vec![Vec::new(); n];
        let mut chains4 = vec![Vec::new(); n];
        for i in 0..n {
            for r in &spec.runes[i].prefer {
                if let Some(right) = &r.when.right {
                    let reach = chain_reach(right);
                    if reach >= 2 {
                        chains3[i].push(right.clone());
                    }
                    if reach >= 3 {
                        chains4[i].push(right.clone());
                    }
                }
            }
        }
        Builder {
            eng: Engine::new(spec, features, share, share_delta),
            third_seat: FxMap::default(),
            third_joint: FxMap::default(),
            pv3: FxMap::default(),
            vv3: FxMap::default(),
            fourth_seat: FxMap::default(),
            pv4: FxMap::default(),
            vv4: FxMap::default(),
            sigs: FxMap::default(),
            sig_intern: std::collections::HashMap::new(),
            shapes: vec![None; n],
            left_conds: vec![None; n],
            left_classes: vec![None; n],
            probes,
            chains3,
            chains4,
            third_verdicts: FxMap::default(),
            fourth_verdicts: FxMap::default(),
        }
    }

    fn input_shapes(&mut self, family: u8) -> Vec<(u8, i8)> {
        if let Some(v) = &self.shapes[family as usize] {
            return v.clone();
        }
        let mut out: Vec<(u8, i8)> = Vec::new();
        for stance in &self.eng.spec.runes[family as usize].stances {
            let mut seams: Vec<i8> = vec![NONE_H];
            for row in &stance.exits {
                seams.push(row.height);
            }
            for u in &stance.unlocks {
                if u.exit >= 0 {
                    seams.push(u.exit);
                }
            }
            let mut seen: Vec<i8> = Vec::new();
            for s in seams {
                if !seen.contains(&s) {
                    seen.push(s);
                    out.push((stance.name, s));
                }
            }
        }
        self.shapes[family as usize] = Some(out.clone());
        out
    }

    fn left_conditions(&mut self, follower: u8) -> Vec<Condition> {
        if let Some(v) = &self.left_conds[follower as usize] {
            return v.clone();
        }
        let mut out: Vec<Condition> = Vec::new();
        let rune = &self.eng.spec.runes[follower as usize];
        for stance in &rune.stances {
            for row in &stance.entries {
                out.extend(row.scope.iter().cloned());
            }
        }
        for pool in [&rune.refuse, &rune.prefer] {
            for record in pool {
                if let Some(c) = &record.when.left {
                    out.push(c.clone());
                }
            }
        }
        self.left_conds[follower as usize] = Some(out.clone());
        out
    }

    fn virtual_left(family: u8, stance: u8, seam: i8) -> LeftCtx {
        LeftCtx {
            kind: K_LETTER,
            has: true,
            settled: Settled {
                cell: CellId { rune: family, stance, entry: NONE_H, exit: seam, adj: 0 },
                seam,
                extension: 0,
            },
        }
    }

    fn signature(&mut self, follower: u8, family: u8, stance: u8, seam: i8) -> u32 {
        let key = (follower, family, stance, seam);
        if let Some(&v) = self.sigs.get(&key) {
            return v;
        }
        let virtual_left = Self::virtual_left(family, stance, seam);
        let conds = self.left_conditions(follower);
        let verdicts: Vec<bool> = conds
            .iter()
            .map(|c| self.eng.cond_matches_left(c, &virtual_left, seam))
            .collect();
        let next = self.sig_intern.len() as u32;
        let id = *self.sig_intern.entry((seam, verdicts)).or_insert(next);
        self.sigs.insert(key, id);
        id
    }

    fn third_live(&mut self, family: u8, r1: u8, r2: u8) -> bool {
        let stage_one =
            self.prospect_varies_third(family, r1, r2) || self.vote_varies_third(family, r1, r2);
        if stage_one {
            let key = (family, r1, r2);
            let verdict = match self.third_seat.get(&key) {
                Some(&v) => v,
                None => {
                    let v = self.seat_varies(family, r1, r2, NO_TOK);
                    self.third_seat.insert(key, v);
                    v
                }
            };
            if verdict {
                return true;
            }
        }
        let key = (family, r1, r2);
        if let Some(&v) = self.third_joint.get(&key) {
            return v;
        }
        let mut verdict = false;
        for i in 0..self.probes.len() {
            let t = self.probes[i];
            if self.fourth_live(family, r1, r2, t) {
                verdict = true;
                break;
            }
        }
        self.third_joint.insert(key, verdict);
        verdict
    }

    fn prospect_varies_third(&mut self, family: u8, r1: u8, r2: u8) -> bool {
        for (stance, seam) in self.input_shapes(family) {
            let sig = self.signature(r1, family, stance, seam);
            let key = (r1, r2, sig);
            let verdict = match self.pv3.get(&key) {
                Some(&v) => v,
                None => {
                    let v = self.third_class_live(family, stance, seam, r1, r2);
                    self.pv3.insert(key, v);
                    v
                }
            };
            if verdict {
                return true;
            }
        }
        false
    }

    fn third_class_live(&mut self, family: u8, stance: u8, seam: i8, r1: u8, r2: u8) -> bool {
        let candidate = Candidate { stance, entry: NONE_H, seam, order_index: 0, exit_index: -1 };
        let baseline = self.eng.prospect(family, &candidate, &[r1, r2, TOK_EDGE, TOK_EDGE]);
        for i in 0..self.probes.len() {
            let t = self.probes[i];
            let edge4 = self.eng.prospect(family, &candidate, &[r1, r2, t, TOK_EDGE]);
            if edge4 != baseline {
                return true;
            }
            if self.eng.prospect(family, &candidate, &[r1, r2, t, TOK_UNKNOWN]) != edge4 {
                return true;
            }
        }
        false
    }

    fn fourth_live(&mut self, family: u8, r1: u8, r2: u8, r3: u8) -> bool {
        let stage_one = self.prospect_varies_fourth(family, r1, r2, r3)
            || self.vote_varies_fourth(family, r1, r2, r3);
        if !stage_one {
            return false;
        }
        let key = (family, r1, r2, r3);
        if let Some(&v) = self.fourth_seat.get(&key) {
            return v;
        }
        let v = self.seat_varies(family, r1, r2, r3);
        self.fourth_seat.insert(key, v);
        v
    }

    fn prospect_varies_fourth(&mut self, family: u8, r1: u8, r2: u8, r3: u8) -> bool {
        for (stance, seam) in self.input_shapes(family) {
            let sig = self.signature(r1, family, stance, seam);
            let key = (r1, r2, r3, sig);
            let verdict = match self.pv4.get(&key) {
                Some(&v) => v,
                None => {
                    let v = self.fourth_class_live(family, stance, seam, r1, r2, r3);
                    self.pv4.insert(key, v);
                    v
                }
            };
            if verdict {
                return true;
            }
        }
        false
    }

    fn fourth_class_live(
        &mut self,
        family: u8,
        stance: u8,
        seam: i8,
        r1: u8,
        r2: u8,
        r3: u8,
    ) -> bool {
        let candidate = Candidate { stance, entry: NONE_H, seam, order_index: 0, exit_index: -1 };
        let baseline = self.eng.prospect(family, &candidate, &[r1, r2, r3, TOK_EDGE]);
        for i in 0..self.probes.len() {
            let t = self.probes[i];
            if self.eng.prospect(family, &candidate, &[r1, r2, r3, t]) != baseline {
                return true;
            }
        }
        false
    }

    fn vote_varies_third(&mut self, family: u8, r1: u8, r2: u8) -> bool {
        if r1 == family || self.eng.spec.runes[r1 as usize].prefer.is_empty() {
            return false;
        }
        for (stance, seam) in self.input_shapes(family) {
            let sig = self.signature(r1, family, stance, seam);
            let key = (r1, r2, sig);
            let verdict = match self.vv3.get(&key) {
                Some(&v) => v,
                None => {
                    let v = self.vote_class_live(family, stance, seam, r1, r2, NO_TOK);
                    self.vv3.insert(key, v);
                    v
                }
            };
            if verdict {
                return true;
            }
        }
        false
    }

    fn vote_varies_fourth(&mut self, family: u8, r1: u8, r2: u8, r3: u8) -> bool {
        if r1 == family || self.eng.spec.runes[r1 as usize].prefer.is_empty() {
            return false;
        }
        for (stance, seam) in self.input_shapes(family) {
            let sig = self.signature(r1, family, stance, seam);
            let key = (r1, r2, r3, sig);
            let verdict = match self.vv4.get(&key) {
                Some(&v) => v,
                None => {
                    let v = self.vote_class_live(family, stance, seam, r1, r2, r3);
                    self.vv4.insert(key, v);
                    v
                }
            };
            if verdict {
                return true;
            }
        }
        false
    }

    fn vote_class_live(
        &mut self,
        family: u8,
        stance: u8,
        seam: i8,
        r1: u8,
        r2: u8,
        r3: u8,
    ) -> bool {
        let candidate = Candidate { stance, entry: NONE_H, seam, order_index: 0, exit_index: -1 };
        let owner = r1;
        let edge_left = LeftCtx::boundary(K_EDGE);
        let n = self.eng.spec.runes[owner as usize].prefer.len();
        for ri in 0..n {
            if r3 == NO_TOK {
                let baseline = self.eng.prefer_favors(
                    owner,
                    1,
                    ri,
                    family,
                    &candidate,
                    &edge_left,
                    &[r1, r2, TOK_EDGE, TOK_EDGE],
                );
                for i in 0..self.probes.len() {
                    let t = self.probes[i];
                    let edge4 = self.eng.prefer_favors(
                        owner,
                        1,
                        ri,
                        family,
                        &candidate,
                        &edge_left,
                        &[r1, r2, t, TOK_EDGE],
                    );
                    if edge4 != baseline {
                        return true;
                    }
                    let unk = self.eng.prefer_favors(
                        owner,
                        1,
                        ri,
                        family,
                        &candidate,
                        &edge_left,
                        &[r1, r2, t, TOK_UNKNOWN],
                    );
                    if unk != edge4 {
                        return true;
                    }
                }
            } else {
                let baseline = self.eng.prefer_favors(
                    owner,
                    1,
                    ri,
                    family,
                    &candidate,
                    &edge_left,
                    &[r1, r2, r3, TOK_EDGE],
                );
                for i in 0..self.probes.len() {
                    let t = self.probes[i];
                    let v = self.eng.prefer_favors(
                        owner,
                        1,
                        ri,
                        family,
                        &candidate,
                        &edge_left,
                        &[r1, r2, r3, t],
                    );
                    if v != baseline {
                        return true;
                    }
                }
            }
        }
        false
    }

    fn seat_left_classes(&mut self, family: u8) -> Vec<LeftCtx> {
        if let Some(v) = &self.left_classes[family as usize] {
            return v.clone();
        }
        let mut out = vec![
            LeftCtx::boundary(K_EDGE),
            LeftCtx::boundary(K_SPACE),
            LeftCtx::boundary(K_ZWNJ),
            LeftCtx::boundary(K_NAMER),
        ];
        let mut seen: FxSet<u32> = FxSet::default();
        let order = self.eng.spec.order.clone();
        for left_family in order {
            for (stance, seam) in self.input_shapes(left_family) {
                let sig = self.signature(family, left_family, stance, seam);
                if !seen.insert(sig) {
                    continue;
                }
                out.push(Self::virtual_left(left_family, stance, seam));
            }
        }
        self.left_classes[family as usize] = Some(out.clone());
        out
    }

    fn seat_outcome(&mut self, left: &LeftCtx, token: u8, tokens: &[u8; 4]) -> u64 {
        match self.eng.transition_trace(left, token, tokens) {
            Ok(trace) => {
                let c = trace.settled.cell;
                (c.rune as u64)
                    | ((c.stance as u64) << 8)
                    | (((c.entry as i16 + 2) as u64) << 16)
                    | (((c.exit as i16 + 2) as u64) << 24)
                    | ((c.adj as u64) << 32)
            }
            Err(Fail::Raised) => SEAT_RAISED,
            Err(_) => SEAT_UNREACHABLE,
        }
    }

    fn seat_varies(&mut self, family: u8, r1: u8, r2: u8, r3: u8) -> bool {
        let token = family;
        let classes = self.seat_left_classes(family);
        for left in &classes {
            let baseline = if r3 == NO_TOK {
                self.seat_outcome(left, token, &[r1, r2, TOK_EDGE, TOK_EDGE])
            } else {
                self.seat_outcome(left, token, &[r1, r2, r3, TOK_EDGE])
            };
            if baseline == SEAT_RAISED {
                return true;
            }
            if baseline == SEAT_UNREACHABLE {
                continue;
            }
            for i in 0..self.probes.len() {
                let t = self.probes[i];
                if r3 == NO_TOK {
                    let edge4 = self.seat_outcome(left, token, &[r1, r2, t, TOK_EDGE]);
                    if edge4 == SEAT_RAISED || edge4 == SEAT_UNREACHABLE || edge4 != baseline {
                        return true;
                    }
                    let unk = self.seat_outcome(left, token, &[r1, r2, t, TOK_UNKNOWN]);
                    if unk == SEAT_RAISED || unk == SEAT_UNREACHABLE || unk != edge4 {
                        return true;
                    }
                } else {
                    let v = self.seat_outcome(left, token, &[r1, r2, r3, t]);
                    if v == SEAT_RAISED || v == SEAT_UNREACHABLE || v != baseline {
                        return true;
                    }
                }
            }
        }
        false
    }

    fn third_matters(&mut self, family: u8, r1: u8, r2: u8) -> bool {
        let key = (family, r1, r2);
        if let Some(&v) = self.third_verdicts.get(&key) {
            return v;
        }
        let tokens = [r1, r2, TOK_UNKNOWN, TOK_UNKNOWN];
        let mut verdict = false;
        for i in 0..self.chains3[family as usize].len() {
            let c = self.chains3[family as usize][i].clone();
            if self.eng.cond_matches_right(&c, &tokens, 0).is_none() {
                verdict = true;
                break;
            }
        }
        if !verdict {
            verdict = self.third_live(family, r1, r2);
        }
        self.third_verdicts.insert(key, verdict);
        verdict
    }

    fn fourth_matters(&mut self, family: u8, r1: u8, r2: u8, r3: u8) -> bool {
        let key = (family, r1, r2, r3);
        if let Some(&v) = self.fourth_verdicts.get(&key) {
            return v;
        }
        let tokens = [r1, r2, r3, TOK_UNKNOWN];
        let mut verdict = false;
        for i in 0..self.chains4[family as usize].len() {
            let c = self.chains4[family as usize][i].clone();
            if self.eng.cond_matches_right(&c, &tokens, 0).is_none() {
                verdict = true;
                break;
            }
        }
        if !verdict {
            verdict = self.fourth_live(family, r1, r2, r3);
        }
        self.fourth_verdicts.insert(key, verdict);
        verdict
    }
}

// --- the fixpoint (table.build_tables) --------------------------------------

#[derive(Hash, Eq, PartialEq, Clone, Copy)]
struct SeenKey {
    lkind: u8,
    lhas: bool,
    lrune: u8,
    lstance: u8,
    lentry: i8,
    lexit: i8,
    ladj: u16,
    lseam: i8,
    lext: i8,
    irune: u8,
    r1c: u8,
    r2a: u8,
    r3a: u8,
}

#[derive(Hash, Eq, PartialEq, Clone, Copy)]
struct WinKey {
    irune: u8,
    locked: bool,
    lkind: u8,
    lrune: u8,
    lstance: u8,
    lentry: i8,
    lexit: i8,
    ladj: u16,
    r1: u8,
    r2: u8,
    r3: u8,
    r4: u8,
}

#[derive(Clone, Copy)]
struct Row {
    settled: Settled,
    joint: bool,
    prospect: i32,
}

pub struct BuildResult {
    pub windows: usize,
    pub cells: usize,
    pub checksum: u64,
    pub nocand: u64,
    pub stranded: u64,
    pub raised: u64,
    pub candidates: u64,
    pub prospect: u64,
    pub trace: u64,
    pub favors: u64,
    pub memo_entries: usize,
    pub fired: u32,
    pub text_sink: u64,
}

fn build_tables(
    spec: &Spec,
    features: u8,
    share: Share<'_>,
    share_delta: u8,
) -> (BuildResult, FxMap<MemoKey, Trace>) {
    let mut b = Builder::new(spec, features, share, share_delta);
    let letters: Vec<u8> = (0..spec.n_letters as u8).collect();
    let mut right_options: Vec<u8> = vec![TOK_EDGE, TOK_SPACE, TOK_ZWNJ, TOK_NAMER];
    right_options.extend_from_slice(&letters);
    let mut formation_pairs: Vec<(u8, u8)> = Vec::new();
    for &i in &spec.order {
        if let Some(seq) = spec.runes[i as usize].sequence {
            formation_pairs.push(seq);
        }
    }

    let mut transitions: FxMap<WinKey, Row> = FxMap::default();
    let mut seen: FxSet<SeenKey> = FxSet::default();
    let mut worklist: Vec<(LeftCtx, u8, u8, u8, u8)> = Vec::new();
    for kind in [K_EDGE, K_SPACE, K_ZWNJ, K_NAMER] {
        for &name in &spec.order {
            worklist.push((LeftCtx::boundary(kind), name, NO_TOK, NO_TOK, NO_TOK));
        }
    }
    let mut nocand = 0u64;
    let mut stranded = 0u64;
    let mut raised = 0u64;

    while let Some((left, rune_name, r1c, r2a, r3a)) = worklist.pop() {
        let sk = SeenKey {
            lkind: left.kind,
            lhas: left.has,
            lrune: left.settled.cell.rune,
            lstance: left.settled.cell.stance,
            lentry: left.settled.cell.entry,
            lexit: left.settled.cell.exit,
            ladj: left.settled.cell.adj,
            lseam: left.settled.seam,
            lext: left.settled.extension,
            irune: rune_name,
            r1c,
            r2a,
            r3a,
        };
        if !seen.insert(sk) {
            continue;
        }
        let locked = left.kind == K_ZWNJ && spec.runes[rune_name as usize].entry_bearing;
        let seq = spec.runes[rune_name as usize].sequence;

        let r1_options: Vec<u8> = if r1c != NO_TOK { vec![r1c] } else { right_options.clone() };
        for &right1 in &r1_options {
            let right2_options: Vec<u8> = if tok_is_letter(right1) {
                let mut v: Vec<u8> = right_options
                    .iter()
                    .copied()
                    .filter(|&r| !(tok_is_letter(r) && formation_pairs.contains(&(right1, r))))
                    .collect();
                if r2a != NO_TOK {
                    v.retain(|&r| r == r2a);
                }
                if let Some((_, b2)) = seq {
                    v.retain(|&r| !tok_is_letter(r) || r != b2);
                }
                v
            } else {
                vec![TOK_EDGE]
            };
            for &right2 in &right2_options {
                let right3_slots: Vec<u8> = if tok_is_letter(right1)
                    && tok_is_letter(right2)
                    && b.third_matters(rune_name, right1, right2)
                {
                    let mut v: Vec<u8> = right_options
                        .iter()
                        .copied()
                        .filter(|&r| !(tok_is_letter(r) && formation_pairs.contains(&(right2, r))))
                        .collect();
                    if r3a != NO_TOK {
                        v.retain(|&r| r == r3a);
                    }
                    v
                } else {
                    vec![NO_TOK]
                };
                for &right3 in &right3_slots {
                    let right4_slots: Vec<u8> = if right3 != NO_TOK
                        && tok_is_letter(right3)
                        && b.fourth_matters(rune_name, right1, right2, right3)
                    {
                        right_options
                            .iter()
                            .copied()
                            .filter(|&r| {
                                !(tok_is_letter(r) && formation_pairs.contains(&(right3, r)))
                            })
                            .collect()
                    } else {
                        vec![NO_TOK]
                    };
                    for &right4 in &right4_slots {
                        let wk = WinKey {
                            irune: rune_name,
                            locked,
                            lkind: left.kind,
                            lrune: if left.has { left.settled.cell.rune } else { 0 },
                            lstance: if left.has { left.settled.cell.stance } else { 0 },
                            lentry: if left.has { left.settled.cell.entry } else { 0 },
                            lexit: if left.has { left.settled.cell.exit } else { 0 },
                            ladj: if left.has { left.settled.cell.adj } else { 0 },
                            r1: right1,
                            r2: if tok_is_letter(right1) { right2 } else { NO_TOK },
                            r3: right3,
                            r4: right4,
                        };
                        let settled;
                        if let Some(existing) = transitions.get(&wk) {
                            settled = existing.settled;
                        } else {
                            let tokens = [
                                right1,
                                right2,
                                if right3 != NO_TOK { right3 } else { TOK_EDGE },
                                if right4 != NO_TOK { right4 } else { TOK_EDGE },
                            ];
                            match b.eng.transition_trace(&left, rune_name, &tokens) {
                                Ok(trace) => {
                                    settled = trace.settled;
                                    transitions.insert(
                                        wk,
                                        Row {
                                            settled: trace.settled,
                                            joint: trace.joint_floor,
                                            prospect: trace.prospect,
                                        },
                                    );
                                }
                                Err(e) => {
                                    match e {
                                        Fail::Stranded => stranded += 1,
                                        Fail::NoCandidates => nocand += 1,
                                        Fail::Raised => raised += 1,
                                    }
                                    continue;
                                }
                            }
                        }
                        if tok_is_letter(right1) {
                            let successor_allowed = if right3 != NO_TOK { right3 } else { r3a };
                            let successor_r3 = right4;
                            worklist.push((
                                LeftCtx { kind: K_LETTER, has: true, settled },
                                right1,
                                right2,
                                successor_allowed,
                                successor_r3,
                            ));
                        }
                    }
                }
            }
        }
    }

    // Canonical rendering: the same tab-joined row text model.py hashes, sorted the same way.
    let mut lines: Vec<String> = Vec::with_capacity(transitions.len());
    let mut cells: FxSet<Settled> = FxSet::default();
    for (wk, row) in transitions.iter() {
        cells.insert(row.settled);
        let input_label = if wk.locked {
            format!("qs{:02}.noentry", wk.irune)
        } else {
            format!("qs{:02}", wk.irune)
        };
        let left_label = if wk.lkind == K_LETTER {
            cell_label(&CellId {
                rune: wk.lrune,
                stance: wk.lstance,
                entry: wk.lentry,
                exit: wk.lexit,
                adj: wk.ladj,
            })
        } else {
            match wk.lkind {
                K_EDGE => "edge".to_string(),
                K_SPACE => "space".to_string(),
                K_ZWNJ => "zwnj".to_string(),
                _ => "namer-dot".to_string(),
            }
        };
        let na = "#NA".to_string();
        lines.push(format!(
            "{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}",
            input_label,
            left_label,
            token_label(wk.r1),
            if wk.r2 == NO_TOK { na.clone() } else { token_label(wk.r2) },
            if wk.r3 == NO_TOK { na.clone() } else { token_label(wk.r3) },
            if wk.r4 == NO_TOK { na.clone() } else { token_label(wk.r4) },
            cell_label(&row.settled.cell),
            if row.joint { 1 } else { 0 },
            row.prospect,
            row.settled.extension
        ));
    }
    lines.sort_unstable();
    let mut checksum: u64 = 0xcbf29ce484222325;
    for line in &lines {
        for b in line.as_bytes() {
            checksum = (checksum ^ (*b as u64)).wrapping_mul(0x100000001b3);
        }
        checksum = (checksum ^ 10).wrapping_mul(0x100000001b3);
    }

    // A cell in model.py's sense is a distinct settled cell; the extension rides along in Settled.
    let mut cell_ids: FxSet<CellId> = FxSet::default();
    for s in cells.iter() {
        cell_ids.insert(s.cell);
    }

    let res = BuildResult {
        windows: lines.len(),
        cells: cell_ids.len(),
        checksum,
        nocand,
        stranded,
        raised,
        candidates: b.eng.n_candidates,
        prospect: b.eng.n_prospect,
        trace: b.eng.n_trace,
        favors: b.eng.n_favors,
        memo_entries: b.eng.trace_cache.len(),
        fired: b.eng.fired_count,
        text_sink: b.eng.text_sink,
    };
    let cache = std::mem::take(&mut b.eng.trace_cache);
    (res, cache)
}

const CONFIG_FEATURES: [u8; 6] = [0, 1, 2, 4, 1 | 4, 8];
const CONFIG_NAMES: [&str; 6] = ["default", "ss03", "ss04", "ss05", "ss03+ss05", "ss10"];

fn emit(impl_name: &str, mode: &str, letters: usize, wall: f64, results: &[(String, BuildResult)]) {
    print!(
        "{{\"impl\":\"{}\",\"mode\":\"{}\",\"letters\":{},\"wall_seconds\":{:.6},\"configs\":[",
        impl_name, mode, letters, wall
    );
    for (i, (name, r)) in results.iter().enumerate() {
        if i > 0 {
            print!(",");
        }
        print!(
            "{{\"config\":\"{}\",\"windows\":{},\"cells\":{},\"checksum\":{},\"nocand\":{},\"stranded\":{},\"raised\":{},\"candidates\":{},\"prospect\":{},\"trace\":{},\"favors\":{},\"memo_entries\":{},\"fired\":{},\"text_sink\":{}}}",
            name, r.windows, r.cells, r.checksum, r.nocand, r.stranded, r.raised, r.candidates,
            r.prospect, r.trace, r.favors, r.memo_entries, r.fired, r.text_sink
        );
    }
    println!("]}}");
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args[1] == "memo" {
        memo::run();
        return;
    }
    let spec_path = &args[1];
    let mode = if args.len() > 2 { args[2].clone() } else { "one".to_string() };
    let letters: usize = if args.len() > 3 { args[3].parse().unwrap() } else { 15 };
    let spec = load_spec(spec_path, letters);

    let t0 = Instant::now();
    let mut results: Vec<(String, BuildResult)> = Vec::new();
    match mode.as_str() {
        "one" => {
            let (r, _) = build_tables(&spec, CONFIG_FEATURES[0], Share::None, 0);
            results.push(("default".to_string(), r));
        }
        "six" => {
            let (r0, donor) = build_tables(&spec, CONFIG_FEATURES[0], Share::None, 0);
            results.push((CONFIG_NAMES[0].to_string(), r0));
            for i in 1..6 {
                let (r, _) = build_tables(
                    &spec,
                    CONFIG_FEATURES[i],
                    Share::Local(&donor),
                    CONFIG_FEATURES[i],
                );
                results.push((CONFIG_NAMES[i].to_string(), r));
            }
        }
        "six-noshare" => {
            for i in 0..6 {
                let (r, _) = build_tables(&spec, CONFIG_FEATURES[i], Share::None, 0);
                results.push((CONFIG_NAMES[i].to_string(), r));
            }
        }
        "six-par-noshare" => {
            // No cross-configuration share at all: six independent fixpoints, one per thread. This is the
            // upper bound on what parallelism alone buys, and the answer to "does the share still pay".
            let spec_ref = &spec;
            let mut collected: Vec<(usize, BuildResult)> = std::thread::scope(|s| {
                let mut handles = Vec::new();
                for i in 0..6 {
                    handles.push(s.spawn(move || {
                        let (r, _) = build_tables(spec_ref, CONFIG_FEATURES[i], Share::None, 0);
                        (i, r)
                    }));
                }
                handles.into_iter().map(|h| h.join().unwrap()).collect()
            });
            collected.sort_by_key(|(i, _)| *i);
            for (i, r) in collected {
                results.push((CONFIG_NAMES[i].to_string(), r));
            }
        }
        "six-par" => {
            // The donor configuration must finish first — its memo is the cross-configuration share — then the
            // five recipients run concurrently against it behind an RwLock, which is where the real build's
            // single-process TraceShare constraint stops being a serialisation.
            let (r0, donor) = build_tables(&spec, CONFIG_FEATURES[0], Share::None, 0);
            results.push((CONFIG_NAMES[0].to_string(), r0));
            let shared = Arc::new(RwLock::new(donor));
            let spec_ref = &spec;
            let mut collected: Vec<(usize, BuildResult)> = std::thread::scope(|s| {
                let mut handles = Vec::new();
                for i in 1..6 {
                    let shared = Arc::clone(&shared);
                    handles.push(s.spawn(move || {
                        let (r, _) = build_tables(
                            spec_ref,
                            CONFIG_FEATURES[i],
                            Share::Shared(&shared),
                            CONFIG_FEATURES[i],
                        );
                        (i, r)
                    }));
                }
                handles.into_iter().map(|h| h.join().unwrap()).collect()
            });
            collected.sort_by_key(|(i, _)| *i);
            for (i, r) in collected {
                results.push((CONFIG_NAMES[i].to_string(), r));
            }
        }
        _ => panic!("unknown mode"),
    }
    let wall = t0.elapsed().as_secs_f64();
    emit("rust", &mode, letters, wall, &results);
}
