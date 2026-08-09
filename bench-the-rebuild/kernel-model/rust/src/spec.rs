// Spec loading: the same flat integer text file model.py reads, parsed into packed structs.

pub const NONE_H: i8 = -1; // "none" state
pub const UNSET_H: i8 = -2; // axis not constrained

pub const K_LETTER: u8 = 0;
pub const K_EDGE: u8 = 1;
pub const K_SPACE: u8 = 2;
pub const K_ZWNJ: u8 = 3;
pub const K_NAMER: u8 = 4;
pub const K_UNKNOWN: u8 = 5;

// A right-slot token, packed: 0..=17 is a letter carrying that rune index, 20..=24 are the non-letter kinds.
pub const TOK_EDGE: u8 = 20;
pub const TOK_SPACE: u8 = 21;
pub const TOK_ZWNJ: u8 = 22;
pub const TOK_NAMER: u8 = 23;
pub const TOK_UNKNOWN: u8 = 24;

#[inline(always)]
pub fn tok_kind(t: u8) -> u8 {
    if t < 20 {
        K_LETTER
    } else {
        t - 19
    }
}

#[inline(always)]
pub fn tok_is_letter(t: u8) -> bool {
    t < 20
}

#[derive(Clone, Debug)]
pub struct Condition {
    pub family: u32, // bitmask over rune indices
    pub has_family: bool,
    pub klass: Vec<u8>,
    pub stance_mask: u8,
    pub joined_at: i8, // UNSET_H when unconstrained
    pub stroke: i8,    // -1 when unconstrained
    pub is_token: i8,  // -1 unconstrained, 0 = "boundary", else a kind
    pub except_: Vec<Condition>,
    pub then: Option<Box<Condition>>,
}

#[derive(Clone, Debug)]
pub struct When {
    pub left: Option<Condition>,
    pub right: Option<Condition>,
    pub self_entry: i8, // -1 unset, 0 none, 1 live
    pub self_exit: i8,
    pub word: i8, // -1 unset, 0 initial, 1 medial, 2 final, 3 isolated
    pub feature: i8,
}

#[derive(Clone, Debug)]
pub struct SurfaceRow {
    pub height: i8,
    pub selectable: bool,
    pub scope: Vec<Condition>,
    pub prov: u32,
}

#[derive(Clone, Debug)]
pub struct Unlock {
    pub feature: i8,
    pub entry: i8,
    pub exit: i8,
    pub pairing: Option<(i8, i8)>,
    pub when: Option<When>,
    pub prov: u32,
}

#[derive(Clone, Debug)]
pub struct Stance {
    pub name: u8,
    pub entries: Vec<SurfaceRow>,
    pub exits: Vec<SurfaceRow>,
    pub never: Vec<(i8, i8)>,
    pub only: Option<Vec<(i8, i8)>>,
    pub unlocks: Vec<Unlock>,
    pub require_entry: bool,
    pub require_exit: bool,
}

#[derive(Clone, Debug)]
pub struct PolicyRecord {
    pub when: When,
    pub stance: i8,
    pub entry: i8,
    pub exit: i8,
    pub has_entry: bool,
    pub has_exit: bool,
    pub cell: Option<(i8, i8)>,
    pub over: Option<(i8, i8)>,
    pub absolute: bool,
    pub by: i8,
    pub ident: u32,
    pub weight: i32,
    pub prov: u32,
}

#[derive(Clone, Debug)]
pub struct Rune {
    pub index: u8,
    pub sequence: Option<(u8, u8)>,
    pub stances: Vec<Stance>,
    pub order: Vec<u8>,
    pub refuse: Vec<PolicyRecord>,
    pub prefer: Vec<PolicyRecord>,
    pub extend: Vec<PolicyRecord>,
    pub contract: Vec<PolicyRecord>,
    pub entry_strokes: u8,
    pub entry_bearing: bool,
    pub min_stroke: i8,
    // The features this rune can feel: its unlocks plus its feature-gated policy records. TraceShare serves a
    // donor trace only when no rune named in the memo key is sensitive to the recipient's feature delta.
    pub feature_mask: u8,
}

