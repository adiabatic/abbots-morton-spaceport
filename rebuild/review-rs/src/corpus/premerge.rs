//! The pre-merge snapshot the review facts are derived from. [`PremergeSnapshot::from_columns`] takes the stream's pre-merge units' columns in load order ([`PremergeColumns`]) beside the merged table, and [`PremergeSnapshot::rebase`] records, from the merge's [`Compaction`], each pre-merge unit's survivor's row and whether the merge removed it. The class and config-set ids name the table's string table and tuple pool, and the window offsets its value column, so every read of the snapshot takes the table it was captured beside.

use std::collections::BTreeSet;
use std::fmt;

use super::table::{Compaction, PremergeColumns, UnitTable};

/// What [`PremergeSnapshot::rebase`] refuses, a `ValueError`: a compaction over another count of rows than the snapshot captured.
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

/// The workload as the build saw it before the ink-duplicate merge, one entry a pre-merge unit in load order: its class id and config-set id into the table's string table and tuple pool, its no-verdict byte, and its window's offsets into the table's value column, which the merge does not move; after [`PremergeSnapshot::rebase`], its survivor's row in the merged table and a byte that is 1 for a unit the merge removed. The identical classes are the ledger's `ink_identical` ids.
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
    /// The snapshot of the stream's pre-merge units, taking over their columns, over the identical classes `identical_classes`.
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

    /// Each pre-merge unit's class and no-verdict flag in capture order.
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
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::corpus::table::tests::{load, mini_ledger, mini_windows};

    #[test]
    fn a_rebased_snapshot_maps_each_premerge_unit_to_its_survivor() {
        let loaded = load(mini_windows(), &mini_ledger()).expect("the stream loads");
        let mut snapshot = PremergeSnapshot::from_columns(loaded.premerge, ["class-b".to_owned()]);
        snapshot
            .rebase(loaded.compaction)
            .expect("the rebase holds");
        assert_eq!(snapshot.survivor.as_deref(), Some(&[3, 0, 1, 2, 3][..]));
        assert_eq!(snapshot.removed.as_deref(), Some(&[1, 0, 0, 0, 0][..]));
        let table = &loaded.workload.table;
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
