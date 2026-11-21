//! The change groups, as `rebuild/review/change_groups.py` assigns them: whether a unit of an `ink_identical` ledger class is listed under a change group instead (`reroutes`), the configurations no ruling explains (`unexplained_configs`), and the group itself (`assign_change_group`): the set gate (`set_gate_group`), the ligature guard, the junction kind (`_junction_group`, `_height`), then the width.

use std::collections::BTreeSet;
use std::fmt;

use crate::pyfmt::repr::repr_str;
use crate::pyfmt::{decimal_value, is_digit};

/// `change_groups.UNMATCHED`, which is also `audit.UNMATCHED_CLASS`: the class of a unit no ledger ruling covers.
pub const UNMATCHED: &str = "UNMATCHED";

/// `change_groups.SET_GATE`: the stylistic sets the set gate tries, in order, and the group each one names.
pub const SET_GATE: [(&str, &str); 4] = [
    ("ss04", "only-with-ss04"),
    ("ss10", "only-with-ss10"),
    ("ss03", "only-with-ss03"),
    ("ss05", "only-with-ss05"),
];

/// `change_groups.SEVERAL_SETS`: the set gate's group when no one set is on in every unexplained configuration.
pub const SEVERAL_SETS: &str = "only-with-several-sets";

/// CPython's default `sys.int_info.default_max_str_digits`: `int` refuses a decimal string with more digits.
const INT_MAX_STR_DIGITS: usize = 4300;

/// A unit the rules cannot group, as `assign_change_group` raises `ValueError` for it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ChangeGroupError(pub String);

impl fmt::Display for ChangeGroupError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for ChangeGroupError {}

type Result<T> = std::result::Result<T, ChangeGroupError>;

/// `_config_features`: the stylistic sets a configuration token turns on, none for `default`.
pub fn config_features(config: &str) -> BTreeSet<&str> {
    if config == "default" {
        BTreeSet::new()
    } else {
        config.split('+').collect()
    }
}

/// `change_groups.reroutes`: whether a unit of ledger class `class_id` is listed under a change group instead, because its class is one of `identical_classes`, the class does not exempt it from verdicts, and no machine check approves it.
pub fn reroutes(
    class_id: &str,
    no_verdict: bool,
    machine_approved: bool,
    identical_classes: &[String],
) -> bool {
    identical_classes.iter().any(|class| class == class_id) && !no_verdict && !machine_approved
}

/// `change_groups.unexplained_configs`: the configurations, in the unit's order, mapped to UNMATCHED or to one of `identical_classes` whose ink changed; every configuration when the unit has no per-configuration classes. `config_classes` and `ink_deltas` are dicts as ordered pairs, a later pair winning as in Python's dict.
pub fn unexplained_configs<'u>(
    configs: &'u [String],
    config_classes: &[(String, String)],
    ink_deltas: &[(String, String)],
    identical_classes: &[String],
) -> Vec<&'u str> {
    if config_classes.is_empty() {
        return configs.iter().map(String::as_str).collect();
    }
    configs
        .iter()
        .filter(|config| {
            let class = config_classes
                .iter()
                .rev()
                .find(|(held, _)| held == *config)
                .map(|(_, class)| class.as_str());
            class == Some(UNMATCHED)
                || (class.is_some_and(|class| identical_classes.iter().any(|held| held == class))
                    && ink_deltas.iter().any(|(held, _)| held == *config))
        })
        .map(String::as_str)
        .collect()
}

/// `change_groups.set_gate_group`: the set group for a unit whose unexplained configurations are `configs`, or None when one of them is the default. An empty list is refused, since a unit that takes a change group always has a configuration no ruling explains.
pub fn set_gate_group(configs: &[&str]) -> Result<Option<&'static str>> {
    let features: Vec<BTreeSet<&str>> = configs
        .iter()
        .map(|config| config_features(config))
        .collect();
    if features.is_empty() {
        return Err(ChangeGroupError(
            "a unit that takes a change group has a config no ledger ruling explains".to_owned(),
        ));
    }
    if features.iter().any(BTreeSet::is_empty) {
        return Ok(None);
    }
    Ok(SET_GATE
        .iter()
        .find(|(tag, _)| features.iter().all(|feature| feature.contains(tag)))
        .map_or(Some(SEVERAL_SETS), |(_, group)| Some(group)))
}

/// A join height as `int` reads a junction token's digits, held exactly: its decimal digits without leading zeros, ordered by their count and then by the digits, which is the order of the numbers.
#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord)]
struct Height(usize, Vec<u32>);

