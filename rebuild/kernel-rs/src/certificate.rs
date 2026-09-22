//! One realizing string per settlement rule, read off the rows the fixpoint recorded rather than searched for over the finished table. A rule is realizable exactly when some string reaches a window it first-matches, and the rows already pin such a string for every window they hold: a row's successor at the next position is a row whose input is its right1, whose left is its outcome and whose right slots are its own shifted one up, so a chain of rows from a seed — a row whose left is a boundary, reached by the text of that one boundary — to the row spells the inputs that put the row's left state where the row found it, and the row's own slots spell the right context the settlement read. [`Prefixes`] is that chain for every row, the shortest one, found by one breadth-first pass over the rows in their key order; what is left open is only the tail past the last pinned slot — the section 5.7 formation guard's second slot for a surviving pair at the window's end, and the slot a formed ligature at the window's end must still stand before. This module closes that tail, checks the closed window still first-matches the rule under the fold's own first-match-wins, and hands back one token stream per rule; the build writes them into the windows head, and `run_m1`'s witness stage settles each one through the crate and asserts the rule fires at the position the certificate names. That check is what proves the pins a chain carries: a chain whose prefix settled to some other left state would settle its certificate to some other rule. The pins a chain cannot carry are refused here, before any certificate is written: a rule whose replayed rows no chain reaches at all is a left state the worklist admitted beside a right1 no producing window had, and [`certify`] names the rule, the row and the pin rather than handing the witness stage a table with nothing to vouch for its rules.
//!
//! The chain is the shortest one and not the worklist's own because the worklist is a stack: an item's first visitor is whatever the depth-first walk reached it through, and the chains that fall out of that are thousands of letters long, each of which the witness stage would settle wave by wave. Breadth-first over the rows, every row's chain is as short as any text that reaches it.
//!
//! The tail closure is a bounded search, because a token appended to satisfy one constraint can open another: a follower that makes a pair survive may itself begin a survivable pair, a ligature standing at the new end needs its own follower. Each step appends the one token the first open constraint asks for, re-reads the whole stream, and tries the candidates in a fixed order — the boundary glyphs first, since a boundary closes every constraint behind it, then the letters in name order — to a depth no realizable row needs more than a couple of steps of. Several closures are kept per row and several rows per rule, because a concrete tail can hand the window to an earlier rule whose deep class admits the tail's token where the row's `#NA` did not; the certificate is the first closure of the first row whose concrete window the rule wins, the rows tried shortest chain first.
//!
//! What a certificate is not: a proof that HarfBuzz applies the rule. That is `gate:conform`'s, over the compiled font. A certificate proves the rule reachable in the kernel's own settlement, which is the realizability half of the dead-rule alarm — the half the fold's never-first refusal cannot state, because that replay reads the table's own rows and a row is realizable only if its left state is.

use std::collections::VecDeque;

use crate::fixpoint::right_token_label;
use crate::fold::{LabelRows, Rule, boundaryish, first_match, rules_by_input};
use crate::hash::HashSet;
use crate::index::SpecIndex;
use crate::options::WindowOptions;
use crate::types::{EDGE, NAMER_DOT, RightToken, SPACE, TokenKind, ZWNJ};

/// How many replayed rows the fold keeps per rule for the certificates to try, the ones with the shortest chains. A popular rule first-matches tens of thousands of rows; the bound is on the candidates, never on the verdict, since a rule whose only certifiable row sat past it fails the build loudly rather than passing.
pub const ROW_CAP: usize = 32;

/// How many tokens the tail closure may append before giving a row up. Every constraint reads at most two slots past the token that raised it, so a closure that needs more than a few appends is chasing a chain of pairs no realizable row carries.
const CLOSURE_DEPTH: usize = 6;

/// How many closed streams the search keeps per row before moving to the next row.
const CLOSURE_CAP: usize = 8;

/// The label a slot the window does not carry is spelled with, `table.NA_LABEL`.
const NA_LABEL: &str = "#NA";

/// The label the run edge carries, `table.EDGE_LABEL`.
const EDGE_LABEL: &str = "#EDGE";

/// The chain length of a row no seed reaches through the producer relation.
pub const UNREACHED: u32 = u32::MAX;

/// Every row's shortest producer chain: the row it is reached from and how many rows the chain holds before it, zero for a seed. Built by one breadth-first pass over the rows in their key order, so a successor set — the rows at the next position whose input, left and pinned right slots the row fixes, out to the last slot the successor's own window carries — is a handful of contiguous runs found by binary search, one per depth the successor may have stopped carrying slots at, and a run once reached is never scanned again, because its every row was assigned the first time. A row the pass never reaches keeps [`UNREACHED`], which the fold treats as the longest chain there is.
pub struct Prefixes {
    dist: Vec<u32>,
    parent: Vec<u32>,
}

