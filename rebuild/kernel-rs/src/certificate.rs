//! Builds one certificate per settlement rule: a token stream whose settlement should make the rule fire. The stream is read off the rows the fixpoint recorded, not searched for over the finished table.
//!
//! A rule can fire when some string reaches a window the rule first-matches, and the rows already record such a string for every window they hold. A row's successor is a row whose input is the row's right1, whose left is the row's outcome, and whose right1, right2, and right3 are the row's right2, right3, and right4, as far as both rows carry them. A seed is a row whose left is a boundary, reached by the text of that one boundary. A chain of successors from a seed to a row gives the inputs that put the row's left state in place, and the row's own slots give the right context settlement read. [`Prefixes`] finds the shortest such chain for every row with one breadth-first pass over the rows in their key order.
//!
//! The chain fixes every token up to the row's last carried right slot, and only the tail after it is open. A surviving formation pair at the end of the stream still needs the section 5.7 guard's follower and second slot, and a formed ligature at the end still needs a next token before which it forms. This module closes that tail, checks that the closed window still first-matches the rule under the fold's first-match-wins, and returns one token stream per rule. The build writes them into the windows head, and `run_m1`'s witness stage settles each one through the crate and checks that its rule first-matches at some position. That check verifies the pins the chain carries: a chain whose prefix settled to a different left state would settle its certificate to a different rule. When no chain reaches any of a rule's rows, the worklist admitted a left state beside a right1 that no producing window had, and [`certify`] fails the build, naming the rule, the row, and the pin.
//!
//! The chain is the shortest one, not the worklist's own, because the worklist is a stack. An item's first visitor is whatever the depth-first order reached it through, so those chains are thousands of letters long, and the witness stage would have to settle every letter. A breadth-first chain is as short as any text that reaches the row.
//!
//! The tail closure is a bounded search, because a token appended to satisfy one constraint can raise another: a follower that makes a pair survive can itself begin a surviving pair, and a ligature at the new end needs its own follower. Each step appends one token the first open constraint asks for and re-reads the whole stream. It tries boundaries first, because a boundary raises no new constraint, then letters in name order, up to `CLOSURE_DEPTH` appends. Several closures are kept per row and several rows per rule, because a concrete tail can make an earlier rule first-match the window, when that rule's deep class admits the tail's token where the row's `#NA` did not. The certificate is the first closure, of the first row in shortest-chain order, whose window the rule wins.
//!
//! A certificate does not show that HarfBuzz applies the rule; `gate:conform` checks that over the compiled font. A certificate shows that the rule is reachable under the kernel's own settlement. The fold's never-first check cannot show this, because it replays only the table's own rows, and a row is reachable only if its left state is.

use std::collections::VecDeque;

use crate::fixpoint::right_token_label;
use crate::fold::{LabelRows, Rule, boundaryish, first_match, rules_by_input};
use crate::hash::HashSet;
use crate::index::SpecIndex;
use crate::options::WindowOptions;
use crate::types::{EDGE, NAMER_DOT, RightToken, SPACE, TokenKind, ZWNJ};

/// How many of a rule's first-matched rows, shortest chains first, the fold keeps for [`certify`] to try. The cap limits only the candidates: if a rule's only certifiable row is past the cap, the build fails.
pub const ROW_CAP: usize = 32;

/// How many tokens the tail closure may append before giving a row up. Every constraint reads at most two tokens past the pair or ligature that raises it, so a closure that needs more than a few appends is following a chain of pairs that no reachable row carries.
const CLOSURE_DEPTH: usize = 6;

/// How many closed streams the search keeps per row before moving to the next row.
const CLOSURE_CAP: usize = 8;

/// The label of a slot the window does not carry, `table.NA_LABEL`.
const NA_LABEL: &str = "#NA";

/// The label the run edge carries, `table.EDGE_LABEL`.
const EDGE_LABEL: &str = "#EDGE";

/// The chain length of a row no seed reaches through the producer relation.
pub const UNREACHED: u32 = u32::MAX;

/// Every row's shortest producer chain: the row it is reached from, and how many rows the chain holds before it (zero for a seed). Because the rows are in key order, a row's successors are a few contiguous runs found by binary search, one for each slot at which a successor's window may stop carrying slots. A run is scanned at most once, because every row in it is assigned the first time. A row the pass never reaches keeps [`UNREACHED`], which the fold ranks as the longest chain.
pub struct Prefixes {
    dist: Vec<u32>,
    parent: Vec<u32>,
}

/// One successor query, cut at the producer's first deep slot that is `#NA`. Every query holds the successor's input (the producer's right1), its left (the producer's outcome), and its right1 (the producer's right2). A query also holds the producer's right3 and right4 while they are carried. Labels past the cut are not stored, so they cannot make two otherwise equal queries differ.
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
    /// Finds every row's shortest producer chain. Each distinct successor query is searched only for the first producer in queue order that asks it. An equal query yields the same runs, whose rows the first producer has already assigned, so skipping it changes no parent or distance. Distinct queries are still searched even when their runs overlap, so the `scanned` set skips only the runs the reference search skips.
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
            // A successor carries the producer's pins only as far as its own window enumerated slots, and every slot after that is `#NA`. So besides the run that matches the whole pinned prefix, each shorter prefix followed by `#NA` is also a run of successors: for example, the rows whose non-deep input leaves the third and fourth slots at `#NA`.
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

/// Asserts that two prefix searches found the same parent and distance for every row.
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

