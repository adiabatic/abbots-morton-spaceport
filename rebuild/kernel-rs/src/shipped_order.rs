//! Checks the settlement order the font ships against one configuration's rows, for the `replay-emitted` subcommand. The emitter folds every configuration's table into the one settlement lookup the font ships (`emit_gsub._ordered_settle_rules`), and no per-configuration check reads the order that fold produces. The fold's partition assertion and the string replay walk each table's rules in the table's own order, the witness stage first-matches each certificate in that same order, and read-back compares the font to the plan, not the plan to the tables.
//!
//! The walk renames every row of the configuration's enumeration into the labels the configuration's marker lookups produce, tries the emitted rules for the row's input in shipped order, and requires the first rule that matches to give the row's outcome (the input itself when no rule matches). A row whose shipped-order outcome differs from the table's is an error naming the configuration, the row, the emitted rule that fired, and the table's own rule.
//!
//! The walk checks every row, not a sample. The rows are the table's own, at the table's grain. Rows are renamed before matching because, under a configuration, every raw label of a rune whose capability the active sets change is renamed to its marker twin, and the emitted rules already use the twin names. A deep slot that holds a class token is checked against the whole class. When the rules are indexed, each deep slot of each emitted rule is compared with every class of the configuration. A class that a rule's slot contains entirely matches through its token. A class that the slot contains only in part makes the walk try the row once per member (once per member pair when both deep slots are classes), and each member's first match must give the row's outcome; the fiber construction makes that outcome the same for every member. `fold::assert_deep_class_unions` does not cover this case: it checks a configuration's own rules against its own classes, but the shipped lookup holds every configuration's rules, so a rule folded from another configuration's fiber partition can match part of a class. That is harmless only when the rule gives each member the row's outcome, which is what the member-by-member check verifies.
//!
//! The walk is O(rows), with a bounded number of rules per input, and settles nothing: the tables already hold the settled outcomes, and the walk checks that the shipped lookup reproduces them. That makes it cheap enough to run on every build, keyed on the same inputs as the tables. The HarfBuzz belt also checks the shipped order, but its key (`artifact_cycle.conform_skip_fingerprint`) covers the compile code and the emitted lookup's behavior classes, not the runes, so it skips a rune edit that adds no new behavior class.

use std::io::BufRead;

use crate::artifacts::WINDOWS_FORMAT;
use crate::fold::{Rule, first_match, rules_by_input};
use crate::hash::{HashMap, HashSet};
use crate::replay::Labels;

/// The result of one configuration's walk: the rows it checked, and how many of them it checked member by member because an emitted lookahead class contained their deep class only in part.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Report {
    pub rows: u64,
    pub expanded: u64,
}

/// How many disagreements a walk reports before it stops, so the error message stays short.
const NAMED_DISAGREEMENTS: usize = 5;

/// How one configuration's marker lookups relabel its table's rows. `renames` maps each raw label to the marker twin used under this configuration (`model.raw_rename_map`). `classes` lists the deep classes in the table's rows, each token with its members as raw labels (`DecisionTable.deep_classes`).
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Context {
    pub renames: Vec<(String, String)>,
    pub classes: Vec<(String, Vec<String>)>,
}

/// Parses a context file: one tab-separated record per line, either `rename<tab><raw><tab><twin>` or `class<tab><token><tab><member> <member> …`. Any other line is an error.
pub fn read_context(text: &str) -> Result<Context, String> {
    let mut context = Context::default();
    for (number, line) in text.lines().enumerate() {
        let fields: Vec<&str> = line.split('\t').collect();
        match fields.as_slice() {
            ["rename", raw, twin] if !raw.is_empty() && !twin.is_empty() => {
                context
                    .renames
                    .push(((*raw).to_owned(), (*twin).to_owned()));
            }
            ["class", token, members] if !token.is_empty() && !members.is_empty() => {
                context.classes.push((
                    (*token).to_owned(),
                    members.split(' ').map(str::to_owned).collect(),
                ));
            }
            _ => {
                return Err(format!(
                    "context line {} is neither a rename nor a class record: {line:?}",
                    number + 1
                ));
            }
        }
    }
    Ok(context)
}

