//! One raw window slot to the right of a settled position: [`RightToken`]. Its representation is private to this module, so every read of a token goes through one of its methods.
//!
//! The methods fall into three groups. The questions ([`RightToken::kind`], [`RightToken::in_family`], [`RightToken::in_class`], [`RightToken::has_entry_stroke`]) answer one predicate about the token. [`RightToken::key_ordinal`] returns the token's identity for a memo key or a row of output, and `the_key_ordinal_callers_are_the_pinned_roster` in this module's tests pins the files that call it. The rune accessors ([`RightToken::rune`], [`RightToken::letter`], [`RightToken::ordinal`], [`RightToken::letter_ordinal`]) hand out the rune itself, for a caller that makes the token the rune or a slot of a nested enumeration, prospect, settlement or acceptor check, or that needs the rune for something no question covers.
//!
//! A letter can also be swept: a grouped fourth-slot prospect sweep ([`crate::liveness`]) marks the token it varies, and the mark travels with the token as slots are rebuilt, while equality, ordering, hashing and keys ignore it. This module's thread-local recorder is off unless a recording ask runs. While one does, a question asked of a swept token at the ask's own level records the question and its answer; a call made with a swept argument at that level is a nested question, inside which nothing records, and the engine records the call and its answer when it returns ([`enter`], [`leave`]); and a rune accessor called on a swept token at that level, or a comparison or hash of one, poisons the ask, which then completes as usual but stands for no other token. An unswept token never touches the recorder.

use std::cell::RefCell;
use std::collections::BTreeSet;
use std::hash::{Hash, Hasher};

use crate::engine::Slots;
use crate::error::SettleErrorKind;
use crate::index::{Ordinal, SpecIndex};
use crate::model::Sym;
use crate::types::{Candidate, LeftContext, TokenKind};

/// One raw window slot: a boundary, an unknown, or a letter naming its rune. `settle.RightToken`, with the same equality: the kind is part of the value, so `UNKNOWN` and `EDGE` are different tokens, and two letter tokens are equal when their runes are.
///
/// A letter also carries its rune's [`Ordinal`], because the memo keys store ordinals. A token created by [`SpecIndex::letter`] passes its ordinal to every key built from it, so building a key needs no rune lookup. Under the index that created it, the ordinal is a function of the name, so the equality is still equality of runes.
///
/// A letter is either plain or swept ([`RightToken::swept`]). Only a grouped sweep makes a swept token, of the token it varies, and the state travels with the token as slots are rebuilt. Equality, ordering, hashing, `Debug` and [`RightToken::key_ordinal`] treat a swept letter as the plain one, so no key, ordering or row changes; the state only tells the recorder which token's reads to watch.
///
/// The ordering exists so a token can key a `BTreeMap`. It orders the boundaries and the unknown before every letter, and letters by interning order, which is the order the dump happened to mention names in. Where an output order depends on a token, as in the guard sweep's rows, sort by the resolved name instead. Equality, ordering, hashing and `Debug` are the plain representation's derived ones, so a token hashes into a key and prints exactly as that representation does.
#[derive(Clone, Copy)]
pub struct RightToken(Repr);

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
enum Repr {
    Edge,
    Space,
    Zwnj,
    NamerDot,
    Unknown,
    Letter(Sym, Ordinal),
    Swept(Sym, Ordinal),
}

impl RightToken {
    /// Equality between two tokens one of which is swept and which differ in their representation: a comparison reads the swept token's identity, so it poisons a recording ask at its own level ([`poison`]).
    #[cold]
    #[inline(never)]
    fn compared(self, other: Self) -> bool {
        poison(Accessor::Compare);
        self.plain() == other.plain()
    }

    /// The representation with a swept letter read as the plain one, which every comparison, hash and print goes through.
    #[inline]
    fn plain(self) -> Repr {
        match self.0 {
            Repr::Swept(rune, ordinal) => Repr::Letter(rune, ordinal),
            other => other,
        }
    }
}

