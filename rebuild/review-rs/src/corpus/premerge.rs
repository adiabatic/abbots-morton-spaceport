//! The pre-merge snapshot the review facts are derived from, as `facts.PremergeSnapshot` holds it. [`PremergeSnapshot::from_columns`] takes the stream's pre-merge units' columns in load order ([`PremergeColumns`]) beside the merged table, and [`PremergeSnapshot::rebase`] records, from the merge's [`Compaction`], each pre-merge unit's survivor's row and whether the merge removed it. The class and config-set ids name the table's string table and tuple pool, and the window offsets its value column, so every read of the snapshot takes the table it was captured beside.
//!
//! [`stage_premerge`] is the `premerge` stage of the stage fixtures.

use std::collections::BTreeSet;
use std::fmt;
use std::io::Write;
use std::path::Path;

use super::audit::stage_line;
use super::input::BuildInput;
use super::table::{
    ColumnDigest, Compaction, PremergeColumns, StreamedLoad, UNIT_PREFIX, UnitTable, count_value,
    dump_stream, int, stage_units,
};
use crate::pyfmt::PyValue;
use crate::wire::{Result, WireError};

/// What [`PremergeSnapshot::rebase`] refuses (`PremergeSnapshot.rebase`'s `ValueError`): a compaction over another count of rows than the snapshot captured.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct UnevenCompaction {
    pub compacted: usize,
    pub captured: usize,
}

impl fmt::Display for UnevenCompaction {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            formatter,
            "a compaction over {} rows, against {} captured",
            self.compacted, self.captured
        )
    }
}

/// One pre-merge unit's window in the audit's format, class, no-verdict flag and configs, as [`PremergeSnapshot::grains`] yields them in capture order (`facts.Grain`).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Grain<'a> {
    pub codepoints: String,
    pub class_id: &'a str,
    pub no_verdict: bool,
    pub configs: &'a [String],
}

/// The workload as the build saw it before the ink-duplicate merge (`facts.PremergeSnapshot`), one entry a pre-merge unit in load order: its class id and config-set id into the table's string table and tuple pool, its no-verdict byte, and its window's offsets into the table's value column, which the merge does not move; after [`PremergeSnapshot::rebase`], its survivor's row in the merged table and a byte that is 1 for a unit the merge removed. The identical classes are the ledger's `ink_identical` ids.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct PremergeSnapshot {
    pub n: usize,
    pub identical_classes: BTreeSet<String>,
    pub class_id: Vec<u32>,
    pub no_verdict: Vec<u8>,
    pub configs: Vec<u32>,
    pub window_start: Vec<u32>,
    pub window_n: Vec<u8>,
    pub survivor: Option<Vec<u32>>,
    pub removed: Option<Vec<u8>>,
}

impl PremergeSnapshot {
    /// The snapshot of the stream's pre-merge units (`PremergeSnapshot.from_columns`), taking over their columns, over the identical classes `identical_classes`.
    pub fn from_columns(
        columns: PremergeColumns,
        identical_classes: impl IntoIterator<Item = String>,
    ) -> Self {
        Self {
            n: columns.class_id.len(),
            identical_classes: identical_classes.into_iter().collect(),
            class_id: columns.class_id,
            no_verdict: columns.no_verdict,
            configs: columns.configs,
            window_start: columns.window_start,
            window_n: columns.window_n,
            survivor: None,
            removed: None,
        }
    }

    pub fn len(&self) -> usize {
        self.n
    }

    pub fn is_empty(&self) -> bool {
        self.n == 0
    }

    /// Record where each pre-merge unit's survivor sits in the merged table and which units the merge removed, refusing a compaction over another count of rows.
    pub fn rebase(&mut self, compaction: Compaction) -> std::result::Result<(), UnevenCompaction> {
        if compaction.survivor.len() != self.n {
            return Err(UnevenCompaction {
                compacted: compaction.survivor.len(),
                captured: self.n,
            });
        }
        self.survivor = Some(compaction.survivor);
        self.removed = Some(compaction.removed);
        Ok(())
    }

