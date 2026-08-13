//! The table build's worklist fixpoint, `rebuild/pipeline/table.py`'s `_enumerate`: every window one configuration's alphabet can reach, each settled exactly once, recorded as the row `table.assemble_tables` folds into the two tables a build persists. This is the half of the build a port replaces wholesale — every line here consults the settlement engine — and the one value the Python half needs afterwards, so the boundary between the two is this module's return type and nothing else.
//!
//! The worklist is the exactness argument rather than a traversal detail, and Python's comment above it is the specification. An item is a left state together with the pins that left was reached under: a settled left is reachable only alongside the right1 that was the producing window's right2, because an entry refusal or an unlock conditioned on the follower makes any other combination contradictory — the left would never have committed there. The right2 allowed-set carries the late-formation guard's second slot onto a surviving pair's trail window, and the right3 allowed-set carries a producing window's enumerated right4 the same way, pinning a depth-4-decided left's successor windows to the third lookahead that was actually behind them. `None` is unrestricted in both, and both are frozen sets compared by content, never by identity.
//!
//! LIFO discipline with the `seen` check at pop time is contract rather than convenience. In the pinned candidacy world the product is order-independent — the dedup is by window key, a hit reuses the recorded settled because the left label is injective into the trace's inputs, and the fired set is the union over a window set no traversal order can change — but under class grain (sub-issue #45) the first visitor of a fibre fixes its representative, so the order rows are traced in reaches the output there. Reproducing Python's push order exactly is cheaper than re-deriving, on every later reading, whether it still matters.
//!
//! What is deliberately absent is everything `_enumerate` guards behind `deriver is not None`: `_DeepFibreDeriver`, `_PendingDeepRow`, the class-grain rows and the section 2.6 echo check are sub-issue #45's. In this world `_deep_world` is false, so `class_grain` is false and the deriver is None, and the label-grain path ported here is the whole function. Deep window slots are not absent with them — `depth3_inputs` is mode-independent, so windows carrying a concrete third or fourth lookahead enumerate here exactly as they do in Python, with the option pipelines and the successor pins that go with them.
//!
//! One engine settles everything, and the two slot filters borrow it rather than building their own. That is load-bearing twice over: the trace memo makes a re-reached window free, and `Engine::fired` is the product's `cited_provenance`, so a filter probing through a second engine would silently shrink what the dead-policy gate is told fired.

use std::collections::{BTreeSet, HashMap, HashSet};
use std::rc::Rc;

use crate::census::{FourthSlotFilter, ThirdSlotFilter, fourth_slot_inputs, third_slot_inputs};
use crate::engine::{Engine, EngineModes, Slots};
use crate::error::SettleError;
use crate::index::SpecIndex;
use crate::model::Sym;
use crate::options::{FollowerMap, WindowOptions};
use crate::stream::{FixpointProduct, TransitionRow, feature_config_token};
use crate::types::{CellId, EDGE, LeftContext, RightToken, Settled, TokenKind, cell_label};

/// The label a slot the window does not carry is spelled with, `table.NA_LABEL`. A boundary at right1 puts it in the second slot as well: nothing follows a run edge inside one window.
const NA_LABEL: &str = "#NA";

/// The label the run edge carries, `table.EDGE_LABEL`. The other three boundaries label as the glyphs they ship as, which is why only this one needs a name of its own.
const EDGE_LABEL: &str = "#EDGE";

/// The boundary lefts the fixpoint seeds from, in `_enumerate`'s own order. Every reachable left state is a settled letter or one of these four.
const SEED_KINDS: [TokenKind; 4] = [
    TokenKind::Edge,
    TokenKind::Space,
    TokenKind::Zwnj,
    TokenKind::NamerDot,
];

/// What a worklist pin allows, Python's `frozenset[RightToken]`: a set compared and hashed by content so two items pinned to the same tokens are one item, behind an [`Rc`] so an item is cheap to clone into the `seen` set. The ordering the `BTreeSet` imposes is interning order and is never read — membership, intersection and equality are the only questions asked.
type Allowed = Rc<BTreeSet<RightToken>>;

/// The six labels one window is keyed by, `table.Window.key`: the input glyph, the left, and the four right slots.
type WindowKey = [String; 6];

/// One worklist item, `_enumerate`'s five-tuple: the left state, the input rune, the right1 the left was reached alongside, and the two allowed-sets pinning the slots past it. Its equality is the `seen` key exactly — Python compares `(left.kind, left.settled)` where a [`LeftContext`] is those two fields and nothing else.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
struct Item {
    left: LeftContext,
    rune: Sym,
    right1: Option<RightToken>,
    right2_allowed: Option<Allowed>,
    right3_allowed: Option<Allowed>,
}

