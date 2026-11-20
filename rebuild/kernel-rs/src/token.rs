//! One raw window slot to the right of a settled position: [`RightToken`]. Its representation is private to this module, so every read of a token goes through one of its methods.
//!
//! The methods fall into three groups. The questions ([`RightToken::kind`], [`RightToken::in_family`], [`RightToken::in_class`], [`RightToken::has_entry_stroke`]) answer one predicate about the token. [`RightToken::key_ordinal`] returns the token's identity for a memo key or a row of output, and `the_key_ordinal_callers_are_the_pinned_roster` in this module's tests pins the files that call it. The rune accessors ([`RightToken::rune`], [`RightToken::letter`], [`RightToken::ordinal`], [`RightToken::letter_ordinal`]) hand out the rune itself, for a caller that makes the token the rune or a slot of a nested enumeration, prospect, settlement or acceptor check, or that needs the rune for something no question covers.

use std::collections::BTreeSet;

use crate::index::{Ordinal, SpecIndex};
use crate::model::Sym;
use crate::types::TokenKind;

/// One raw window slot: a boundary, an unknown, or a letter naming its rune. `settle.RightToken`, with the same equality: the kind is part of the value, so `UNKNOWN` and `EDGE` are different tokens, and two letter tokens are equal when their runes are.
///
/// A letter also carries its rune's [`Ordinal`], because the memo keys store ordinals. A token created by [`SpecIndex::letter`] passes its ordinal to every key built from it, so building a key needs no rune lookup. Under the index that created it, the ordinal is a function of the name, so the derived equality is still equality of runes.
///
/// The derived ordering exists so a token can key a `BTreeMap`. It orders the boundaries and the unknown before every letter, and letters by interning order, which is the order the dump happened to mention names in. Where an output order depends on a token, as in the guard sweep's rows, sort by the resolved name instead. Equality, ordering, hashing and `Debug` are the representation's own, so a token hashes into a key and prints exactly as the representation does.
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct RightToken(Repr);

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
enum Repr {
    Edge,
    Space,
    Zwnj,
    NamerDot,
    Unknown,
    Letter(Sym, Ordinal),
}

impl std::fmt::Debug for RightToken {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        self.0.fmt(formatter)
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

    /// Which kind of slot this is.
    pub fn kind(self) -> TokenKind {
        match self.0 {
            Repr::Edge => TokenKind::Edge,
            Repr::Space => TokenKind::Space,
            Repr::Zwnj => TokenKind::Zwnj,
            Repr::NamerDot => TokenKind::NamerDot,
            Repr::Unknown => TokenKind::Unknown,
            Repr::Letter(_, _) => TokenKind::Letter,
        }
    }

    /// Whether this slot is a letter whose rune is one of `families`: a condition's `family:` list.
    pub fn in_family(self, families: &[Sym]) -> bool {
        match self.0 {
            Repr::Letter(rune, _) => families.contains(&rune),
            _ => false,
        }
    }

    /// Whether this slot is a letter whose rune is one of `members`: a class's membership as [`SpecIndex::class_members`] returns it, read by the caller so the class is journaled where it always is.
    pub fn in_class(self, members: &BTreeSet<Sym>) -> bool {
        match self.0 {
            Repr::Letter(rune, _) => members.contains(&rune),
            _ => false,
        }
    }

    /// Whether this slot is a letter whose rune declares an entry row with this stroke, through [`SpecIndex::entry_strokes`], which journals the rune.
    pub fn has_entry_stroke(self, index: &SpecIndex, stroke: Sym) -> bool {
        match self.0 {
            Repr::Letter(rune, _) => index.entry_strokes(rune).contains(&stroke),
            _ => false,
        }
    }

    /// The rune-field ordinal of the rune this slot names, or `None` when it is not a letter. This is the one accessor that returns a token's identity for building a memo key or writing a row of output, and nothing else may call it for any other purpose.
    pub fn key_ordinal(self) -> Option<Ordinal> {
        match self.0 {
            Repr::Letter(_, ordinal) => Some(ordinal),
            _ => None,
        }
    }

    /// The rune this slot names, or `None` when it is not a letter. `RightToken.rune`, for reads that have not yet checked the kind.
    pub fn rune(self) -> Option<Sym> {
        match self.0 {
            Repr::Letter(rune, _) => Some(rune),
            _ => None,
        }
    }

    /// The rune this slot names, for reads that have already checked it is a letter. Panics on any other kind, as `RightToken.letter` raises `ValueError`: reaching it means a caller skipped the kind check, which is a kernel bug and not a settlement outcome.
    pub fn letter(self) -> Sym {
        match self.0 {
            Repr::Letter(rune, _) => rune,
            _ => panic!("{} token has no rune", self.kind().as_str()),
        }
    }

    /// The rune-field ordinal of the rune this slot names, or `None` when it is not a letter: [`RightToken::rune`] in the form the memo keys store.
    pub fn ordinal(self) -> Option<Ordinal> {
        match self.0 {
            Repr::Letter(_, ordinal) => Some(ordinal),
            _ => None,
        }
    }

    /// The rune-field ordinal, for reads that have already checked it is a letter. Panics on any other kind, as [`RightToken::letter`] does.
    pub fn letter_ordinal(self) -> Ordinal {
        match self.0 {
            Repr::Letter(_, ordinal) => ordinal,
            _ => panic!("{} token has no rune", self.kind().as_str()),
        }
    }
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
    }
}
