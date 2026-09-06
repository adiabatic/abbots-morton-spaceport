//! A finished trace memo detached from the engine that filled it, and the rule under which another engine may read it. The memo is a pure function of the raw window: an entry records what one engine settled for one collapsed left, one input and four raw slots, together with the pointers that evaluation fired, and by the window-locality theorem (`doc/rebuild-design.md` §10) that answer is a function of the crate, the script registry and the rune files the key names, and of nothing else. So a memo one enumeration finished can answer another enumeration's windows wherever the two enumerations agree on every rune a key names — and the configuration corollary says exactly where a configuration disagrees with `default`: on the windows naming a rune with an unlock, a `feature:`-conditioned record, or an unlock gate under that configuration, and on nothing else.
//!
//! That is the whole mechanism of the per-configuration delta enumeration (issue #178). `default` enumerates first and its engine hands its memo over as a [`MemoSnapshot`]; every other configuration's engine takes that snapshot as a [`MemoBase`] whose [`Exclusion`] names the configuration's unlocking runes ([`unlocking_runes`]), runs the same worklist from the same seeds in the same order, and finds every window naming no unlocking rune already answered. Nothing about the worklist is seeded: reachability is re-derived by the traversal itself, which is what keeps a cell another configuration reaches first, or reaches only there, out of the theorem's way — the memo answers what a window settles to, never whether the window exists. The fired journal survives the same way a hit on the engine's own memo survives it: a base entry carries the delta its evaluation journaled, and a hit replays it, so a delta configuration's `cited_provenance` is the union over the windows it visited exactly as a from-scratch enumeration's is.
//!
//! The snapshot is shared behind an [`Arc`] rather than copied per configuration, because the memo is the enumeration's high-water mark and a copy per delta configuration would put the fan-out back on the memory bound the delta was meant to lift. It therefore holds no `Rc`, no reference into any engine, and no ladder — the fixpoint never records one — and a base is read-only from the moment it is built.

use std::collections::{HashMap, HashSet};
use std::sync::Arc;

use crate::engine::{Pointer, TraceEntry, TraceKey};
use crate::index::SpecIndex;
use crate::model::{PolicyRecord, Sym, When};
use crate::types::{Settled, TransitionTrace};

/// One engine's finished trace memo: the entries with the three tables their seats index. The tables are the memo's own pools flattened, so an entry read through the snapshot resolves exactly as it resolved through the engine that recorded it.
#[derive(Debug, Default)]
pub struct MemoSnapshot {
    pub(crate) entries: HashMap<TraceKey, TraceEntry>,
    pub(crate) settled: Vec<Settled>,
    pub(crate) notes: Vec<Vec<String>>,
    pub(crate) deltas: Vec<Box<[Pointer]>>,
}

impl MemoSnapshot {
    /// How many windows this snapshot answers.
    pub fn len(&self) -> usize {
        self.entries.len()
    }

    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    /// The trace one entry stands for, rebuilt out of the tables exactly as the recording engine's miss returned it, less the ladder no fixpoint records.
    pub(crate) fn trace(&self, entry: TraceEntry) -> TransitionTrace {
        TransitionTrace {
            settled: self.settled[entry.settled.index()].clone(),
            joint_floor: entry.joint_floor,
            prospect: i64::from(entry.prospect),
            decided_stage: entry.decided_stage,
            notes: self.notes[entry.notes.index()].to_vec(),
            ladder: None,
        }
    }

    /// The settled record one entry names, where it sits.
    pub(crate) fn settled(&self, entry: TraceEntry) -> &Settled {
        &self.settled[entry.settled.index()]
    }

    /// The fired delta one entry names, where it sits.
    pub(crate) fn delta(&self, entry: TraceEntry) -> &[Pointer] {
        &self.deltas[entry.delta.index()]
    }
}

/// The runes a base may not answer for: a key naming any of them is a miss on that base, whatever the base holds. Membership is by the six runes a [`TraceKey`] carries — the left cell's, the input's and the four raw slots' — because those are the only runes the engine reads while settling the window (the module doc says why the theorem makes that the whole list).
#[derive(Clone, Debug, Default)]
pub struct Exclusion {
    runes: HashSet<Sym>,
}