/// One emitted rule prepared for matching: its index in the shipped order, the five context slots as sorted label ids (`None` for an unconstrained slot, and with the tokens of classes the slot contains entirely added), and the outcome's id.
struct EmittedRule {
    seat: usize,
    slots: [Option<Vec<u32>>; 5],
    outcome: u32,
}

/// The rule, slot, and class token where a deep slot rejected a class token it contains in part, after every earlier slot matched. It tells the caller to try the row member by member.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct Split {
    seat: usize,
    slot: usize,
    token: u32,
}

/// The emitted rules grouped by input label, each input's rules in shipped order, and the (rule, slot, class token) triples where the slot contains the class in part, so that [`Order::first`] can tell a partial class match from a plain miss.
struct Order {
    by_input: HashMap<u32, Vec<EmittedRule>>,
    splits: HashSet<(usize, usize, u32)>,
}

impl Order {
    /// Indexes the emitted rules. Every class of the configuration is compared with every deep slot: a class the slot contains entirely is added to the slot as its token, and a class it contains in part is recorded as a split.
    fn new(labels: &mut Labels, rules: &[Rule], classes: &[(u32, Vec<u32>)]) -> Self {
        let mut by_input: HashMap<u32, Vec<EmittedRule>> = HashMap::default();
        let mut splits: HashSet<(usize, usize, u32)> = HashSet::default();
        for (seat, rule) in rules.iter().enumerate() {
            let input = labels.intern(&rule.input_glyph);
            let mut slot = |members: &Option<Vec<std::rc::Rc<str>>>| {
                members.as_ref().map(|members| {
                    let mut ids: Vec<u32> =
                        members.iter().map(|member| labels.intern(member)).collect();
                    ids.sort_unstable();
                    ids.dedup();
                    ids
                })
            };
            let mut slots = [
                slot(&rule.backtrack),
                slot(&rule.look1),
                slot(&rule.look2),
                slot(&rule.look3),
                slot(&rule.look4),
            ];
            for (index, held) in slots.iter_mut().enumerate().skip(3) {
                let Some(ids) = held else {
                    continue;
                };
                let mut whole: Vec<u32> = Vec::new();
                for (token, members) in classes {
                    let inside = members
                        .iter()
                        .filter(|member| ids.binary_search(member).is_ok())
                        .count();
                    if inside == members.len() {
                        whole.push(*token);
                    } else if inside > 0 {
                        splits.insert((seat, index, *token));
                    }
                }
                if !whole.is_empty() {
                    ids.extend(whole);
                    ids.sort_unstable();
                }
            }
            let outcome = labels.intern(&rule.outcome);
            by_input.entry(input).or_default().push(EmittedRule {
                seat,
                slots,
                outcome,
            });
        }
        Self { by_input, splits }
    }

    /// The first emitted rule for `input` whose five slots match `window`, or `None` when no rule matches. When a deep slot rejects a class token it contains in part, after every earlier slot matched, this returns that [`Split`] so the caller can expand the row.
    fn first(&self, input: u32, window: [u32; 5]) -> Result<Option<&EmittedRule>, Split> {
        'rules: for rule in self.by_input.get(&input).map_or(&[][..], Vec::as_slice) {
            for (index, (slot, label)) in rule.slots.iter().zip(window).enumerate() {
                let Some(members) = slot else {
                    continue;
                };
                if members.binary_search(&label).is_ok() {
                    continue;
                }
                if index >= 3 && self.splits.contains(&(rule.seat, index, label)) {
                    return Err(Split {
                        seat: rule.seat,
                        slot: index,
                        token: label,
                    });
                }
                continue 'rules;
            }
            return Ok(Some(rule));
        }
        Ok(None)
    }
}

