//! Produces the four outputs of one configuration's fold: `settlement-<config>.tsv`, `treaties-<config>.tsv`, the windows enumeration, and the contract digest ([`table_digest`]). Separators, the `-` for an absent slot, and every ordering match the writers and readers in `rebuild/pipeline/table.py`. `rebuild/test_windows.py` reads a built artifact back through `table.read_windows` and `table.read_treaty_tsv`, writes it again with `DecisionTable.write_tsv` and `TreatyTable.write_tsv`, and requires the same bytes and the same `table.table_digest`.
//!
//! The windows payload is written uncompressed. `run_m1._pack_windows` gzips it into `windows-<config>.tsv.gz` with a zeroed timestamp. This keeps the compressor out of the crate, whose only dependency is serde_json, and makes the decompressed bytes the artifact's identity.
//!
//! The digest is not written to a file. `build-tables` prints it on stdout as one JSON line per configuration, and the caller keeps it. No other step reads it, and a file would be one more copy that could go stale.
//!
//! [`table_digest`] feeds SHA-256 section by section and reuses one line buffer for the window, treaty, and cell rows, so its memory does not grow with the number of windows. Writing and hashing the windows share the row formatter but are separate passes.

use std::fmt::Write as _;
use std::io::Write as _;
use std::path::Path;
use std::rc::Rc;

use crate::emit::{escape_into, json_string};
use crate::fold::{DecisionTable, Rule, TreatyTable};
use crate::hash::HashSet;
use crate::index::SpecIndex;
use crate::sha256;
use crate::stream::{TransitionRow, cell_key};
use crate::types::{CellId, adjustment_text};

/// The marker the windows head line carries, `table.WINDOWS_FORMAT`.
pub const WINDOWS_FORMAT: &str = "ams-m1-windows/2";

/// The column line that precedes the window rows, `table.WINDOWS_COLUMNS`.
const WINDOWS_COLUMNS: [&str; 7] = [
    "input",
    "left",
    "lookahead1",
    "lookahead2",
    "lookahead3",
    "lookahead4",
    "outcome",
];

/// One slot as the TSV and the digest write it: the members joined by spaces, or `-` for a slot the rule leaves unconstrained. An empty class is also written `-`, as Python's truthiness test on the tuple does.
fn slot_text(slot: &Option<Vec<Rc<str>>>) -> String {
    match slot {
        Some(members) if !members.is_empty() => members
            .iter()
            .map(|member| &**member)
            .collect::<Vec<&str>>()
            .join(" "),
        _ => "-".to_owned(),
    }
}

/// One rule's provenance as the TSV and the digest write it: the pointers joined by `; `, with empty pointers dropped and repeats removed in first-seen order.
fn provenance_text(rule: &Rule) -> String {
    let mut seen: Vec<&str> = Vec::new();
    for pointer in &rule.provenance {
        if !pointer.is_empty() && !seen.contains(&pointer.as_str()) {
            seen.push(pointer);
        }
    }
    seen.join("; ")
}

/// The nine tab-separated fields of one settlement row, shared by the TSV and the digest.
fn rule_line(rule: &Rule) -> String {
    [
        (*rule.input_glyph).to_owned(),
        slot_text(&rule.backtrack),
        slot_text(&rule.look1),
        slot_text(&rule.look2),
        slot_text(&rule.look3),
        slot_text(&rule.look4),
        (*rule.outcome).to_owned(),
        if rule.joint { "joint" } else { "-" }.to_owned(),
        provenance_text(rule),
    ]
    .join("\t")
}

/// `DecisionTable.write_tsv`: the config comment, the column line, then one line per rule in emission order.
pub fn settlement_tsv(decision: &DecisionTable) -> String {
    let mut out = format!("# settlement table, config {}\n", decision.config);
    out.push_str(
        "input\tbacktrack\tlookahead1\tlookahead2\tlookahead3\tlookahead4\toutcome\tjoint\tprovenance\n",
    );
    for rule in &decision.rules {
        out.push_str(&rule_line(rule));
        out.push('\n');
    }
    out
}

