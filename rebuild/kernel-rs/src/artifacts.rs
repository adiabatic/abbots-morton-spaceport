//! The four things one configuration's fold leaves behind: `settlement-<config>.tsv`, `treaties-<config>.tsv`, the windows enumeration and the contract digest. Every separator, every `-` standing for an absent slot and every ordering here was transcribed from `rebuild/pipeline/table.py`'s writers rather than reinvented, and byte-identity of these files across that move was the whole proof standard. Two of those writers are still there, no longer as the build's path but as the second implementation `rebuild/test_windows.py` holds these bytes to: it reads an artifact back through `table.read_windows` and `table.read_treaty_tsv`, writes it out again through `DecisionTable.write_tsv` and `TreatyTable.write_tsv`, and requires the same bytes and the same `table.table_digest`.
//!
//! The windows payload is written **uncompressed**. `run_m1._pack_windows` gzips it into `windows-<config>.tsv.gz` with a zeroed stamp, which keeps the compressor on the side of the boundary that already owns it — this crate carries serde_json and nothing else, as the transitions stream's own note says — and keeps the artifact's identity claim on the decompressed bytes.
//!
//! The digest is not a file. It is one scalar per configuration, reported on stdout for the caller to hold in acceptance order, rather than a second per-configuration artifact family that nothing else reads and that a stale copy could poison.

use std::collections::HashSet;
use std::fmt::Write as _;
use std::io::Write as _;
use std::path::Path;
use std::rc::Rc;

use crate::emit::{escape_into, json_string};
use crate::fold::{DecisionTable, Rule, TreatyTable};
use crate::index::SpecIndex;
use crate::sha256;
use crate::stream::{TransitionRow, cell_key};
use crate::types::{CellId, adjustment_text};

/// The marker the windows head line carries, `table.WINDOWS_FORMAT`.
pub const WINDOWS_FORMAT: &str = "ams-m1-windows/2";

/// The column line the windows body is introduced by, `table.WINDOWS_COLUMNS`.
const WINDOWS_COLUMNS: [&str; 7] = [
    "input",
    "left",
    "lookahead1",
    "lookahead2",
    "lookahead3",
    "lookahead4",
    "outcome",
];

/// One slot as the TSV and the digest spell it: the members joined by spaces, and `-` for a slot the rule leaves unconstrained. An empty class spells `-` too, which is what Python's truthiness test on the tuple does.
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

/// One rule's provenance as the TSV and the digest spell it: the pointers joined by `; `, empties dropped and repeats collapsed in first-seen order.
fn provenance_text(rule: &Rule) -> String {
    let mut seen: Vec<&str> = Vec::new();
    for pointer in &rule.provenance {
        if !pointer.is_empty() && !seen.contains(&pointer.as_str()) {
            seen.push(pointer);
        }
    }
    seen.join("; ")
}

/// The nine tab-separated fields of one settlement row, which the TSV and the digest share verbatim.
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

/// The column line every settlement TSV carries after its config comment, which [`read_settlement_tsv`] holds a file to before reading a row.
const SETTLEMENT_COLUMNS: &str =
    "input\tbacktrack\tlookahead1\tlookahead2\tlookahead3\tlookahead4\toutcome\tjoint\tprovenance";

/// [`settlement_tsv`]'s inverse: the ordered rules a persisted `settlement-<config>.tsv` spells, in file order, which is the emission order the shipped GSUB carries. The string replay ([`crate::replay`]) reads a build's rules back through this rather than re-costing the fixpoint they fold from, so the reader is held to the writer by a round trip over the fixture's own tables and refuses anything the writer would not have written — a missing config comment, a column line that is not the one above, a row that is not nine fields. `-` reads back as the unconstrained slot the writer spells it for, so a rule whose class was empty reads back unconstrained as well: the two are one slot to a first-match replay, because a rule matching no label at a slot never wins a window and the fold refuses a rule no row first-matches.
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

/// One slot's members as [`slot_text`] spelled them, `None` for the `-` that stands for an unconstrained slot.
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

/// The cells of one table in `table._cell_key` order, which the windows head and the digest both spell them in. Deduplicated, because `DecisionTable._cells` is a `frozenset` and neither writer may spell one cell twice.
fn sorted_cells<'a>(index: &SpecIndex, cells: &'a [CellId]) -> Vec<&'a CellId> {
    let mut seated: Vec<(crate::stream::CellKey, &CellId)> = cells
        .iter()
        .map(|cell| (cell_key(index, cell), cell))
        .collect();
    seated.sort_by(|left, right| left.0.cmp(&right.0));
    // A whole-list dedup rather than the adjacent-only one, for the reason `stream::write_transitions` gives beside its own: equal cells always sort together, but the sort key is the label view, so only injectivity of `_cell_key` would make adjacency sufficient.
    let mut counted: HashSet<&CellId> = HashSet::new();
    seated.retain(|(_, cell)| counted.insert(*cell));
    seated.into_iter().map(|(_, cell)| cell).collect()
}