    /// The pre-merge unit `row`'s window, read from the value column of `table`, the table the snapshot was captured beside.
    pub fn codepoints<'a>(&self, table: &'a UnitTable, row: usize) -> &'a [u16] {
        let start = self.window_start[row] as usize;
        &table.window_values()[start..start + usize::from(self.window_n[row])]
    }

    /// Each pre-merge unit's grain in capture order, its names read from `table`, the table the snapshot was captured beside.
    pub fn grains<'a>(&'a self, table: &'a UnitTable) -> impl Iterator<Item = Grain<'a>> + 'a {
        (0..self.n).map(move |row| Grain {
            codepoints: format_codepoints(self.codepoints(table, row)),
            class_id: table.strings.get(self.class_id[row]),
            no_verdict: self.no_verdict[row] != 0,
            configs: table.tuples.get(self.configs[row]),
        })
    }

    /// Each pre-merge unit's class and no-verdict flag in capture order (`PremergeSnapshot.class_rows`).
    pub fn class_rows<'a>(
        &'a self,
        table: &'a UnitTable,
    ) -> impl Iterator<Item = (&'a str, bool)> + 'a {
        (0..self.n).map(move |row| {
            (
                table.strings.get(self.class_id[row]),
                self.no_verdict[row] != 0,
            )
        })
    }

    /// Each column's length in `PremergeSnapshot.columns`' order: the five captured, then the survivor and removed columns once rebased.
    pub fn column_lengths(&self) -> Vec<usize> {
        let mut lengths = vec![
            self.class_id.len(),
            self.no_verdict.len(),
            self.configs.len(),
            self.window_start.len(),
            self.window_n.len(),
        ];
        if let (Some(survivor), Some(removed)) = (&self.survivor, &self.removed) {
            lengths.extend([survivor.len(), removed.len()]);
        }
        lengths
    }
}

/// A window in the audit's format: colon-joined uppercase hex, at least four digits a code point (`audit.format_codepoints`).
fn format_codepoints(values: &[u16]) -> String {
    values
        .iter()
        .map(|value| format!("{value:04X}"))
        .collect::<Vec<_>>()
        .join(":")
}

/// The keys of the `premerge` stage's lines beside its units and column (`units_stage_fixtures.PREMERGE_*_KEY`).
const IDENTICAL_KEY: &str = "identical-classes";
const LENGTHS_KEY: &str = "column-lengths";

/// Write the `premerge` stage's lines over a rebased snapshot captured beside `table`, as the Python stage's `premerge_boundary` lays them out: a `unit:<ordinal>` line for each ordinal in `units`, the unit's row of the snapshot's columns followed by its grain; the column over every unit; the identical classes sorted; and each column's length.
fn premerge_lines(
    snapshot: &PremergeSnapshot,
    table: &UnitTable,
    units: &std::collections::HashSet<usize>,
    out: &mut impl Write,
) -> Result<()> {
    let (Some(survivor), Some(removed)) = (&snapshot.survivor, &snapshot.removed) else {
        return Err(WireError(
            "the premerge stage answers over a rebased snapshot".to_owned(),
        ));
    };
    let mut digest = ColumnDigest::new();
    for (row, grain) in snapshot.grains(table).enumerate() {
        let value = PyValue::Tuple(vec![
            int(snapshot.class_id[row]),
            int(snapshot.no_verdict[row]),
            int(snapshot.configs[row]),
            int(snapshot.window_start[row]),
            int(snapshot.window_n[row]),
            int(survivor[row]),
            int(removed[row]),
            PyValue::Tuple(vec![
                PyValue::str(&grain.codepoints),
                PyValue::str(grain.class_id),
                PyValue::Bool(grain.no_verdict),
                PyValue::str_tuple(grain.configs),
            ]),
        ]);
        if units.contains(&row) {
            stage_line(out, &format!("{UNIT_PREFIX}{row}"), &value)?;
        }
        digest.add(&value);
    }
    digest.write(out, "value")?;
    stage_line(
        out,
        IDENTICAL_KEY,
        &PyValue::List(
            snapshot
                .identical_classes
                .iter()
                .map(PyValue::str)
                .collect(),
        ),
    )?;
    stage_line(
        out,
        LENGTHS_KEY,
        &PyValue::List(
            snapshot
                .column_lengths()
                .into_iter()
                .map(count_value)
                .collect(),
        ),
    )
}