/// The column line that follows the config comment in every settlement TSV. [`read_settlement_tsv`] requires it.
const SETTLEMENT_COLUMNS: &str =
    "input\tbacktrack\tlookahead1\tlookahead2\tlookahead3\tlookahead4\toutcome\tjoint\tprovenance";

/// The inverse of [`settlement_tsv`]: the rules of a persisted `settlement-<config>.tsv` in file order, which is the emission order of the shipped GSUB. The string replay ([`crate::replay`], through `fanout::run_config_replay`) and the `replay-emitted` walk read a build's rules through this instead of recomputing the fixpoint. A round-trip test over the fixture's tables checks it against the writer. It fails on input the writer would not produce: a missing config comment, a different column line, a row that is not nine fields, or a joint flag other than `joint` or `-`.
///
/// A `-` reads back as an unconstrained slot, so a rule whose class was empty would read back unconstrained. A folded table has no such rule: it would match no window, and the fold fails on a rule that no row first-matches.
pub fn read_settlement_tsv(text: &str) -> Result<Vec<Rule>, String> {
    let mut lines = text.lines();
    match lines.next() {
        Some(head) if head.starts_with("# settlement table, config ") => {}
        _ => return Err("not a settlement table: no config comment on the first line".to_owned()),
    }
    if lines.next() != Some(SETTLEMENT_COLUMNS) {
        return Err("not a settlement table: the second line is not the column line".to_owned());
    }
    let mut rules: Vec<Rule> = Vec::new();
    for (seat, line) in lines.enumerate() {
        let fields: Vec<&str> = line.split('\t').collect();
        let [
            input,
            backtrack,
            look1,
            look2,
            look3,
            look4,
            outcome,
            joint,
            provenance,
        ] = fields.as_slice()
        else {
            return Err(format!(
                "settlement row {} has {} tab-separated fields, expected 9",
                seat + 1,
                fields.len()
            ));
        };
        let joint = match *joint {
            "joint" => true,
            "-" => false,
            other => {
                return Err(format!(
                    "settlement row {} has joint flag {other:?}, expected joint or -",
                    seat + 1
                ));
            }
        };
        rules.push(Rule {
            input_glyph: Rc::from(*input),
            backtrack: slot_members(backtrack),
            look1: slot_members(look1),
            look2: slot_members(look2),
            look3: slot_members(look3),
            look4: slot_members(look4),
            outcome: Rc::from(*outcome),
            provenance: provenance
                .split("; ")
                .filter(|pointer| !pointer.is_empty())
                .map(str::to_owned)
                .collect(),
            joint,
        });
    }
    Ok(rules)
}

/// One slot's members as [`slot_text`] wrote them, or `None` for the `-` of an unconstrained slot.
fn slot_members(text: &str) -> Option<Vec<Rc<str>>> {
    if text == "-" {
        return None;
    }
    Some(text.split(' ').map(Rc::from).collect())
}

/// `TreatyTable.write_tsv`.
pub fn treaty_tsv(treaty: &TreatyTable) -> String {
    let mut out = format!("# treaty table, config {}\n", treaty.config);
    out.push_str("left\tright\tjunction\textension\tkern\n");
    for row in &treaty.rows {
        let _ = writeln!(
            out,
            "{}\t{}\t{}\t{}\t{}",
            row.left, row.right, row.junction, row.extension, row.kern
        );
    }
    out
}