impl PartialEq for RightToken {
    #[inline]
    fn eq(&self, other: &Self) -> bool {
        self.0 == other.0 || ((self.is_swept() || other.is_swept()) && self.compared(*other))
    }
}

impl Eq for RightToken {}

impl PartialOrd for RightToken {
    #[inline]
    fn partial_cmp(&self, other: &Self) -> Option<std::cmp::Ordering> {
        Some(self.cmp(other))
    }
}

impl Ord for RightToken {
    #[inline]
    fn cmp(&self, other: &Self) -> std::cmp::Ordering {
        if self.is_swept() || other.is_swept() {
            poison(Accessor::Compare);
        }
        self.plain().cmp(&other.plain())
    }
}

impl Hash for RightToken {
    #[inline]
    fn hash<H: Hasher>(&self, state: &mut H) {
        if self.is_swept() {
            poison(Accessor::Compare);
        }
        self.plain().hash(state);
    }
}

impl std::fmt::Debug for RightToken {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        self.plain().fmt(formatter)
    }
}

/// The run edge, `settle.EDGE`.
pub const EDGE: RightToken = RightToken(Repr::Edge);
/// A space, `settle.SPACE`.
pub const SPACE: RightToken = RightToken(Repr::Space);
/// A zero-width non-joiner, `settle.ZWNJ`.
pub const ZWNJ: RightToken = RightToken(Repr::Zwnj);
/// The namer dot, `settle.NAMER_DOT`.
pub const NAMER_DOT: RightToken = RightToken(Repr::NamerDot);
/// A slot outside the evaluated window, `settle.UNKNOWN`.
pub const UNKNOWN: RightToken = RightToken(Repr::Unknown);

impl RightToken {
    /// The letter token for a rune and its rune-field ordinal, which must be the ordinal the index reading the token's keys gives the rune: [`SpecIndex::letter`] takes it from the index, and the fixpoint rebuilds a stored token through [`SpecIndex::rune_at_ordinal`].
    pub fn of_letter(rune: Sym, ordinal: Ordinal) -> Self {
        Self(Repr::Letter(rune, ordinal))
    }

    /// The boundary or unknown token for a kind, or `None` for [`TokenKind::Letter`], which needs a rune. The inverse of [`RightToken::kind`], used by the `settle-cases` reader and [`crate::replay::alphabet`] to rebuild boundary tokens from a kind's name.
    pub fn of_kind(kind: TokenKind) -> Option<Self> {
        match kind {
            TokenKind::Edge => Some(EDGE),
            TokenKind::Space => Some(SPACE),
            TokenKind::Zwnj => Some(ZWNJ),
            TokenKind::NamerDot => Some(NAMER_DOT),
            TokenKind::Unknown => Some(UNKNOWN),
            TokenKind::Letter => None,
        }
    }

    /// The swept form of a letter, which a grouped sweep gives the token it varies; any other token is returned unchanged.
    pub(crate) fn swept(self) -> Self {
        match self.0 {
            Repr::Letter(rune, ordinal) => Self(Repr::Swept(rune, ordinal)),
            _ => self,
        }
    }

    /// Whether this is a swept letter.
    #[inline]
    pub(crate) fn is_swept(self) -> bool {
        matches!(self.0, Repr::Swept(_, _))
    }

    /// This token with a swept letter replaced by `with`: how a recorded nested question is asked again of another token.
    pub(crate) fn sweep_to(self, with: Self) -> Self {
        if self.is_swept() { with } else { self }
    }

    /// Which kind of slot this is. A swept letter's kind is a question too, but every token a sweep groups is a letter, so the recorder folds it once instead of recording each read.
    #[inline]
    pub fn kind(self) -> TokenKind {
        match self.0 {
            Repr::Edge => TokenKind::Edge,
            Repr::Space => TokenKind::Space,
            Repr::Zwnj => TokenKind::Zwnj,
            Repr::NamerDot => TokenKind::NamerDot,
            Repr::Unknown => TokenKind::Unknown,
            Repr::Letter(_, _) | Repr::Swept(_, _) => TokenKind::Letter,
        }
    }

