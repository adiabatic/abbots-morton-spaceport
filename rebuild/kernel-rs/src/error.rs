//! The crate's two error types. An [`IngestError`] means a dump could not be read, and the run stops. A [`SettleError`] is the outcome for one window that does not settle, and callers record it as a value.

use std::fmt;

/// A dump this build cannot read: bad JSON, a wrong format marker, a record whose fields are not the ones `rebuild/pipeline/model.py` declares, a value of the wrong JSON type, or a number that is not an integer inside `i64`. The path names where in the tree the error was found, and prints with the innermost step last.
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

    /// Adds one enclosing step (a field name, a mapping key, or an array index) as the error propagates outward.
    #[must_use]
    pub fn at(mut self, step: impl Into<String>) -> Self {
        self.path.push(step.into());
        self
    }

    /// The message without the path.
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

/// The error settlement returns for a window that does not settle.
///
/// [`crate::cases`] and [`crate::fiber`] read the variant through [`SettleError::kind`] and sort it into three outcomes: E-INCOMPARABLE, E-AMBIGUOUS, and unreachable, which covers both E-UNACCEPTED-EXIT and [`SettleError::Plain`]. `cases` writes these as the buckets `E-INCOMPARABLE`, `E-AMBIGUOUS`, and `E-UNREACHABLE`, which `settle.SettleError.bucket` carries on the Python side. Merging E-INCOMPARABLE with E-AMBIGUOUS, or either of them with the unreachable pair, would merge fibers that the review corpus and the join fold tell apart. [`crate::liveness`] sorts the variants into two outcomes: a raise (E-INCOMPARABLE or E-AMBIGUOUS) and unreachable. Outside the tests, no reader distinguishes E-UNACCEPTED-EXIT from the plain error.
///
/// The variants share one type so a caller can catch all four in one arm, as the simulated prospect's fallback in [`crate::engine`] does.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum SettleError {
    /// E-INCOMPARABLE, raised in three ways. Prefer records of two runes match the window, demand different outcomes, and have equal or non-nested conditions, and no `resolve:` on either rune that names the other's record in `against:` matches the window; the message ends in a paste-ready `resolve:` stub, and this is the only conflict a resolve settles (`Engine::apply_resolution` in [`crate::engine`]). Extend records, or contract records, of one rune co-match the window with equal or non-nested conditions and different demands ([`crate::specificity::pick_most_specific`]); that path never reads resolves, so the message tells the author to edit the records. Resolve records raise it themselves when two matching resolves pick differently or a matching resolve's `pick:` admits no surviving candidate.
    Incomparable(String),
    /// E-AMBIGUOUS: two prefer records of the same rune match the window and demand conflicting outcomes, and their conditions are equal or overlap without nesting. The engine raises it before it consults any `resolve:` record, so no resolve settles it; the author edits the prefers instead, as design section 6.2 describes.
    Ambiguous(String),
    /// E-UNACCEPTED-EXIT: the left neighbor committed an exit that this letter has no cell to accept.
    UnacceptedExit(String),
    /// Any other window that does not settle, such as a letter with no candidate cells, or a spec defect found during settlement.
    Plain(String),
}

/// The variant of a [`SettleError`] without its message, so outcomes can be counted, compared, and used as map keys.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum SettleErrorKind {
    Incomparable,
    Ambiguous,
    UnacceptedExit,
    Plain,
}

impl SettleError {
    /// Which of the four outcomes this is.
    pub fn kind(&self) -> SettleErrorKind {
        match self {
            Self::Incomparable(_) => SettleErrorKind::Incomparable,
            Self::Ambiguous(_) => SettleErrorKind::Ambiguous,
            Self::UnacceptedExit(_) => SettleErrorKind::UnacceptedExit,
            Self::Plain(_) => SettleErrorKind::Plain,
        }
    }

    /// The message this outcome carries.
    pub fn message(&self) -> &str {
        match self {
            Self::Incomparable(message)
            | Self::Ambiguous(message)
            | Self::UnacceptedExit(message)
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
    use crate::hash::HashMap;

    use super::*;

    fn one_of_each() -> [SettleError; 4] {
        [
            SettleError::Incomparable("neither dominates".to_owned()),
            SettleError::Ambiguous("two left standing".to_owned()),
            SettleError::UnacceptedExit("nothing to settle into".to_owned()),
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
                SettleErrorKind::UnacceptedExit,
                SettleErrorKind::Plain,
            ]
        );
        for (position, kind) in kinds.iter().enumerate() {
            for other in &kinds[position + 1..] {
                assert_ne!(kind, other);
            }
        }
    }

    #[test]
    fn a_kind_keys_a_map_and_survives_the_message_changing() {
        let mut tally: HashMap<SettleErrorKind, usize> = HashMap::default();
        for outcome in one_of_each() {
            *tally.entry(outcome.kind()).or_default() += 1;
        }
        *tally
            .entry(SettleError::Ambiguous("a different sentence".to_owned()).kind())
            .or_default() += 1;
        assert_eq!(tally.len(), 4);
        assert_eq!(tally[&SettleErrorKind::Ambiguous], 2);
        assert_eq!(tally[&SettleErrorKind::UnacceptedExit], 1);
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