/// The cells of one table, sorted by `table._cell_key` and deduplicated, as the windows head and the digest both list them. The deduplication matches `DecisionTable._cells`, which is a `frozenset`.
fn sorted_cells<'a>(index: &SpecIndex, cells: &'a [CellId]) -> Vec<&'a CellId> {
    let mut seated: Vec<(crate::stream::CellKey, &CellId)> = cells
        .iter()
        .map(|cell| (cell_key(index, cell), cell))
        .collect();
    seated.sort_by(|left, right| left.0.cmp(&right.0));
    // Deduplicate across the whole list, as `stream::write_transitions` does. The sort key is the label view, and an adjacent-only dedup would be correct only if `_cell_key` were injective, which this code does not assume.
    let mut counted: HashSet<&CellId> = HashSet::default();
    seated.retain(|(_, cell)| counted.insert(*cell));
    seated.into_iter().map(|(_, cell)| cell).collect()
}

/// Writes the uncompressed windows payload to `path`: a head line with the format marker and a JSON head, the column line, then one row per enumerated window.
///
/// The head's keys, in order, are `config`, `inputs` (the fingerprint of the sources the table was built from), `identity_guard_rules`, `cited_provenance`, `cells`, `deep_classes`, `rules`, and `certificates`. The set-valued ones are sorted here. `table.read_windows` reads the keys by name. `certificates` holds one token list per rule, in rule order: the strings from [`crate::certificate`] that should make each rule fire, which `run_m1`'s witness stage settles. They are left out of both `table.table_digest` and `table.windows_digest` because they are evidence about the rules, not part of the rules.
pub fn write_windows(
    index: &SpecIndex,
    decision: &DecisionTable,
    inputs: &str,
    path: &Path,
) -> Result<(), std::io::Error> {
    let file = std::fs::File::create(path)?;
    let mut out = std::io::BufWriter::with_capacity(1 << 20, file);
    let mut line = String::new();
    line.push_str("# ");
    line.push_str(WINDOWS_FORMAT);
    line.push('\t');
    head_into(&mut line, index, decision, inputs);
    line.push('\n');
    line.push_str(&WINDOWS_COLUMNS.join("\t"));
    line.push('\n');
    out.write_all(line.as_bytes())?;
    for row in &decision.transitions {
        line.clear();
        window_line_into(&mut line, row, decision);
        out.write_all(line.as_bytes())?;
    }
    out.flush()
}

fn head_into(out: &mut String, index: &SpecIndex, decision: &DecisionTable, inputs: &str) {
    let mut cited: Vec<&str> = decision
        .cited_provenance
        .iter()
        .map(String::as_str)
        .collect();
    cited.sort_unstable();
    cited.dedup();
    let cited: Vec<String> = cited.iter().map(|pointer| json_string(pointer)).collect();
    let cells: Vec<String> = sorted_cells(index, &decision.cells)
        .into_iter()
        .map(|cell| cell_json(index, cell))
        .collect();
    let mut classes: Vec<&(String, Vec<String>)> = decision.deep_classes.iter().collect();
    classes.sort();
    let classes: Vec<String> = classes
        .iter()
        .map(|(token, members)| {
            let quoted: Vec<String> = members.iter().map(|member| json_string(member)).collect();
            format!("[{},[{}]]", json_string(token), quoted.join(","))
        })
        .collect();
    let rules: Vec<String> = decision.rules.iter().map(rule_json).collect();
    let certificates: Vec<String> = decision
        .certificates
        .iter()
        .map(|tokens| {
            let quoted: Vec<String> = tokens.iter().map(|token| json_string(token)).collect();
            format!("[{}]", quoted.join(","))
        })
        .collect();
    let _ = write!(
        out,
        "{{\"config\":{},\"inputs\":{},\"identity_guard_rules\":{},\"cited_provenance\":[{}],\"cells\":[{}],\"deep_classes\":[{}],\"rules\":[{}],\"certificates\":[{}]}}",
        json_string(&decision.config),
        json_string(inputs),
        decision.identity_guard_rules,
        cited.join(","),
        cells.join(","),
        classes.join(","),
        rules.join(","),
        certificates.join(",")
    );
}