    /// Whether this slot is a letter whose rune is one of `families`: a condition's `family:` list. A swept letter records the question.
    #[inline]
    pub fn in_family(self, families: &[Sym]) -> bool {
        match self.0 {
            Repr::Letter(rune, _) => families.contains(&rune),
            Repr::Swept(rune, _) => {
                let answer = families.contains(&rune);
                record_family(families, answer);
                answer
            }
            _ => false,
        }
    }

    /// Whether this slot is a letter whose rune is one of `members`: the membership of class `klass` under `owner`, as [`SpecIndex::class_members`] returns it, read by the caller so the class is journaled where it always is. A swept letter records the question.
    #[inline]
    pub fn in_class(self, klass: Sym, owner: Option<Sym>, members: &BTreeSet<Sym>) -> bool {
        match self.0 {
            Repr::Letter(rune, _) => members.contains(&rune),
            Repr::Swept(rune, _) => {
                let answer = members.contains(&rune);
                record(Question::Class(klass, owner, answer));
                answer
            }
            _ => false,
        }
    }

    /// Whether this slot is a letter whose rune declares an entry row with this stroke, through [`SpecIndex::entry_strokes`], which journals the rune. A swept letter records the question.
    #[inline]
    pub fn has_entry_stroke(self, index: &SpecIndex, stroke: Sym) -> bool {
        match self.0 {
            Repr::Letter(rune, _) => index.entry_strokes(rune).contains(&stroke),
            Repr::Swept(rune, _) => {
                let answer = index.entry_strokes(rune).contains(&stroke);
                record(Question::Stroke(stroke, answer));
                answer
            }
            _ => false,
        }
    }

    /// The rune-field ordinal of the rune this slot names, or `None` when it is not a letter. This is the one accessor that returns a token's identity for building a memo key or writing a row of output, and nothing else may call it for any other purpose. A swept letter answers as the plain one and records nothing.
    #[inline]
    pub fn key_ordinal(self) -> Option<Ordinal> {
        match self.0 {
            Repr::Letter(_, ordinal) | Repr::Swept(_, ordinal) => Some(ordinal),
            _ => None,
        }
    }

    /// The rune this slot names, or `None` when it is not a letter. `RightToken.rune`, for reads that have not yet checked the kind. On a swept letter it poisons the recording ask ([`poison`]).
    #[inline]
    pub fn rune(self) -> Option<Sym> {
        match self.0 {
            Repr::Letter(rune, _) => Some(rune),
            Repr::Swept(rune, _) => {
                poison(Accessor::Rune);
                Some(rune)
            }
            _ => None,
        }
    }

    /// The rune this slot names, for reads that have already checked it is a letter. Panics on any other kind, as `RightToken.letter` raises `ValueError`: reaching it means a caller skipped the kind check, which is a kernel bug and not a settlement outcome. On a swept letter it poisons the recording ask ([`poison`]).
    #[inline]
    pub fn letter(self) -> Sym {
        match self.0 {
            Repr::Letter(rune, _) => rune,
            Repr::Swept(rune, _) => {
                poison(Accessor::Letter);
                rune
            }
            _ => panic!("{} token has no rune", self.kind().as_str()),
        }
    }

    /// The rune-field ordinal of the rune this slot names, or `None` when it is not a letter: [`RightToken::rune`] in the form the memo keys store. On a swept letter it poisons the recording ask ([`poison`]).
    #[inline]
    pub fn ordinal(self) -> Option<Ordinal> {
        match self.0 {
            Repr::Letter(_, ordinal) => Some(ordinal),
            Repr::Swept(_, ordinal) => {
                poison(Accessor::Ordinal);
                Some(ordinal)
            }
            _ => None,
        }
    }

    /// The rune-field ordinal, for reads that have already checked it is a letter. Panics on any other kind, as [`RightToken::letter`] does. On a swept letter it poisons the recording ask ([`poison`]).
    #[inline]
    pub fn letter_ordinal(self) -> Ordinal {
        match self.0 {
            Repr::Letter(_, ordinal) => ordinal,
            Repr::Swept(_, ordinal) => {
                poison(Accessor::LetterOrdinal);
                ordinal
            }
            _ => panic!("{} token has no rune", self.kind().as_str()),
        }
    }
}