/// One exact successor query, shortened at the first carried deep slot that is `#NA`. The three labels every query carries are the next input, the producer's settled outcome and its shifted first right slot; a query whose producer carries either deep slot keeps that label too. The enum's length therefore makes labels beyond the cutoff unable to distinguish two otherwise equal queries.
#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
enum SuccessorQuery<'a> {
    ThroughRight1([&'a str; 3]),
    ThroughRight2([&'a str; 4]),
    ThroughRight3([&'a str; 5]),
}

impl<'a> SuccessorQuery<'a> {
    fn from_prefix(prefix: [&'a str; 5], pinned: usize) -> Self {
        match pinned {
            3 => Self::ThroughRight1(prefix[..3].try_into().expect("three pinned labels")),
            4 => Self::ThroughRight2(prefix[..4].try_into().expect("four pinned labels")),
            5 => Self::ThroughRight3(prefix),
            _ => unreachable!("a successor query carries three to five labels"),
        }
    }
}

impl Prefixes {
    /// Finds every shortest producer chain, searching an exact successor query only for its first FIFO producer. Equal signatures generate the same ordered sequence of ranges; the first producer therefore assigns every still-unreached row any equal producer could assign, and later producers can neither assign a row nor replace its parent. Distinct signatures still search independently even where their shorter `#NA` ranges overlap, leaving the exact-range index to suppress only the same concrete range the reference search suppresses.
    pub fn over<'a>(rows: &LabelRows<'a>) -> Prefixes {
        let count = rows.len();
        let mut dist = vec![UNREACHED; count];
        let mut parent = vec![UNREACHED; count];
        let mut queue: VecDeque<u32> = VecDeque::new();
        let mut signatures: HashSet<SuccessorQuery<'a>> = HashSet::default();
        let mut scanned: HashSet<(u32, u32)> = HashSet::default();
        for (row, length) in dist.iter_mut().enumerate() {
            if boundaryish(rows.left(row)) {
                *length = 0;
                queue.push_back(row as u32);
            }
        }
        while let Some(row) = queue.pop_front() {
            let at = row as usize;
            let key = rows.key(at);
            if boundaryish(key[2]) {
                continue;
            }
            let outcome: &str = rows.outcome(at);
            let prefix: [&str; 5] = [key[2], outcome, key[3], key[4], key[5]];
            let pinned = prefix[3..]
                .iter()
                .position(|label| *label == NA_LABEL)
                .map_or(5, |open| 3 + open);
            if !signatures.insert(SuccessorQuery::from_prefix(prefix, pinned)) {
                continue;
            }
            let mut runs: Vec<(usize, usize)> = Vec::new();
            for carried in 3..=pinned {
                let mut wanted: Vec<&str> = prefix[..carried].to_vec();
                if carried < pinned {
                    wanted.push(NA_LABEL);
                }
                let width = wanted.len();
                let start = partition(rows, |key| key[..width] < wanted[..]);
                let end = partition(rows, |key| key[..width] <= wanted[..]);
                if start < end {
                    runs.push((start, end));
                }
            }
            for (start, end) in runs {
                if !scanned.insert((start as u32, end as u32)) {
                    continue;
                }
                for next in start..end {
                    if dist[next] == UNREACHED {
                        dist[next] = dist[at] + 1;
                        parent[next] = row;
                        queue.push_back(next as u32);
                    }
                }
            }
        }
        Prefixes { dist, parent }
    }

    #[cfg(test)]
    pub(crate) fn over_reference(rows: &LabelRows<'_>) -> Prefixes {
        let count = rows.len();
        let mut dist = vec![UNREACHED; count];
        let mut parent = vec![UNREACHED; count];
        let mut queue: VecDeque<u32> = VecDeque::new();
        let mut scanned: HashSet<(u32, u32)> = HashSet::default();
        for (row, length) in dist.iter_mut().enumerate() {
            if boundaryish(rows.left(row)) {
                *length = 0;
                queue.push_back(row as u32);
            }
        }
        while let Some(row) = queue.pop_front() {
            let at = row as usize;
            let key = rows.key(at);
            if boundaryish(key[2]) {
                continue;
            }
            let outcome: &str = rows.outcome(at);
            let prefix: [&str; 5] = [key[2], outcome, key[3], key[4], key[5]];
            let pinned = prefix[3..]
                .iter()
                .position(|label| *label == NA_LABEL)
                .map_or(5, |open| 3 + open);
            // A successor carries the producer's pins out to the last slot its own window enumerated, and a slot it never split stands at `#NA` behind the ones it did; so beside the run keyed on the whole pinned prefix, every shorter prefix followed by `#NA` is a run of successors too — the rows a non-deep input leaves with its third and fourth slots dropped, whose pins the worklist forwards onto their own successors.
            let mut runs: Vec<(usize, usize)> = Vec::new();
            for carried in 3..=pinned {
                let mut wanted: Vec<&str> = prefix[..carried].to_vec();
                if carried < pinned {
                    wanted.push(NA_LABEL);
                }
                let width = wanted.len();
                let start = partition(rows, |key| key[..width] < wanted[..]);
                let end = partition(rows, |key| key[..width] <= wanted[..]);
                if start < end {
                    runs.push((start, end));
                }
            }
            for (start, end) in runs {
                if !scanned.insert((start as u32, end as u32)) {
                    continue;
                }
                for next in start..end {
                    if dist[next] == UNREACHED {
                        dist[next] = dist[at] + 1;
                        parent[next] = row;
                        queue.push_back(next as u32);
                    }
                }
            }
        }
        Prefixes { dist, parent }
    }

    /// Every row's chain length, [`UNREACHED`] for a row no seed reaches.
    pub fn dist(&self) -> &[u32] {
        &self.dist
    }

    /// The rows of `row`'s chain, the seed first and `row` itself last, or `None` for an unreached row.
    fn chain(&self, row: usize) -> Option<Vec<usize>> {
        if self.dist[row] == UNREACHED {
            return None;
        }
        let mut chain: Vec<usize> = Vec::with_capacity(self.dist[row] as usize + 1);
        let mut at = row;
        loop {
            chain.push(at);
            if self.dist[at] == 0 {
                break;
            }
            at = self.parent[at] as usize;
        }
        chain.reverse();
        Some(chain)
    }
}

/// Assert exact parent and distance parity between two prefix searches. Fold integration fixtures use this before comparing the candidate rows and certificate text derived from the chains.
#[cfg(test)]
pub(crate) fn assert_same_prefixes(expected: &Prefixes, actual: &Prefixes) {
    assert_eq!(actual.dist, expected.dist, "prefix distances differ");
    assert_eq!(actual.parent, expected.parent, "prefix parents differ");
}

/// The first row index for which `before` is false, over rows in their key order.
fn partition(rows: &LabelRows<'_>, before: impl Fn(&[&str; 6]) -> bool) -> usize {
    let mut low = 0usize;
    let mut high = rows.len();
    while low < high {
        let mid = low + (high - low) / 2;
        if before(&rows.key(mid)) {
            low = mid + 1;
        } else {
            high = mid;
        }
    }
    low
}

/// One certificate per rule, in rule order, each a token stream spelled in the windows vocabulary: rune names for letters, the three boundary glyph labels for the boundaries. `first_rows` is what [`crate::fold::first_match_rows`] handed back, the rows each rule first-matched under the replay, shortest chain first. A rule no row of its own closes into a certificate is refused, naming the rule, because the fold has already proven a replayed row first-matches it and a row that cannot be realized is a pin the worklist got wrong. The refusal comes in two sentences, for the two ways a row fails to realize. A row no producer chain reaches — no row of the enumeration settles to its left before its input with its pinned right slots behind, so [`Prefixes`] left it at [`UNREACHED`] — is a pin failure at the row itself: the worklist admitted a left state beside a right1 no producing window had, and the refusal names the rule, the row, and which of the pins does not hold (`pin_failure`), provided the enumeration settles to that left somewhere, which a fixpoint's every letter left does. A row a chain does reach but whose tail no closure carries to a window the rule wins is the other sentence, since there the pins held and the constraint search came up empty.
///
/// A product the spec cannot spell certifies nothing and answers the empty list: a rule one of whose rows renders as no text — a deep-slot member the spec models no rune for — or whose unreached row stands on a left no row settles to, is a hand-built product's, the fold's own test bench, and not a build's, since an enumeration's every label is a modeled rune or a boundary and its every letter left is some window's outcome. The windows head then carries no certificates, and `run_m1`'s witness stage refuses a table whose certificates do not cover its rules, so the empty answer can never reach a font; but that count is a belt under this module's own refusals, never the sentence a build's pin failure is meant to fire.
pub fn certify(
    index: &SpecIndex,
    options: &mut WindowOptions<'_>,
    prefixes: &Prefixes,
    rows: &LabelRows<'_>,
    rules: &[Rule],
    first_rows: &[Vec<usize>],
) -> Result<Vec<Vec<String>>, String> {
    let by_input = rules_by_input(rules);
    let mut certificates: Vec<Vec<String>> = Vec::with_capacity(rules.len());
    for (seat, rule) in rules.iter().enumerate() {
        let mut found: Option<Vec<RightToken>> = None;
        let mut spelled_any = false;
        let mut unspellable_any = false;
        let mut unreached: Option<usize> = None;
        'rows: for &row in &first_rows[seat] {
            let (tokens, position) = match pinned_tokens(index, prefixes, rows, row) {
                Ok(Some(pinned)) => pinned,
                Ok(None) => {
                    unreached.get_or_insert(row);
                    continue;
                }
                Err(_) => {
                    unspellable_any = true;
                    continue;
                }
            };
            spelled_any = true;
            let mut closed: Vec<Vec<RightToken>> = Vec::new();
            closures(index, options, tokens, CLOSURE_DEPTH, &mut closed)?;
            for candidate in closed {
                let key = window_at(
                    index,
                    &candidate,
                    position,
                    rows.input_glyph(row),
                    rows.left(row),
                );
                let spelled: [&str; 6] = [&key[0], &key[1], &key[2], &key[3], &key[4], &key[5]];
                if first_match(&by_input, spelled) == Some(seat) {
                    found = Some(candidate);
                    break 'rows;
                }
            }
        }
        match found {
            Some(tokens) => certificates.push(
                tokens
                    .into_iter()
                    .map(|token| right_token_label(index, token))
                    .collect(),
            ),
            None if unspellable_any => return Ok(Vec::new()),
            None if !spelled_any => {
                let row = unreached.expect("a rule the replay handed rows to has at least one");
                match pin_failure(rows, seat, rule, row, first_rows[seat].len()) {
                    Some(complaint) => return Err(complaint),
                    None => return Ok(Vec::new()),
                }
            }
            None => {
                return Err(format!(
                    "rule {seat} ({} -> {}) first-matches {} replayed row(s) but none of them closes into a string it first-matches; the worklist's pins for those rows do not hold",
                    rule.input_glyph,
                    rule.outcome,
                    first_rows[seat].len()
                ));
            }
        }
    }
    Ok(certificates)
}

/// The sentence a rule whose every replayed row lies on no producer chain is refused with: the rule, the first such row, and the pin that does not hold — found by asking the rows for the producer the chain needed, one pinned slot at a time. A producer of a row is a row at the position before it whose right1 is the row's input, whose outcome is the row's left, and whose deeper right slots are the row's own shifted one down, out to the first slot the row does not carry; the first of those conditions no row of the enumeration meets is the pin the worklist admitted without a window to produce it. `None` for a left no row of the enumeration settles to at all: the worklist only ever pins a left it settled in some window, so a left that is nobody's outcome is a hand-built product's, and answers the empty certificate list rather than a pin failure.
fn pin_failure(
    rows: &LabelRows<'_>,
    seat: usize,
    rule: &Rule,
    row: usize,
    count: usize,
) -> Option<String> {
    let key = rows.key(row);
    let [input, left, right1, right2, right3, _right4] = key;
    let mut producers: Vec<usize> = (0..rows.len())
        .filter(|&other| rows.outcome(other).as_ref() == left)
        .collect();
    if producers.is_empty() {
        return None;
    }
    let mut sentence = String::new();
    producers.retain(|&other| rows.right1(other).as_ref() == input);
    if producers.is_empty() {
        sentence.push_str(&format!(
            "rows settle to {left}, but none of them before {input}, so the pin of that left before this input was never produced"
        ));
    } else {
        let pins: [(&str, &str, &str); 3] = [
            (right1, "right1", "second"),
            (right2, "right2", "third"),
            (right3, "right3", "fourth"),
        ];
        for (slot, (label, name, ordinal)) in pins.into_iter().enumerate() {
            if label == NA_LABEL {
                break;
            }
            producers.retain(|&other| rows.key(other)[3 + slot] == label);
            if producers.is_empty() {
                sentence.push_str(&format!(
                    "rows settle to {left} before {input}, but none of them carries {label} at its {ordinal} slot, so the pin of {name} {label} beside that left was never produced"
                ));
                break;
            }
        }
        if sentence.is_empty() {
            sentence.push_str(
                "its producers exist but none of them lies on a chain from a seed itself",
            );
        }
    }
    Some(format!(
        "rule {seat} ({} -> {}) first-matches {count} replayed row(s) and none of them lies on a producer chain from a seed; the first, ({}, {}, {}, {}, {}, {}), is a pin failure: {sentence}",
        rule.input_glyph, rule.outcome, key[0], key[1], key[2], key[3], key[4], key[5]
    ))
}

/// The tokens one replayed row pins, and the position of its input among them: the chain's seed boundary unless it is the run edge, the input of every row of the chain before this one, the row's input as its family, then its right slots out to the first one the window does not carry. Every one of these is fixed by the rows; only what follows is the closure's to choose. `None` for a row no seed reaches, an error for a label the spec cannot spell.
fn pinned_tokens(
    index: &SpecIndex,
    prefixes: &Prefixes,
    rows: &LabelRows<'_>,
    row: usize,
) -> Result<Option<(Vec<RightToken>, usize)>, String> {
    let Some(chain) = prefixes.chain(row) else {
        return Ok(None);
    };
    let mut tokens: Vec<RightToken> = Vec::with_capacity(chain.len() + 5);
    if let Some(boundary) = slot_token(index, rows.left(chain[0]))? {
        tokens.push(boundary);
    }
    for &earlier in &chain[..chain.len() - 1] {
        tokens.push(input_token(index, rows.input_glyph(earlier))?);
    }
    let position = tokens.len();
    tokens.push(input_token(index, rows.input_glyph(row))?);
    for label in [
        &**rows.right1(row),
        &**rows.right2(row),
        &**rows.right3(row),
        &**rows.right4(row),
    ] {
        match slot_token(index, label)? {
            Some(token) => tokens.push(token),
            None => break,
        }
    }
    Ok(Some((tokens, position)))
}

/// The letter a row's input label spells: the family ahead of any stance or lock suffix.
fn input_token(index: &SpecIndex, input_glyph: &str) -> Result<RightToken, String> {
    letter_of(index, input_glyph.split('.').next().unwrap_or(input_glyph))
}

/// The token a rune name spells, or a refusal naming a label the spec never interned — a class id reaching here would be one, and the fold expands those away before a row gets this far.
fn letter_of(index: &SpecIndex, name: &str) -> Result<RightToken, String> {
    index
        .sym_of(name)
        .filter(|rune| index.is_modeled(*rune))
        .and_then(|rune| index.letter(rune))
        .ok_or_else(|| format!("certificate: {name} names no rune the spec models"))
}

/// The token one right-slot label stands for in a text: `None` for the run edge and for a slot the window does not carry, since both mean the text stops there.
fn slot_token(index: &SpecIndex, label: &str) -> Result<Option<RightToken>, String> {
    match label {
        EDGE_LABEL | NA_LABEL => Ok(None),
        "space" => Ok(Some(SPACE)),
        "uni200C" => Ok(Some(ZWNJ)),
        "periodcentered" => Ok(Some(NAMER_DOT)),
        name => letter_of(index, name).map(Some),
    }
}

/// The six labels the window at `position` carries once the stream is closed: the row's own input and left labels, then the four right slots read off the tokens with the standing cascade — `#EDGE` past the end of the stream, and `#NA` from the first boundary on, since no record peeks past one.
fn window_at(
    index: &SpecIndex,
    tokens: &[RightToken],
    position: usize,
    input_label: &str,
    left_label: &str,
) -> [String; 6] {
    let mut rights: [String; 4] = [
        NA_LABEL.to_owned(),
        NA_LABEL.to_owned(),
        NA_LABEL.to_owned(),
        NA_LABEL.to_owned(),
    ];
    let mut open = true;
    for (slot, right) in rights.iter_mut().enumerate() {
        if !open {
            break;
        }
        let at = position + 1 + slot;
        *right = match tokens.get(at) {
            Some(token) => right_token_label(index, *token),
            None => EDGE_LABEL.to_owned(),
        };
        open = !boundaryish(right);
    }
    let [right1, right2, right3, right4] = rights;
    [
        input_label.to_owned(),
        left_label.to_owned(),
        right1,
        right2,
        right3,
        right4,
    ]
}

/// What the first open constraint of a stream asks for.
enum Verdict {
    /// Every formation constraint the stream raises is satisfied within it.
    Closed,
    /// A constraint is violated by tokens already in the stream, so no append can rescue it.
    Dead,
    /// A constraint reads one slot past the end, and these are the tokens that would satisfy it there, in the order to try them.
    Needs(Vec<RightToken>),
}

/// The formation constraints a post-formation token stream has to satisfy for the raw replay to hand the same stream back, read left to right and stopping at the first one that is not satisfied within the stream: a surviving formation pair needs the section 5.7 guard to fire, which is a follower the pair's survivable map names and a second slot that follower's allowance admits; and a formed ligature needs its own guard not to fire over the two raw tokens after it. A pair before a boundary always forms, so a boundary follower is dead rather than open.
fn open_constraint(
    index: &SpecIndex,
    options: &mut WindowOptions<'_>,
    tokens: &[RightToken],
) -> Result<Verdict, String> {
    let count = tokens.len();
    for (at, token) in tokens.iter().enumerate() {
        if token.kind() != TokenKind::Letter {
            continue;
        }
        let lead = token.letter();
        if at + 1 < count
            && tokens[at + 1].kind() == TokenKind::Letter
            && options
                .formation_pairs
                .contains(&(lead, tokens[at + 1].letter()))
        {
            let Some(map) = options
                .survivable
                .get(&(lead, tokens[at + 1].letter()))
                .cloned()
            else {
                return Ok(Verdict::Dead);
            };
            if at + 2 >= count {
                let mut followers: Vec<RightToken> = map
                    .keys()
                    .map(|rune| {
                        index
                            .letter(*rune)
                            .expect("a follower map keys modeled runes")
                    })
                    .collect();
                followers.sort_by(|left, right| {
                    index
                        .resolve(left.letter())
                        .cmp(index.resolve(right.letter()))
                });
                return Ok(Verdict::Needs(followers));
            }
            let follower = tokens[at + 2];
            if follower.kind() != TokenKind::Letter {
                return Ok(Verdict::Dead);
            }
            match map.get(&follower.letter()) {
                None => return Ok(Verdict::Dead),
                Some(None) => {}
                Some(Some(allowed)) => {
                    let second = tokens.get(at + 3).copied().unwrap_or(EDGE);
                    if !allowed.contains(&second) {
                        if at + 3 < count {
                            return Ok(Verdict::Dead);
                        }
                        return Ok(Verdict::Needs(ordered(
                            index,
                            allowed.iter().copied().filter(|token| *token != EDGE),
                        )));
                    }
                }
            }
        }
        if options.liga_sequences.contains_key(&lead) {
            let next1 = tokens.get(at + 1).copied().unwrap_or(EDGE);
            if next1.kind() != TokenKind::Letter {
                continue;
            }
            let next2 = tokens.get(at + 2).copied().unwrap_or(EDGE);
            if !options
                .liga_formed_before(lead, next1, Some(next2))
                .map_err(|error| error.to_string())?
            {
                if at + 2 < count {
                    return Ok(Verdict::Dead);
                }
                let mut candidates: Vec<RightToken> = Vec::new();
                for option in options.right_boundaries.clone() {
                    if option != EDGE
                        && options
                            .liga_formed_before(lead, next1, Some(option))
                            .map_err(|error| error.to_string())?
                    {
                        candidates.push(option);
                    }
                }
                for option in options.right_letters.clone() {
                    if options
                        .liga_formed_before(lead, next1, Some(option))
                        .map_err(|error| error.to_string())?
                    {
                        candidates.push(option);
                    }
                }
                return Ok(Verdict::Needs(candidates));
            }
        }
    }
    Ok(Verdict::Closed)
}

/// Candidates in the order the search tries them: the boundaries in their standing order, then the letters by name.
fn ordered(index: &SpecIndex, candidates: impl Iterator<Item = RightToken>) -> Vec<RightToken> {
    let mut boundaries: Vec<RightToken> = Vec::new();
    let mut letters: Vec<RightToken> = Vec::new();
    for candidate in candidates {
        if candidate.kind() == TokenKind::Letter {
            letters.push(candidate);
        } else {
            boundaries.push(candidate);
        }
    }
    boundaries.sort();
    letters.sort_by(|left, right| {
        index
            .resolve(left.letter())
            .cmp(index.resolve(right.letter()))
    });
    boundaries.extend(letters);
    boundaries
}

/// Every closure of `tokens` the bounded search reaches, up to [`CLOSURE_CAP`] of them: the stream itself when nothing is open, else each candidate the first open constraint asks for, appended and closed in turn. A candidate that would form a pair no survivable window admits with the stream's last token is skipped before it is appended, since the re-read would only find it dead.
fn closures(
    index: &SpecIndex,
    options: &mut WindowOptions<'_>,
    tokens: Vec<RightToken>,
    depth: usize,
    out: &mut Vec<Vec<RightToken>>,
) -> Result<(), String> {
    if out.len() >= CLOSURE_CAP {
        return Ok(());
    }
    match open_constraint(index, options, &tokens)? {
        Verdict::Closed => out.push(tokens),
        Verdict::Dead => {}
        Verdict::Needs(candidates) => {
            if depth == 0 {
                return Ok(());
            }
            for candidate in candidates {
                if let Some(last) = tokens.last()
                    && last.kind() == TokenKind::Letter
                    && options.formation_impossible(last.letter(), candidate)
                {
                    continue;
                }
                let mut extended = tokens.clone();
                extended.push(candidate);
                closures(index, options, extended, depth - 1, out)?;
                if out.len() >= CLOSURE_CAP {
                    break;
                }
            }
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::fixpoint::{EnumerationModes, enumerate_transitions};
    use crate::fold::{FoldRow, expand, first_match_rows, fold_product};
    use crate::index::fixtures;
    use crate::stream::{FixpointProduct, Label, LabelPool, TransitionRow};
    use crate::types::{NotesSeat, SettledSeat};
    use std::rc::Rc;

    const SHIPPING: EnumerationModes = EnumerationModes {
        simulated_prospect: true,
        vote_slots: true,
        deep_classes: true,
    };

    fn checked_prefixes(rows: &LabelRows<'_>) -> Prefixes {
        let reference = Prefixes::over_reference(rows);
        let production = Prefixes::over(rows);
        assert_same_prefixes(&reference, &production);
        production
    }

    /// A compact hand-built label-row stream. Each row gets its own outcome seat; the prefix searches read no settled record or cell, so those product tables stay empty.
    fn prefix_fixture(records: &[[&str; 7]]) -> (FixpointProduct, Vec<FoldRow>) {
        let mut labels = LabelPool::default();
        let mut outcomes = Vec::with_capacity(records.len());
        let mut transitions = Vec::with_capacity(records.len());
        for record in records {
            let [input_glyph, left, right1, right2, right3, right4, outcome] =
                (*record).map(|text| labels.intern(text));
            let settled = SettledSeat::at(outcomes.len());
            outcomes.push(outcome);
            transitions.push(TransitionRow {
                input_glyph,
                left,
                right1,
                right2,
                right3,
                right4,
                settled,
                left_settled: None,
                provenance: NotesSeat::at(0),
                prospect: 0,
                joint: false,
            });
        }
        transitions.sort_by(|left, right| left.key(&labels).cmp(&right.key(&labels)));
        let product = FixpointProduct {
            config: "prefix-fixture".to_owned(),
            transitions,
            labels,
            outcomes,
            deep_classes: Vec::new(),
            cited_provenance: Vec::new(),
            cells: Vec::new(),
            seats: Vec::new(),
            notes: vec![Vec::new()],
        };
        let fold = product
            .transitions
            .iter()
            .enumerate()
            .map(|(seat, row)| FoldRow {
                seat: seat as u32,
                right3: Rc::clone(product.labels.text(row.right3)),
                right4: Rc::clone(product.labels.text(row.right4)),
                joint: false,
            })
            .collect();
        (product, fold)
    }

    fn query_at<'a>(rows: &LabelRows<'a>, row: usize) -> (SuccessorQuery<'a>, usize) {
        let key = rows.key(row);
        let prefix = [key[2], rows.outcome(row).as_ref(), key[3], key[4], key[5]];
        let pinned = prefix[3..]
            .iter()
            .position(|label| *label == NA_LABEL)
            .map_or(5, |open| 3 + open);
        (SuccessorQuery::from_prefix(prefix, pinned), pinned)
    }

    fn ranges_at(rows: &LabelRows<'_>, row: usize) -> Vec<(usize, usize)> {
        let key = rows.key(row);
        let prefix = [key[2], rows.outcome(row).as_ref(), key[3], key[4], key[5]];
        let (_, pinned) = query_at(rows, row);
        let mut ranges = Vec::new();
        for carried in 3..=pinned {
            let mut wanted = prefix[..carried].to_vec();
            if carried < pinned {
                wanted.push(NA_LABEL);
            }
            let width = wanted.len();
            let start = partition(rows, |candidate| candidate[..width] < wanted[..]);
            let end = partition(rows, |candidate| candidate[..width] <= wanted[..]);
            if start < end {
                ranges.push((start, end));
            }
        }
        ranges
    }

    /// The signature is the exact query through its last carried slot: text behind the first deep `#NA` cannot split it, while a carried label can.
    #[test]
    fn successor_query_signatures_stop_at_the_carried_slot_cutoff() {
        let shallow_a =
            SuccessorQuery::from_prefix(["next", "outcome", "right1", NA_LABEL, "behind-a"], 3);
        let shallow_b =
            SuccessorQuery::from_prefix(["next", "outcome", "right1", NA_LABEL, "behind-b"], 3);
        let carried =
            SuccessorQuery::from_prefix(["next", "outcome", "right1", "right2", NA_LABEL], 4);
        let mut signatures: HashSet<SuccessorQuery<'_>> = HashSet::default();
        assert!(signatures.insert(shallow_a));
        assert!(!signatures.insert(shallow_b));
        assert!(signatures.insert(carried));
    }

    /// Equal signatures recur first through two same-distance boundary seeds and then through a row they reach, while distinct cutoff-3/4/5 signatures share strictly nested nonempty ranges. Production retains every reference parent and distance across the shape, including the first FIFO parent of every nested target.
    #[test]
    fn prefixes_preserve_fifo_parents_across_the_successor_shapes() {
        let (product, fold_rows) = prefix_fixture(&[
            ["S3", EDGE_LABEL, "A", "A", NA_LABEL, NA_LABEL, "O"],
            ["S3", "uni200C", "A", "A", NA_LABEL, NA_LABEL, "O"],
            ["S4", EDGE_LABEL, "A", "A", "C", NA_LABEL, "O"],
            ["S5", EDGE_LABEL, "A", "A", "C", "D", "O"],
            ["A", "O", "A", NA_LABEL, NA_LABEL, NA_LABEL, "X0"],
            ["A", "O", "A", "A", NA_LABEL, NA_LABEL, "O"],
            ["A", "O", "A", "C", NA_LABEL, NA_LABEL, "X1"],
            ["A", "O", "A", "C", "D", NA_LABEL, "X2"],
            [
                "U",
                "never",
                "edge",
                NA_LABEL,
                NA_LABEL,
                NA_LABEL,
                "unreached",
            ],
        ]);
        let rows = LabelRows::new(&product, &fold_rows);
        let reference = Prefixes::over_reference(&rows);
        let production = Prefixes::over(&rows);
        assert_same_prefixes(&reference, &production);
        let find = |input: &str, left: &str| {
            (0..rows.len())
                .find(|&row| {
                    rows.input_glyph(row).as_ref() == input && rows.left(row).as_ref() == left
                })
                .expect("the fixture row exists")
        };
        let shallow = find("S3", EDGE_LABEL);
        let tied = find("S3", "uni200C");
        let middle = find("S4", EDGE_LABEL);
        let deep = find("S5", EDGE_LABEL);
        let repeated_later = (0..rows.len())
            .find(|&row| {
                rows.input_glyph(row).as_ref() == "A"
                    && rows.left(row).as_ref() == "O"
                    && rows.outcome(row).as_ref() == "O"
            })
            .expect("the later repeated signature exists");
        let unreachable = find("U", "never");
        assert_eq!(production.dist[shallow], 0);
        assert_eq!(production.dist[tied], 0);
        assert_eq!(production.dist[repeated_later], 1);
        assert_eq!(production.parent[repeated_later], shallow as u32);
        assert_eq!(production.dist[unreachable], UNREACHED);
        let (shallow_query, shallow_cutoff) = query_at(&rows, shallow);
        let (tied_query, tied_cutoff) = query_at(&rows, tied);
        let (later_query, later_cutoff) = query_at(&rows, repeated_later);
        assert_eq!(shallow_query, tied_query);
        assert_eq!(shallow_query, later_query);
        assert_eq!([shallow_cutoff, tied_cutoff, later_cutoff], [3, 3, 3]);
        assert_eq!(query_at(&rows, middle).1, 4);
        assert_eq!(query_at(&rows, deep).1, 5);

        let shallow_ranges = ranges_at(&rows, shallow);
        let middle_ranges = ranges_at(&rows, middle);
        let deep_ranges = ranges_at(&rows, deep);
        assert_eq!(shallow_ranges.len(), 1);
        assert_eq!(middle_ranges.len(), 2);
        assert_eq!(deep_ranges.len(), 3);
        let outer = shallow_ranges[0];
        let nested: Vec<(usize, usize)> = middle_ranges
            .iter()
            .chain(&deep_ranges)
            .copied()
            .filter(|range| *range != outer)
            .collect();
        assert!(!nested.is_empty());
        assert!(
            nested
                .iter()
                .all(|&(start, end)| outer.0 <= start && end <= outer.1),
            "the distinct deeper signatures do not search nested ranges"
        );
    }

    /// The sharing rule is independent of the enumeration world's deep-class grain and the two prospect/vote arms.
    #[test]
    fn prefixes_match_the_reference_in_every_enumeration_world() {
        let index = fixtures::mini();
        for modes in [
            SHIPPING,
            EnumerationModes {
                simulated_prospect: true,
                vote_slots: true,
                deep_classes: false,
            },
            EnumerationModes {
                simulated_prospect: true,
                vote_slots: false,
                deep_classes: true,
            },
            EnumerationModes {
                simulated_prospect: false,
                vote_slots: true,
                deep_classes: true,
            },
            EnumerationModes {
                simulated_prospect: false,
                vote_slots: false,
                deep_classes: true,
            },
        ] {
            let product = enumerate_transitions(&index, &[], modes).expect("the fixpoint closes");
            let fold_rows = expand(&product);
            let rows = LabelRows::new(&product, &fold_rows);
            let reference = Prefixes::over_reference(&rows);
            let production = Prefixes::over(&rows);
            assert_same_prefixes(&reference, &production);
        }
    }

    /// Every row of the fixture's product sits on a producer chain from a seed, a short one, and each link of the chain is the successor relation the rows pin: the next row's input is this row's right1, its left is this row's outcome, and its right slots are this row's shifted one up wherever this row carries them.
    #[test]
    fn every_row_of_the_fixture_is_reached_by_a_short_chain_the_rows_pin() {
        let index = fixtures::mini();
        let product = enumerate_transitions(&index, &[], SHIPPING).expect("the fixpoint closes");
        let fold_rows = expand(&product);
        let rows = LabelRows::new(&product, &fold_rows);
        let prefixes = checked_prefixes(&rows);
        assert!(!rows.is_empty());
        let longest = prefixes.dist().iter().copied().max().expect("rows");
        assert!(longest != UNREACHED, "a row no seed reaches");
        assert!(longest <= 8, "a chain {longest} rows long on the fixture");
        for row in 0..rows.len() {
            let chain = prefixes.chain(row).expect("reached");
            assert!(boundaryish(rows.left(chain[0])));
            for pair in chain.windows(2) {
                let (from, to) = (rows.key(pair[0]), rows.key(pair[1]));
                assert_eq!(rows.outcome(pair[0]).as_ref(), to[1]);
                assert_eq!(from[2], to[0]);
                assert_eq!(from[3], to[2]);
                for (pinned, next) in [(from[4], to[3]), (from[5], to[4])] {
                    if pinned == NA_LABEL || next == NA_LABEL {
                        break;
                    }
                    assert_eq!(pinned, next);
                }
            }
        }
    }

    /// Every rule of the fixture's fold carries a certificate; each one extends the pinned tokens of one of the rows the replay handed that rule, and the window it carries at that row's position — the row's own input and left, the tail read off the certificate — first-matches the rule under the fold's own first-match. The same check the build ran, restated from the artifact side.
    #[test]
    fn every_rule_of_the_fixture_carries_a_certificate_it_first_matches() {
        let index = fixtures::mini();
        let product = enumerate_transitions(&index, &[], SHIPPING).expect("the fixpoint closes");
        let folded = fold_product(&index, product.clone()).expect("and folds");
        let decision = &folded.decision;
        assert_eq!(decision.certificates.len(), decision.rules.len());
        assert!(!decision.rules.is_empty());
        let fold_rows = expand(&product);
        let rows = LabelRows::new(&product, &fold_rows);
        let prefixes = checked_prefixes(&rows);
        let first_rows = first_match_rows(
            &rows,
            &decision.rules,
            Some(&folded.replay_lefts),
            ROW_CAP,
            Some(prefixes.dist()),
        )
        .expect("the replay the build ran");
        let by_input = rules_by_input(&decision.rules);
        for (seat, certificate) in decision.certificates.iter().enumerate() {
            assert!(certificate.len() <= 16, "{certificate:?}");
            let tokens: Vec<RightToken> = certificate
                .iter()
                .map(|label| {
                    slot_token(&index, label)
                        .expect("a certificate spells known labels")
                        .expect("and never the edge or #NA")
                })
                .collect();
            let (row, position) = first_rows[seat]
                .iter()
                .find_map(|&row| {
                    let (pinned, position) = pinned_tokens(&index, &prefixes, &rows, row)
                        .expect("pinned")
                        .expect("reached");
                    tokens.starts_with(&pinned).then_some((row, position))
                })
                .expect("the certificate extends one of the rule's replayed rows");
            let key = window_at(
                &index,
                &tokens,
                position,
                rows.input_glyph(row),
                rows.left(row),
            );
            let spelled: [&str; 6] = [&key[0], &key[1], &key[2], &key[3], &key[4], &key[5]];
            assert_eq!(
                first_match(&by_input, spelled),
                Some(seat),
                "{certificate:?}"
            );
        }
    }

    /// A rule nothing first-matches never reaches the certificates: the replay refuses it first, so a poisoned rule list fails in the fold with the never-first sentence rather than here with a certificate one.
    #[test]
    fn a_rule_no_row_first_matches_is_refused_before_certification() {
        let index = fixtures::mini();
        let product = enumerate_transitions(&index, &[], SHIPPING).expect("the fixpoint closes");
        let folded = fold_product(&index, product.clone()).expect("and folds");
        let fold_rows = expand(&product);
        let rows = LabelRows::new(&product, &fold_rows);
        let mut rules = folded.decision.rules.clone();
        rules.push(Rule {
            input_glyph: rules[0].input_glyph.clone(),
            backtrack: Some(vec!["qsNever.loop".into()]),
            look1: None,
            look2: None,
            look3: None,
            look4: None,
            outcome: rules[0].input_glyph.clone(),
            provenance: Vec::new(),
            joint: false,
        });
        let complaint = first_match_rows(&rows, &rules, None, ROW_CAP, None)
            .expect_err("the dead rule is refused");
        assert!(
            complaint.contains("no replayed row first-matches"),
            "{complaint}"
        );
    }

    /// A row no producer chain reaches — a phantom the worklist would have pinned wrongly, here a real row re-keyed to a pin no window before it carries — is a pin failure naming the rule, the row and the pin, not the empty answer a hand-built product gets; and a product the spec cannot spell still gets that empty answer.
    #[test]
    fn a_row_no_chain_reaches_is_a_pin_failure_and_an_unspellable_one_is_not() {
        let index = fixtures::mini();
        let product = enumerate_transitions(&index, &[], SHIPPING).expect("the fixpoint closes");
        let mut rules = fold_product(&index, product.clone())
            .expect("and folds")
            .decision
            .rules;
        let mut letters: Vec<Label> = Vec::new();
        let mut lefts: Vec<Label> = Vec::new();
        for row in &product.transitions {
            if !boundaryish(product.labels.text(row.right1)) && !letters.contains(&row.right1) {
                letters.push(row.right1);
            }
            if !boundaryish(product.labels.text(row.left)) && !lefts.contains(&row.left) {
                lefts.push(row.left);
            }
        }
        let carried = |row: &TransitionRow, slot: usize, label: &Label| {
            product.transitions.iter().any(|other| {
                other.input_glyph == row.input_glyph
                    && match slot {
                        0 => other.left == *label,
                        1 => other.left == row.left && other.right1 == *label,
                        _ => {
                            other.left == row.left
                                && other.right1 == row.right1
                                && other.right2 == *label
                        }
                    }
            })
        };
        let (real, slot, label) = product
            .transitions
            .iter()
            .filter(|row| {
                !boundaryish(product.labels.text(row.left))
                    && !boundaryish(product.labels.text(row.right1))
            })
            .find_map(|row| {
                [0usize, 1, 2].into_iter().find_map(|slot| {
                    let pool = if slot == 0 { &lefts } else { &letters };
                    pool.iter()
                        .find(|label| !carried(row, slot, label))
                        .map(|label| (row, slot, *label))
                })
            })
            .expect("a letter-left row and a pin no window before it carries");
        let mut phantom = real.clone();
        match slot {
            0 => phantom.left = label,
            1 => phantom.right1 = label,
            _ => phantom.right2 = label,
        }
        let mut phantom_product = product.clone();
        let settled = phantom_product.settled(&phantom).clone();
        phantom.settled = SettledSeat::at(phantom_product.seats.len());
        phantom_product.seats.push(settled);
        let outcome = phantom_product.labels.intern("qsPhantom.pin");
        phantom_product.outcomes.push(outcome);
        phantom_product.transitions.push(phantom.clone());
        phantom_product.transitions.sort_by(|left, right| {
            left.key(&phantom_product.labels)
                .cmp(&right.key(&phantom_product.labels))
        });
        rules.insert(
            0,
            Rule {
                input_glyph: Rc::clone(phantom_product.labels.text(phantom.input_glyph)),
                backtrack: Some(vec![Rc::clone(phantom_product.labels.text(phantom.left))]),
                look1: Some(vec![Rc::clone(phantom_product.labels.text(phantom.right1))]),
                look2: Some(vec![Rc::clone(phantom_product.labels.text(phantom.right2))]),
                look3: None,
                look4: None,
                outcome: Rc::from("qsPhantom.pin"),
                provenance: Vec::new(),
                joint: false,
            },
        );
        let fold_rows = expand(&phantom_product);
        let rows = LabelRows::new(&phantom_product, &fold_rows);
        let prefixes = checked_prefixes(&rows);
        let phantom_row = (0..rows.len())
            .find(|&row| rows.outcome(row).as_ref() == "qsPhantom.pin")
            .expect("the phantom remains in the expansion");
        assert_eq!(prefixes.dist[phantom_row], UNREACHED);
        let first_rows = first_match_rows(&rows, &rules, None, ROW_CAP, Some(prefixes.dist()))
            .expect("the phantom's rule wins it");
        let mut options = WindowOptions::new(&index).expect("the fixture's options build");
        let complaint = certify(&index, &mut options, &prefixes, &rows, &rules, &first_rows)
            .expect_err("the phantom row is a pin failure");
        assert!(complaint.starts_with("rule 0 ("), "{complaint}");
        assert!(complaint.contains("is a pin failure"), "{complaint}");
        assert!(
            complaint.contains(&format!(
                "({}, {}, {}, {}",
                phantom_product.labels.text(phantom.input_glyph),
                phantom_product.labels.text(phantom.left),
                phantom_product.labels.text(phantom.right1),
                phantom_product.labels.text(phantom.right2)
            )),
            "{complaint}"
        );
        assert!(complaint.contains("never produced"), "{complaint}");
        let label = product.labels.text(label);
        let pin = if slot == 0 {
            format!("rows settle to {label}, but none of them before")
        } else {
            format!("carries {label} at its")
        };
        assert!(complaint.contains(&pin), "{complaint}");

        let mut bench_row = product
            .transitions
            .iter()
            .find(|row| {
                boundaryish(product.labels.text(row.left))
                    && !boundaryish(product.labels.text(row.right1))
            })
            .expect("a seed row with a letter at right1")
            .clone();
        let mut bench = product.clone();
        bench_row.right2 = bench.labels.intern("qsNever");
        let settled = bench.settled(&bench_row).clone();
        bench_row.settled = SettledSeat::at(bench.seats.len());
        bench.seats.push(settled);
        let outcome = bench.labels.intern("qsPhantom.pin");
        bench.outcomes.push(outcome);
        bench.transitions.push(bench_row.clone());
        bench
            .transitions
            .sort_by(|left, right| left.key(&bench.labels).cmp(&right.key(&bench.labels)));
        let fold_rows = expand(&bench);
        let rows = LabelRows::new(&bench, &fold_rows);
        let prefixes = checked_prefixes(&rows);
        rules[0] = Rule {
            input_glyph: Rc::clone(bench.labels.text(bench_row.input_glyph)),
            backtrack: None,
            look1: Some(vec![Rc::clone(bench.labels.text(bench_row.right1))]),
            look2: Some(vec![Rc::from("qsNever")]),
            look3: None,
            look4: None,
            outcome: Rc::from("qsPhantom.pin"),
            provenance: Vec::new(),
            joint: false,
        };
        let first_rows = first_match_rows(&rows, &rules, None, ROW_CAP, Some(prefixes.dist()))
            .expect("the rule wins its row");
        let answer = certify(&index, &mut options, &prefixes, &rows, &rules, &first_rows)
            .expect("a hand-built product certifies nothing rather than failing");
        assert!(answer.is_empty());
    }

    /// The tail closure's two constraints, on the ligature fixture: a stream ending in a surviving formation pair is closed with a follower under which the guard fires, and one ending in a formed ligature is closed so the ligature still stands. Both are read back through the same constraint check, so a closed stream is one the check calls closed.
    #[test]
    fn a_closed_stream_raises_no_open_constraint() {
        let index = fixtures::mini();
        let mut options = WindowOptions::new(&index).expect("the fixture's options build");
        let product = enumerate_transitions(&index, &[], SHIPPING).expect("the fixpoint closes");
        let fold_rows = expand(&product);
        let rows = LabelRows::new(&product, &fold_rows);
        let prefixes = checked_prefixes(&rows);
        let mut closed_any = false;
        for row in 0..rows.len() {
            let (tokens, _position) = pinned_tokens(&index, &prefixes, &rows, row)
                .expect("pinned")
                .expect("reached");
            let mut closed = Vec::new();
            closures(&index, &mut options, tokens, CLOSURE_DEPTH, &mut closed)
                .expect("the closure runs");
            for stream in closed {
                closed_any = true;
                assert!(matches!(
                    open_constraint(&index, &mut options, &stream).expect("re-read"),
                    Verdict::Closed
                ));
            }
        }
        assert!(closed_any);
    }
}