impl Height {
    fn is_zero(&self) -> bool {
        self.0 == 0
    }
}

/// `change_groups._height`: a junction token's join height, or None for `break`. A token that is not `break` or `y` and digits is refused, and so is one whose digits `int` does not read: a digit that is not decimal, or more digits than `int` reads.
fn height(token: &str) -> Result<Option<Height>> {
    if token == "break" {
        return Ok(None);
    }
    let refused = || {
        ChangeGroupError(format!(
            "a changed junction token is break or yN, not {}",
            repr_str(token)
        ))
    };
    let Some(rest) = token.strip_prefix('y') else {
        return Err(refused());
    };
    if rest.is_empty() || !rest.chars().all(is_digit) {
        return Err(refused());
    }
    let digits: Vec<u32> = rest
        .chars()
        .map(decimal_value)
        .collect::<Option<_>>()
        .ok_or_else(|| {
            ChangeGroupError(format!(
                "invalid literal for int() with base 10: {}",
                repr_str(rest)
            ))
        })?;
    if digits.len() > INT_MAX_STR_DIGITS {
        return Err(ChangeGroupError(format!(
            "the junction token {} has more digits than int reads",
            repr_str(token)
        )));
    }
    let significant: Vec<u32> = digits.into_iter().skip_while(|&digit| digit == 0).collect();
    Ok(Some(Height(significant.len(), significant)))
}

/// `change_groups._junction_group`: the group the changed junctions decide, or None when every junction is as the old font had it. A new join decides first, then a dropped join, then the first change of height.
fn junction_group(before: &[String], after: &[String]) -> Result<Option<&'static str>> {
    let mut changed: Vec<(Option<Height>, Option<Height>)> = Vec::new();
    for (was, now) in before.iter().zip(after) {
        if was != now {
            let old = height(was)?;
            let new = height(now)?;
            if old != new {
                changed.push((old, new));
            }
        }
    }
    let Some((first_old, first_new)) = changed.first() else {
        return Ok(None);
    };
    if let Some(gained) = changed
        .iter()
        .find_map(|(old, new)| old.is_none().then_some(new.as_ref()).flatten())
    {
        return Ok(Some(if gained.is_zero() {
            "new-join-at-baseline"
        } else {
            "new-join-at-xheight-or-top"
        }));
    }
    if changed.iter().any(|(_, new)| new.is_none()) {
        return Ok(Some("join-dropped"));
    }
    match (first_old, first_new) {
        (Some(old), Some(new)) => Ok(Some(if new < old {
            "join-lowered"
        } else {
            "join-raised"
        })),
        _ => unreachable!("a change that neither gains nor drops a join has two heights"),
    }
}

/// What `assign_change_group` reads from an enriched unit (`change_groups.ChangeGroupInput`): the unit's configurations and per-configuration classes, the junction and cell tuples, and the two highlights' advance totals.
pub struct ChangeGroupInput<'a> {
    pub configs: &'a [String],
    pub config_classes: &'a [(String, String)],
    pub before_junctions: &'a [String],
    pub after_junctions: &'a [String],
    pub after_cells: &'a [String],
    pub advance_before: i64,
    pub advance_after: i64,
}