/// One question a recording ask put to its swept token, with the answer the ask read. A leaf question is asked of the token itself; a nested question is a call the ask made with the swept token among its arguments, kept with those arguments, the swept positions still swept, so it can be made again for another token ([`RightToken::sweep_to`]), and with the answer as its caller reads it, an error as its kind.
#[derive(Clone, Debug)]
pub(crate) enum Question {
    Family(Box<[Sym]>, bool),
    Class(Sym, Option<Sym>, bool),
    Stroke(Sym, bool),
    Prospect {
        rune: Sym,
        candidate: Candidate,
        slots: Slots,
        recorded: bool,
        answer: Result<i64, SettleErrorKind>,
    },
    Candidates {
        left: LeftContext,
        rune: Sym,
        right1: RightToken,
        right2: RightToken,
        answer: Result<(usize, usize), SettleErrorKind>,
    },
    Acceptor {
        candidate: Candidate,
        rune: Sym,
        right1: RightToken,
        right2: RightToken,
        answer: Result<bool, SettleErrorKind>,
    },
}

/// The reads that poison a recording ask: the rune accessors, and a comparison or hash of a swept token against another token.
#[derive(Clone, Copy, Debug)]
pub(crate) enum Accessor {
    Rune,
    Letter,
    Ordinal,
    LetterOrdinal,
    Compare,
}

/// What a recording ask leaves behind ([`end_recording`]): its questions when it may stand for other tokens, or why it may not: a rune accessor poisoned it, a memo answered its own prospect or window, or it did not take its value from a simulated trace it settled.
#[derive(Debug)]
pub(crate) enum Recorded {
    Transcript(Vec<Question>),
    Poisoned(Accessor),
    AnsweredFromMemo,
    NotSimulated,
}

/// How a call with token arguments relates to the recording ask ([`enter`]).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Role {
    /// Not recorded: no argument is swept, no ask is recording, or the call is inside a nested question.
    Plain,
    /// The recording ask's own top-level prospect.
    Ask,
    /// A nested question the caller records with [`leave`] once it returns.
    Nested,
}

/// This thread's recorder: whether an ask is recording, how deep inside nested questions the evaluation is, and what the ask has recorded. It is touched only by a swept token's reads and by a call that has a swept argument, so an evaluation with no swept token costs nothing new. Each engine runs on one thread, as with the read journal ([`crate::index`]).
#[derive(Debug)]
struct Recorder {
    recording: bool,
    depth: u32,
    ask_entered: bool,
    poisoned: Option<Accessor>,
    simulated: bool,
    unqualified: bool,
    questions: Vec<Question>,
}

thread_local! {
    static RECORDER: RefCell<Recorder> = const {
        RefCell::new(Recorder {
            recording: false,
            depth: 0,
            ask_entered: false,
            poisoned: None,
            simulated: false,
            unqualified: false,
            questions: Vec::new(),
        })
    };
}

/// Append a leaf question while an ask records at its own level. Only a swept token calls it, so it is kept out of line from the questions' common path.
#[cold]
#[inline(never)]
fn record(question: Question) {
    RECORDER.with(|recorder| {
        let mut recorder = recorder.borrow_mut();
        if recorder.recording && recorder.depth == 0 {
            recorder.questions.push(question);
        }
    });
}

/// [`record`] for a family question, which copies the list only when it is recorded.
#[cold]
#[inline(never)]
fn record_family(families: &[Sym], answer: bool) {
    RECORDER.with(|recorder| {
        let mut recorder = recorder.borrow_mut();
        if recorder.recording && recorder.depth == 0 {
            recorder
                .questions
                .push(Question::Family(families.into(), answer));
        }
    });
}

