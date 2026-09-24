//! Derives the fibers for class-grain enumeration. For each live context `(input family, right1, right2)`, the letters in the static third-slot option list are partitioned into fibers by the results of an outcome probe. Each context is derived the first time it is requested and memoized for the build.
//!
//! A fiber is a set of third-slot letters that the enumeration may collapse into one row. A candidate letter `t3`'s fiber key has three parts:
//!
//! 1. The probe results: for every left class in [`ProspectLiveness::seat_left_classes`] and every probed coordinate, the full row-visible record, which is the settled triple, the prospect, the joint-floor flag, and the notes. E-INCOMPARABLE, E-AMBIGUOUS, and every other error are three distinct values, and [`crate::error::SettleError`] keeps those outcomes apart for this reason: merging any two would merge fibers that the review surface and the treaty fold tell apart.
//! 2. The `fourth_slot_matters` verdict.
//! 3. Where that verdict is true, the r4 option list [`WindowOptions::right4_options`] computes for this member. Because the key stores the computed list, a filter added to that pipeline without a key update makes [`crate::fixpoint`]'s partition assertion fail instead of silently splitting a fiber.
//!
//! The probed coordinates are bounded. Where the fourth slot is dead they are `(EDGE, UNKNOWN)`: an r4-dead member's row is traced only at `EDGE` and enqueues no r4 pin, so no letter in the fourth slot is read for it. Where `fourth_slot_matters` is true they are the whole probe alphabet followed by `UNKNOWN`. Those are the contexts where the input letter's settlement can change under a specific `(third, fourth)` pair, which is how the fibers account for the joint34 counterexample ([`crate::liveness`]).
//!
//! Parts 2 and 3 make the members of one r3 fiber share one r4 sub-enumeration, whose r4 fibers are the r4 option letters grouped by their probe results. Because the probe results depend on `t3`, the r4 partition is per `(context, r3 fiber)`, not per context. Grouping follows option-pipeline order: each boundary is its own singleton where it stands, and letters with the same column of the probe matrix share a group placed at its first member.
//!
//! The deriver asks for the raw filter verdict `fourth_slot_matters(family, right1, right2, t3)`, not that verdict ANDed with the depth-4 census. The fixpoint applies the census (`deep4_inputs`) separately when it decides whether a fiber's r4 groups become slot-4 entries, and the partition assertion does the same. In the deep world the census is every rune, so the AND changes nothing there; the pinned-world assertions still read it.
//!
//! The probes run on the build's own tracing engine, so their traces go into the shared memo and their fired pointers into `Engine::fired`, as the liveness probes' do. The one assumption taken from elsewhere instead of probed is the left-class collapse in [`ProspectLiveness::seat_left_classes`]. The fixpoint's echo check tests it on every build at real lefts, real entries, and real adjustments.

use std::rc::Rc;

use crate::census::FourthSlotFilter;
use crate::engine::{Engine, Slots};
use crate::error::{SettleError, SettleErrorKind};
use crate::hash::HashMap;
use crate::liveness::ProspectLiveness;
use crate::model::Sym;
use crate::options::WindowOptions;
use crate::types::{EDGE, LeftContext, RightToken, Settled, TokenKind, UNKNOWN};

/// The coordinates an r4-dead member is probed at. The enumeration traces such a member's row only at `EDGE` and enqueues no r4 pin, so no letter in the fourth slot is ever read for it, and probing the whole alphabet would key the fiber on windows nothing reads.
const DEAD_FOURTH_COORDS: [RightToken; 2] = [EDGE, UNKNOWN];

/// One probed window's row-visible record. A settled window keeps every field a row reports: the settled triple, the prospect, the joint-floor flag, and the notes. The three error outcomes stay distinct, because a fiber that merged an E-INCOMPARABLE window with an unreachable one would merge outcomes the review surface and the treaty fold tell apart.
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

/// One candidate third letter's fiber key: the `fourth_slot_matters` verdict, the computed r4 option list where that verdict is true, and the probe matrix, with one row per left class and one column per probed coordinate.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
struct FiberKey {
    fourth_matters: bool,
    options4: Vec<RightToken>,
    probe: Vec<Vec<FiberRecord>>,
}

