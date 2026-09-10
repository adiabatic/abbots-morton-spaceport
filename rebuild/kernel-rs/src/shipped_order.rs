//! The shipped first-match order replayed against one configuration's rows, behind the `replay-emitted` verb. The emitter folds every configuration's table into the one settlement lookup the font ships (`emit_gsub._ordered_settle_rules`), and that fold decides an order none of the per-configuration proofs read: the fold's partition assertion and the string replay walk each table's own rules in its own order, the witness stage first-matches each certificate in the same order, and read-back compares the font to the plan rather than the plan to the tables. This walk is where the plan meets the tables: every row of the configuration's enumeration is renamed into the stream the configuration's marker lookups produce, the emitted rules of its input are tried in the order they ship, and the first one whose context admits the row has to answer with the row's own outcome, the identity where no rule matches. A row the shipped order answers differently from the table is a refusal naming the configuration, the row, the emitted rule that fired and the table's own rule.
//!
//! Three things make the walk exact rather than sampled. The rows are the table's own, so every window the tables were built for is tried, at the grain the tables hold it. The labels are the stream's: under a configuration every raw label of a rune whose capability the active sets change is worn as its marker twin, and the emitted rules already spell that, so the rows are renamed through the same map before a rule is tried. And a deep slot standing at a class token is tried through the whole class rather than a representative: at index time every emitted look class is held against every class of the configuration, a class it holds whole matches through the token itself, and a class it admits in part is the cue to expand — the row is tried once per member (per member pair where both deep slots are classes) and every member's first match has to answer the row's outcome, which the fiber construction makes member-uniform. The expansion is what keeps the walk exact where `fold::assert_deep_class_unions` cannot reach: that assertion holds a configuration's own rules to its own classes, and in the shipped lookup the rules of every configuration sit together, so a rule folded from another configuration's fiber partition can admit part of a class the belt at horizon 4 then proves benign, because it answers those members as the row does.
//!
//! The walk is O(rows) with a bounded set of rules per input and nothing settled: the tables are the settled answer, and what is checked is whether the lookup that ships reads them back. That is what lets it run on every build, keyed on the same inputs as the tables, where the HarfBuzz belt that also proves the shipped order keys on code and on behavior classes and skips a rune edit that mints no new shape.

use std::collections::{HashMap, HashSet};
use std::io::BufRead;

use crate::artifacts::WINDOWS_FORMAT;
use crate::fold::{Rule, first_match, rules_by_input};
use crate::replay::Labels;

/// What one configuration's walk answered: how many rows it tried, and how many of them it tried member by member because an emitted look class admitted their deep class in part.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Report {
    pub rows: u64,
    pub expanded: u64,
}

/// How many disagreements a walk names before it stops: enough to see a shape, few enough that the complaint stays one screen.
const NAMED_DISAGREEMENTS: usize = 5;

/// What one configuration's stream does to the labels its table spells: the marker fold, raw label to the twin the stream wears under this configuration (`emit_gsub._raw_rename_map`), and the deep classes the table's rows stand at, each token with its members in the table's raw label space (`DecisionTable.deep_classes`).
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Context {
    pub renames: Vec<(String, String)>,
    pub classes: Vec<(String, Vec<String>)>,
}

/// A context file read back: one tab-separated record per line, `rename<tab><raw><tab><twin>` for the marker fold and `class<tab><token><tab><member> <member> …` for a deep class. Nothing else is a record, and a line that is not one of the two is refused rather than skipped.
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

/// One emitted rule as the walk tries it: its seat in the shipped order, the five constrained slots as sorted id lists with the class tokens a slot holds whole folded in, `None` for an unconstrained slot, and the outcome's id.
struct EmittedRule {
    seat: usize,
    slots: [Option<Vec<u32>>; 5],
    outcome: u32,
}

/// Which slot of a rule turned a class token away after every slot before it matched, and the token: the cue to try the row member by member.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct Split {
    seat: usize,
    slot: usize,
    token: u32,
}