/// Mark the recording ask as unusable for other tokens: at its own level it read its swept token's rune through an accessor no question covers. The ask completes as it would anyway. Only a swept token calls it, so it is kept out of line from the accessors' common path.
#[cold]
#[inline(never)]
fn poison(accessor: Accessor) {
    RECORDER.with(|recorder| {
        let mut recorder = recorder.borrow_mut();
        if recorder.recording && recorder.depth == 0 && recorder.poisoned.is_none() {
            recorder.poisoned = Some(accessor);
        }
    });
}

/// Start recording one ask on this thread.
pub(crate) fn begin_recording() {
    RECORDER.with(|recorder| {
        let mut recorder = recorder.borrow_mut();
        recorder.recording = true;
        recorder.depth = 0;
        recorder.ask_entered = false;
        recorder.poisoned = None;
        recorder.simulated = false;
        recorder.unqualified = false;
        recorder.questions.clear();
    });
}

/// Stop recording and return what the ask recorded: its questions when it was not poisoned, its own prospect was not answered from a memo, and it took its value from the follower's simulated trace.
pub(crate) fn end_recording() -> Recorded {
    RECORDER.with(|recorder| {
        let mut recorder = recorder.borrow_mut();
        recorder.recording = false;
        if let Some(accessor) = recorder.poisoned {
            Recorded::Poisoned(accessor)
        } else if recorder.unqualified {
            Recorded::AnsweredFromMemo
        } else if !recorder.simulated {
            Recorded::NotSimulated
        } else {
            Recorded::Transcript(std::mem::take(&mut recorder.questions))
        }
    })
}

/// The role of a call whose token arguments are `tokens`. Without a swept argument it is [`Role::Plain`] and the recorder is not touched. The first call with one in a recording ask, at its own level, is the ask itself when `may_be_ask` is set; any later one is a nested question, and entering it raises the depth until [`leave`].
#[inline]
pub(crate) fn enter(tokens: &[RightToken], may_be_ask: bool) -> Role {
    if !tokens.iter().any(|token| token.is_swept()) {
        return Role::Plain;
    }
    RECORDER.with(|recorder| {
        let mut recorder = recorder.borrow_mut();
        if !recorder.recording || recorder.depth > 0 {
            return Role::Plain;
        }
        if may_be_ask && !recorder.ask_entered {
            recorder.ask_entered = true;
            return Role::Ask;
        }
        recorder.depth += 1;
        Role::Nested
    })
}

/// Close a nested question [`enter`] opened and record it.
pub(crate) fn leave(question: Question) {
    RECORDER.with(|recorder| {
        let mut recorder = recorder.borrow_mut();
        recorder.depth -= 1;
        recorder.questions.push(question);
    });
}

/// The recording ask's own prospect was answered from a memo, so its path is unknown.
pub(crate) fn note_answered_from_memo() {
    RECORDER.with(|recorder| recorder.borrow_mut().unqualified = true);
}

