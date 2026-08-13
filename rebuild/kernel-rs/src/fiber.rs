//! The issue-26 fiber source, `rebuild/pipeline/table.py`'s `_Fiber`, `_ContextFibers` and `_DeepFiberDeriver`: per live context `(input family, right1, right2)`, the static third-slot option list's letters partitioned into fibers of an outcome-probe function, derived lazily on first reach and memoized per build.
//!
//! A fiber is an equivalence class of third tokens that the enumeration may collapse into one row. The key per candidate letter `t3` has three components. First the probe function itself: for every left class in [`ProspectLiveness::seat_left_classes`] and every bounded coordinate, the full row-visible record — the settled triple, the prospect, the joint-floor flag and the notes — with the three raise identities kept as three distinct values (E-INCOMPARABLE, E-AMBIGUOUS, and everything else; [`crate::error::SettleError`] carries that split for exactly this reason, and collapsing any two would silently merge fibers the review surface and the treaty fold read apart). Second the `fourth_slot_matters` verdict itself. Third, for members whose verdict is true, the *computed* r4 option list, run through [`WindowOptions::right4_options`] per member — structurally, so a filter added to that pipeline without a key update fails [`crate::fixpoint`]'s partition assertion loudly instead of silently splitting a fiber.
//!
//! The coordinate set is bounded rather than the full grid: `(EDGE, UNKNOWN)` where the fourth slot is dead — an r4-dead member is traced only at EDGE and enqueues no r4 pin, so deeper coordinates are unread for it — widening to the whole probe alphabet with `UNKNOWN` appended after it exactly where `fourth_slot_matters` is true, which is where a seat can move under a specific `(third, fourth)` pair and is what absorbs the joint34 counterexample at fiber grain. The append order is load-bearing twice: the r4 grouping indexes the probe matrix by a token's position in that coordinate list, so `UNKNOWN` must sit past every probe token rather than among them.
//!
//! Components two and three are what make an r3 class induce one shared r4 sub-enumeration: its `t4` groups under the probe function restricted to the option list are the r4 fibers, and because the probe function is indexed by `t3` the r4 partition is per `(context, r3 class)` and never per context alone. Grouping runs in option-pipeline order — a boundary is its own singleton where it stands, and letters group by their column of the probe matrix with each bucket taking the seat of its first member.
//!
//! The verdict the deriver asks for is the raw filter verdict, `fourth_slot_matters(family, right1, right2, t3)`, and deliberately not that ANDed with the depth-4 census: the fixpoint applies `rune_name in deep4_inputs` separately when it decides whether a fiber's r4 groups become slot-4 entries, and the partition assertion replays the same distinction. Under the deep world the census is every rune and the AND is invisible; the pinned world's assertions still read it.
//!
//! The probes run on the build's own tracing engine, so their traces land in the shared memo and their fired pointers in `Engine::fired`, exactly as the liveness probes' traces already do. The one imported rather than probed assumption is the left-class collapse [`ProspectLiveness::seat_left_classes`] already trusts; the fixpoint's per-build echo check is the standing guard on it at real-left, real-entry, real-adjustment grain.

use std::collections::HashMap;
use std::rc::Rc;

use crate::census::FourthSlotFilter;
use crate::engine::{Engine, Slots};
use crate::error::{SettleError, SettleErrorKind};
use crate::liveness::ProspectLiveness;
use crate::model::Sym;
use crate::options::WindowOptions;
use crate::types::{EDGE, LeftContext, RightToken, Settled, TokenKind, UNKNOWN};

/// The coordinates an r4-dead member is probed at, `_DeepFiberDeriver.context`'s `coords = (EDGE, UNKNOWN)`. Such a member is traced only at `EDGE` by the enumeration and enqueues no r4 pin, so no deeper coordinate is ever read for it and probing the whole alphabet there would key the fiber on windows nothing consults.
const DEAD_FOURTH_COORDS: [RightToken; 2] = [EDGE, UNKNOWN];

/// One probed window's row-visible record, `_DeepFiberDeriver._record`. A settled window carries everything a row reports — the settled triple, the prospect, the joint-floor flag and the notes — and the three raise identities stay three distinct values, because a fiber that merged an E-INCOMPARABLE window with an unreachable one would collapse two outcomes the review surface and the treaty fold read apart.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
enum FiberRecord {
    Settled {
        settled: Settled,
        prospect: i64,
        joint_floor: bool,
        notes: Vec<String>,
    },
    Incomparable,
    Ambiguous,
    Unreachable,
}

/// One candidate third token's whole fiber key: the `fourth_slot_matters` verdict, the computed r4 option list where that verdict is true, and the probe matrix itself, one row per left class and one column per bounded coordinate.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
struct FiberKey {
    fourth_matters: bool,
    options4: Vec<RightToken>,
    probe: Vec<Vec<FiberRecord>>,
}