/// The shipped order keyed by input label, each input's rules in the order they ship, and the split classes indexed so a match that fails on one can tell the cue to expand from a plain miss.
struct Order {
    by_input: HashMap<u32, Vec<EmittedRule>>,
    splits: HashSet<(usize, usize, u32)>,
}

impl Order {
    /// The emitted rules indexed, with every class of the configuration held against every look class: a class the slot admits whole joins the slot under its token, a class it admits in part is recorded as a split.
    fn new(labels: &mut Labels, rules: &[Rule], classes: &[(u32, Vec<u32>)]) -> Self {
        let mut by_input: HashMap<u32, Vec<EmittedRule>> = HashMap::new();
        let mut splits: HashSet<(usize, usize, u32)> = HashSet::new();
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

    /// The first emitted rule of `input` whose five slots admit the window, or `None` where the input stands; a slot that turns a class token away after every slot before it matched is a split class, handed back for the caller to expand.
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

/// One configuration's rows walked against the shipped order.
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
    /// A walk for `config`, whose own table is `table` — read only to name the rule the table answered a disagreeing row with — against `order`, the rules of every configuration in the order they ship, under `context`, the configuration's marker fold and deep classes.
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

    /// Every row of a windows enumeration read from `source` — the head line under [`WINDOWS_FORMAT`], the column line, then one row per line — tried against the shipped order, member by member where an emitted look class admits the row's deep class in part. A row the order answers differently from the table is a disagreement, and the walk stops at [`NAMED_DISAGREEMENTS`] of them; a head this build does not write, or a row that is not seven fields, is a refusal on its own.
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

    /// One disagreement spelled: the row, the member pair it was tried at where a class was expanded, what the table answered and by which of its rules, and what the shipped order answered and by which emitted rule. The table's rule is found in the table's own raw label space — the member tried, or the class's first member, carried back through the marker fold — since the table's rules spell members and raw labels where the row spells a class token and the stream a twin.
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

/// How many members of a slot a complaint spells before counting the rest: a committed-left block runs to hundreds of cells, and a complaint is read, not parsed.
const SPELLED_MEMBERS: usize = 4;

/// One rule as a complaint names it: the input, the five slots with `any` for an unconstrained one and a long class cut to its first members, and the outcome, with the first provenance pointer — for an emitted rule, the table rules it folded from.
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

    /// The fixture's enumeration spelled as the plain payload `artifacts::write_windows` files: the head line, the column line and one row per window.
    fn windows_text(decision: &DecisionTable) -> String {
        let mut text = format!(
            "# {WINDOWS_FORMAT}\t{{\"config\":\"{}\"}}\ninput\tleft\tlookahead1\tlookahead2\tlookahead3\tlookahead4\toutcome\n",
            decision.config
        );
        for row in &decision.transitions {
            for label in row.key() {
                text.push_str(label);
                text.push('\t');
            }
            text.push_str(&row.outcome);
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

    /// A table's own order answers every one of its rows — the fold's partition assertion restated through the shipped-order walk — and the report counts the rows.
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

    /// Two rules of one input swapped in the order, so that a row the later rule answered is now answered by the earlier one, is named: the configuration, the row, the emitted rule that fired and the table's own rule.
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

    /// The rows are renamed into the configuration's stream before a rule is tried: an order spelled in a twin's name answers the same rows once the context renames the raw label to the twin, and answers none of them without the rename.
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

    /// An emitted look class that admits one of the configuration's deep classes in part expands the row: tried member by member, a rule that answers every member as the row does passes, and one that answers a member differently is named with the member it was tried at. Hand-built, because the fixture's alphabet is too small to enumerate a multi-member class.
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

    /// The context file's two records read back, and anything else is refused with its line number.
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

    /// A payload that is not a windows enumeration, or a row that is not seven fields, is refused before any row is judged.
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