/// What a recorded window carries beyond the six labels that key it — `table.Transition`'s remaining fields, kept apart from the key so the labels are stored once rather than in both the map's key and its value.
struct Row {
    outcome: String,
    settled: Settled,
    left_settled: Option<Settled>,
    joint: bool,
    prospect: i64,
    provenance: Vec<String>,
}

/// One configuration's whole fixpoint, `table.enumerate_transitions`: the rows in their key order, the cells they settle into, and the provenance the engine fired while tabulating.
///
/// The engine is built here rather than handed in, and it is built pinned — `simulated_prospect` and `vote_slots` both off, which is `table._deep_world` false. That is the world this sub-issue answers, and it is what lets the censuses be the chain censuses alone; sub-issue #45 widens both this construction and [`crate::census`]'s pre-gates together, since neither is meaningful without the other.
pub fn enumerate_transitions(
    index: &SpecIndex,
    features: &[Sym],
) -> Result<FixpointProduct, String> {
    enumerate_seeded(index, features, contract_seeds)
}

/// [`enumerate_transitions`] with the seeding left open, which is how the order-independence of the pinned world is testable at all. Production always passes [`contract_seeds`]; a test passes a permutation and asserts the same product, which is a statement about this world rather than about the discipline, since class grain will make the first visitor of a fibre decide its representative.
fn enumerate_seeded(
    index: &SpecIndex,
    features: &[Sym],
    seeds: fn(&WindowOptions<'_>) -> Vec<Item>,
) -> Result<FixpointProduct, String> {
    let mut engine = Engine::with_modes(
        index,
        features.iter().copied(),
        EngineModes {
            simulated_prospect: false,
            vote_slots: false,
            trace_memo: true,
            ..EngineModes::default()
        },
    );
    let config = feature_config_token(index, features.iter().copied());
    let mut options = WindowOptions::new(index).map_err(complaint)?;
    let deep_inputs = third_slot_inputs(index);
    let deep4_inputs = fourth_slot_inputs(index);
    let mut third_slot_matters = ThirdSlotFilter::new(index);
    let mut fourth_slot_matters = FourthSlotFilter::new(index);

    let mut transitions: HashMap<WindowKey, Row> = HashMap::new();
    let mut seen: HashSet<Item> = HashSet::new();
    let mut worklist = seeds(&options);

    while let Some(item) = worklist.pop() {
        // Python tests the `seen` set at pop time and adds there too; one insertion answers both, since a set that already held the item is exactly the skip.
        if !seen.insert(item.clone()) {
            continue;
        }
        let Item {
            left,
            rune,
            right1: right1_constraint,
            right2_allowed,
            right3_allowed,
        } = item;
        let locked = left.kind == TokenKind::Zwnj && index.is_entry_bearing(rune);
        let raw = index.resolve(rune);
        let input_label = if locked {
            locked_glyph_name(raw)
        } else {
            raw.to_owned()
        };
        let left_label = if left.kind == TokenKind::Letter {
            let settled = left.settled.as_ref().expect(
                "a letter left carries the cell it settled into, exactly as _enumerate asserts",
            );
            cell_label(index, &settled.cell)
        } else {
            boundary_left_label(left.kind).to_owned()
        };
        // The trace reads the raw letter whatever the label says: locking is a fact about the glyph the emitted lookup substitutes, not about what settles.
        let token = RightToken::Letter(rune);
        let right1_options: Vec<RightToken> = match right1_constraint {
            Some(constraint) => vec![constraint],
            None => boundaries_then_letters(&options),
        };

        for right1 in right1_options {
            let follower_map: Option<Rc<FollowerMap>> = if right1.kind() == TokenKind::Letter
                && options.formation_pairs.contains(&(rune, right1.letter()))
            {
                match options.survivable.get(&(rune, right1.letter())) {
                    Some(map) => Some(Rc::clone(map)),
                    // A formation pair with no survivable window at all is inadmissible outright: the pair always forms, so no window of it enumerates.
                    None => continue,
                }
            } else {
                None
            };
            let right2_options: Vec<RightToken> = if right1.kind() == TokenKind::Letter {
                let lead = right1.letter();
                let mut kept: Vec<RightToken> = boundaries_then_letters(&options);
                kept.retain(|option| {
                    !(option.kind() == TokenKind::Letter
                        && options.formation_pairs.contains(&(lead, option.letter()))
                        && !options.survivable.contains_key(&(lead, option.letter())))
                });
                if let Some(map) = &follower_map {
                    kept.retain(|option| {
                        option.kind() == TokenKind::Letter && map.contains_key(&option.letter())
                    });
                }
                if let Some(pin) = &right2_allowed {
                    kept.retain(|option| pin.contains(option));
                }
                if options.liga_sequences.contains_key(&rune) {
                    kept = retain_formed_before(&mut options, kept, rune, |option| {
                        (right1, Some(option))
                    })?;
                }
                if options.liga_sequences.contains_key(&lead) {
                    kept = retain_formed_before(&mut options, kept, lead, |option| (option, None))?;
                }
                kept
            } else {
                vec![EDGE]
            };

            for right2 in right2_options {
                let deep3_live = deep_inputs.contains(&rune)
                    && right1.kind() == TokenKind::Letter
                    && right2.kind() == TokenKind::Letter
                    && third_slot_matters
                        .matters(&mut engine, rune, right1.letter(), right2.letter())
                        .map_err(complaint)?;
                let right3_slots: Vec<Option<RightToken>> = if deep3_live {
                    let mut candidates = options
                        .right3_options(right1, right2, follower_map.as_deref())
                        .map_err(complaint)?;
                    if let Some(pin) = &right3_allowed {
                        candidates.retain(|option| pin.contains(option));
                    }
                    candidates.into_iter().map(Some).collect()
                } else {
                    vec![None]
                };

                for right3 in right3_slots {
                    let fourth_live = match right3 {
                        Some(third) => {
                            deep4_inputs.contains(&rune)
                                && third.kind() == TokenKind::Letter
                                && fourth_slot_matters
                                    .matters(
                                        &mut engine,
                                        rune,
                                        right1.letter(),
                                        right2.letter(),
                                        third.letter(),
                                    )
                                    .map_err(complaint)?
                        }
                        None => false,
                    };
                    let right4_slots: Vec<Option<RightToken>> = if fourth_live {
                        options
                            .right4_options(
                                right1,
                                right2,
                                right3.expect("a live fourth slot has a concrete third"),
                            )
                            .map_err(complaint)?
                            .into_iter()
                            .map(Some)
                            .collect()
                    } else {
                        vec![None]
                    };

                    for right4 in right4_slots {
                        let window_key: WindowKey = [
                            input_label.clone(),
                            left_label.clone(),
                            right_label(index, right1),
                            if right1.kind() == TokenKind::Letter {
                                right_label(index, right2)
                            } else {
                                NA_LABEL.to_owned()
                            },
                            slot_label(index, right3),
                            slot_label(index, right4),
                        ];
                        // A worklist item with different pins can re-reach a window key already recorded; the recorded row's settled state is what a re-trace would return, because the left label is injective into the trace's inputs, so a hit skips straight to the successor enqueue — whose pins still differ per item. The left-state comparison is that premise made executable, and can only fire if `cell_label` stops being injective over settled lefts.
                        let settled = if let Some(existing) = transitions.get(&window_key) {
                            if existing.left_settled != left.settled {
                                return Err(partition_complaint(
                                    index,
                                    &window_key,
                                    existing.left_settled.as_ref(),
                                    left.settled.as_ref(),
                                ));
                            }
                            existing.settled.clone()
                        } else {
                            let trace = engine
                                .transition_trace(
                                    &left,
                                    token,
                                    Slots::new(
                                        right1,
                                        right2,
                                        right3.unwrap_or(EDGE),
                                        right4.unwrap_or(EDGE),
                                    ),
                                )
                                .map_err(complaint)?;
                            let settled = trace.settled.clone();
                            transitions.insert(
                                window_key,
                                Row {
                                    outcome: cell_label(index, &trace.settled.cell),
                                    settled: trace.settled,
                                    left_settled: left.settled.clone(),
                                    joint: trace.joint_floor,
                                    prospect: trace.prospect,
                                    provenance: trace.notes,
                                },
                            );
                            settled
                        };

                        if right1.kind() == TokenKind::Letter {
                            let successor_allowed = if let Some(third) = right3 {
                                Some(singleton(third))
                            } else {
                                let from_map = follower_map
                                    .as_ref()
                                    .and_then(|map| map.get(&right2.letter()).cloned().flatten());
                                // A right3 pin this window could not enumerate — the input is not deep — still names the raw token one past it, which is the successor's right2. Forward it, or a depth-4-decided left leaks follower windows no text can reach and the conform transition gate reports them as dead.
                                match (from_map, &right3_allowed) {
                                    (allowed, None) => allowed.map(Rc::new),
                                    (None, Some(pin)) => Some(Rc::clone(pin)),
                                    (Some(allowed), Some(pin)) => Some(Rc::new(
                                        allowed.intersection(pin.as_ref()).copied().collect(),
                                    )),
                                }
                            };
                            worklist.push(Item {
                                left: LeftContext::letter(settled),
                                rune: right1.letter(),
                                right1: Some(right2),
                                right2_allowed: successor_allowed,
                                right3_allowed: right4.map(singleton),
                            });
                        }
                    }
                }
            }
        }
    }

    let mut rows: Vec<TransitionRow> = transitions
        .into_iter()
        .map(|(key, row)| {
            let [input_glyph, left, right1, right2, right3, right4] = key;
            TransitionRow {
                input_glyph,
                left,
                right1,
                right2,
                right3,
                right4,
                outcome: row.outcome,
                settled: row.settled,
                left_settled: row.left_settled,
                joint: row.joint,
                prospect: row.prospect,
                provenance: row.provenance,
            }
        })
        .collect();
    rows.sort_by(|left, right| left.key().cmp(&right.key()));
    // The product's cells are a `frozenset` on the Python side; collapsing the repeats here rather than at the emitter keeps one cell per seat out of one clone per row.
    let mut counted: HashSet<&CellId> = HashSet::new();
    let mut cells: Vec<CellId> = Vec::new();
    for row in &rows {
        if counted.insert(&row.settled.cell) {
            cells.push(row.settled.cell.clone());
        }
    }
    let cited_provenance = engine
        .fired()
        .iter()
        .map(|pointer| pointer.text(index))
        .collect();
    Ok(FixpointProduct {
        config,
        transitions: rows,
        deep_classes: Vec::new(),
        cited_provenance,
        cells,
    })
}

/// The seeds the fixpoint starts from, `_enumerate`'s doubly nested seed loop: every letter against every boundary left, boundary-major, unpinned. Pushed in this order and popped from the back, which is the traversal the class grain of sub-issue #45 will read.
fn contract_seeds(options: &WindowOptions<'_>) -> Vec<Item> {
    let mut seeds = Vec::with_capacity(SEED_KINDS.len() * options.letters.len());
    for kind in SEED_KINDS {
        for &rune in &options.letters {
            seeds.push(Item {
                left: LeftContext::boundary(kind),
                rune,
                right1: None,
                right2_allowed: None,
                right3_allowed: None,
            });
        }
    }
    seeds
}

/// The option list every right slot starts from: the four boundaries, then the letters in sorted-name order.
fn boundaries_then_letters(options: &WindowOptions<'_>) -> Vec<RightToken> {
    let mut all = Vec::with_capacity(options.right_boundaries.len() + options.right_letters.len());
    all.extend_from_slice(&options.right_boundaries);
    all.extend_from_slice(&options.right_letters);
    all
}

/// The two ligature filters of the right2 pipeline, `_enumerate`'s `liga_formed_before` comprehensions: keep the options the formed `liga` can still stand before, with `slots` naming the two post-formation neighbors each option supplies. A loop rather than a `retain`, because the verdict consults the guard and can fail.
///
/// The deeper slots' pipelines run the same shape inside [`WindowOptions`], and the split is Python's: the second slot's filters are spelled inline in `_enumerate` while the third and fourth slots' live in `_WindowOptions`, because only the deeper two have a second caller in the partition assertion. A filter added to this pipeline therefore belongs here, exactly as it belongs there in Python.
fn retain_formed_before(
    options: &mut WindowOptions<'_>,
    candidates: Vec<RightToken>,
    liga: Sym,
    slots: impl Fn(RightToken) -> (RightToken, Option<RightToken>),
) -> Result<Vec<RightToken>, String> {
    let mut kept = Vec::with_capacity(candidates.len());
    for option in candidates {
        let (next1, next2) = slots(option);
        if options
            .liga_formed_before(liga, next1, next2)
            .map_err(complaint)?
        {
            kept.push(option);
        }
    }
    Ok(kept)
}

/// One token as a pin's allowed-set, Python's `frozenset({token})`.
fn singleton(token: RightToken) -> Allowed {
    Rc::new(BTreeSet::from([token]))
}

/// The ZWNJ chokepoint twin's display name for a raw input glyph, `model.locked_glyph_name`.
fn locked_glyph_name(raw_name: &str) -> String {
    format!("{raw_name}.noentry")
}

/// One right slot's label, `table._right_token_label`: a letter is its rune's name and every boundary its own spelling.
fn right_label(index: &SpecIndex, token: RightToken) -> String {
    match token {
        RightToken::Letter(rune) => index.resolve(rune).to_owned(),
        other => boundary_left_label(other.kind()).to_owned(),
    }
}

/// A deep slot's label, which is [`right_label`] when the window enumerated the slot and [`NA_LABEL`] when it did not.
fn slot_label(index: &SpecIndex, token: Option<RightToken>) -> String {
    token.map_or_else(|| NA_LABEL.to_owned(), |token| right_label(index, token))
}

/// The label a boundary carries at either end of a window, `table.BOUNDARY_LEFT_LABELS`: the run edge's own name, and for the other three the glyph the boundary ships as. A letter or an unknown panics here exactly as the Python mapping raises `KeyError` for it.
fn boundary_left_label(kind: TokenKind) -> &'static str {
    match kind {
        TokenKind::Edge => EDGE_LABEL,
        TokenKind::Space => "space",
        TokenKind::Zwnj => "uni200C",
        TokenKind::NamerDot => "periodcentered",
        other => panic!(
            "{} has no boundary label, exactly as table.BOUNDARY_LEFT_LABELS has no key for it",
            other.as_str()
        ),
    }
}