impl Exclusion {
    /// An exclusion over exactly these runes.
    pub fn of(runes: impl IntoIterator<Item = Sym>) -> Self {
        Self {
            runes: runes.into_iter().collect(),
        }
    }

    /// An exclusion naming nothing, under which a base answers every key it holds.
    pub fn none() -> Self {
        Self::default()
    }

    /// Whether this base may answer for `key`: none of the runes it names is excluded.
    pub(crate) fn admits(&self, key: &TraceKey) -> bool {
        self.runes.is_empty() || !key.runes_named().any(|rune| self.runes.contains(&rune))
    }

    /// The runes this exclusion names.
    pub fn runes(&self) -> &HashSet<Sym> {
        &self.runes
    }
}

/// One memo another engine may read, and the runes it may not read it for.
#[derive(Clone, Debug)]
pub struct MemoBase {
    pub memo: Arc<MemoSnapshot>,
    pub excluded: Exclusion,
}

/// Every rune whose settlement can differ between `default` and the configuration enabling `features`: a rune with an unlock under one of them, or with any record — an unlock's own gate, a refusal, a prefer, an extension, a contraction or a resolution — whose `when:` names one. The scan reads every `when:` a rune can carry rather than the record kinds `rebuild/test_spec_load.py` pins feature conditions to, so a kind gaining a feature gate widens this set without an edit here.
///
/// A rune outside this set carries no record that reads the feature set at all, so every window naming only such runes settles identically under both configurations; that is the configuration corollary of the window-locality theorem, and the reason the delta enumeration excludes exactly this set from its base.
pub fn unlocking_runes(index: &SpecIndex, features: &[Sym]) -> HashSet<Sym> {
    let enabled: HashSet<Sym> = features.iter().copied().collect();
    let gated = |when: &When| {
        when.feature
            .is_some_and(|feature| enabled.contains(&feature))
    };
    let record_gated = |records: &[PolicyRecord]| records.iter().any(|record| gated(&record.when));
    index
        .runes()
        .iter()
        .filter(|(_, rune)| {
            let policy = &rune.policy;
            rune.stances.iter().any(|(_, stance)| {
                stance.surface.unlocks.iter().any(|unlock| {
                    enabled.contains(&unlock.feature) || unlock.when.as_ref().is_some_and(gated)
                })
            }) || record_gated(&policy.refuse)
                || record_gated(&policy.prefer)
                || record_gated(&policy.extend)
                || record_gated(&policy.contract)
                || record_gated(&policy.resolve)
        })
        .map(|(name, _)| *name)
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::index::fixtures;

    /// The mini fixture unlocks a `qsMay` entry under `ss03` and nothing else under anything, so `ss03` names `qsMay` alone and a feature nothing unlocks names no rune.
    #[test]
    fn the_unlocking_runes_of_a_configuration_are_the_ones_reading_its_features() {
        let index = fixtures::mini();
        let ss03 = fixtures::sym(&index, "ss03");
        let named = unlocking_runes(&index, &[ss03]);
        let names: Vec<&str> = {
            let mut names: Vec<&str> = named.iter().map(|rune| index.resolve(*rune)).collect();
            names.sort_unstable();
            names
        };
        assert_eq!(names, ["qsMay"]);
        assert!(unlocking_runes(&index, &[]).is_empty());
    }

    /// An exclusion is a miss on any key naming one of its runes, on the left or on any raw slot, and an empty exclusion admits everything.
    #[test]
    fn an_exclusion_refuses_a_key_naming_any_of_its_runes_anywhere() {
        let index = fixtures::mini();
        let may = fixtures::sym(&index, "qsMay");
        let pea = fixtures::sym(&index, "qsPea");
        let tea = fixtures::sym(&index, "qsTea");
        let excluded = Exclusion::of([may]);
        let mut key = TraceKey::for_test(pea, [Some(tea), None, None, None]);
        assert!(excluded.admits(&key));
        assert!(Exclusion::none().admits(&key));
        key.runes[2] = Some(may);
        assert!(!excluded.admits(&key));
        key.runes[2] = None;
        key.left_rune = Some(may);
        assert!(!excluded.admits(&key));
        key.left_rune = None;
        key.token = may;
        assert!(!excluded.admits(&key));
    }
}