/// Checks one configuration's rows against the shipped order.
pub struct Walk<'a> {
    config: &'a str,
    labels: Labels,
    order: Order,
    renames: HashMap<u32, u32>,
    raw_labels: HashMap<u32, u32>,
    classes: HashMap<u32, Vec<u32>>,
    representatives: HashMap<String, String>,
    table: &'a [Rule],
    shipped: &'a [Rule],
    disagreements: Vec<String>,
}

impl<'a> Walk<'a> {
    /// A walk for `config`. `table` is the configuration's own table, read only to name the table's rule in a disagreement. `order` is every configuration's rules in shipped order. `context` holds the configuration's marker renames and deep classes.
    pub fn new(config: &'a str, table: &'a [Rule], order: &'a [Rule], context: &Context) -> Self {
        let mut labels = Labels::new();
        let renames: HashMap<u32, u32> = context
            .renames
            .iter()
            .map(|(raw, twin)| (labels.intern(raw), labels.intern(twin)))
            .collect();
        let classes: Vec<(u32, Vec<u32>)> = context
            .classes
            .iter()
            .map(|(token, members)| {
                let token = labels.intern(token);
                let members: Vec<u32> = members
                    .iter()
                    .map(|member| {
                        let raw = labels.intern(member);
                        renames.get(&raw).copied().unwrap_or(raw)
                    })
                    .collect();
                (token, members)
            })
            .collect();
        let indexed = Order::new(&mut labels, order, &classes);
        let raw_labels: HashMap<u32, u32> =
            renames.iter().map(|(raw, twin)| (*twin, *raw)).collect();
        let representatives: HashMap<String, String> = context
            .classes
            .iter()
            .filter_map(|(token, members)| {
                members
                    .first()
                    .map(|member| (token.clone(), member.clone()))
            })
            .collect();
        Self {
            config,
            labels,
            order: indexed,
            renames,
            raw_labels,
            classes: classes.into_iter().collect(),
            representatives,
            table,
            shipped: order,
            disagreements: Vec::new(),
        }
    }

