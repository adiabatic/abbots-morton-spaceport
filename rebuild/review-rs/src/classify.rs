//! The after font's junctions as the drafter's replay reads them: `classify.JunctionClassifier.classify` over the curs sets the font facts carry ([`JunctionClassifier`]), and `shaping.row_for` with `junctions_for`, `last_glyph_covering` and `first_glyph_covering` ([`row_for`]).

use crate::fontfacts::{FontFacts, Junctions, Side};
use crate::pyfmt::PyValue;
use crate::pyfmt::repr::repr;
use crate::shape::{Features, ShapeError, Shaper};

/// The junction token of two adjacent glyphs that share no curs height.
pub const BREAK: &str = "break";

/// The junction token of a code point gap inside one glyph.
pub const LIGATURE: &str = "lig";

/// `classify.JunctionClassifier` over the after font's curs sets.
pub struct JunctionClassifier<'a> {
    junctions: &'a Junctions,
}

impl<'a> JunctionClassifier<'a> {
    /// The classifier over the after font's curs sets in `facts`.
    pub fn new(facts: &'a FontFacts) -> Self {
        Self {
            junctions: facts.junctions(),
        }
    }

    /// `classify`: the heights, ascending, at which `left` exits and `right` enters, each as `y<height>` and joined with `+`, or `break` when there is none. Glyphs are after-font glyph ids, which name glyphs one to one.
    pub fn classify(&self, left: u32, right: u32) -> String {
        let joined: Vec<String> = self
            .junctions
            .heights()
            .iter()
            .enumerate()
            .filter(|&(index, _)| {
                self.junctions.has_exit(index, left) && self.junctions.has_entry(index, right)
            })
            .map(|(_, height)| format!("y{height}"))
            .collect();
        if joined.is_empty() {
            BREAK.to_owned()
        } else {
            joined.join("+")
        }
    }
}

/// One shaped text as `rowmodel.Row` holds it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Row {
    pub codepoints: Vec<u32>,
    pub glyphs: Vec<String>,
    pub clusters: Vec<u32>,
    pub junctions: Vec<String>,
    pub positions: Vec<(i32, i32, i32)>,
}

/// `last_glyph_covering`: the last glyph whose cluster is at or before `position` while the clusters stay there, or the ValueError's message when the first glyph's cluster is past it.
pub fn last_glyph_covering(clusters: &[u32], position: usize) -> Result<usize, String> {
    let mut covering = None;
    for (index, &cluster) in clusters.iter().enumerate() {
        if cluster as usize <= position {
            covering = Some(index);
        } else {
            break;
        }
    }
    covering.ok_or_else(|| {
        let clusters = PyValue::Tuple(
            clusters
                .iter()
                .map(|&cluster| PyValue::Int(cluster.into()))
                .collect(),
        );
        format!(
            "no glyph covers input position {position} (clusters={})",
            repr(&clusters).expect("a tuple of ints has a repr")
        )
    })
}

/// `first_glyph_covering`: the first glyph of the cluster [`last_glyph_covering`] lands in.
pub fn first_glyph_covering(clusters: &[u32], position: usize) -> Result<usize, String> {
    let last = last_glyph_covering(clusters, position)?;
    let value = clusters[last];
    let mut first = last;
    while first > 0 && clusters[first - 1] == value {
        first -= 1;
    }
    Ok(first)
}

/// What [`row_for`] can fail with: a shaping failure, or the ValueError a covering raises.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum RowError {
    Shape(ShapeError),
    Value(String),
}

/// `row_for` with the after font: shape `text` under `features` and classify the gap after each code point but the last, `lig` when one glyph covers both sides and the classifier's token for the glyph covering the left and the first glyph covering the right otherwise.
pub fn row_for(
    shaper: &mut Shaper,
    facts: &FontFacts,
    text: &str,
    features: &Features,
) -> Result<Row, RowError> {
    let result = shaper
        .shape(Side::After, text, features)
        .map_err(RowError::Shape)?;
    let classifier = JunctionClassifier::new(facts);
    let codepoints: Vec<u32> = text.chars().map(u32::from).collect();
    let mut junctions = Vec::with_capacity(codepoints.len().saturating_sub(1));
    for k in 0..codepoints.len().saturating_sub(1) {
        let left = last_glyph_covering(&result.clusters, k).map_err(RowError::Value)?;
        let right_last = last_glyph_covering(&result.clusters, k + 1).map_err(RowError::Value)?;
        if left == right_last {
            junctions.push(LIGATURE.to_owned());
            continue;
        }
        let right = first_glyph_covering(&result.clusters, k + 1).map_err(RowError::Value)?;
        junctions.push(classifier.classify(result.glyphs[left], result.glyphs[right]));
    }
    Ok(Row {
        codepoints,
        glyphs: result
            .names(facts.font(Side::After))
            .map(str::to_owned)
            .collect(),
        clusters: result.clusters.clone(),
        junctions,
        positions: result.positions.clone(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    const KEY: &str = "00000000000000000000000000000000000000aa";

    fn facts() -> FontFacts {
        let text = format!(
            r#"{{"format":"ams-review-font-facts/1","fonts":{{"before":{{"glyphs":[]}},"after":{{"glyphs":[["a","{KEY}",0,0],["b","{KEY}",0,0],["c","{KEY}",0,0]],"exits":[[0,["a","b"]],[5,["a"]]],"entries":[[0,["b"]],[5,["b","c"]]]}},"junior":{{"glyphs":[]}}}},"shapes":{{"{KEY}":[true,[[0,0]]]}}}}"#
        );
        FontFacts::parse(&text).expect("facts")
    }

    #[test]
    fn a_pair_joins_at_every_shared_height_in_ascending_order_or_breaks() {
        let facts = facts();
        let classifier = JunctionClassifier::new(&facts);
        assert_eq!(classifier.classify(0, 1), "y0+y5");
        assert_eq!(classifier.classify(0, 2), "y5");
        assert_eq!(classifier.classify(1, 1), "y0");
        assert_eq!(classifier.classify(2, 1), "break");
    }

    #[test]
    fn a_covering_finds_the_cluster_a_position_falls_in() {
        let clusters = [0, 0, 2, 3, 3];
        assert_eq!(last_glyph_covering(&clusters, 0), Ok(1));
        assert_eq!(last_glyph_covering(&clusters, 1), Ok(1));
        assert_eq!(first_glyph_covering(&clusters, 1), Ok(0));
        assert_eq!(first_glyph_covering(&clusters, 4), Ok(3));
        assert_eq!(
            last_glyph_covering(&[], 0),
            Err("no glyph covers input position 0 (clusters=())".to_owned())
        );
        assert_eq!(
            last_glyph_covering(&[1], 0),
            Err("no glyph covers input position 0 (clusters=(1,))".to_owned())
        );
    }
}