/// `change_groups.assign_change_group`: the change group of a unit that takes one, from the set gate, the ligature guard, the junction kind and then the width. `ink_deltas` are the unit's per-configuration ink deltas and `identical_classes` the ledger's `ink_identical` ids.
pub fn assign_change_group(
    enriched: &ChangeGroupInput<'_>,
    ink_deltas: &[(String, String)],
    identical_classes: &[String],
) -> Result<&'static str> {
    let unexplained = unexplained_configs(
        enriched.configs,
        enriched.config_classes,
        ink_deltas,
        identical_classes,
    );
    if let Some(group) = set_gate_group(&unexplained)? {
        return Ok(group);
    }
    let (before, after) = (enriched.before_junctions, enriched.after_junctions);
    if before.len() != after.len() || after.len() + 1 != enriched.after_cells.len() {
        return Ok("ligature-forms-or-splits");
    }
    if let Some(group) = junction_group(before, after)? {
        return Ok(group);
    }
    let width = enriched.advance_after - enriched.advance_before;
    Ok(match width.signum() {
        -1 => "same-joins-narrower",
        1 => "same-joins-wider",
        _ => "same-joins-same-width",
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn texts(items: &[&str]) -> Vec<String> {
        items.iter().map(|item| (*item).to_owned()).collect()
    }

    fn pairs(items: &[(&str, &str)]) -> Vec<(String, String)> {
        items
            .iter()
            .map(|(key, value)| ((*key).to_owned(), (*value).to_owned()))
            .collect()
    }

    #[test]
    fn an_identical_class_reroutes_only_a_unit_no_ruling_or_machine_check_settles() {
        let identical = texts(&["same-ink"]);
        assert!(reroutes("same-ink", false, false, &identical));
        assert!(!reroutes("same-ink", true, false, &identical));
        assert!(!reroutes("same-ink", false, true, &identical));
        assert!(!reroutes("other", false, false, &identical));
    }

    #[test]
    fn unexplained_configs_are_the_unmatched_and_the_identical_ones_whose_ink_changed() {
        let configs = texts(&["default", "ss03", "ss05"]);
        let identical = texts(&["same-ink"]);
        let classes = pairs(&[
            ("default", "UNMATCHED"),
            ("ss03", "same-ink"),
            ("ss05", "same-ink"),
        ]);
        let deltas = pairs(&[("ss05", "d")]);
        assert_eq!(
            unexplained_configs(&configs, &classes, &deltas, &identical),
            ["default", "ss05"]
        );
        assert_eq!(
            unexplained_configs(&configs, &[], &deltas, &identical),
            ["default", "ss03", "ss05"]
        );
    }

    #[test]
    fn the_set_gate_names_the_first_set_on_everywhere_and_yields_to_the_default() {
        assert_eq!(
            set_gate_group(&["ss03+ss05", "ss05"]),
            Ok(Some("only-with-ss05"))
        );
        assert_eq!(
            set_gate_group(&["ss03+ss04", "ss04+ss10"]),
            Ok(Some("only-with-ss04"))
        );
        assert_eq!(set_gate_group(&["ss03", "ss05"]), Ok(Some(SEVERAL_SETS)));
        assert_eq!(set_gate_group(&["ss03", "default"]), Ok(None));
        assert!(set_gate_group(&[]).is_err());
    }

    #[test]
    fn a_height_reads_as_int_reads_the_digits_and_refuses_what_it_refuses() {
        assert_eq!(height("break"), Ok(None));
        assert_eq!(height("y05"), height("y5"));
        assert!(height("y5").expect("y5") < height("y10").expect("y10"));
        assert!(height("y\u{0665}").expect("y5").is_some());
        assert_eq!(height("y\u{0665}"), height("y5"));
        assert!(height("y").is_err());
        assert!(height("y\u{00B2}").is_err());
        assert!(height("lig").is_err());
        assert!(height("y-1").is_err());
    }

    #[test]
    fn a_new_join_decides_before_a_dropped_one_and_then_the_first_height_change() {
        let group = |before: &[&str], after: &[&str]| junction_group(&texts(before), &texts(after));
        assert_eq!(
            group(&["y5", "break"], &["y0", "y0"]),
            Ok(Some("new-join-at-baseline"))
        );
        assert_eq!(
            group(&["break", "y0"], &["y5", "break"]),
            Ok(Some("new-join-at-xheight-or-top"))
        );
        assert_eq!(
            group(&["y5", "y0"], &["y0", "break"]),
            Ok(Some("join-dropped"))
        );
        assert_eq!(
            group(&["y5", "y0"], &["y0", "y5"]),
            Ok(Some("join-lowered"))
        );
        assert_eq!(group(&["y0"], &["y00"]), Ok(None));
        assert!(group(&["y0"], &["lig"]).is_err());
    }

    #[test]
    fn with_no_junction_changed_the_width_decides() {
        let configs = texts(&["default"]);
        let junctions = texts(&["y0"]);
        let cells = texts(&["a", "b"]);
        let input = |before: i64, after: i64| ChangeGroupInput {
            configs: &configs,
            config_classes: &[],
            before_junctions: &junctions,
            after_junctions: &junctions,
            after_cells: &cells,
            advance_before: before,
            advance_after: after,
        };
        assert_eq!(
            assign_change_group(&input(10, 9), &[], &[]),
            Ok("same-joins-narrower")
        );
        assert_eq!(
            assign_change_group(&input(10, 11), &[], &[]),
            Ok("same-joins-wider")
        );
        assert_eq!(
            assign_change_group(&input(10, 10), &[], &[]),
            Ok("same-joins-same-width")
        );
        let one_cell = texts(&["a"]);
        let ligature = ChangeGroupInput {
            after_cells: &one_cell,
            ..input(10, 10)
        };
        assert_eq!(
            assign_change_group(&ligature, &[], &[]),
            Ok("ligature-forms-or-splits")
        );
    }
}