    /// Checks every row of a windows enumeration read from `source` (the head line under [`WINDOWS_FORMAT`], the column line, then one row per line) against the shipped order, member by member where an emitted lookahead class contains the row's deep class only in part. A row whose shipped-order outcome differs from the table's is a disagreement, and the walk stops after [`NAMED_DISAGREEMENTS`] of them. A head line with another format, a wrong column line, or a row without seven fields is an error by itself.
    pub fn walk(&mut self, source: &mut impl BufRead) -> Result<Report, String> {
        let mut line = String::new();
        self.read_line(source, &mut line)?;
        if !line.starts_with(&format!("# {WINDOWS_FORMAT}\t")) {
            return Err(format!("not a {WINDOWS_FORMAT} enumeration"));
        }
        self.read_line(source, &mut line)?;
        if line.trim_end_matches('\n')
            != "input\tleft\tlookahead1\tlookahead2\tlookahead3\tlookahead4\toutcome"
        {
            return Err("the second line is not the windows column line".to_owned());
        }
        let mut report = Report::default();
        let by_input = rules_by_input(self.table);
        loop {
            line.clear();
            let read = source
                .read_line(&mut line)
                .map_err(|error| format!("reading the enumeration: {error}"))?;
            if read == 0 {
                break;
            }
            let text = line.trim_end_matches('\n');
            let mut fields = text.split('\t');
            let mut raw: [&str; 7] = [""; 7];
            for slot in raw.iter_mut() {
                *slot = fields.next().ok_or_else(|| {
                    format!(
                        "row {} of the enumeration is not seven fields: {text:?}",
                        report.rows + 1
                    )
                })?;
            }
            if fields.next().is_some() {
                return Err(format!(
                    "row {} of the enumeration is not seven fields: {text:?}",
                    report.rows + 1
                ));
            }
            report.rows += 1;
            let mut ids = [0u32; 7];
            for (id, field) in ids.iter_mut().zip(raw) {
                let interned = self.labels.intern(field);
                *id = self.renames.get(&interned).copied().unwrap_or(interned);
            }
            let [input, left, right1, right2, right3, right4, outcome] = ids;
            let window = [left, right1, right2, right3, right4];
            match self.order.first(input, window) {
                Ok(fired) => {
                    let fired = fired.map(|rule| (rule.seat, rule.outcome));
                    if fired.map_or(input, |(_, answer)| answer) != outcome {
                        let sentence = self.disagree(raw, None, fired, &by_input);
                        self.disagreements.push(sentence);
                    }
                }
                Err(_) => {
                    report.expanded += 1;
                    let members3 = self
                        .classes
                        .get(&right3)
                        .cloned()
                        .unwrap_or_else(|| vec![right3]);
                    let members4 = self
                        .classes
                        .get(&right4)
                        .cloned()
                        .unwrap_or_else(|| vec![right4]);
                    'members: for member3 in &members3 {
                        for member4 in &members4 {
                            let concrete = [left, right1, right2, *member3, *member4];
                            let fired = self.order.first(input, concrete).map_err(|split| {
                                format!(
                                    "{}: emitted rule {} turns {} away at its {} as a class, though it is a member label",
                                    self.config,
                                    split.seat,
                                    self.labels.text(split.token),
                                    slot_name(split.slot)
                                )
                            })?;
                            let fired = fired.map(|rule| (rule.seat, rule.outcome));
                            if fired.map_or(input, |(_, answer)| answer) != outcome {
                                let sentence = self.disagree(
                                    raw,
                                    Some((*member3, *member4)),
                                    fired,
                                    &by_input,
                                );
                                self.disagreements.push(sentence);
                                break 'members;
                            }
                        }
                    }
                }
            }
            if self.disagreements.len() >= NAMED_DISAGREEMENTS {
                return Err(self.complaint());
            }
        }
        if self.disagreements.is_empty() {
            Ok(report)
        } else {
            Err(self.complaint())
        }
    }

    /// Formats one disagreement: the row, the member pair it was tried at when a class was expanded, the table's outcome and the table rule that produced it, and the emitted rule that fired with its outcome. The table's rule is looked up with raw labels, using the member tried (or the class's first member) mapped back through the marker renames, because the table's rules name members and raw labels where the row names a class token and the stream names a twin.
    fn disagree(
        &self,
        raw: [&str; 7],
        members: Option<(u32, u32)>,
        fired: Option<(usize, u32)>,
        by_input: &HashMap<&str, Vec<(usize, &Rule)>>,
    ) -> String {
        let deep = |slot: usize, member: Option<u32>| -> &str {
            match member {
                Some(member) => self
                    .labels
                    .text(self.raw_labels.get(&member).copied().unwrap_or(member)),
                None => self
                    .representatives
                    .get(raw[slot])
                    .map_or(raw[slot], String::as_str),
            }
        };
        let right3 = deep(4, members.map(|(member3, _)| member3));
        let right4 = deep(5, members.map(|(_, member4)| member4));
        let own = first_match(by_input, [raw[0], raw[1], raw[2], raw[3], right3, right4])
            .map_or_else(
                || "no rule of its own table".to_owned(),
                |seat| format!("its table's rule {seat} ({})", rule_repr(&self.table[seat])),
            );
        let shipped = match fired {
            Some((seat, answer)) => format!(
                "emitted rule {seat} ({}) fires first and answers {}",
                rule_repr(&self.shipped[seat]),
                self.labels.text(answer)
            ),
            None => "no emitted rule matches, so the input stands".to_owned(),
        };
        let at = match members {
            Some((member3, member4)) => format!(
                " tried at ({}, {})",
                self.labels.text(member3),
                self.labels.text(member4)
            ),
            None => String::new(),
        };
        format!(
            "{}: row {}{at} settles to {} by {}, but in the shipped order {shipped}",
            self.config,
            self.spell_row(raw),
            raw[6],
            own
        )
    }

    fn read_line(&self, source: &mut impl BufRead, line: &mut String) -> Result<(), String> {
        line.clear();
        let read = source
            .read_line(line)
            .map_err(|error| format!("reading the enumeration: {error}"))?;
        if read == 0 {
            return Err("the enumeration ends before its column line".to_owned());
        }
        Ok(())
    }

    fn spell_row(&self, raw: [&str; 7]) -> String {
        format!(
            "({}, {}, {}, {}, {}, {})",
            raw[0], raw[1], raw[2], raw[3], raw[4], raw[5]
        )
    }

    fn complaint(&self) -> String {
        format!(
            "{} shipped-order disagreement(s) over the table's rows: {}",
            self.disagreements.len(),
            self.disagreements.join("; ")
        )
    }
}