/// One r3 fiber of a live context: its member letters, their shared `fourth_slot_matters` verdict, and, only where that verdict is true, their shared r4 sub-enumeration.
///
/// `members` is in sorted-name order, which is the order of the static option list, so the first member is the deterministic representative. `r4_groups` is the computed r4 option list split into boundary singletons and r4 letter fibers, in option-pipeline order. It is empty when the fourth slot is dead.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Fiber {
    pub members: Vec<RightToken>,
    pub fourth_matters: bool,
    pub r4_groups: Vec<Vec<RightToken>>,
}

/// One live context's third-slot partition: the static option list's boundaries in their own order, and its letters as fibers, ordered by each fiber's first member.
///
/// The boundaries are listed here so the enumeration does not derive them again: it walks them before the fibers and pins them the same way it pins a fiber's members. A boundary is always a class of one, so none is grouped.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ContextFibers {
    pub boundary_options: Vec<RightToken>,
    pub fibers: Vec<Fiber>,
}

/// The per-build fiber deriver. Apart from its own memo, everything it uses belongs to the fixpoint and is passed in on each call: the engine, the liveness probe, the fourth-slot filter, and the option pipelines. Using the fixpoint's own instances means the deriver's probes share their memos and fire into the `Engine::fired` set the product reports as `cited_provenance`.
#[derive(Debug, Default)]
pub struct DeepFiberDeriver {
    contexts: HashMap<(Sym, Sym, Sym), Rc<ContextFibers>>,
}

impl DeepFiberDeriver {
    /// A deriver with an empty memo.
    pub fn new() -> Self {
        Self::default()
    }

    /// This context's fiber partition, derived on the first request and memoized.
    ///
    /// The static option list is [`WindowOptions::right3_options`] over the follower map [`WindowOptions::context_follower_map`] returns for `(family, right1)`, the same call the enumeration makes. Its non-letter entries become [`ContextFibers::boundary_options`] unchanged; its letters are probed and grouped.
    ///
    /// The fixpoint asks only for contexts whose third slot is live. The deriver does not check this, because the caller has already computed that verdict.
    ///
    /// The fourth-slot filter gets the liveness probe only when the engine's modes make a deep world, which is the rule the fixpoint follows when it calls the same filter ([`crate::census`]). A deriver only runs in a deep world, so the probe is always passed in practice.
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
        let letter = |rune: Sym| {
            engine
                .index()
                .letter(rune)
                .expect("a context names modeled runes")
        };
        let token = letter(family);
        let r1tok = letter(right1);
        let r2tok = letter(right2);
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