/// The recording ask's own prospect took its value from the follower's simulated trace, settled here.
pub(crate) fn note_simulated() {
    RECORDER.with(|recorder| recorder.borrow_mut().simulated = true);
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Every Rust source of the crate except this module, as (path relative to the crate root, text).
    fn crate_sources() -> Vec<(String, String)> {
        let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"));
        let mut pending: Vec<std::path::PathBuf> =
            ["src", "tests"].iter().map(|dir| root.join(dir)).collect();
        let mut sources = Vec::new();
        while let Some(dir) = pending.pop() {
            for entry in std::fs::read_dir(&dir).expect("the crate's source directories read") {
                let path = entry.expect("a directory entry reads").path();
                if path.is_dir() {
                    pending.push(path);
                    continue;
                }
                if path.extension().is_none_or(|ext| ext != "rs") || path.ends_with("src/token.rs")
                {
                    continue;
                }
                let source = std::fs::read_to_string(&path).expect("a crate source file reads");
                let relative = path
                    .strip_prefix(root)
                    .unwrap_or(&path)
                    .display()
                    .to_string();
                sources.push((relative, source));
            }
        }
        sources.sort();
        sources
    }

    /// Whether a source names the token's representation: the private enum, one of its variants as a `RightToken` path, or the wrapper's tuple constructor or pattern.
    fn names_the_representation(source: &str) -> bool {
        let variants = ["Edge", "Space", "Zwnj", "NamerDot", "Unknown", "Letter"];
        source.match_indices("RightToken").any(|(at, name)| {
            if source[..at].ends_with(|c: char| c.is_alphanumeric() || c == '_') {
                return false;
            }
            let rest = &source[at + name.len()..];
            if rest.starts_with('(') {
                return true;
            }
            rest.strip_prefix("::").is_some_and(|path| {
                variants.iter().any(|variant| {
                    path.strip_prefix(variant).is_some_and(|after| {
                        !after.starts_with(|c: char| c.is_alphanumeric() || c == '_')
                    })
                })
            })
        }) || source.match_indices("Repr").any(|(at, name)| {
            !source[..at].ends_with(|c: char| c.is_alphanumeric() || c == '_')
                && !source[at + name.len()..].starts_with(|c: char| c.is_alphanumeric() || c == '_')
        })
    }

    #[test]
    fn the_representation_check_catches_a_variant_or_the_wrapper() {
        for stray in [
            "if let RightToken::Letter(rune, _) = token {}",
            "token == RightToken::Zwnj",
            "let RightToken(inner) = token;",
            "match token.0 { Repr::Edge => 1, _ => 2 }",
        ] {
            assert!(names_the_representation(stray), "missed: {stray}");
        }
        for clean in [
            "let token = RightToken::of_letter(rune, ordinal);",
            "RightToken::of_kind(kind)",
            "tokens.map(RightToken::key_ordinal)",
            "let tokens: Vec<RightToken> = Vec::new();",
            "struct LetterRepresentation;",
        ] {
            assert!(!names_the_representation(clean), "flagged: {clean}");
        }
    }

    #[test]
    fn no_module_outside_the_token_module_names_its_representation() {
        let strays: Vec<String> = crate_sources()
            .into_iter()
            .filter(|(_, source)| names_the_representation(source))
            .map(|(path, _)| path)
            .collect();
        assert!(
            strays.is_empty(),
            "these files name RightToken's representation; read a token through its methods instead: {}",
            strays.join(", ")
        );
    }

    #[test]
    fn the_key_ordinal_callers_are_the_pinned_roster() {
        let callers: Vec<String> = crate_sources()
            .into_iter()
            .filter(|(_, source)| {
                source.lines().any(|line| {
                    !line.trim_start().starts_with("//") && line.contains("key_ordinal")
                })
            })
            .map(|(path, _)| path)
            .collect();
        assert_eq!(
            callers,
            ["src/engine.rs", "src/fixpoint.rs", "src/liveness.rs"],
            "a new caller of RightToken::key_ordinal is a deliberate edit: it must build a memo key or a row of output and nothing else, and joins this roster"
        );
    }

    #[test]
    fn a_token_compares_hashes_orders_and_prints_as_its_representation() {
        let index = crate::index::fixtures::mini();
        let tea = index
            .letter(crate::index::fixtures::sym(&index, "qsTea"))
            .expect("qsTea is registered");
        let pea = index
            .letter(crate::index::fixtures::sym(&index, "qsPea"))
            .expect("qsPea is registered");
        let ordered = [EDGE, SPACE, ZWNJ, NAMER_DOT, UNKNOWN];
        for pair in ordered.windows(2) {
            assert!(pair[0] < pair[1]);
        }
        assert!(UNKNOWN < tea && UNKNOWN < pea);
        assert_eq!(tea.cmp(&pea), tea.0.cmp(&pea.0));
        let hash = |value: &dyn Fn(&mut crate::hash::FastHasher)| {
            let mut hasher = crate::hash::FastHasher::default();
            value(&mut hasher);
            std::hash::Hasher::finish(&hasher)
        };
        assert_eq!(
            hash(&|hasher| std::hash::Hash::hash(&tea, hasher)),
            hash(&|hasher| std::hash::Hash::hash(&tea.0, hasher))
        );
        assert_eq!(format!("{tea:?}"), format!("{:?}", tea.0));
        assert_eq!(format!("{EDGE:?}"), "Edge");
        let swept = tea.swept();
        assert!(swept.is_swept() && !tea.is_swept());
        assert_eq!(swept, tea);
        assert_ne!(swept, pea);
        assert_eq!(swept.cmp(&pea), tea.cmp(&pea));
        assert_eq!(pea.cmp(&swept), pea.cmp(&tea));
        assert_eq!(
            hash(&|hasher| std::hash::Hash::hash(&swept, hasher)),
            hash(&|hasher| std::hash::Hash::hash(&tea, hasher))
        );
        assert_eq!(swept.key_ordinal(), tea.key_ordinal());
        assert_eq!(format!("{swept:?}"), format!("{tea:?}"));
        assert_eq!(EDGE.swept(), EDGE);
        assert!(!EDGE.swept().is_swept());
        assert_eq!(swept.sweep_to(pea), pea);
        assert_eq!(tea.sweep_to(pea), tea);
    }

    /// A recording ask records the questions its swept token answers at its own level, records nothing for an unswept token or inside a nested question, keeps the nested question itself, and is poisoned by a rune accessor or a comparison at its own level but not inside a nested question.
    #[test]
    fn a_recording_ask_records_its_swept_tokens_questions_and_a_raw_read_poisons_it() {
        let index = crate::index::fixtures::mini();
        let sym = |name: &str| crate::index::fixtures::sym(&index, name);
        let tea = index.letter(sym("qsTea")).expect("qsTea is registered");
        let pea = index.letter(sym("qsPea")).expect("qsPea is registered");
        let swept = tea.swept();
        let families = [sym("qsTea")];

        assert_eq!(enter(&[swept], true), Role::Plain, "no ask is recording");
        assert!(swept.in_family(&families));

        begin_recording();
        assert_eq!(enter(&[pea, EDGE], true), Role::Plain, "no swept argument");
        assert_eq!(enter(&[pea, swept], true), Role::Ask);
        assert!(swept.in_family(&families));
        assert!(
            !pea.in_family(&families),
            "an unswept token records nothing"
        );
        assert_eq!(enter(&[swept], false), Role::Nested);
        assert!(
            !swept.in_family(&[sym("qsPea")]),
            "inside a nested question nothing is recorded"
        );
        assert_eq!(swept.rune(), Some(sym("qsTea")), "nor poisons");
        assert_eq!(
            enter(&[swept], true),
            Role::Plain,
            "a call inside a nested question is plain"
        );
        leave(Question::Stroke(sym("qsPea"), false));
        note_simulated();
        match end_recording() {
            Recorded::Transcript(questions) => {
                assert_eq!(questions.len(), 2, "{questions:?}");
                assert!(
                    matches!(&questions[0], Question::Family(list, true) if **list == families)
                );
                assert!(matches!(questions[1], Question::Stroke(_, false)));
            }
            other => panic!("the ask records its two questions: {other:?}"),
        }

        begin_recording();
        assert_eq!(enter(&[swept], true), Role::Ask);
        assert_eq!(swept.letter(), sym("qsTea"));
        assert!(swept.in_family(&families));
        note_simulated();
        assert!(matches!(
            end_recording(),
            Recorded::Poisoned(Accessor::Letter)
        ));

        begin_recording();
        assert_eq!(enter(&[swept], true), Role::Ask);
        assert_ne!(swept, pea, "a comparison reads the swept token");
        note_simulated();
        assert!(matches!(
            end_recording(),
            Recorded::Poisoned(Accessor::Compare)
        ));

        begin_recording();
        assert_eq!(enter(&[swept], true), Role::Ask);
        note_answered_from_memo();
        note_simulated();
        assert!(
            matches!(end_recording(), Recorded::AnsweredFromMemo),
            "an ask a memo answered stands for no other token"
        );

        begin_recording();
        assert_eq!(enter(&[swept], true), Role::Ask);
        assert!(
            matches!(end_recording(), Recorded::NotSimulated),
            "an ask that did not take a simulated trace stands for no other token"
        );
    }
}