/// The `premerge` stage: load the audit its build input names as the build's stream does ([`dump_stream`]), take the snapshot of its pre-merge units over the ledger's `ink_identical` classes and rebase it onto the merge's compaction, and answer with the lines `premerge_lines` writes, a `unit:<ordinal>` line for each one the stage file asks for. A stage file with no lines, from a build whose stream did not run, is answered with none.
pub fn stage_premerge(input_path: &Path, stage_file: &Path, out: &mut impl Write) -> Result<()> {
    let input = BuildInput::read(input_path)?;
    let (units, lines) = stage_units(stage_file)?;
    if lines == 0 {
        return Ok(());
    }
    let StreamedLoad {
        workload,
        premerge,
        compaction,
        ..
    } = dump_stream(&input, stage_file)?;
    let identical = input
        .ledger
        .iter()
        .filter(|entry| entry.ink_identical)
        .map(|entry| entry.id.clone());
    let mut snapshot = PremergeSnapshot::from_columns(premerge, identical);
    snapshot
        .rebase(compaction)
        .map_err(|uneven| WireError(format!("ValueError: {uneven}")))?;
    premerge_lines(&snapshot, &workload.table, &units, out)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::change_groups::UNMATCHED;
    use crate::corpus::table::tests::{load, mini_ledger, mini_windows};

    #[test]
    fn a_rebased_snapshot_reads_each_premerge_unit_and_its_survivor() {
        let loaded = load(mini_windows(), &mini_ledger()).expect("the stream loads");
        let mut snapshot = PremergeSnapshot::from_columns(loaded.premerge, ["class-b".to_owned()]);
        assert_eq!(snapshot.len(), 5);
        assert_eq!(snapshot.column_lengths(), [5; 5]);
        snapshot
            .rebase(loaded.compaction)
            .expect("the rebase holds");
        assert_eq!(snapshot.survivor.as_deref(), Some(&[3, 0, 1, 2, 3][..]));
        assert_eq!(snapshot.removed.as_deref(), Some(&[1, 0, 0, 0, 0][..]));
        assert_eq!(snapshot.column_lengths(), [5; 7]);
        let table = &loaded.workload.table;
        let grains: Vec<(String, &str, bool, Vec<&str>)> = snapshot
            .grains(table)
            .map(|grain| {
                (
                    grain.codepoints,
                    grain.class_id,
                    grain.no_verdict,
                    grain.configs.iter().map(String::as_str).collect(),
                )
            })
            .collect();
        assert_eq!(
            grains,
            [
                ("E650:E652".to_owned(), "class-a", false, vec!["ss03"]),
                ("E652".to_owned(), "class-a", false, vec!["default"]),
                ("E650".to_owned(), "class-b", true, vec!["default"]),
                ("E652".to_owned(), "class-b", true, vec!["ss03"]),
                (
                    "E650:E652".to_owned(),
                    UNMATCHED,
                    false,
                    vec!["default", "ss04"]
                ),
            ]
        );
        let rows: Vec<(&str, bool)> = snapshot.class_rows(table).collect();
        assert_eq!(rows[2], ("class-b", true));
    }

    #[test]
    fn a_rebase_refuses_a_compaction_over_another_count_of_rows() {
        let loaded = load(mini_windows(), &mini_ledger()).expect("the stream loads");
        let mut snapshot = PremergeSnapshot::from_columns(loaded.premerge, []);
        let uneven = snapshot
            .rebase(Compaction {
                survivor: vec![0; 4],
                removed: vec![0; 4],
            })
            .expect_err("four rows against five");
        assert_eq!(
            uneven.to_string(),
            "a compaction over 4 rows, against 5 captured"
        );
        assert_eq!(snapshot.survivor, None);
    }
}