        let mut seats: HashMap<Rc<FiberKey>, usize> = HashMap::default();
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

/// One probed window's record: the trace's row-visible fields when the window settles, or one of the three error outcomes when it does not.
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

/// One r3 fiber's r4 sub-enumeration: its computed option list, split in pipeline order. Each boundary is a singleton where it stands, and letters are grouped by their column of the probe matrix, each group placed where its first member falls.
///
/// A letter's column is its records under every left class at that letter's own coordinate, found by its position in `full_coords`. The column borrows the records instead of cloning them, since a column is only compared with other columns of the same matrix.
fn r4_groups(key: &FiberKey, full_coords: &[RightToken]) -> Vec<Vec<RightToken>> {
    let coord_index: HashMap<RightToken, usize> = full_coords
        .iter()
        .enumerate()
        .map(|(seat, coord)| (*coord, seat))
        .collect();
    let mut ordered: Vec<Vec<RightToken>> = Vec::new();
    let mut by_column: HashMap<Vec<&FiberRecord>, usize> = HashMap::default();
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

    /// The `belt_spec` fixture from [`crate::liveness`]'s tests, whose one context has a live fourth slot at exactly one third letter.
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

    /// A right-context condition for `qsTea`'s prefer: `qsMay` in the slot after `qsTea`, anything in the slot after that, and `fourth` in the next, which is the input letter's fourth slot. The two reaches below differ only in that last condition, so comparing them isolates its effect on the r4 grouping.
    fn reach_past_may(fourth: &str) -> String {
        fixtures::condition(&[
            ("family", &fixtures::names(&["qsMay"])),
            ("then", &fixtures::condition(&[("then", fourth)])),
        ])
    }

    /// The reach whose last condition accepts only a boundary: `EDGE` matches, `UNKNOWN` matches optimistically, and no letter matches, so every r4 letter has the same column.
    fn any_boundary_fourth() -> String {
        reach_past_may(&fixtures::condition(&[("is_token", "\"boundary\"")]))
    }

    /// The same reach naming `qsIt` in that position instead, which is the one change that splits the column the r4 letters shared.
    fn one_named_fourth() -> String {
        reach_past_may(&fixtures::condition(&[(
            "family",
            &fixtures::names(&["qsIt"]),
        )]))
    }

    /// A fixture whose fourth slot is live at every third letter. `qsTea`'s absolute prefer drops its exit when the reach matches, and the reach reads the input letter's fourth slot but places no condition on the third, so every third letter probes alike and the whole alphabet forms one fiber. The r4 grouping still has to partition the option list, so the probe-matrix columns alone decide it.
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

    /// One context's partition formatted as the `liveness-cases` subcommand formats it, with every token passed through [`right_token_label`], so an expected string here reads like that subcommand's output.
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

    /// A fixture in which the third letter decides between the two record-vs-record errors. With lookahead `qsTea qsMay qsIt`, `qsPea`'s first two prefers demand different stances, which is E-AMBIGUOUS within one rune. With lookahead `qsTea qsMay qsMay`, `qsPea`'s third prefer conflicts with `qsTea`'s vote, which is E-INCOMPARABLE across two runes. Every other third letter settles.
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

    /// The objects a context is derived through, one instance of each, as the fixpoint passes them.
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

    /// The full partition of the joint34 context: the static list's boundaries in their own order, its letters split by the outcome probe, and the one fiber with a live fourth slot carrying its r4 sub-enumeration.
    ///
    /// The r4 groups follow the option pipeline: each boundary a singleton where it stands, then the letters grouped by probe-matrix column, with `qsIt` apart from the three that probe alike.
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

    /// Two requests for one context return the same derivation instead of probing twice.
    #[test]
    fn a_context_is_derived_once_and_handed_out_by_reference() {
        let index = belt_spec();
        let mut scaffolding = Scaffolding::new(&index);
        let first = scaffolding.context(&index, ["qsPea", "qsTea", "qsMay"]);
        let second = scaffolding.context(&index, ["qsPea", "qsTea", "qsMay"]);
        assert!(Rc::ptr_eq(&first, &second));
    }

    /// The r4 letters group by their probe-matrix column and by nothing else. Changing one condition of the follower's reach, from any boundary to one named letter, moves that letter out of the group the other letters share. The boundaries stay singletons at their option-list positions, and the r3 partition does not change.
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

    /// The deriver probes an r4-dead member only at `EDGE` and `UNKNOWN`, and a live one across the whole alphabet. Probing the dead member more widely would not change the partition, but every traced window adds its fired pointers to the product's `cited_provenance`, so the coordinate list affects the output.
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
                    index.letter(pea).expect("the fixture models it"),
                    Slots::new(
                        index.letter(tea).expect("the fixture models it"),
                        index.letter(may).expect("the fixture models it"),
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

    /// The three error outcomes are three values: a third letter whose window raises E-INCOMPARABLE and one whose window raises E-AMBIGUOUS go into two separate fibers, and the letters that settle go into a third.
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
                        fixtures::letter(&index, "qsPea"),
                        Slots::new(
                            fixtures::letter(&index, "qsTea"),
                            fixtures::letter(&index, "qsMay"),
                            fixtures::letter(&index, third),
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

    /// The same split at the record level, plus the third error outcome a key can hold: a window with nothing to settle into is its own value, distinct from both conflicts and from every settled record.
    #[test]
    fn a_probed_window_records_four_outcomes_that_never_collapse() {
        let index = raising_spec();
        let mut engine = engine_in(&index);
        let seat = fixtures::letter(&index, "qsPea");
        let window = |third: &str| {
            Slots::new(
                fixtures::letter(&index, "qsTea"),
                fixtures::letter(&index, "qsMay"),
                fixtures::letter(&index, third),
                EDGE,
            )
        };
        let edge = LeftContext::boundary(TokenKind::Edge);
        let committed = LeftContext::letter(
            &index,
            Settled {
                cell: CellId {
                    rune: fixtures::sym(&index, "qsTea"),
                    stance: fixtures::sym(&index, "hook"),
                    entry: None,
                    exit: Some(fixtures::sym(&index, "baseline")),
                    adjustments: Vec::new(),
                },
                seam: Some(fixtures::sym(&index, "baseline")),
                extension: 0,
            },
        );
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
        let seat = fixtures::letter(&index, "qsPea");
        let edge = LeftContext::boundary(TokenKind::Edge);
        let slots = Slots::new(
            fixtures::letter(&index, "qsTea"),
            fixtures::letter(&index, "qsMay"),
            fixtures::letter(&index, "qsPea"),
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
