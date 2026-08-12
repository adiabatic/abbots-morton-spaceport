//! The crate's two error families, which are deliberately not one family.
//!
//! [`IngestError`] is ordinary: a dump was unreadable, so the run stops. [`SettleError`] is a kernel outcome that later work observes rather than merely reports, and it is defined here now — before anything raises it — so the discriminant it carries is fixed before the code that keys on it exists.

use std::fmt;

/// A dump this build cannot read: bad JSON, a wrong format marker, a record whose fields are not the ones `rebuild/pipeline/model.py` declares, a value of the wrong JSON type, a number that is not an integer inside `i64`. The path names where in the tree the trouble was found, innermost step last.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct IngestError {
    message: String,
    path: Vec<String>,
}

impl IngestError {
    /// An error with no path yet. Callers add the path on the way out with [`IngestError::at`].
    pub fn new(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
            path: Vec::new(),
        }
    }

    /// Name one enclosing step — a field name, a mapping key, an array index — as the error propagates outward.
    #[must_use]
    pub fn at(mut self, step: impl Into<String>) -> Self {
        self.path.push(step.into());
        self
    }

    /// The complaint on its own, without the path.
    pub fn message(&self) -> &str {
        &self.message
    }
}

impl fmt::Display for IngestError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        if self.path.is_empty() {
            return write!(formatter, "{}", self.message);
        }
        let steps: Vec<&str> = self.path.iter().rev().map(String::as_str).collect();
        write!(formatter, "at {}: {}", steps.join("."), self.message)
    }
}

impl std::error::Error for IngestError {}

/// What settlement raises when a window will not settle. The four variants are four distinct outcomes downstream and must stay distinguishable: the class-grain fibre keys treat E-INCOMPARABLE, E-AMBIGUOUS, and the plain settle error as three separate values, so collapsing any two of them would silently merge fibres that the review surface and the treaty fold read apart. E-STRANDED is the skippable flavor — the liveness probes catch it on their own terms — which is why it is a variant here rather than a plain error with a different sentence in it.
///
/// The Python originals are `specificity.EIncomparableError` and `specificity.EAmbiguousError`, and `settle.SettleError` and `settle.EStrandedError`. All four are raised by [`crate::engine`] and by the specificity order under it, and [`crate::cases`] is where the discriminant is read: it buckets the four into the corpus's three, which is why collapsing any two of them here would go unnoticed there. Ingest failures are [`IngestError`] and never belong here.
///
/// Python's hierarchy is not this crate's: `EIncomparableError` and `EAmbiguousError` derive from `SpecificityError` rather than from `SettleError`, so a Python `except SettleError:` catches neither. Call sites that must catch all four say so — `_prospect`'s fallback is the one that does — and the port's `Result` has no such split, so those catch sets live in the engine's own `match` arms instead of in this type.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum SettleError {
    /// E-INCOMPARABLE: two policy records whose conditions overlap without nesting — neither one's match set contains the other's — while demanding different outcomes, both of them matching the window at hand (`specificity.EIncomparableError`). The overlap is a fact rather than a possibility, so the raise asks for an authored `resolve:` instead of guessing.
    Incomparable(String),
    /// E-AMBIGUOUS: a genuine record-vs-record tie — two policy records with equal match sets demanding different outcomes (`specificity.EAmbiguousError`, whose definition this is). The prefer stage raises it a shade wider than that: two records of one rune collide here whether their conditions are equal or merely non-nested, because the `resolve:` that would settle a non-nested crossing names another rune's record and so has nothing to say about a collision inside a single rune.
    Ambiguous(String),
    /// E-STRANDED: a window with nothing to settle into, which the liveness probes account for separately.
    Stranded(String),
    /// The plain settle error, which is its own outcome and not a fallback bucket for the other three.
    Plain(String),
}

/// The fieldless discriminant of a [`SettleError`], carried apart from the message so outcomes can be counted, compared, and used as map keys without the sentence riding along.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum SettleErrorKind {
    Incomparable,
    Ambiguous,
    Stranded,
    Plain,
}

impl SettleError {
    /// Which of the four outcomes this is.
    pub fn kind(&self) -> SettleErrorKind {
        match self {
            Self::Incomparable(_) => SettleErrorKind::Incomparable,
            Self::Ambiguous(_) => SettleErrorKind::Ambiguous,
            Self::Stranded(_) => SettleErrorKind::Stranded,
            Self::Plain(_) => SettleErrorKind::Plain,
        }
    }

    /// The message this outcome carries.
    pub fn message(&self) -> &str {
        match self {
            Self::Incomparable(message)
            | Self::Ambiguous(message)
            | Self::Stranded(message)
            | Self::Plain(message) => message,
        }
    }
}

impl fmt::Display for SettleError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.message())
    }
}

impl std::error::Error for SettleError {}

#[cfg(test)]
mod tests {
    use std::collections::HashMap;

    use super::*;

    fn one_of_each() -> [SettleError; 4] {
        [
            SettleError::Incomparable("neither dominates".to_owned()),
            SettleError::Ambiguous("two left standing".to_owned()),
            SettleError::Stranded("nothing to settle into".to_owned()),
            SettleError::Plain("will not settle".to_owned()),
        ]
    }

    #[test]
    fn the_four_outcomes_have_four_distinct_kinds() {
        let kinds: Vec<SettleErrorKind> = one_of_each().iter().map(SettleError::kind).collect();
        assert_eq!(
            kinds,
            [
                SettleErrorKind::Incomparable,
                SettleErrorKind::Ambiguous,
                SettleErrorKind::Stranded,
                SettleErrorKind::Plain,
            ]
        );
        for (seat, kind) in kinds.iter().enumerate() {
            for other in &kinds[seat + 1..] {
                assert_ne!(kind, other);
            }
        }
    }

    #[test]
    fn a_kind_keys_a_map_and_survives_the_message_changing() {
        let mut tally: HashMap<SettleErrorKind, usize> = HashMap::new();
        for outcome in one_of_each() {
            *tally.entry(outcome.kind()).or_default() += 1;
        }
        *tally
            .entry(SettleError::Ambiguous("a different sentence".to_owned()).kind())
            .or_default() += 1;
        assert_eq!(tally.len(), 4);
        assert_eq!(tally[&SettleErrorKind::Ambiguous], 2);
        assert_eq!(tally[&SettleErrorKind::Stranded], 1);
    }

    #[test]
    fn an_outcome_keeps_its_message() {
        let outcome = SettleError::Incomparable("neither dominates".to_owned());
        assert_eq!(outcome.message(), "neither dominates");
        assert_eq!(outcome.to_string(), "neither dominates");
    }

    #[test]
    fn an_ingest_error_prints_the_path_it_was_found_at() {
        let error = IngestError::new("expected an integer, got a string")
            .at("y_offset")
            .at("bitmap")
            .at("deep")
            .at("stances")
            .at("qsZoo")
            .at("runes");
        assert_eq!(
            error.to_string(),
            "at runes.qsZoo.stances.deep.bitmap.y_offset: expected an integer, got a string"
        );
        assert_eq!(error.message(), "expected an integer, got a string");
        assert_eq!(IngestError::new("bare").to_string(), "bare");
    }
}