fn slot_name(slot: usize) -> &'static str {
    match slot {
        0 => "backtrack",
        1 => "look1",
        2 => "look2",
        3 => "look3",
        _ => "look4",
    }
}

/// How many members of a slot an error message lists before it counts the rest. A committed-left block runs to hundreds of cells.
const SPELLED_MEMBERS: usize = 4;

/// Formats one rule for an error message: the input, the five slots (`any` for an unconstrained slot, and a long class cut to its first members), the outcome, and the first provenance pointer, which for an emitted rule names a table rule it was folded from.
fn rule_repr(rule: &Rule) -> String {
    let slot = |members: &Option<Vec<std::rc::Rc<str>>>| match members {
        None => "any".to_owned(),
        Some(members) => {
            let names: Vec<&str> = members
                .iter()
                .take(SPELLED_MEMBERS)
                .map(|member| &**member)
                .collect();
            if members.len() > SPELLED_MEMBERS {
                format!(
                    "[{} … {} more]",
                    names.join(" "),
                    members.len() - SPELLED_MEMBERS
                )
            } else {
                format!("[{}]", names.join(" "))
            }
        }
    };
    let provenance = rule
        .provenance
        .first()
        .map_or(String::new(), |pointer| format!(", from {pointer}"));
    format!(
        "{} {} {} {} {} {} -> {}{provenance}",
        rule.input_glyph,
        slot(&rule.backtrack),
        slot(&rule.look1),
        slot(&rule.look2),
        slot(&rule.look3),
        slot(&rule.look4),
        rule.outcome
    )
}

#[cfg(test)]
mod tests {
    use std::rc::Rc;

    use super::*;
    use crate::fixpoint::{EnumerationModes, enumerate_transitions};
    use crate::fold::{DecisionTable, fold_product};
    use crate::index::{SpecIndex, fixtures};

    const SHIPPING: EnumerationModes = EnumerationModes {
        simulated_prospect: true,
        vote_slots: true,
        deep_classes: true,
    };

    fn table(index: &SpecIndex, features: &[Sym]) -> DecisionTable {
        let product =
            enumerate_transitions(index, features, SHIPPING).expect("the fixpoint closes");
        fold_product(index, product).expect("and folds").decision
    }

    use crate::model::Sym;

    /// The fixture's enumeration in the plain payload format `artifacts::write_windows` writes: the head line, the column line, and one row per window.
    fn windows_text(decision: &DecisionTable) -> String {
        let mut text = format!(
            "# {WINDOWS_FORMAT}\t{{\"config\":\"{}\"}}\ninput\tleft\tlookahead1\tlookahead2\tlookahead3\tlookahead4\toutcome\n",
            decision.config
        );
        for row in &decision.transitions {
            for label in row.key(&decision.labels) {
                text.push_str(label);
                text.push('\t');
            }
            text.push_str(decision.outcome(row));
            text.push('\n');
        }
        text
    }

    fn context_of(decision: &DecisionTable) -> Context {
        Context {
            renames: Vec::new(),
            classes: decision.deep_classes.clone(),
        }
    }

