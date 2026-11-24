//! The explain text a fragment ships: `ExplainReport.render` (`rebuild/pipeline/explain.py`) over the [`Report`] the settler gives, with `settle.cell_label` as the kernel's [`cell_label`] and `model.feature_config_token` ([`render`]), and the enricher's filter, which keeps the header and the divergent positions' blocks ([`filter_explain`]).

use std::fmt;

use ams_m1_kernel::index::SpecIndex;
use ams_m1_kernel::model::Sym;
use ams_m1_kernel::types::{cell_label, is_boundary_settled, provenance_pointer};

use crate::pyfmt::is_space;
use crate::settle::{ISOLATED_OVERLAY_STAGE, Report};

/// A rendered text the explain filter cannot read ([`filter_explain`]).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ExplainError(pub String);

impl fmt::Display for ExplainError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for ExplainError {}

type Result<T> = std::result::Result<T, ExplainError>;

fn error<T>(message: impl Into<String>) -> Result<T> {
    Err(ExplainError(message.into()))
}

/// What [`filter_explain`] splits the rendered text on: the blank line and the head of each position's block.
const BLOCK_SEPARATOR: &str = "\n\nposition ";

/// A configuration's token, `model.feature_config_token`: the features sorted and joined with `+`, or `default` for none.
pub fn feature_config_token(features: &[String]) -> String {
    let mut enabled: Vec<&str> = features.iter().map(String::as_str).collect();
    enabled.sort_unstable();
    if enabled.is_empty() {
        "default".to_owned()
    } else {
        enabled.join("+")
    }
}

/// A height as the explain text writes it, Python's `height or "none"`.
fn height_text(index: &SpecIndex, height: Option<Sym>) -> &str {
    height
        .map(|height| index.resolve(height))
        .filter(|text| !text.is_empty())
        .unwrap_or("none")
}

/// The explain text of one report, `ExplainReport.render`: the sequence and configuration, the settled labels, and one block per position, a boundary's or an overlay letter's one line and otherwise the ranked candidates, the eliminations with their provenance, the deciding stage, the joint tiebreak, the notes and the settled cell.
pub fn render(index: &SpecIndex, report: &Report) -> String {
    let vocab = index.vocab();
    let mut lines: Vec<String> = Vec::new();
    let sequence = report
        .codepoints
        .iter()
        .map(|value| format!("{value:04X}"))
        .collect::<Vec<_>>()
        .join(":");
    lines.push(format!(
        "sequence {sequence}   config {}",
        feature_config_token(&report.features)
    ));
    lines.push(format!(
        "settled: {}",
        report
            .traces
            .iter()
            .map(|trace| cell_label(index, &trace.settled.cell))
            .collect::<Vec<_>>()
            .join(" ")
    ));
    for (position, trace) in report.traces.iter().enumerate() {
        let settled = &trace.settled;
        let rune = index.resolve(settled.cell.rune);
        lines.push(String::new());
        lines.push(format!("position {position}: {rune}"));
        if is_boundary_settled(vocab, settled) {
            lines.push(
                if rune == "space" || rune == "zwnj" {
                    "  boundary token; splits run"
                } else {
                    "  boundary token; does not split the run"
                }
                .to_owned(),
            );
            continue;
        }
        if trace.decided_stage == ISOLATED_OVERLAY_STAGE {
            lines.push(format!(
                "  isolated overlay: the ss10 input substitution renders the letter as its anchor-free copy before formation, so nothing settles and both junctions break   settled: {}",
                cell_label(index, &settled.cell)
            ));
            continue;
        }
        lines.push(
            "  candidates (join-count = left junction + own junction + optimistic prospect):"
                .to_owned(),
        );
        let ranking = &trace.ranking;
        for ranked in &ranking.ranked {
            let candidate = &ranked.candidate;
            let marker = if (candidate.stance, candidate.entry, candidate.junction)
                == (settled.cell.stance, settled.cell.entry, settled.junction)
            {
                "->"
            } else {
                "  "
            };
            lines.push(format!(
                "  {marker} {:<16} entry={:<10} junction={:<10} join-count={} prospect={}",
                index.resolve(candidate.stance),
                height_text(index, candidate.entry),
                height_text(index, candidate.junction),
                ranked.join_count,
                ranked.prospect
            ));
        }
        if !ranking.eliminations.is_empty() {
            lines.push("  eliminated before ranking:".to_owned());
            for elimination in &ranking.eliminations {
                let source = elimination.provenance.as_ref().map_or(String::new(), |at| {
                    format!("  [{}]", provenance_pointer(index, at))
                });
                lines.push(format!(
                    "    - ({}) {}{source}",
                    elimination.stage.as_str(),
                    elimination.description
                ));
            }
        }
        let mut decided = format!("  decided by: {}", trace.decided_stage);
        if let Some(runner) = &ranking.runner_up {
            decided.push_str(&format!(
                " (over {} entry={} junction={})",
                index.resolve(runner.stance),
                height_text(index, runner.entry),
                height_text(index, runner.junction)
            ));
        }
        lines.push(decided);
        if trace.joint_tiebreak {
            lines.push(
                "  joint: the final tiebreak broke a realization tie — routed to the expensive test tier"
                    .to_owned(),
            );
        }
        for note in &trace.notes {
            lines.push(format!("  note: {note}"));
        }
        lines.push(format!(
            "  settled: {}   junction={}   extension={}",
            cell_label(index, &settled.cell),
            height_text(index, settled.junction),
            settled.extension
        ));
    }
    lines.join("\n")
}