/// One r3 letter fiber of a live context, `table._Fiber`: the member tokens, the member-uniform `fourth_slot_matters` verdict, and — only where that verdict is true — the shared r4 sub-enumeration.
///
/// `members` arrives in sorted-letter order, which is the order the static option list already has, so the first member is the deterministic representative. `r4_groups` is the computed r4 option list partitioned into boundary singletons and r4 letter fibers, in option-pipeline order; a dead fourth carries none at all.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Fiber {
    pub members: Vec<RightToken>,
    pub fourth_matters: bool,
    pub r4_groups: Vec<Vec<RightToken>>,
}

/// One live context's whole third-slot partition, `table._ContextFibers`: the static option list's boundaries in their own order, and its letters as fibers in first-member-encountered order.
///
/// The boundaries are carried rather than re-derived because the enumeration walks them ahead of the fibers and pins them exactly as it pins a fiber's members, and because a boundary third slot is a class of one by definition — nothing about the outcome probe would ever merge two of them.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ContextFibers {
    pub boundary_options: Vec<RightToken>,
    pub fibers: Vec<Fiber>,
}

/// The per-build fiber deriver, `table._DeepFiberDeriver`. Everything it needs beyond its own memo arrives per call — the engine, the liveness probe, the fourth-slot filter and the option pipelines are all the fixpoint's, lent for the derivation, because a second copy of any of them would fork a memo the product reports through.
#[derive(Debug, Default)]
pub struct DeepFiberDeriver {
    contexts: HashMap<(Sym, Sym, Sym), Rc<ContextFibers>>,
}

impl DeepFiberDeriver {
    /// A deriver with an empty memo.
    pub fn new() -> Self {
        Self::default()
    }

    /// This context's fiber partition, derived on first reach and memoized after, `_DeepFiberDeriver.context`.
    ///
    /// The static option list is [`WindowOptions::right3_options`] over the follower map [`WindowOptions::context_follower_map`] hands back for `(family, right1)` — the same computation the enumeration runs, not a restatement of it. Its non-letter entries become [`ContextFibers::boundary_options`] untouched; its letters are probed and grouped.
    ///
    /// A context is only ever asked for where the third slot is live, but the deriver does not check that and answers whatever it is asked, exactly as Python's does: the caller that knows the verdict is the caller that has already computed it.
    ///
    /// The fourth-slot filter is lent the liveness probe on the same terms `fourth_slot_filter` builds one on — where the engine's own modes make a deep world, and nowhere else. That is not a second opinion about the world but the very closure Python's `enumerate_transitions` hands its deriver, which is the one the enumeration itself asks; a deriver only ever runs in a deep world anyway, so the two spellings can only differ where nothing calls either.
    #[allow(clippy::too_many_arguments)]
    pub fn context(
        &mut self,
        engine: &mut Engine<'_>,
        liveness: &mut ProspectLiveness<'_>,
        fourth: &mut FourthSlotFilter<'_>,
        options: &mut WindowOptions<'_>,
        family: Sym,
        right1: Sym,
        right2: Sym,
    ) -> Result<Rc<ContextFibers>, SettleError> {
        if let Some(cached) = self.contexts.get(&(family, right1, right2)) {
            return Ok(Rc::clone(cached));
        }
        let token = RightToken::Letter(family);
        let r1tok = RightToken::Letter(right1);
        let r2tok = RightToken::Letter(right2);
        let follower_map = options.context_follower_map(family, right1);
        let static_options = options.right3_options(r1tok, r2tok, follower_map.as_deref())?;
        let boundary_options: Vec<RightToken> = static_options
            .iter()
            .copied()
            .filter(|option| option.kind() != TokenKind::Letter)
            .collect();
        let lefts = liveness.seat_left_classes(engine, family)?;
        let mut full_coords: Vec<RightToken> = liveness.probe_tokens().as_ref().clone();
        full_coords.push(UNKNOWN);
        let deep_world = engine.simulated_prospect() || engine.vote_slots();

        let mut seats: HashMap<Rc<FiberKey>, usize> = HashMap::new();
        let mut grouped: Vec<(Rc<FiberKey>, Vec<RightToken>)> = Vec::new();
        for third in static_options {
            if third.kind() != TokenKind::Letter {
                continue;
            }
            let fourth_matters = fourth.matters(
                engine,
                deep_world.then_some(&mut *liveness),
                family,
                right1,
                right2,
                third.letter(),
            )?;
            let (coords, options4): (&[RightToken], Vec<RightToken>) = if fourth_matters {
                (
                    full_coords.as_slice(),
                    options.right4_options(r1tok, r2tok, third)?,
                )
            } else {
                (DEAD_FOURTH_COORDS.as_slice(), Vec::new())
            };
            let mut probe: Vec<Vec<FiberRecord>> = Vec::with_capacity(lefts.len());
            for left in lefts.iter() {
                probe.push(
                    coords
                        .iter()
                        .map(|&coord| {
                            record(engine, left, token, Slots::new(r1tok, r2tok, third, coord))
                        })
                        .collect(),
                );
            }
            let key = Rc::new(FiberKey {
                fourth_matters,
                options4,
                probe,
            });
            match seats.get(&key) {
                Some(&seat) => grouped[seat].1.push(third),
                None => {
                    seats.insert(Rc::clone(&key), grouped.len());
                    grouped.push((key, vec![third]));
                }
            }
        }

        let fibers: Vec<Fiber> = grouped
            .into_iter()
            .map(|(key, members)| Fiber {
                members,
                fourth_matters: key.fourth_matters,
                r4_groups: if key.fourth_matters {
                    r4_groups(&key, &full_coords)
                } else {
                    Vec::new()
                },
            })
            .collect();
        let context = Rc::new(ContextFibers {
            boundary_options,
            fibers,
        });
        self.contexts
            .insert((family, right1, right2), Rc::clone(&context));
        Ok(context)
    }
}