    fn walk(decision: &DecisionTable, order: &[Rule], context: &Context) -> Result<Report, String> {
        let mut walk = Walk::new(&decision.config, &decision.rules, order, context);
        walk.walk(&mut windows_text(decision).as_bytes())
    }

    /// A table's own rules, in the table's order, give every row's outcome (the fold's partition assertion, checked through the walk), and the report counts the rows.
    #[test]
    fn a_tables_own_order_answers_every_row() {
        let index = fixtures::mini();
        let decision = table(&index, &[]);
        let report = walk(&decision, &decision.rules, &context_of(&decision))
            .expect("the table reads itself back");
        assert_eq!(report.rows as usize, decision.transitions.len());
        assert!(report.rows > 0);
        assert_eq!(report.expanded, 0);
    }

    /// Swapping two rules of one input, so that a row the later rule matched is now matched by the earlier one, produces an error naming the configuration, the row, the emitted rule that fired, and the table's own rule.
    #[test]
    fn a_swap_that_moves_a_row_names_the_row_and_both_rules() {
        let index = fixtures::mini();
        let decision = table(&index, &[]);
        let context = context_of(&decision);
        let mut found: Option<String> = None;
        'pairs: for first in 0..decision.rules.len() {
            for second in first + 1..decision.rules.len() {
                let (a, b) = (&decision.rules[first], &decision.rules[second]);
                if a.input_glyph != b.input_glyph || a.outcome == b.outcome {
                    continue;
                }
                let mut order = decision.rules.clone();
                order.swap(first, second);
                if let Err(complaint) = walk(&decision, &order, &context) {
                    found = Some(complaint);
                    break 'pairs;
                }
            }
        }
        let complaint = found.expect("some swap of the fixture's rules moves a row");
        assert!(
            complaint.contains("shipped-order disagreement"),
            "{complaint}"
        );
        assert!(complaint.contains("default: row ("), "{complaint}");
        assert!(complaint.contains("emitted rule "), "{complaint}");
        assert!(complaint.contains("its table's rule "), "{complaint}");
    }

    /// Rows are renamed into the configuration's labels before matching: an order written with a twin's name passes once the context renames the raw label to the twin, and fails without the rename.
    #[test]
    fn a_rename_carries_the_rows_into_the_streams_labels() {
        let index = fixtures::mini();
        let decision = table(&index, &[]);
        let raw: Rc<str> = Rc::from("qsPea");
        let twin: Rc<str> = Rc::from("qsPea.ss03");
        let relabel = |label: &Rc<str>| {
            if *label == raw {
                Rc::clone(&twin)
            } else {
                Rc::clone(label)
            }
        };
        let slot = |members: &Option<Vec<Rc<str>>>| {
            members
                .as_ref()
                .map(|members| members.iter().map(relabel).collect())
        };
        let order: Vec<Rule> = decision
            .rules
            .iter()
            .map(|rule| Rule {
                input_glyph: relabel(&rule.input_glyph),
                backtrack: slot(&rule.backtrack),
                look1: slot(&rule.look1),
                look2: slot(&rule.look2),
                look3: slot(&rule.look3),
                look4: slot(&rule.look4),
                outcome: relabel(&rule.outcome),
                provenance: rule.provenance.clone(),
                joint: rule.joint,
            })
            .collect();
        let mut context = context_of(&decision);
        let without =
            walk(&decision, &order, &context).expect_err("the raw rows miss the twin's rules");
        assert!(without.contains("qsPea"), "{without}");
        context
            .renames
            .push(("qsPea".to_owned(), "qsPea.ss03".to_owned()));
        walk(&decision, &order, &context).expect("renamed, every row is answered");
    }

    /// When an emitted lookahead class contains one of the configuration's deep classes only in part, the row is tried member by member: a rule that gives every member the row's outcome passes, and one that gives a member a different outcome is reported with that member. The rules are built by hand because the fixture's alphabet is too small to produce a multi-member class.
    #[test]
    fn a_split_deep_class_is_tried_member_by_member() {
        let rule = |look3: &[&str], outcome: &str| Rule {
            input_glyph: Rc::from("qsPea"),
            backtrack: None,
            look1: None,
            look2: None,
            look3: Some(look3.iter().map(|member| Rc::from(*member)).collect()),
            look4: None,
            outcome: Rc::from(outcome),
            provenance: Vec::new(),
            joint: false,
        };
        let payload = format!(
            "# {WINDOWS_FORMAT}\t{{}}\ninput\tleft\tlookahead1\tlookahead2\tlookahead3\tlookahead4\toutcome\nqsPea\t#EDGE\tqsPea\tqsPea\t#Cabc\t#NA\tqsPea.whole\n"
        );
        let context = read_context("class\t#Cabc\tqsPea qsTea\n").expect("one class");
        let whole = [rule(&["qsPea", "qsTea"], "qsPea.whole")];
        let mut walk = Walk::new("default", &whole, &whole, &context);
        let report = walk
            .walk(&mut payload.as_bytes())
            .expect("the class matches through its token");
        assert_eq!(
            report,
            Report {
                rows: 1,
                expanded: 0
            }
        );
        let benign = [rule(&["qsPea"], "qsPea.whole"), whole[0].clone()];
        let mut walk = Walk::new("default", &whole, &benign, &context);
        let report = walk
            .walk(&mut payload.as_bytes())
            .expect("every member answers as the row does");
        assert_eq!(
            report,
            Report {
                rows: 1,
                expanded: 1
            }
        );
        let split = [rule(&["qsTea"], "qsPea.split"), whole[0].clone()];
        let mut walk = Walk::new("default", &whole, &split, &context);
        let complaint = walk
            .walk(&mut payload.as_bytes())
            .expect_err("one member is answered differently");
        assert!(complaint.contains("tried at (qsTea, #NA)"), "{complaint}");
        assert!(complaint.contains("emitted rule 0"), "{complaint}");
        assert!(complaint.contains("qsPea.split"), "{complaint}");
    }

    /// The context file's two record kinds parse, and any other line is an error naming its line number.
    #[test]
    fn a_context_file_is_renames_and_classes_and_nothing_else() {
        let context = read_context("rename\tqsPea\tqsPea.ss03\nclass\t#Cabc\tqsPea qsTea\n")
            .expect("two records");
        assert_eq!(
            context.renames,
            vec![("qsPea".to_owned(), "qsPea.ss03".to_owned())]
        );
        assert_eq!(
            context.classes,
            vec![(
                "#Cabc".to_owned(),
                vec!["qsPea".to_owned(), "qsTea".to_owned()]
            )]
        );
        assert_eq!(
            read_context("").expect("empty is empty"),
            Context::default()
        );
        let complaint =
            read_context("rename\tqsPea\tqsPea.ss03\nlabel\tx\n").expect_err("a stray record");
        assert!(complaint.contains("line 2"), "{complaint}");
    }

    /// A payload that is not a windows enumeration, or a row without seven fields, is an error.
    #[test]
    fn a_malformed_payload_is_refused() {
        let index = fixtures::mini();
        let decision = table(&index, &[]);
        let context = context_of(&decision);
        let mut walk = Walk::new("default", &decision.rules, &decision.rules, &context);
        let complaint = walk
            .walk(&mut "# something else\n".as_bytes())
            .expect_err("not an enumeration");
        assert!(complaint.contains(WINDOWS_FORMAT), "{complaint}");
        let mut text = windows_text(&decision);
        text.push_str("qsPea\t#EDGE\n");
        let mut walk = Walk::new("default", &decision.rules, &decision.rules, &context);
        let complaint = walk.walk(&mut text.as_bytes()).expect_err("a short row");
        assert!(complaint.contains("not seven fields"), "{complaint}");
    }
}
