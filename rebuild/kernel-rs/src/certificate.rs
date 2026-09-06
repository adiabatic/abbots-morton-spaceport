//! One realizing string per settlement rule, read off the rows the fixpoint recorded rather than searched for over the finished table. A rule is realizable exactly when some string reaches a window it first-matches, and the rows already pin such a string for every window they hold: a row's successor at the next position is a row whose input is its right1, whose left is its outcome and whose right slots are its own shifted one up, so a chain of rows from a seed — a row whose left is a boundary, reached by the text of that one boundary — to the row spells the inputs that put the row's left state where the row found it, and the row's own slots spell the right context the settlement read. [`Prefixes`] is that chain for every row, the shortest one, found by one breadth-first pass over the rows in their key order; what is left open is only the tail past the last pinned slot — the section 5.7 formation guard's second slot for a surviving pair at the window's end, and the slot a formed ligature at the window's end must still stand before. This module closes that tail, checks the closed window still first-matches the rule under the fold's own first-match-wins, and hands back one token stream per rule; the build writes them into the windows head, and `run_m1`'s witness stage settles each one through the crate and asserts the rule fires at the position the certificate names. That check is what proves the pins: a chain whose prefix settled to some other left state would settle its certificate to some other rule.
//!
//! The chain is the shortest one and not the worklist's own because the worklist is a stack: an item's first visitor is whatever the depth-first walk reached it through, and the chains that fall out of that are thousands of letters long, each of which the witness stage would settle wave by wave. Breadth-first over the rows, every row's chain is as short as any text that reaches it.
//!
//! The tail closure is a bounded search, because a token appended to satisfy one constraint can open another: a follower that makes a pair survive may itself begin a survivable pair, a ligature standing at the new end needs its own follower. Each step appends the one token the first open constraint asks for, re-reads the whole stream, and tries the candidates in a fixed order — the boundary glyphs first, since a boundary closes every constraint behind it, then the letters in name order — to a depth no realizable row needs more than a couple of steps of. Several closures are kept per row and several rows per rule, because a concrete tail can hand the window to an earlier rule whose deep class admits the tail's token where the row's `#NA` did not; the certificate is the first closure of the first row whose concrete window the rule wins, the rows tried shortest chain first.
//!
//! What a certificate is not: a proof that HarfBuzz applies the rule. That is `gate:conform`'s, over the compiled font. A certificate proves the rule reachable in the kernel's own settlement, which is the realizability half of the dead-rule alarm — the half the fold's never-first refusal cannot state, because that replay reads the table's own rows and a row is realizable only if its left state is.

use std::collections::{HashSet, VecDeque};

use crate::fixpoint::right_token_label;
use crate::fold::{LabelRows, Rule, boundaryish, first_match, rules_by_input};
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

/// Every row's shortest producer chain: the row it is reached from and how many rows the chain holds before it, zero for a seed. Built by one breadth-first pass over the rows in their key order, so a successor set — the rows at the next position whose input, left and pinned right slots the row fixes — is a contiguous run found by binary search, and a run once reached is never scanned again, because its every row was assigned the first time. A row the pass never reaches keeps [`UNREACHED`], which the fold treats as the longest chain there is.
pub struct Prefixes {
    dist: Vec<u32>,
    parent: Vec<u32>,
}

impl Prefixes {
    pub fn over(rows: &LabelRows<'_>) -> Prefixes {
        let count = rows.len();
        let mut dist = vec![UNREACHED; count];
        let mut parent = vec![UNREACHED; count];
        let mut queue: VecDeque<u32> = VecDeque::new();
        let mut scanned: HashSet<(u32, u32)> = HashSet::new();
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
            let prefix = &prefix[..pinned];
            let start = partition(rows, |key| &key[..pinned] < prefix);
            let end = partition(rows, |key| &key[..pinned] <= prefix);
            if start == end || !scanned.insert((start as u32, end as u32)) {
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

/// One certificate per rule, in rule order, each a token stream spelled in the windows vocabulary: rune names for letters, the three boundary glyph labels for the boundaries. `first_rows` is what [`crate::fold::first_match_rows`] handed back, the rows each rule first-matched under the replay, shortest chain first. A rule no row of its own closes into a certificate is refused, naming the rule, because the fold has already proven a replayed row first-matches it and a row that cannot be realized is a pin the worklist got wrong.
///
/// A product the spec cannot spell certifies nothing and answers the empty list: a rule none of whose rows renders as a text — a deep-slot member the spec models no rune for — is a hand-built product's, the fold's own test bench, and not a build's, since an enumeration's every label is a modeled rune or a boundary. The windows head then carries no certificates, and `run_m1`'s witness stage refuses a table whose certificates do not cover its rules, so the empty answer can never reach a font.
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
        'rows: for &row in &first_rows[seat] {
            let Some((tokens, position)) = pinned_tokens(index, prefixes, rows, row).ok().flatten()
            else {
                continue;
            };
            spelled_any = true;
            let mut closed: Vec<Vec<RightToken>> = Vec::new();
            closures(index, options, tokens, CLOSURE_DEPTH, &mut closed)?;
            for candidate in closed {
                let base = rows.base(row);
                let key = window_at(index, &candidate, position, &base.input_glyph, &base.left);
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
            None if !spelled_any => return Ok(Vec::new()),
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
    let base = rows.base(row);
    tokens.push(input_token(index, &base.input_glyph)?);
    for label in [
        &*base.right1,
        &*base.right2,
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
        .map(RightToken::Letter)
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
                let mut followers: Vec<RightToken> =
                    map.keys().copied().map(RightToken::Letter).collect();
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
    use crate::fold::{expand, first_match_rows, fold_product};
    use crate::index::fixtures;

    const SHIPPING: EnumerationModes = EnumerationModes {
        simulated_prospect: true,
        vote_slots: true,
        deep_classes: true,
    };

    /// Every row of the fixture's product sits on a producer chain from a seed, a short one, and each link of the chain is the successor relation the rows pin: the next row's input is this row's right1, its left is this row's outcome, and its right slots are this row's shifted one up wherever this row carries them.
    #[test]
    fn every_row_of_the_fixture_is_reached_by_a_short_chain_the_rows_pin() {
        let index = fixtures::mini();
        let product = enumerate_transitions(&index, &[], SHIPPING).expect("the fixpoint closes");
        let fold_rows = expand(&product);
        let rows = LabelRows::new(&product.transitions, &product.notes, &fold_rows);
        let prefixes = Prefixes::over(&rows);
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
                    if pinned == NA_LABEL {
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
        let rows = LabelRows::new(&product.transitions, &product.notes, &fold_rows);
        let prefixes = Prefixes::over(&rows);
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
            let base = rows.base(row);
            let key = window_at(&index, &tokens, position, &base.input_glyph, &base.left);
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
        let rows = LabelRows::new(&product.transitions, &product.notes, &fold_rows);
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

    /// The tail closure's two constraints, on the ligature fixture: a stream ending in a surviving formation pair is closed with a follower under which the guard fires, and one ending in a formed ligature is closed so the ligature still stands. Both are read back through the same constraint check, so a closed stream is one the check calls closed.
    #[test]
    fn a_closed_stream_raises_no_open_constraint() {
        let index = fixtures::mini();
        let mut options = WindowOptions::new(&index).expect("the fixture's options build");
        let product = enumerate_transitions(&index, &[], SHIPPING).expect("the fixpoint closes");
        let fold_rows = expand(&product);
        let rows = LabelRows::new(&product.transitions, &product.notes, &fold_rows);
        let prefixes = Prefixes::over(&rows);
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