/// One cell as the windows head writes it and `table.read_windows` reads it: the rune, the stance, the two heights with `null` for an absent side, and the adjustment tokens.
fn cell_json(index: &SpecIndex, cell: &CellId) -> String {
    let adjustments: Vec<String> = cell
        .adjustments
        .iter()
        .map(|token| json_string(&adjustment_text(index, *token)))
        .collect();
    format!(
        "[{},{},{},{},[{}]]",
        json_string(index.resolve(cell.rune)),
        json_string(index.resolve(cell.stance)),
        cell.entry.map_or_else(
            || "null".to_owned(),
            |height| json_string(index.resolve(height))
        ),
        cell.exit.map_or_else(
            || "null".to_owned(),
            |height| json_string(index.resolve(height))
        ),
        adjustments.join(",")
    )
}

/// One rule as the windows head writes it, matching `table._rule_row`: the input, the five slots as member arrays or `null`, the outcome, the provenance unchanged, and the joint flag.
fn rule_json(rule: &Rule) -> String {
    let mut out = String::new();
    out.push('[');
    escape_into(&mut out, &rule.input_glyph);
    for slot in [
        &rule.backtrack,
        &rule.look1,
        &rule.look2,
        &rule.look3,
        &rule.look4,
    ] {
        out.push(',');
        match slot {
            None => out.push_str("null"),
            Some(members) => {
                out.push('[');
                for (seat, member) in members.iter().enumerate() {
                    if seat > 0 {
                        out.push(',');
                    }
                    escape_into(&mut out, member);
                }
                out.push(']');
            }
        }
    }
    out.push(',');
    escape_into(&mut out, &rule.outcome);
    out.push_str(",[");
    for (seat, pointer) in rule.provenance.iter().enumerate() {
        if seat > 0 {
            out.push(',');
        }
        escape_into(&mut out, pointer);
    }
    out.push(']');
    out.push_str(if rule.joint { ",true]" } else { ",false]" });
    out
}

/// `table.table_digest`: one hash that shows whether two builds of one configuration agree on the ordered rules with their provenance and joint flags, every enumerated window row, the treaty rows, the reachable cells, the cited provenance, and the identity-guard count.
///
/// Only the cells section uses Python reprs instead of tab-joined text: an absent height is the string `None` and the adjustments are a tuple repr, because the Python function interpolates the dataclass fields into an f-string.
pub fn table_digest(index: &SpecIndex, decision: &DecisionTable, treaty: &TreatyTable) -> String {
    let mut digest = sha256::Sha256::new();
    let mut line = String::new();
    let _ = writeln!(&mut line, "config\t{}", decision.config);
    digest.update(line.as_bytes());
    for rule in &decision.rules {
        digest.update(rule_line(rule).as_bytes());
        digest.update(b"\n");
    }
    digest.update(b"--windows--\n");
    for row in &decision.transitions {
        line.clear();
        window_line_into(&mut line, row, decision);
        digest.update(line.as_bytes());
    }
    digest.update(b"--treaty--\n");
    for row in &treaty.rows {
        line.clear();
        let _ = writeln!(
            &mut line,
            "{}\t{}\t{}\t{}\t{}",
            row.left, row.right, row.junction, row.extension, row.kern
        );
        digest.update(line.as_bytes());
    }
    digest.update(b"--cells--\n");
    for cell in sorted_cells(index, &decision.cells) {
        let adjustments: Vec<String> = cell
            .adjustments
            .iter()
            .map(|token| crate::stream::python_repr(&adjustment_text(index, *token)))
            .collect();
        line.clear();
        let _ = writeln!(
            &mut line,
            "{}\t{}\t{}\t{}\t{}",
            index.resolve(cell.rune),
            index.resolve(cell.stance),
            cell.entry.map_or("None", |height| index.resolve(height)),
            cell.exit.map_or("None", |height| index.resolve(height)),
            crate::stream::python_tuple(&adjustments)
        );
        digest.update(line.as_bytes());
    }
    digest.update(b"--provenance--\n");
    let mut cited: Vec<&str> = decision
        .cited_provenance
        .iter()
        .map(String::as_str)
        .collect();
    cited.sort_unstable();
    cited.dedup();
    for pointer in cited {
        digest.update(pointer.as_bytes());
        digest.update(b"\n");
    }
    line.clear();
    let _ = writeln!(&mut line, "--guards--\t{}", decision.identity_guard_rules);
    digest.update(line.as_bytes());
    digest.finish()
}