/// One certificate per rule, in rule order. Each is a token stream in the windows vocabulary: rune names for letters, and the three boundary glyph labels for boundaries. `first_rows` is the result of [`crate::fold::first_match_rows`]: the rows each rule first-matched under the replay, shortest chain first.
///
/// The fold has already shown that a replayed row first-matches every rule, so a rule for which no row closes into a certificate means the worklist got a pin wrong, and this returns an error naming the rule. There are two error messages, one for each way a row can fail. If no producer chain reaches any of the rule's rows, `pin_failure` names the first row and the pin that does not hold: the worklist admitted a left state beside a right1 that no producing window had. If a chain reaches a row but no closure of its tail gives a window the rule wins, the pins held and the constraint search found nothing.
///
/// A hand-built test product can get an empty list instead of an error, in two cases: one of a rule's rows has a label the spec models no rune for, or an unreached row's left is no row's outcome. A build's product can do neither, because every label of an enumeration is a modeled rune or a boundary and every letter left is some window's outcome. `run_m1`'s witness stage fails a table whose certificate count differs from its rule count, so an empty list cannot reach a font; the errors here are the check meant to catch a build's pin failure.
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

/// The error message for a rule none of whose replayed rows lies on a producer chain. It names the rule, the first such row, and the pin that does not hold. A producer of the row would be a row whose outcome is the row's left, whose right1 is the row's input, and whose right2, right3, and right4 equal the row's right1, right2, and right3 up to the first `#NA`. This filters the rows by those conditions in that order, and the first condition no row meets is the pin the worklist admitted without a window to produce it. Returns `None` when no row settles to the row's left at all. The worklist only pins a left it settled in some window, so that happens only in a hand-built product, which gets the empty certificate list.
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

/// The tokens one replayed row pins, and the position of the row's input among them. The tokens are the seed's boundary (left out when it is the run edge), the family of each earlier row's input on the chain, the family of the row's input, and then the row's right slots up to the first `#NA` or `#EDGE`. The closure chooses only what follows. Returns `None` for a row no seed reaches, and an error for a label the spec does not model.
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

/// The letter token for a row's input label: the family before any stance or lock suffix.
fn input_token(index: &SpecIndex, input_glyph: &str) -> Result<RightToken, String> {
    letter_of(index, input_glyph.split('.').next().unwrap_or(input_glyph))
}

/// The letter token for a rune name, or an error when the spec models no rune by that name. A class id would be such a name, but the fold expands class ids before rows reach this module.
fn letter_of(index: &SpecIndex, name: &str) -> Result<RightToken, String> {
    index
        .sym_of(name)
        .filter(|rune| index.is_modeled(*rune))
        .and_then(|rune| index.letter(rune))
        .ok_or_else(|| format!("certificate: {name} names no rune the spec models"))
}

/// The token for one right-slot label: `None` for `#EDGE` and `#NA`, since both mean the text stops there.
fn slot_token(index: &SpecIndex, label: &str) -> Result<Option<RightToken>, String> {
    match label {
        EDGE_LABEL | NA_LABEL => Ok(None),
        "space" => Ok(Some(SPACE)),
        "uni200C" => Ok(Some(ZWNJ)),
        "periodcentered" => Ok(Some(NAMER_DOT)),
        name => letter_of(index, name).map(Some),
    }
}

/// The six labels of the window at `position` in a closed stream: the row's own input and left labels, then the four right slots read off the tokens. A slot past the end of the stream is `#EDGE`, and every slot after the first boundary or `#EDGE` is `#NA`, since no record reads past a boundary.
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

/// Checks the formation constraints a post-formation token stream must satisfy for the raw replay to return the same stream, left to right, and reports the first one the stream does not satisfy. A surviving formation pair needs the section 5.7 guard to fire, which takes a follower the pair's survivable map names and a second slot that follower's allowed set admits. A formed ligature needs its own guard not to fire over the two raw tokens after it. A pair before a boundary always forms, so a boundary follower makes the stream [`Verdict::Dead`].
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

/// Candidates in the order the search tries them: the boundaries in [`crate::options::RIGHT_BOUNDARIES`] order, then the letters by name.
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

/// Appends to `out` every closure of `tokens` the bounded search reaches, up to [`CLOSURE_CAP`] in all: the stream itself when nothing is open, or else each candidate the first open constraint asks for, appended and closed in turn. A candidate that would form a pair with the stream's last token that no survivable window admits is skipped before it is appended, since the re-read would find the stream dead.
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

    /// Two seeds at the same distance and a row they reach all ask the same successor query, while three queries cut at three, four, and five labels have nested nonempty runs. The production search keeps every parent and distance the reference search finds, including the first parent in queue order of every row in a nested run.
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

    /// The production search matches the reference search with deep classes on and off and with each simulated-prospect and vote-slot setting.
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

    /// Every row of the fixture's product lies on a short producer chain from a seed, and each link is a successor: the next row's input is this row's right1, its left is this row's outcome, and its right slots are this row's right2, right3, and right4 wherever both carry them.
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

    /// Every rule of the fixture's fold has a certificate. Each certificate extends the pinned tokens of one of the rule's replayed rows, and the window at that row's position (the row's own input and left, with right slots read off the certificate) first-matches the rule.
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

    /// A rule that no row first-matches fails in the fold's replay with the never-first message, before certification.
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

    /// A row no producer chain reaches (here a real row re-keyed to a pin that no earlier window carries) is a pin failure naming the rule, the row, and the pin. A product with a label the spec does not model gets the empty list instead.
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

    /// On the mini fixture, every stream the tail closure returns for a row's pinned tokens reads back as closed under the same constraint check.
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