/// One settlement outcome as the verb's one-line complaint. The two failure families the fixpoint can raise — a window that will not settle, and the partition premise below — are one sentence at this boundary, because exit 1 with the sentence on stderr is the verb's whole answer to either.
fn complaint(error: SettleError) -> String {
    error.to_string()
}

/// `_enumerate`'s `PartitionError` sentence: one window label reached from two different left states, which means `cell_label` has stopped telling those states apart. The window and the two states are spelled in the crate's own idiom rather than Python's `repr` — nothing compares this text, and a `Settled` printed structurally would name its heights by interning id.
fn partition_complaint(
    index: &SpecIndex,
    key: &WindowKey,
    existing: Option<&Settled>,
    arriving: Option<&Settled>,
) -> String {
    format!(
        "window {key:?} reached from two left states sharing one label: {} vs {}",
        left_state_text(index, existing),
        left_state_text(index, arriving)
    )
}

/// One left state as the partition complaint names it: the cell it settled into, the seam it committed, and the connector pixels on that seam.
fn left_state_text(index: &SpecIndex, settled: Option<&Settled>) -> String {
    match settled {
        None => "a boundary left".to_owned(),
        Some(state) => format!(
            "{} (seam {}, extension {})",
            cell_label(index, &state.cell),
            state.seam.map_or("none", |height| index.resolve(height)),
            state.extension
        ),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::index::fixtures;
    use crate::stream::emit_transitions;

    /// The two heights the ordinary fixtures join at.
    const HEIGHTS: &[(&str, &str)] = &[("baseline", "0"), ("x-height", "5")];

    /// The registry the fixpoint fixtures share — `fixtures::four_family_registry` with the height table left open, so the partition check can declare the aliasing pair it needs beside the ordinary heights.
    fn registry(heights: &[(&str, &str)]) -> String {
        fixtures::registry(&[
            ("heights", &fixtures::map(heights)),
            (
                "boundary_tokens",
                &fixtures::map(&[
                    ("space", r#"{"codepoint":32,"splits_runs":true}"#),
                    ("zwnj", r#"{"codepoint":8204,"splits_runs":false}"#),
                ]),
            ),
            (
                "families",
                &fixtures::map(&[
                    ("qsPea", r#"{"codepoint":58960,"sequence":null}"#),
                    ("qsTea", r#"{"codepoint":58962,"sequence":null}"#),
                    ("qsMay", r#"{"codepoint":58981,"sequence":null}"#),
                ]),
            ),
        ])
    }

    /// One stance declaring an entry row and an exit row per named height, which is every surface field these fixtures read.
    fn stance(name: &str, entries: &[&str], exits: &[&str]) -> String {
        fixtures::stance(
            name,
            &[(
                "surface",
                &fixtures::surface(&[("entries", &rows(entries)), ("exits", &rows(exits))]),
            )],
        )
    }

    /// A surface side as the mapping from height name to its row.
    fn rows(heights: &[&str]) -> String {
        let built: Vec<(String, String)> = heights
            .iter()
            .map(|height| ((*height).to_owned(), fixtures::row(height, &[])))
            .collect();
        let entries: Vec<(&str, &str)> = built
            .iter()
            .map(|(height, row)| (height.as_str(), row.as_str()))
            .collect();
        fixtures::map(&entries)
    }

    fn rune(name: &str, stances: &[(&str, String)], extra: &[(&str, &str)]) -> String {
        let entries: Vec<(&str, &str)> = stances
            .iter()
            .map(|(name, body)| (*name, body.as_str()))
            .collect();
        let stances = fixtures::map(&entries);
        let mut fields = vec![("stances", stances.as_str())];
        fields.extend_from_slice(extra);
        fixtures::rune(name, &fields)
    }

    fn spec_of(runes: &[(&str, String)], registry: &str) -> SpecIndex {
        let entries: Vec<(&str, &str)> = runes
            .iter()
            .map(|(name, body)| (*name, body.as_str()))
            .collect();
        fixtures::index_of(&fixtures::dump(&fixtures::map(&entries), registry))
    }

    /// A right condition testing each family in turn, one `then:` hop per name — the shape the deep censuses count hops on.
    fn chain(families: &[&str]) -> String {
        let (head, rest) = families
            .split_first()
            .expect("a chain names at least one slot");
        let family = fixtures::names(&[*head]);
        if rest.is_empty() {
            return fixtures::condition(&[("family", &family)]);
        }
        fixtures::condition(&[("family", &family), ("then", &chain(rest))])
    }

    /// A `prefer` favoring one stance, gated on a right condition and nothing else.
    fn prefer_stance(stance: &str, right: &str) -> String {
        fixtures::record(&[
            ("kind", "\"prefer\""),
            ("stance", &fixtures::quote(stance)),
            ("when", &fixtures::when(&[("right", right)])),
        ])
    }

    /// A `prefer` favoring whichever candidate commits one named exit, gated the same way.
    fn prefer_exit(height: &str, right: &str) -> String {
        let wanted = fixtures::quote(height);
        fixtures::record(&[
            ("kind", "\"prefer\""),
            ("cell", &fixtures::map(&[("exit", wanted.as_str())])),
            ("when", &fixtures::when(&[("right", right)])),
        ])
    }

    fn policy(records: &[&str]) -> String {
        fixtures::policy(&[("prefer", &fixtures::seq(records))])
    }

    fn product(index: &SpecIndex) -> FixpointProduct {
        enumerate_transitions(index, &[]).expect("the fixture's fixpoint closes")
    }

    /// The rows an input reaches at one left, as the four right slots alone.
    fn slots_at(product: &FixpointProduct, input: &str, left: &str) -> Vec<[String; 4]> {
        product
            .transitions
            .iter()
            .filter(|row| row.input_glyph == input && row.left == left)
            .map(|row| {
                [
                    row.right1.clone(),
                    row.right2.clone(),
                    row.right3.clone(),
                    row.right4.clone(),
                ]
            })
            .collect()
    }

    /// The distinct `(input, left)` pairs a product records, in its own row order.
    fn heads(product: &FixpointProduct) -> Vec<(String, String)> {
        let mut pairs: Vec<(String, String)> = Vec::new();
        for row in &product.transitions {
            let pair = (row.input_glyph.clone(), row.left.clone());
            if !pairs.contains(&pair) {
                pairs.push(pair);
            }
        }
        pairs
    }

    /// The single-letter alphabet the closure arithmetic is read against: one stance that neither accepts an entry nor offers an exit, so every window settles into the one cell and the fixpoint reaches exactly one letter left.
    fn lone_letter() -> SpecIndex {
        spec_of(
            &[(
                "qsPea",
                rune("qsPea", &[("half", stance("half", &[], &[]))], &[]),
            )],
            &registry(HEIGHTS),
        )
    }

    /// The three-letter alphabet the deep slots and the pins are read against.
    ///
    /// `qsPea` carries the only deep chain — a `prefer` whose right condition reaches three slots on, so `qsTea qsMay qsPea qsTea` is the one continuation that decides its window — and the two stances it chooses between exit at different heights, which is what makes the choice visible one letter later: `qsTea` accepts an entry at either height, so the left the deep window commits is a different cell for each. `qsMay` accepts a baseline entry and offers no exit, so it ends every chain.
    fn deep_alphabet() -> SpecIndex {
        let pea = rune(
            "qsPea",
            &[
                ("half", stance("half", &[], &["baseline"])),
                ("full", stance("full", &[], &["x-height"])),
            ],
            &[(
                "policy",
                &policy(&[&prefer_stance(
                    "full",
                    &chain(&["qsTea", "qsMay", "qsPea", "qsTea"]),
                )]),
            )],
        );
        let tea = rune(
            "qsTea",
            &[(
                "plain",
                stance("plain", &["baseline", "x-height"], &["baseline"]),
            )],
            &[],
        );
        let may = rune(
            "qsMay",
            &[("plain", stance("plain", &["baseline"], &[]))],
            &[],
        );
        spec_of(
            &[("qsPea", pea), ("qsTea", tea), ("qsMay", may)],
            &registry(HEIGHTS),
        )
    }

    #[test]
    fn a_one_letter_alphabet_closes_over_the_lefts_it_reaches() {
        let index = lone_letter();
        let product = product(&index);
        assert_eq!(
            heads(&product),
            [
                ("qsPea".to_owned(), "#EDGE".to_owned()),
                ("qsPea".to_owned(), "periodcentered".to_owned()),
                ("qsPea".to_owned(), "qsPea.half".to_owned()),
                ("qsPea".to_owned(), "space".to_owned()),
                ("qsPea".to_owned(), "uni200C".to_owned()),
            ],
            "the four boundary lefts the seeds start from, and the one settled letter left they reach"
        );
        // Nine windows at every left: the four boundary right1s, whose second slot is #NA because nothing follows a boundary inside one window, and the letter right1 with its five right2 options.
        assert_eq!(
            slots_at(&product, "qsPea", "#EDGE")
                .iter()
                .map(|slots| slots.join(" "))
                .collect::<Vec<String>>(),
            [
                "#EDGE #NA #NA #NA",
                "periodcentered #NA #NA #NA",
                "qsPea #EDGE #NA #NA",
                "qsPea periodcentered #NA #NA",
                "qsPea qsPea #NA #NA",
                "qsPea space #NA #NA",
                "qsPea uni200C #NA #NA",
                "space #NA #NA #NA",
                "uni200C #NA #NA #NA",
            ]
        );
        assert_eq!(
            product.transitions.len(),
            45,
            "nine windows at each of five lefts"
        );
        // The input never joins, so every window settles into the one cell and the head seats exactly it.
        assert_eq!(product.cells.len(), 1);
        assert!(
            product
                .transitions
                .iter()
                .all(|row| row.outcome == "qsPea.half")
        );
        assert_eq!(product.config, "default");
    }

    #[test]
    fn a_zwnj_left_locks_an_entry_bearing_input_and_leaves_the_rest_bare() {
        let index = spec_of(
            &[
                (
                    "qsPea",
                    rune(
                        "qsPea",
                        &[("half", stance("half", &["baseline"], &[]))],
                        &[],
                    ),
                ),
                (
                    "qsMay",
                    rune("qsMay", &[("plain", stance("plain", &[], &[]))], &[]),
                ),
            ],
            &registry(HEIGHTS),
        );
        assert!(index.is_entry_bearing(fixtures::sym(&index, "qsPea")));
        assert!(!index.is_entry_bearing(fixtures::sym(&index, "qsMay")));
        let product = product(&index);
        let locked: Vec<&str> = product
            .transitions
            .iter()
            .filter(|row| row.left == "uni200C")
            .map(|row| row.input_glyph.as_str())
            .collect();
        assert!(
            locked.contains(&"qsPea.noentry") && locked.contains(&"qsMay"),
            "the chokepoint twin is the entry-bearing input's label alone: {locked:?}"
        );
        assert!(!locked.contains(&"qsPea"));
        // The lock is the row's label and nothing else — the trace settles the raw letter, whose cell is the one the outcome names.
        assert!(
            product
                .transitions
                .iter()
                .filter(|row| row.input_glyph == "qsPea.noentry")
                .all(|row| row.outcome.starts_with("qsPea.half")),
            "the locked twin still settles as qsPea"
        );
        // Nothing else in the product carries the suffix: a ZWNJ at any other slot is an ordinary boundary.
        assert!(
            product
                .transitions
                .iter()
                .all(|row| row.left == "uni200C" || !row.input_glyph.ends_with(".noentry"))
        );
    }

    #[test]
    fn the_deep_slots_split_only_the_windows_the_census_and_both_filters_admit() {
        let index = deep_alphabet();
        let product = product(&index);
        let deep: Vec<&str> = product
            .transitions
            .iter()
            .filter(|row| row.right3 != NA_LABEL)
            .map(|row| row.input_glyph.as_str())
            .collect();
        assert!(
            !deep.is_empty() && deep.iter().all(|input| *input == "qsPea"),
            "the censused input carries a third slot and nothing else does"
        );
        assert!(
            product
                .transitions
                .iter()
                .filter(|row| row.right3 != NA_LABEL)
                .all(|row| row.right1 == "qsTea" && row.right2 == "qsMay"),
            "and only where its chain is still unanswered two slots in"
        );
        let split: Vec<String> = slots_at(&product, "qsPea", "#EDGE")
            .iter()
            .filter(|slots| slots[0] == "qsTea" && slots[1] == "qsMay")
            .map(|slots| format!("{} {}", slots[2], slots[3]))
            .collect();
        assert_eq!(
            split,
            [
                // The third slot's whole option list, and the fourth opening only under the one third token the chain's last hop reads.
                "#EDGE #NA",
                "periodcentered #NA",
                "qsMay #NA",
                "qsPea #EDGE",
                "qsPea periodcentered",
                "qsPea qsMay",
                "qsPea qsPea",
                "qsPea qsTea",
                "qsPea space",
                "qsPea uni200C",
                "qsTea #NA",
                "space #NA",
                "uni200C #NA",
            ]
        );
        // The chain's one full match is what the prefer answers, so the fourth slot is not decorative: it moves the cell the window settles into.
        let outcomes: Vec<(&str, &str)> = product
            .transitions
            .iter()
            .filter(|row| {
                row.input_glyph == "qsPea" && row.left == "#EDGE" && row.right3 == "qsPea"
            })
            .map(|row| (row.right4.as_str(), row.outcome.as_str()))
            .collect();
        assert_eq!(
            outcomes,
            [
                ("#EDGE", "qsPea.half.ex-y0"),
                ("periodcentered", "qsPea.half.ex-y0"),
                ("qsMay", "qsPea.half.ex-y0"),
                ("qsPea", "qsPea.half.ex-y0"),
                ("qsTea", "qsPea.full.ex-y5"),
                ("space", "qsPea.half.ex-y0"),
                ("uni200C", "qsPea.half.ex-y0"),
            ]
        );
    }

    #[test]
    fn a_depth_four_left_pins_the_second_slot_of_the_window_after_its_successor() {
        let index = deep_alphabet();
        let product = product(&index);
        // The left only the fourth slot's one live token reaches: qsPea commits the x-height seam there and nowhere else, so qsTea's entry at that height is the fingerprint of that one continuation.
        assert_eq!(
            slots_at(&product, "qsTea", "qsPea.full.ex-y5")
                .iter()
                .map(|slots| slots.join(" "))
                .collect::<Vec<String>>(),
            ["qsMay qsPea #NA #NA"],
            "the successor's own second slot is pinned to the third lookahead that was enumerated behind it"
        );
        // And the pin the window could not enumerate — qsTea is not deep, so it has no third slot to spend it on — is forwarded onto its own successor's second slot, which is the raw token one past that window.
        assert_eq!(
            slots_at(&product, "qsMay", "qsTea.plain.en-y5.ex-y0")
                .iter()
                .map(|slots| slots.join(" "))
                .collect::<Vec<String>>(),
            ["qsPea qsTea #NA #NA"],
            "without the forward this left would carry every second-slot option, and the extra windows are ones no text can reach"
        );
        // The sibling left is the contrast: reached by plenty of unpinned items, it carries the whole option list behind the same right1.
        let unpinned: Vec<String> = slots_at(&product, "qsMay", "qsTea.plain.en-y0.ex-y0")
            .iter()
            .filter(|slots| slots[0] == "qsPea")
            .map(|slots| slots[1].clone())
            .collect();
        assert_eq!(
            unpinned,
            [
                "#EDGE",
                "periodcentered",
                "qsMay",
                "qsPea",
                "qsTea",
                "space",
                "uni200C"
            ]
        );
    }

    /// The seeds in the exact reverse of the contract order — the deepest permutation available, since it pops last what the shipping order pops first.
    fn reversed_seeds(options: &WindowOptions<'_>) -> Vec<Item> {
        let mut seeds = contract_seeds(options);
        seeds.reverse();
        seeds
    }

    #[test]
    fn a_permuted_seed_order_reaches_the_same_pinned_world_product() {
        let index = deep_alphabet();
        let contract = enumerate_seeded(&index, &[], contract_seeds).expect("the fixpoint closes");
        let reversed = enumerate_seeded(&index, &[], reversed_seeds).expect("the fixpoint closes");
        // Compared as the stream rather than as the product, because two of the product's fields are sets whose vector spelling is the emitter's business: `cited_provenance` comes out of a hash set and has no order of its own.
        assert_eq!(
            emit_transitions(&index, &contract),
            emit_transitions(&index, &reversed)
        );
        assert!(
            contract
                .transitions
                .iter()
                .any(|row| row.right4 != NA_LABEL),
            "and the product both orders reached is the one carrying the pinned deep windows, not a trivially equal pair"
        );
        // Order-independence here is a fact about this world, not about the discipline: the dedup is by window key, a re-reached window reuses the settled a re-trace would return, and the fired set is a union over a window set no traversal can change. Under class grain (sub-issue #45) the first visitor of a fibre fixes its representative, and the push order becomes output-visible.
    }

    #[test]
    fn one_window_label_reached_from_two_left_states_stops_the_build() {
        // Two heights at one y is what makes `cell_label` non-injective, and the prefer picks between them under the one continuation its chain reads — so the deep window commits a cell that labels exactly like its siblings' and compares unequal to them.
        let pea = rune(
            "qsPea",
            &[("half", stance("half", &[], &["baseline", "floor"]))],
            &[(
                "policy",
                &policy(&[&prefer_exit(
                    "floor",
                    &chain(&["qsTea", "qsMay", "qsPea", "qsTea"]),
                )]),
            )],
        );
        let tea = rune(
            "qsTea",
            &[(
                "plain",
                stance("plain", &["baseline", "floor"], &["baseline"]),
            )],
            &[],
        );
        let may = rune(
            "qsMay",
            &[("plain", stance("plain", &["baseline"], &[]))],
            &[],
        );
        let index = spec_of(
            &[("qsPea", pea), ("qsTea", tea), ("qsMay", may)],
            &registry(&[("baseline", "0"), ("floor", "0"), ("x-height", "5")]),
        );
        let complaint = enumerate_transitions(&index, &[]).expect_err("the labels collide");
        assert!(
            complaint.starts_with(
                "window [\"qsTea\", \"qsPea.half.ex-y0\", \"qsMay\", \"qsPea\", \"#NA\", \"#NA\"] reached from two left states sharing one label: "
            ),
            "{complaint}"
        );
        assert!(
            complaint.contains("qsPea.half.ex-y0 (seam floor, extension 0)")
                && complaint.contains("qsPea.half.ex-y0 (seam baseline, extension 0)"),
            "{complaint}"
        );
    }
}