/// Python's `int` over a token `str.split` gave: an optional sign and decimal digits, single underscores allowed between digits. Python also reads non-ASCII decimal digits, which no rendered position index holds, so a token of them is refused here rather than read.
pub(crate) fn python_int(token: &str) -> Option<i64> {
    let (negative, digits) = match token.as_bytes().first() {
        Some(b'-') => (true, &token[1..]),
        Some(b'+') => (false, &token[1..]),
        _ => (false, token),
    };
    if digits.is_empty()
        || digits.starts_with('_')
        || digits.ends_with('_')
        || digits.contains("__")
        || !digits
            .bytes()
            .all(|byte| byte.is_ascii_digit() || byte == b'_')
    {
        return None;
    }
    let value: i64 = digits.replace('_', "").parse().ok()?;
    Some(if negative { -value } else { value })
}

/// The explain text a fragment ships: the rendered text's header and only the blocks of the divergent positions, or every block when there are none. A block's position is read as Python reads it, the first whitespace-separated word before the block's first colon, and a block whose word is no int is refused, as Python's `int` raises.
pub fn filter_explain(rendered: &str, diff_positions: &[i64]) -> Result<String> {
    let blocks: Vec<&str> = rendered.split(BLOCK_SEPARATOR).collect();
    if blocks.len() == 1 {
        return Ok(blocks[0].to_owned());
    }
    let mut kept = vec![blocks[0]];
    for block in &blocks[1..] {
        let head = block.split_once(':').map_or(*block, |(head, _)| head);
        let word = head.split(is_space).find(|word| !word.is_empty());
        let Some(index) = word.and_then(python_int) else {
            return error(format!(
                "the explain block {:?} names no position Python's int reads",
                block.chars().take(80).collect::<String>()
            ));
        };
        if diff_positions.is_empty() || diff_positions.contains(&index) {
            kept.push(block);
        }
    }
    Ok(kept.join(BLOCK_SEPARATOR))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::settle::tests::settlement;
    use crate::settle::{Settler, features_for_config};

    #[test]
    fn a_configuration_token_sorts_its_features_and_names_none_default() {
        assert_eq!(feature_config_token(&[]), "default");
        assert_eq!(
            feature_config_token(&["ss05".to_owned(), "ss03".to_owned()]),
            "ss03+ss05"
        );
    }

    #[test]
    fn a_settled_text_renders_each_position_and_each_boundary_kind() {
        let settlement = settlement();
        let mut settler = Settler::new(&settlement);
        let report = settler
            .explain(&[0xE650, 0xE652, 0x20, 0xE650, 0xB7], &[])
            .expect("settles");
        let candidate = "  candidates (join-count = left junction + own junction + optimistic prospect):\n  -> bare             entry=none       junction=none       join-count=0 prospect=0\n  decided by: only-candidate\n";
        assert_eq!(
            render(settlement.index(), &report),
            format!(
                "sequence E650:E652:0020:E650:00B7   config default\nsettled: qsPea_qsTea.bare space qsPea.bare periodcentered\n\nposition 0: qsPea_qsTea\n{candidate}  settled: qsPea_qsTea.bare   junction=none   extension=0\n\nposition 1: space\n  boundary token; splits run\n\nposition 2: qsPea\n{candidate}  settled: qsPea.bare   junction=none   extension=0\n\nposition 3: namer-dot\n  boundary token; does not split the run"
            )
        );
    }

    #[test]
    fn an_overlay_letter_renders_as_one_line() {
        let settlement = settlement();
        let mut settler = Settler::new(&settlement);
        let report = settler
            .explain(&[0xE650], &features_for_config("ss10"))
            .expect("the overlay");
        assert_eq!(
            render(settlement.index(), &report),
            "sequence E650   config ss10\nsettled: qsPea.bare\n\nposition 0: qsPea\n  isolated overlay: the ss10 input substitution renders the letter as its anchor-free copy before formation, so nothing settles and both junctions break   settled: qsPea.bare"
        );
    }

    #[test]
    fn the_filter_keeps_the_header_and_the_divergent_blocks_or_every_block() {
        let rendered = "head\nsettled: a b c\n\nposition 0: a\n  x\n\nposition 1: b\n  y\n\nposition 2: c\n  z";
        assert_eq!(
            filter_explain(rendered, &[1]).expect("filters"),
            "head\nsettled: a b c\n\nposition 1: b\n  y"
        );
        assert_eq!(
            filter_explain(rendered, &[0, 2, 7]).expect("filters"),
            "head\nsettled: a b c\n\nposition 0: a\n  x\n\nposition 2: c\n  z"
        );
        assert_eq!(filter_explain(rendered, &[]).expect("filters"), rendered);
        assert_eq!(filter_explain("head", &[3]).expect("filters"), "head");
    }

    #[test]
    fn a_block_position_reads_as_pythons_int_and_anything_else_is_refused() {
        assert_eq!(python_int("12"), Some(12));
        assert_eq!(python_int("+1_0"), Some(10));
        assert_eq!(python_int("-3"), Some(-3));
        for token in ["", "-", "_1", "1_", "1__0", "x", "\u{0661}"] {
            assert_eq!(python_int(token), None, "{token:?}");
        }
        assert_eq!(
            filter_explain("head\n\nposition \u{3000}4\u{3000}x: a", &[4]).expect("filters"),
            "head\n\nposition \u{3000}4\u{3000}x: a"
        );
        assert!(filter_explain("head\n\nposition x: a", &[]).is_err());
    }
}