pub struct Spec {
    pub n_letters: usize,
    pub runes: Vec<Rune>,     // indexed by rune index; only `order` members are live
    pub order: Vec<u8>,       // live rune indices, in file order
    pub classes: Vec<u32>,    // predicate-class membership masks
    pub prov_names: Vec<String>,
}

struct Toks<'a> {
    parts: std::str::SplitAsciiWhitespace<'a>,
}

impl<'a> Toks<'a> {
    fn nx(&mut self) -> i64 {
        self.parts.next().unwrap().parse::<i64>().unwrap()
    }
}

fn cond_of(pool: &[Condition], i: i64) -> Option<Condition> {
    if i >= 0 {
        Some(pool[i as usize].clone())
    } else {
        None
    }
}

pub fn height_name(h: i8) -> &'static str {
    match h {
        0 => "baseline",
        1 => "x-height",
        2 => "y6",
        3 => "top",
        _ => "none",
    }
}

pub fn height_y(h: i8) -> i32 {
    match h {
        0 => 0,
        1 => 5,
        2 => 6,
        3 => 8,
        _ => 1_000_000,
    }
}

pub fn load_spec(path: &str, n_letters: usize) -> Spec {
    let text = std::fs::read_to_string(path).expect("spec file");
    let mut conds: Vec<Condition> = Vec::new();
    let mut whens: Vec<When> = Vec::new();
    let mut classes: Vec<u32> = Vec::new();
    let mut prov_names: Vec<String> = Vec::new();
    let mut runes: Vec<Rune> = Vec::new();
    let mut order_list: Vec<u8> = Vec::new();
    let mut n_stances: Vec<usize> = vec![0; 64];
    let mut ident: u32 = 0;
    let letter_mask: u32 = if n_letters >= 32 { u32::MAX } else { (1u32 << n_letters) - 1 };

    let mut prov = |s: String, names: &mut Vec<String>| -> u32 {
        names.push(s);
        (names.len() - 1) as u32
    };

    for line in text.lines() {
        let mut t = Toks { parts: line.split_ascii_whitespace() };
        let key = match t.parts.next() {
            Some(k) => k,
            None => continue,
        };
        match key {
            "header" => {
                let _total = t.nx();
            }
            "class" => {
                let _i = t.nx();
                let m = t.nx() as u64 as u32;
                classes.push(m & letter_mask);
            }
            "rune" => {
                let idx = t.nx() as u8;
                let isliga = t.nx();
                let a = t.nx();
                let b = t.nx();
                let n = t.nx() as usize;
                while runes.len() <= idx as usize {
                    runes.push(Rune {
                        index: runes.len() as u8,
                        sequence: None,
                        stances: Vec::new(),
                        order: Vec::new(),
                        refuse: Vec::new(),
                        prefer: Vec::new(),
                        extend: Vec::new(),
                        contract: Vec::new(),
                        entry_strokes: 0,
                        entry_bearing: false,
                        min_stroke: -1,
                        feature_mask: 0,
                    });
                }
                if isliga != 0 {
                    runes[idx as usize].sequence = Some((a as u8, b as u8));
                }
                n_stances[idx as usize] = n;
                order_list.push(idx);
            }
            "order" => {
                let idx = t.nx() as usize;
                let n = n_stances[idx];
                let mut o = Vec::with_capacity(n);
                for _ in 0..n {
                    o.push(t.nx() as u8);
                }
                runes[idx].order = o;
            }
            "strokes" => {
                let idx = t.nx() as usize;
                let m = t.nx() as u8;
                runes[idx].entry_strokes = m;
                runes[idx].min_stroke = (0..8).find(|b| (m >> b) & 1 == 1).map(|b| b as i8).unwrap_or(-1);
            }
            "cond" => {
                let _i = t.nx();
                let fam_raw = t.nx() as u64 as u32;
                let nk = t.nx();
                let mut klass = Vec::new();
                for _ in 0..nk {
                    klass.push(t.nx() as u8);
                }
                let smask = t.nx() as u8;
                let ja = t.nx() as i8;
                let st = t.nx() as i8;
                let it = t.nx() as i8;
                let ne = t.nx();
                let mut ex = Vec::new();
                for _ in 0..ne {
                    let j = t.nx();
                    ex.push(conds[j as usize].clone());
                }
                let th = t.nx();
                let fam = fam_raw & letter_mask;
                conds.push(Condition {
                    family: fam,
                    has_family: fam != 0,
                    klass,
                    stance_mask: smask,
                    joined_at: ja,
                    stroke: st,
                    is_token: it,
                    except_: ex,
                    then: cond_of(&conds, th).map(Box::new),
                });
            }
            "when" => {
                let _i = t.nx();
                let left = t.nx();
                let right = t.nx();
                let se = t.nx() as i8;
                let sx = t.nx() as i8;
                let wd = t.nx() as i8;
                let ft = t.nx() as i8;
                whens.push(When {
                    left: cond_of(&conds, left),
                    right: cond_of(&conds, right),
                    self_entry: se,
                    self_exit: sx,
                    word: wd,
                    feature: ft,
                });
            }
            "stance" => {
                let rune = t.nx() as usize;
                let sname = t.nx() as u8;
                let req_e = t.nx();
                let req_x = t.nx();
                let ne = t.nx();
                let mut entries = Vec::new();
                for _ in 0..ne {
                    let h = t.nx() as i8;
                    let sel = t.nx();
                    let ns = t.nx();
                    let mut scope = Vec::new();
                    for _ in 0..ns {
                        scope.push(conds[t.nx() as usize].clone());
                    }
                    let pid = prov(
                        format!("qs{:02}.yaml:stances.st{}.entries.{}", rune, sname, height_name(h)),
                        &mut prov_names,
                    );
                    entries.push(SurfaceRow { height: h, selectable: sel != 0, scope, prov: pid });
                }
                let nx_ = t.nx();
                let mut exits = Vec::new();
                for _ in 0..nx_ {
                    let h = t.nx() as i8;
                    let ns = t.nx();
                    let mut scope = Vec::new();
                    for _ in 0..ns {
                        scope.push(conds[t.nx() as usize].clone());
                    }
                    let pid = prov(
                        format!("qs{:02}.yaml:stances.st{}.exits.{}", rune, sname, height_name(h)),
                        &mut prov_names,
                    );
                    exits.push(SurfaceRow { height: h, selectable: true, scope, prov: pid });
                }
                let nn = t.nx();
                let mut never = Vec::new();
                for _ in 0..nn {
                    let a = t.nx() as i8;
                    let b = t.nx() as i8;
                    never.push((a, b));
                }
                let has_only = t.nx();
                let no = t.nx();
                let mut only_rows = Vec::new();
                for _ in 0..no {
                    let a = t.nx() as i8;
                    let b = t.nx() as i8;
                    only_rows.push((a, b));
                }
                let nu = t.nx();
                let mut unlocks: Vec<Unlock> = Vec::new();
                for _ in 0..nu {
                    let feat = t.nx() as i8;
                    let en = t.nx() as i8;
                    let ex_ = t.nx() as i8;
                    let hp = t.nx();
                    let pe = t.nx() as i8;
                    let px = t.nx() as i8;
                    let w = t.nx();
                    let pid = prov(
                        format!("qs{:02}.yaml:stances.st{}.unlocks[{}]", rune, sname, unlocks.len()),
                        &mut prov_names,
                    );
                    unlocks.push(Unlock {
                        feature: feat,
                        entry: en,
                        exit: ex_,
                        pairing: if hp != 0 { Some((pe, px)) } else { None },
                        when: if w >= 0 { Some(whens[w as usize].clone()) } else { None },
                        prov: pid,
                    });
                }
                runes[rune].stances.push(Stance {
                    name: sname,
                    entries,
                    exits,
                    never,
                    only: if has_only != 0 { Some(only_rows) } else { None },
                    unlocks,
                    require_entry: req_e != 0,
                    require_exit: req_x != 0,
                });
            }
            "record" => {
                let rune = t.nx() as usize;
                let kind = t.nx();
                let w = t.nx() as usize;
                let s = t.nx() as i8;
                let entry_raw = t.nx() as i8;
                let exit_raw = t.nx() as i8;
                let hc = t.nx();
                let ce = t.nx() as i8;
                let cx = t.nx() as i8;
                let ho = t.nx();
                let oe = t.nx() as i8;
                let ox = t.nx() as i8;
                let absolute = t.nx();
                let by = t.nx() as i8;
                let when = whens[w].clone();
                let mut weight = 0i32;
                if let Some(c) = &when.left {
                    weight += 2 + c.family.count_ones() as i32;
                }
                if let Some(c) = &when.right {
                    weight += 2 + c.family.count_ones() as i32;
                }
                for f in [when.self_entry, when.self_exit, when.word, when.feature] {
                    if f >= 0 {
                        weight += 1;
                    }
                }
                if s >= 0 {
                    weight += 1;
                }
                if hc != 0 {
                    weight += 1;
                }
                let kname = ["refuse", "prefer", "extend", "contract"][kind as usize];
                let n_existing = match kind {
                    0 => runes[rune].refuse.len(),
                    1 => runes[rune].prefer.len(),
                    2 => runes[rune].extend.len(),
                    _ => runes[rune].contract.len(),
                };
                let pid = prov(
                    format!("qs{:02}.yaml:policy.{}[{}]", rune, kname, n_existing),
                    &mut prov_names,
                );
                let rec = PolicyRecord {
                    when,
                    stance: s,
                    entry: entry_raw,
                    exit: exit_raw,
                    has_entry: entry_raw != UNSET_H,
                    has_exit: exit_raw != UNSET_H,
                    cell: if hc != 0 { Some((ce, cx)) } else { None },
                    over: if ho != 0 { Some((oe, ox)) } else { None },
                    absolute: absolute != 0,
                    by,
                    ident,
                    weight,
                    prov: pid,
                };
                ident += 1;
                match kind {
                    0 => runes[rune].refuse.push(rec),
                    1 => runes[rune].prefer.push(rec),
                    2 => runes[rune].extend.push(rec),
                    _ => runes[rune].contract.push(rec),
                }
            }
            _ => {}
        }
    }

    // Keep letters 0..n_letters plus the ligature runes whose whole sequence survives, in file order.
    let mut live: Vec<u8> = Vec::new();
    for &i in &order_list {
        let r = &runes[i as usize];
        let keep = if (i as usize) < n_letters {
            true
        } else {
            match r.sequence {
                Some((a, b)) => (a as usize) < n_letters && (b as usize) < n_letters,
                None => false,
            }
        };
        if keep {
            live.push(i);
        }
    }
    for i in 0..runes.len() {
        let bearing = runes[i].stances.iter().any(|s| {
            s.entries.iter().any(|r| r.selectable) || s.unlocks.iter().any(|u| u.entry >= 0)
        });
        runes[i].entry_bearing = bearing;
        let mut fmask: u8 = 0;
        for s in &runes[i].stances {
            for u in &s.unlocks {
                if u.feature >= 0 {
                    fmask |= 1 << u.feature;
                }
            }
        }
        for pool in [&runes[i].refuse, &runes[i].prefer, &runes[i].extend, &runes[i].contract] {
            for r in pool {
                if r.when.feature >= 0 {
                    fmask |= 1 << r.when.feature;
                }
            }
        }
        runes[i].feature_mask = fmask;
    }
    Spec { n_letters, runes, order: live, classes, prov_names }
}