/// One window row as seven tab-separated fields (six labels and the outcome) plus a newline, shared by the windows body and the digest.
fn window_line_into(out: &mut String, row: &TransitionRow, decision: &DecisionTable) {
    for label in row.key(&decision.labels) {
        out.push_str(label);
        out.push('\t');
    }
    out.push_str(decision.outcome(row));
    out.push('\n');
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::index::fixtures;

    /// The reader inverts the writer on a real fold: every rule of the fixture's table reads back unchanged and in order, and writing the rules again gives the same bytes.
    #[test]
    fn a_settlement_table_reads_back_into_the_rules_that_wrote_it() {
        use crate::fixpoint::{EnumerationModes, enumerate_transitions};
        use crate::fold::fold_product;

        let index = fixtures::mini();
        let product = enumerate_transitions(&index, &[], EnumerationModes::default())
            .expect("the fixture's fixpoint closes");
        let folded = fold_product(&index, product).expect("and folds");
        let text = settlement_tsv(&folded.decision);
        let rules = read_settlement_tsv(&text).expect("the writer's own bytes read back");
        assert_eq!(rules, folded.decision.rules);
        let again = DecisionTable {
            rules,
            ..folded.decision
        };
        assert_eq!(settlement_tsv(&again), text);
    }

    /// The reader fails on a file with no config comment, on a column line that differs from the writer's, and on a row with fewer than nine fields.
    #[test]
    fn a_settlement_table_the_writer_could_not_have_written_is_refused() {
        let good = "# settlement table, config default\n".to_owned()
            + SETTLEMENT_COLUMNS
            + "\nqsPea\t-\tqsTea qsIt\t-\t-\t-\tqsPea.half\t-\ta:b\n";
        let rules = read_settlement_tsv(&good).expect("one rule");
        assert_eq!(rules.len(), 1);
        assert_eq!(rules[0].backtrack, None);
        assert_eq!(
            rules[0].look1,
            Some(vec![Rc::from("qsTea"), Rc::from("qsIt")])
        );
        assert_eq!(rules[0].provenance, vec!["a:b".to_owned()]);
        assert!(!rules[0].joint);
        assert!(read_settlement_tsv("input\tbacktrack\n").is_err());
        assert!(read_settlement_tsv("# settlement table, config default\ninput\n").is_err());
        let short = "# settlement table, config default\n".to_owned()
            + SETTLEMENT_COLUMNS
            + "\nqsPea\t-\t-\n";
        assert!(read_settlement_tsv(&short).unwrap_err().contains("row 1"));
    }

    /// The cell list that the windows head and the digest share matches `DecisionTable._cells`, a frozenset: it is sorted into `_cell_key` order and lists a repeated cell once.
    #[test]
    fn the_cell_vocabulary_is_sorted_and_spells_a_cell_once() {
        let index = fixtures::mini();
        let pea = CellId {
            rune: fixtures::sym(&index, "qsPea"),
            stance: fixtures::sym(&index, "half"),
            entry: None,
            exit: Some(fixtures::sym(&index, "baseline")),
            adjustments: Vec::new(),
        };
        let tea = CellId {
            rune: fixtures::sym(&index, "qsTea"),
            stance: fixtures::sym(&index, "half"),
            entry: None,
            exit: None,
            adjustments: Vec::new(),
        };
        let counted = [tea.clone(), pea.clone(), pea.clone()];
        let spelled = sorted_cells(&index, &counted);
        assert_eq!(spelled, [&pea, &tea]);
    }
}