/// `write_windows`' payload, uncompressed: the head line carrying the fingerprint of the sources the table was built from, then the column line, then one row per enumerated window.
///
/// The head's keys ride in the order Python's dict literal inserts them — `config`, `inputs`, `identity_guard_rules`, `cited_provenance`, `cells`, `deep_classes`, `rules` — with the set-valued ones sorted here rather than by whoever produced them, and `certificates` after them: one token list per rule, in rule order, the realizing strings [`crate::certificate`] closed, which `run_m1`'s witness stage settles and which stay outside both digests because they are evidence about the rules rather than part of what the rules say.
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
        for label in row.key() {
            line.push_str(label);
            line.push('\t');
        }
        line.push_str(&row.outcome);
        line.push('\n');
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

/// One cell as the windows head spells it, the row `table.read_windows` parses back: the rune, the stance, the two heights with an absent side spelled `null`, and the adjustment tokens.
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

/// One rule as the windows head spells it, `table._rule_row`: the input, the five slots as member arrays or `null`, the outcome, the provenance verbatim, and the joint flag.
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

/// `table.table_digest`: the one scalar saying whether two builds of one configuration agree at full contract grain — the ordered rules with their provenance and joint flags, every enumerated window row as stored, the treaty rows, the reachable cells, the cited provenance and the identity-guard count.
///
/// The cells section is the one place the digest reads Python's own reprs rather than a tab-joined text: an absent height is the string `None` and the adjustments are a tuple repr, because the Python original interpolates the dataclass fields straight into an f-string.
pub fn table_digest(index: &SpecIndex, decision: &DecisionTable, treaty: &TreatyTable) -> String {
    let mut message: Vec<u8> = Vec::new();
    message.extend_from_slice(format!("config\t{}\n", decision.config).as_bytes());
    for rule in &decision.rules {
        message.extend_from_slice(rule_line(rule).as_bytes());
        message.push(b'\n');
    }
    message.extend_from_slice(b"--windows--\n");
    for row in &decision.transitions {
        message.extend_from_slice(window_line(row).as_bytes());
        message.push(b'\n');
    }
    message.extend_from_slice(b"--treaty--\n");
    for row in &treaty.rows {
        let _ = writeln!(
            &mut message,
            "{}\t{}\t{}\t{}\t{}",
            row.left, row.right, row.junction, row.extension, row.kern
        );
    }
    message.extend_from_slice(b"--cells--\n");
    for cell in sorted_cells(index, &decision.cells) {
        let adjustments: Vec<String> = cell
            .adjustments
            .iter()
            .map(|token| crate::stream::python_repr(&adjustment_text(index, *token)))
            .collect();
        let _ = writeln!(
            &mut message,
            "{}\t{}\t{}\t{}\t{}",
            index.resolve(cell.rune),
            index.resolve(cell.stance),
            cell.entry.map_or("None", |height| index.resolve(height)),
            cell.exit.map_or("None", |height| index.resolve(height)),
            crate::stream::python_tuple(&adjustments)
        );
    }
    message.extend_from_slice(b"--provenance--\n");
    let mut cited: Vec<&str> = decision
        .cited_provenance
        .iter()
        .map(String::as_str)
        .collect();
    cited.sort_unstable();
    cited.dedup();
    for pointer in cited {
        message.extend_from_slice(pointer.as_bytes());
        message.push(b'\n');
    }
    let _ = writeln!(
        &mut message,
        "--guards--\t{}",
        decision.identity_guard_rules
    );
    sha256::digest_hex(&message)
}

/// One window row's seven tab-separated labels, which the windows body and the digest share.
fn window_line(row: &TransitionRow) -> String {
    let mut out = String::new();
    for label in row.key() {
        out.push_str(label);
        out.push('\t');
    }
    out.push_str(&row.outcome);
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::index::fixtures;

    /// The reader is the writer's inverse over a real fold: every rule of the fixture's table comes back as it went out, in order, and the bytes it writes again are the bytes it read.
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

    /// What the reader refuses: a file with no config comment, one whose column line is not the writer's, and a row short of its nine fields.
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

    /// The cell vocabulary the windows head and the digest share is `DecisionTable._cells`, a frozenset: sorted into `_cell_key` order and spelling a repeated cell once.
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