/// One probed window's record, `_DeepFiberDeriver._record` — the trace where the window settles, and one of the three raise identities where it does not.
fn record(
    engine: &mut Engine<'_>,
    left: &LeftContext,
    token: RightToken,
    slots: Slots,
) -> FiberRecord {
    match engine.transition_trace(left, token, slots) {
        Ok(trace) => FiberRecord::Settled {
            settled: trace.settled,
            prospect: trace.prospect,
            joint_floor: trace.joint_floor,
            notes: trace.notes,
        },
        Err(error) => match error.kind() {
            SettleErrorKind::Incomparable => FiberRecord::Incomparable,
            SettleErrorKind::Ambiguous => FiberRecord::Ambiguous,
            SettleErrorKind::Stranded | SettleErrorKind::Plain => FiberRecord::Unreachable,
        },
    }
}

/// One r3 fiber's r4 sub-enumeration: its computed option list partitioned in pipeline order, a boundary standing as its own singleton where it sits and letters grouped by their column of the probe matrix, each bucket seated where its first member fell.
///
/// The column of a letter is its records across every left class, read at the coordinate its own position in the probe alphabet names — which is why `UNKNOWN` is appended after that alphabet rather than mixed into it. The column is borrowed out of the matrix rather than copied out of it: a bucket is only ever compared against another bucket of the same matrix, so borrowing says what the grouping means and spares a clone per option per left class.
fn r4_groups(key: &FiberKey, full_coords: &[RightToken]) -> Vec<Vec<RightToken>> {
    let coord_index: HashMap<RightToken, usize> = full_coords
        .iter()
        .enumerate()
        .map(|(seat, coord)| (*coord, seat))
        .collect();
    let mut ordered: Vec<Vec<RightToken>> = Vec::new();
    let mut by_column: HashMap<Vec<&FiberRecord>, usize> = HashMap::new();
    for &option in &key.options4 {
        if option.kind() != TokenKind::Letter {
            ordered.push(vec![option]);
            continue;
        }
        let seat = coord_index[&option];
        let column: Vec<&FiberRecord> = key.probe.iter().map(|row| &row[seat]).collect();
        match by_column.get(&column) {
            Some(&bucket) => ordered[bucket].push(option),
            None => {
                by_column.insert(column, ordered.len());
                ordered.push(vec![option]);
            }
        }
    }
    ordered
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::engine::EngineModes;
    use crate::fixpoint::right_token_label;
    use crate::index::{SpecIndex, fixtures};
    use crate::types::{CellId, LeftContext, Settled, TokenKind};

    fn object(entries: &[(String, String)]) -> String {
        let pairs: Vec<String> = entries
            .iter()
            .map(|(key, value)| format!("\"{key}\":{value}"))
            .collect();
        format!("{{{}}}", pairs.join(","))
    }

    fn row(height: &str, overrides: &[(&str, &str)]) -> (String, String) {
        (height.to_owned(), fixtures::row(height, overrides))
    }

    fn safe(height: &str) -> (String, String) {
        row(height, &[("withdrawal", "\"safe\"")])
    }

    fn surface(entries: &str, exits: &str, extra: &[(&str, &str)]) -> String {
        let mut fields = vec![("entries", entries), ("exits", exits)];
        fields.extend_from_slice(extra);
        fixtures::surface(&fields)
    }

    fn stance(name: &str, surface: &str) -> (String, String) {
        (
            name.to_owned(),
            fixtures::stance(name, &[("surface", surface)]),
        )
    }

    fn letter(name: &str, stances: &[(String, String)], policy: &str) -> (String, String) {
        let stances = object(stances);
        (
            name.to_owned(),
            fixtures::rune(name, &[("stances", stances.as_str()), ("policy", policy)]),
        )
    }

    fn plain_policy() -> String {
        fixtures::policy(&[])
    }

    fn prefer(rune: &str, seat: usize, overrides: &[(&str, &str)]) -> String {
        let pointer =
            fixtures::names(&[&format!("{rune}.yaml"), &format!("policy.prefer[{seat}]")]);
        let mut fields: Vec<(&str, &str)> =
            vec![("kind", "\"prefer\""), ("provenance", pointer.as_str())];
        fields.extend_from_slice(overrides);
        fixtures::record(&fields)
    }

    fn chain(families: &[&str]) -> String {
        let (head, rest) = families.split_first().expect("a chain names a slot");
        let family = fixtures::names(&[*head]);
        if rest.is_empty() {
            return fixtures::condition(&[("family", &family)]);
        }
        fixtures::condition(&[("family", &family), ("then", &chain(rest))])
    }

    fn three_height_registry() -> String {
        fixtures::registry(&[
            (
                "heights",
                &fixtures::map(&[("baseline", "0"), ("x-height", "5"), ("cap", "9")]),
            ),
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
                    ("qsIt", r#"{"codepoint":58992,"sequence":null}"#),
                ]),
            ),
        ])
    }

    fn tall_spec_of(runes: &[(String, String)]) -> SpecIndex {
        fixtures::index_of(&fixtures::dump(&object(runes), &three_height_registry()))
    }

    fn spec_of(runes: &[(String, String)]) -> SpecIndex {
        fixtures::index_of(&fixtures::dump(
            &object(runes),
            &fixtures::four_family_registry(),
        ))
    }

    fn engine_in(index: &SpecIndex) -> Engine<'_> {
        Engine::with_modes(
            index,
            Vec::<crate::model::Sym>::new(),
            EngineModes {
                trace_memo: true,
                ..EngineModes::default()
            },
        )
    }

    /// The `belt_spec` of [`crate::liveness`]'s tests, whose one context has a live fourth slot at exactly one third token.
    fn belt_spec() -> SpecIndex {
        let pea = letter(
            "qsPea",
            &[stance(
                "stroke",
                &surface(
                    &object(&[row("baseline", &[])]),
                    &object(&[safe("cap"), safe("baseline")]),
                    &[],
                ),
            )],
            &fixtures::policy(&[(
                "prefer",
                &fixtures::seq(&[&prefer(
                    "qsPea",
                    0,
                    &[
                        ("cell", &fixtures::map(&[("exit", "\"cap\"")])),
                        ("over", &fixtures::map(&[("exit", "\"baseline\"")])),
                    ],
                )]),
            )]),
        );
        let tea = letter(
            "qsTea",
            &[
                stance(
                    "hook",
                    &surface(
                        &object(&[row("cap", &[])]),
                        &object(&[safe("baseline")]),
                        &[],
                    ),
                ),
                stance(
                    "flat",
                    &surface(
                        &object(&[row("baseline", &[])]),
                        &object(&[safe("baseline")]),
                        &[],
                    ),
                ),
            ],
            &fixtures::policy(&[(
                "prefer",
                &fixtures::seq(&[&prefer(
                    "qsTea",
                    0,
                    &[
                        (
                            "cell",
                            &fixtures::map(&[("entry", "\"cap\""), ("exit", "\"none\"")]),
                        ),
                        (
                            "over",
                            &fixtures::map(&[("entry", "\"cap\""), ("exit", "\"baseline\"")]),
                        ),
                    ],
                )]),
            )]),
        );
        let may = letter(
            "qsMay",
            &[
                stance(
                    "capped",
                    &surface(
                        &object(&[row("baseline", &[])]),
                        "{}",
                        &[("require", &fixtures::names(&["entry"]))],
                    ),
                ),
                stance("free", &surface("{}", &object(&[safe("x-height")]), &[])),
            ],
            &plain_policy(),
        );
        let it = letter(
            "qsIt",
            &[stance(
                "hook",
                &surface(
                    &object(&[row("x-height", &[])]),
                    &object(&[safe("baseline")]),
                    &[("require", &fixtures::names(&["exit"]))],
                ),
            )],
            &plain_policy(),
        );
        tall_spec_of(&[pea, tea, may, it])
    }

    /// `qsMay` at the follower's own second slot, anything at its third, and the caller's condition at its fourth. The two reaches below differ in that last hop alone, which is what makes the pair a controlled experiment on the r4 grouping.
    fn reach_past_may(fourth: &str) -> String {
        fixtures::condition(&[
            ("family", &fixtures::names(&["qsMay"])),
            ("then", &fixtures::condition(&[("then", fourth)])),
        ])
    }

    /// The reach whose fourth hop asks only for a boundary: `EDGE` answers yes, `UNKNOWN` answers optimistically, and every letter answers no, so the whole r4 letter alphabet reads alike and shares one column.
    fn any_boundary_fourth() -> String {
        reach_past_may(&fixtures::condition(&[("is_token", "\"boundary\"")]))
    }

    /// The same reach naming one letter at that hop instead, which is the single change that splits the column the r4 letters shared.
    fn one_named_fourth() -> String {
        reach_past_may(&fixtures::condition(&[(
            "family",
            &fixtures::names(&["qsIt"]),
        )]))
    }

    /// The fixture whose fourth slot is live at every third: `qsTea`'s absolute prefer yields its exit on what stands at the seat's fourth slot and reads nothing at the third, so every letter third looks alike to the probe and the whole alphabet lands in one fiber — while the r4 grouping still has to partition the option list, which is the matrix column doing the work alone.
    fn live_fourth_spec(reach: &str) -> SpecIndex {
        let pea = letter(
            "qsPea",
            &[stance(
                "stroke",
                &surface("{}", &object(&[safe("x-height"), safe("baseline")]), &[]),
            )],
            &fixtures::policy(&[(
                "prefer",
                &fixtures::seq(&[&prefer(
                    "qsPea",
                    0,
                    &[
                        ("cell", &fixtures::map(&[("exit", "\"x-height\"")])),
                        ("over", &fixtures::map(&[("exit", "\"baseline\"")])),
                    ],
                )]),
            )]),
        );
        let tea = letter(
            "qsTea",
            &[stance(
                "hook",
                &surface(
                    &object(&[row("x-height", &[]), row("baseline", &[])]),
                    &object(&[safe("baseline")]),
                    &[(
                        "pairings",
                        r#"{"never":[{"entry":"x-height","exit":"baseline"}],"only":null}"#,
                    )],
                ),
            )],
            &fixtures::policy(&[(
                "prefer",
                &fixtures::seq(&[&prefer(
                    "qsTea",
                    0,
                    &[
                        ("when", &fixtures::when(&[("right", reach)])),
                        ("mode", "\"absolute\""),
                        ("cell", &fixtures::map(&[("exit", "\"none\"")])),
                        ("over", &fixtures::map(&[("exit", "\"baseline\"")])),
                    ],
                )]),
            )]),
        );
        let acceptor = |name: &str| {
            letter(
                name,
                &[stance(
                    "base",
                    &surface(&object(&[row("baseline", &[])]), "{}", &[]),
                )],
                &plain_policy(),
            )
        };
        spec_of(&[pea, tea, acceptor("qsMay"), acceptor("qsIt")])
    }

    /// One context's partition spelled the way the `liveness-cases` verb spells it — every token through [`right_token_label`], which is `table._right_token_label` and therefore the vocabulary the differential compares in, so a golden here reads as the answer a sweep would.
    fn spelled(index: &SpecIndex, context: &ContextFibers) -> String {
        let names = |tokens: &[RightToken]| -> Vec<String> {
            tokens
                .iter()
                .map(|token| right_token_label(index, *token))
                .collect()
        };
        let fibers: Vec<String> = context
            .fibers
            .iter()
            .map(|fiber| {
                let groups: Vec<String> = fiber
                    .r4_groups
                    .iter()
                    .map(|group| names(group).join("+"))
                    .collect();
                format!(
                    "{} fourth {} r4 [{}]",
                    names(&fiber.members).join("+"),
                    fiber.fourth_matters,
                    groups.join(", ")
                )
            })
            .collect();
        format!(
            "boundaries [{}] fibers [{}]",
            names(&context.boundary_options).join(", "),
            fibers.join("; ")
        )
    }

    /// The two record-vs-record raises, told apart by the third slot: `qsPea` demands its two stances at once where the slots past the seat spell `qsMay·qsIt`, which is E-AMBIGUOUS inside one rune, and crosses `qsTea`'s vote where they spell `qsMay·qsMay`, which is E-INCOMPARABLE across two. Every other third token settles.
    fn raising_spec() -> SpecIndex {
        let pea = letter(
            "qsPea",
            &[
                stance("stroke", &surface("{}", &object(&[safe("x-height")]), &[])),
                stance("flourish", &surface("{}", "{}", &[])),
            ],
            &fixtures::policy(&[(
                "prefer",
                &fixtures::seq(&[
                    &prefer(
                        "qsPea",
                        0,
                        &[
                            (
                                "when",
                                &fixtures::when(&[("right", &chain(&["qsTea", "qsMay", "qsIt"]))]),
                            ),
                            ("stance", "\"stroke\""),
                        ],
                    ),
                    &prefer(
                        "qsPea",
                        1,
                        &[
                            (
                                "when",
                                &fixtures::when(&[("right", &chain(&["qsTea", "qsMay", "qsIt"]))]),
                            ),
                            ("stance", "\"flourish\""),
                        ],
                    ),
                    &prefer(
                        "qsPea",
                        2,
                        &[
                            (
                                "when",
                                &fixtures::when(&[("right", &chain(&["qsTea", "qsMay", "qsMay"]))]),
                            ),
                            ("cell", &fixtures::map(&[("exit", "\"x-height\"")])),
                        ],
                    ),
                ]),
            )]),
        );
        let tea = letter(
            "qsTea",
            &[stance(
                "hook",
                &surface(
                    &object(&[row("x-height", &[])]),
                    &object(&[safe("baseline")]),
                    &[(
                        "pairings",
                        r#"{"never":[{"entry":"x-height","exit":"baseline"}],"only":null}"#,
                    )],
                ),
            )],
            &fixtures::policy(&[(
                "prefer",
                &fixtures::seq(&[&prefer(
                    "qsTea",
                    0,
                    &[
                        (
                            "when",
                            &fixtures::when(&[("right", &chain(&["qsMay", "qsMay"]))]),
                        ),
                        ("cell", &fixtures::map(&[("exit", "\"baseline\"")])),
                    ],
                )]),
            )]),
        );
        let may = letter(
            "qsMay",
            &[stance(
                "base",
                &surface(&object(&[row("baseline", &[])]), "{}", &[]),
            )],
            &plain_policy(),
        );
        let it = letter(
            "qsIt",
            &[stance(
                "base",
                &surface(&object(&[row("baseline", &[])]), "{}", &[]),
            )],
            &plain_policy(),
        );
        spec_of(&[pea, tea, may, it])
    }

    /// The scaffolding a context is derived through, all of it the one instance the fixpoint lends.
    struct Scaffolding<'i> {
        engine: Engine<'i>,
        liveness: ProspectLiveness<'i>,
        fourth: FourthSlotFilter<'i>,
        options: WindowOptions<'i>,
        deriver: DeepFiberDeriver,
    }

    impl<'i> Scaffolding<'i> {
        fn new(index: &'i SpecIndex) -> Self {
            Self {
                engine: engine_in(index),
                liveness: ProspectLiveness::new(index),
                fourth: FourthSlotFilter::new(index),
                options: WindowOptions::new(index).expect("the fixture has a guard"),
                deriver: DeepFiberDeriver::new(),
            }
        }

        fn context(&mut self, index: &SpecIndex, names: [&str; 3]) -> Rc<ContextFibers> {
            let [family, right1, right2] = names.map(|name| fixtures::sym(index, name));
            self.deriver
                .context(
                    &mut self.engine,
                    &mut self.liveness,
                    &mut self.fourth,
                    &mut self.options,
                    family,
                    right1,
                    right2,
                )
                .expect("the fixture settles")
        }
    }

    /// The whole partition of the joint34 context: the static list's boundaries carried in their own order, its letters split by the outcome probe, and the one fiber whose fourth slot is live carrying the r4 sub-enumeration its members share.
    ///
    /// The r4 groups are the shape the option pipeline hands back — every boundary a singleton where it stands, then the letters grouped by their column of the probe matrix, `qsIt` apart from the three that read alike.
    #[test]
    fn a_context_partitions_its_thirds_and_hands_each_fiber_its_r4_groups() {
        let index = belt_spec();
        let mut scaffolding = Scaffolding::new(&index);
        let context = scaffolding.context(&index, ["qsPea", "qsTea", "qsMay"]);
        assert_eq!(
            spelled(&index, &context),
            concat!(
                "boundaries [#EDGE, space, uni200C, periodcentered] ",
                "fibers [",
                "qsIt fourth true r4 [#EDGE, space, uni200C, periodcentered, qsIt, qsMay+qsPea+qsTea]; ",
                "qsMay+qsPea+qsTea fourth false r4 []",
                "]"
            )
        );
        let fibers = &context.fibers;
        assert_eq!(
            fibers[1].r4_groups,
            Vec::<Vec<RightToken>>::new(),
            "a dead fourth slot emits no groups at all rather than one group of everything"
        );
        assert!(
            fibers[0].members.len() == 1 && fibers[1].members.len() == 3,
            "the one third whose fourth slot moves the seat is a class of its own, and the three that never do share one"
        );
    }

    /// Two asks for one context hand back the same derivation rather than probing twice, which is what keeps the fired journal a build reports the same warm as cold.
    #[test]
    fn a_context_is_derived_once_and_handed_out_by_reference() {
        let index = belt_spec();
        let mut scaffolding = Scaffolding::new(&index);
        let first = scaffolding.context(&index, ["qsPea", "qsTea", "qsMay"]);
        let second = scaffolding.context(&index, ["qsPea", "qsTea", "qsMay"]);
        assert!(Rc::ptr_eq(&first, &second));
    }

    /// The r4 letters group by their column of the probe matrix and by nothing else. Change one hop of the follower's reach — a boundary fourth for a named letter — and the letter it names leaves the bucket the rest shared, while the boundaries stay singletons where they stand in the option list and the r3 partition above them does not move at all.
    #[test]
    fn one_hop_of_the_reach_splits_the_r4_column_the_letters_shared() {
        let shared = live_fourth_spec(&any_boundary_fourth());
        let mut scaffolding = Scaffolding::new(&shared);
        let context = scaffolding.context(&shared, ["qsPea", "qsTea", "qsMay"]);
        assert_eq!(
            spelled(&shared, &context),
            concat!(
                "boundaries [#EDGE, space, uni200C, periodcentered] ",
                "fibers [",
                "qsIt+qsMay+qsPea+qsTea fourth true ",
                "r4 [#EDGE, space, uni200C, periodcentered, qsIt+qsMay+qsPea+qsTea]",
                "]"
            )
        );

        let split = live_fourth_spec(&one_named_fourth());
        let mut scaffolding = Scaffolding::new(&split);
        let context = scaffolding.context(&split, ["qsPea", "qsTea", "qsMay"]);
        assert_eq!(
            spelled(&split, &context),
            concat!(
                "boundaries [#EDGE, space, uni200C, periodcentered] ",
                "fibers [",
                "qsIt+qsMay+qsPea+qsTea fourth true ",
                "r4 [#EDGE, space, uni200C, periodcentered, qsIt, qsMay+qsPea+qsTea]",
                "]"
            )
        );
    }

    /// An r4-dead member is traced at the two coordinates the enumeration will ever read it at and at no others, while a live one is traced across the whole alphabet. Widening the dead member's sweep is invisible at verdict grain — the fiber key would still partition the same way — but every one of those windows journals its fired pointers into the product's `cited_provenance`, so the coordinate list is output rather than an optimization.
    #[test]
    fn a_dead_fourth_member_is_probed_at_two_coordinates_and_a_live_one_at_the_alphabet() {
        let index = belt_spec();
        let mut scaffolding = Scaffolding::new(&index);
        let context = scaffolding.context(&index, ["qsPea", "qsTea", "qsMay"]);
        let [pea, tea, may] = ["qsPea", "qsTea", "qsMay"].map(|name| fixtures::sym(&index, name));
        let dead = context
            .fibers
            .iter()
            .find(|fiber| !fiber.fourth_matters)
            .expect("three of the four thirds cannot reach a live fourth here")
            .members[0];
        let live = context
            .fibers
            .iter()
            .find(|fiber| fiber.fourth_matters)
            .expect("one third can")
            .members[0];

        let edge_left = LeftContext::boundary(TokenKind::Edge);
        let traced = |third: RightToken, coord: RightToken| {
            scaffolding
                .engine
                .trace_delta(
                    &edge_left,
                    RightToken::Letter(pea),
                    Slots::new(
                        RightToken::Letter(tea),
                        RightToken::Letter(may),
                        third,
                        coord,
                    ),
                )
                .is_some()
        };
        assert!(traced(dead, EDGE) && traced(dead, UNKNOWN));
        assert!(
            !traced(dead, live),
            "a letter coordinate past a dead fourth slot is a window the enumeration never reads, so the deriver never traces it"
        );
        assert!(
            traced(live, live) && traced(live, UNKNOWN),
            "the live member's own sweep is the whole probe alphabet with UNKNOWN past the end of it"
        );
    }

    /// The three raise identities are three values, not one: a third token whose seat raises E-INCOMPARABLE and one whose seat raises E-AMBIGUOUS land in two fibers, and the tokens that settle land in a third.
    #[test]
    fn a_context_splits_the_thirds_its_two_raises_tell_apart() {
        let index = raising_spec();
        let mut scaffolding = Scaffolding::new(&index);
        let kinds: Vec<Result<(), SettleErrorKind>> = ["qsPea", "qsTea", "qsMay", "qsIt"]
            .into_iter()
            .map(|third| {
                scaffolding
                    .engine
                    .transition_trace(
                        &LeftContext::boundary(TokenKind::Edge),
                        RightToken::Letter(fixtures::sym(&index, "qsPea")),
                        Slots::new(
                            RightToken::Letter(fixtures::sym(&index, "qsTea")),
                            RightToken::Letter(fixtures::sym(&index, "qsMay")),
                            RightToken::Letter(fixtures::sym(&index, third)),
                            EDGE,
                        ),
                    )
                    .map(|_| ())
                    .map_err(|error| error.kind())
            })
            .collect();
        assert_eq!(
            kinds,
            [
                Ok(()),
                Ok(()),
                Err(SettleErrorKind::Incomparable),
                Err(SettleErrorKind::Ambiguous)
            ],
            "the fixture's whole point is one third token per raise"
        );
        let context = scaffolding.context(&index, ["qsPea", "qsTea", "qsMay"]);
        assert_eq!(
            spelled(&index, &context),
            concat!(
                "boundaries [#EDGE, space, uni200C, periodcentered] ",
                "fibers [",
                "qsIt fourth false r4 []; ",
                "qsMay fourth false r4 []; ",
                "qsPea+qsTea fourth false r4 []",
                "]"
            ),
            "collapsing the two raises into one value would merge qsIt with qsMay"
        );
    }

    /// The same split at the record itself, with the third identity a fiber key can carry: a window nothing can settle into is a value of its own, distinct from both raises and from every settled record.
    #[test]
    fn a_probed_window_records_four_outcomes_that_never_collapse() {
        let index = raising_spec();
        let mut engine = engine_in(&index);
        let seat = RightToken::Letter(fixtures::sym(&index, "qsPea"));
        let window = |third: &str| {
            Slots::new(
                RightToken::Letter(fixtures::sym(&index, "qsTea")),
                RightToken::Letter(fixtures::sym(&index, "qsMay")),
                RightToken::Letter(fixtures::sym(&index, third)),
                EDGE,
            )
        };
        let edge = LeftContext::boundary(TokenKind::Edge);
        let committed = LeftContext::letter(Settled {
            cell: CellId {
                rune: fixtures::sym(&index, "qsTea"),
                stance: fixtures::sym(&index, "hook"),
                entry: None,
                exit: Some(fixtures::sym(&index, "baseline")),
                adjustments: Vec::new(),
            },
            seam: Some(fixtures::sym(&index, "baseline")),
            extension: 0,
        });
        assert_eq!(
            engine
                .transition_trace(&committed, seat, window("qsPea"))
                .map(|_| ())
                .map_err(|error| error.kind()),
            Err(SettleErrorKind::Stranded),
            "qsPea bears no entry at all, so a committed seam strands it"
        );

        let settled = record(&mut engine, &edge, seat, window("qsPea"));
        let incomparable = record(&mut engine, &edge, seat, window("qsMay"));
        let ambiguous = record(&mut engine, &edge, seat, window("qsIt"));
        let unreachable = record(&mut engine, &committed, seat, window("qsPea"));
        let four = [&settled, &incomparable, &ambiguous, &unreachable];
        for (seat, own) in four.iter().enumerate() {
            for other in &four[seat + 1..] {
                assert_ne!(own, other);
            }
        }
        assert_eq!(incomparable, FiberRecord::Incomparable);
        assert_eq!(ambiguous, FiberRecord::Ambiguous);
        assert_eq!(unreachable, FiberRecord::Unreachable);
        assert!(matches!(settled, FiberRecord::Settled { .. }));

        let key = |raise: FiberRecord| FiberKey {
            fourth_matters: false,
            options4: Vec::new(),
            probe: vec![vec![settled.clone(), raise]],
        };
        assert_ne!(
            key(FiberRecord::Incomparable),
            key(FiberRecord::Ambiguous),
            "two probe matrices that differ only in which conflict raised are two fibers"
        );
        assert_ne!(key(FiberRecord::Ambiguous), key(FiberRecord::Unreachable));
        assert_ne!(
            key(FiberRecord::Incomparable),
            key(FiberRecord::Unreachable)
        );
    }

    /// A record carries every row-visible field, so two windows that settle into one cell but report different notes or a different prospect stay two values.
    #[test]
    fn a_settled_record_carries_the_whole_row_visible_trace() {
        let index = raising_spec();
        let mut engine = engine_in(&index);
        let seat = RightToken::Letter(fixtures::sym(&index, "qsPea"));
        let edge = LeftContext::boundary(TokenKind::Edge);
        let slots = Slots::new(
            RightToken::Letter(fixtures::sym(&index, "qsTea")),
            RightToken::Letter(fixtures::sym(&index, "qsMay")),
            RightToken::Letter(fixtures::sym(&index, "qsPea")),
            EDGE,
        );
        let trace = engine
            .transition_trace(&edge, seat, slots)
            .expect("the fixture settles");
        assert_eq!(
            record(&mut engine, &edge, seat, slots),
            FiberRecord::Settled {
                settled: trace.settled,
                prospect: trace.prospect,
                joint_floor: trace.joint_floor,
                notes: trace.notes,
            }
        );
    }
}
