//! Checks the settlement order the font ships against one configuration's rows, for the `replay-emitted` subcommand. The emitter folds every configuration's table into the one settlement lookup the font ships (`emit_gsub._ordered_settle_rules`), and no per-configuration check reads the order that fold produces. The fold's partition assertion and the string replay walk each table's rules in the table's own order, the witness stage first-matches each certificate in that same order, and read-back compares the font to the plan, not the plan to the tables.
//!
//! The walk renames every row of the configuration's enumeration into the labels the configuration's marker lookups produce, tries the emitted rules for the row's input in shipped order, and requires the first rule that matches to give the row's outcome (the input itself when no rule matches). A `#NA` after a letter ([`crate::fold::open_slot`]) means the window does not carry that slot, so the row claims its outcome for every label there and in every later slot, and the walk checks every such continuation the configuration's stream can carry by first match over the shipped order, the row's own configuration's rules included. The continuations at a slot are the labels the input's candidate rules name there, less class tokens and the context's foreign labels, and one more that stands for every label none of them names and for the end of the run; the walk reads on past each of them, a boundary included, to the end of the window. `uni200C` and a `#NA` after a boundary are literal labels. The cross-configuration exchange ([`crate::crossconfig`]) reads `#NA` the same way when it decides which windows a configuration takes in from another; the walk shares only that definition of an open slot and evaluates the continuations itself, so it checks the exchange's reading independently. A row whose shipped-order outcome differs from the table's, on the row or on any continuation, is an error naming the configuration, the row, the continuation, the emitted rule that fired, and the table's own rule with what it answers there.
//!
//! The walk checks every row, not a sample. The rows are the table's own, at the table's grain. Rows are renamed before matching because, under a configuration, every raw label of a rune whose capability the active sets change is renamed to its marker copy, and the emitted rules already use the copy names. The context also lists the labels the configuration's stream never carries, the other configurations' marker copies and their locked copies; they are never continuations, and a row that carries one is an error, so a wrong list stops the walk instead of narrowing what it checks. A deep slot that holds a class token is checked against the whole class. When the rules are indexed, each deep slot of each emitted rule is compared with every class of the configuration. A class that a rule's slot contains entirely matches through its token. A class that the slot contains only in part makes the walk try the row once per member (once per member pair when both deep slots are classes, and once per look3 member, with look4's continuations checked for each, when look4 is open), and each member's first match must give the row's outcome; the fiber construction makes that outcome the same for every member. `fold::assert_deep_class_unions` does not cover this case: it checks a configuration's own rules against its own classes, but the shipped lookup holds every configuration's rules, so a rule folded from another configuration's fiber partition can match part of a class. That is harmless only when the rule gives each member the row's outcome, which is what the member-by-member check verifies.
//!
//! The walk is O(rows), with a bounded number of rules per input, and settles nothing: the tables already hold the settled outcomes, and the walk checks that the shipped lookup reproduces them. A row's candidate rules are its input's rules that admit its left and then its look1 (all of the former when look1 is open), and the walk keeps both lists for the next row while those labels agree; the rows arrive in key order, so each list is built once per block. A row whose first candidate to pass the slots before its open slot constrains nothing from there on costs what a row without an open slot does, since that rule answers every continuation. Only a row whose deciding rule constrains the open slot or a later one takes the continuation search, which the report counts. The search's scratch is reused from row to row and is bounded by the largest input's emitted rules. That makes the walk cheap enough to run on every build, keyed on the same inputs as the tables. The HarfBuzz conformance sweep also checks the shipped order, but its key (`artifact_cycle.conform_skip_fingerprint`) covers the compile code and the emitted lookup's behavior classes, not the runes, so it skips a rune edit that adds no new behavior class.

use std::io::BufRead;

use crate::artifacts::WINDOWS_FORMAT;
use crate::fold::{NA_LABEL, Rule, first_match, open_slot, rules_by_input};
use crate::hash::HashMap;
use crate::replay::Labels;

/// The result of one configuration's walk: the rows it checked, how many of them it checked member by member because an emitted lookahead class contained their deep class only in part, how many leave a slot open after a letter, and how many of those took the continuation search because their deciding rule constrains the open slot or a later one.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Report {
    pub rows: u64,
    pub checked_per_member: u64,
    pub open_rows: u64,
    pub continued: u64,
}

/// How many disagreements a walk reports before it stops, so the error message stays short.
const NAMED_DISAGREEMENTS: usize = 5;

/// A label flag: the label is a deep class's token.
const CLASS: u8 = 1;

/// A label flag: the configuration's stream never carries the label, because the context lists it as foreign or the configuration's marker renames replace it.
const FOREIGN: u8 = 2;

/// How one configuration's marker lookups relabel its table's rows. `renames` maps each raw label to the marker copy used under this configuration (`model.raw_rename_map`). `classes` lists the deep classes in the table's rows, each token with its members as raw labels (`DecisionTable.deep_classes`). `foreign` lists the labels the shipped lookup can name that this configuration's stream never carries: the marker copies and locked copies every other feature state of a marker rune is spelled with.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Context {
    pub renames: Vec<(String, String)>,
    pub classes: Vec<(String, Vec<String>)>,
    pub foreign: Vec<String>,
}

/// Parses a context file: one tab-separated record per line, `rename<tab><raw><tab><copy>`, `class<tab><token><tab><member> <member> …`, or `foreign<tab><label>`. Any other line is an error.
pub fn read_context(text: &str) -> Result<Context, String> {
    let mut context = Context::default();
    for (number, line) in text.lines().enumerate() {
        let fields: Vec<&str> = line.split('\t').collect();
        match fields.as_slice() {
            ["rename", raw, copy] if !raw.is_empty() && !copy.is_empty() => {
                context
                    .renames
                    .push(((*raw).to_owned(), (*copy).to_owned()));
            }
            ["class", token, members] if !token.is_empty() && !members.is_empty() => {
                context.classes.push((
                    (*token).to_owned(),
                    members.split(' ').map(str::to_owned).collect(),
                ));
            }
            ["foreign", label] if !label.is_empty() => {
                context.foreign.push((*label).to_owned());
            }
            _ => {
                return Err(format!(
                    "context line {} is not a rename, class, or foreign record: {line:?}",
                    number + 1
                ));
            }
        }
    }
    Ok(context)
}

/// One emitted rule prepared for matching: its index in the shipped order, the five context slots as sorted label ids (`None` for an unconstrained slot, and with the tokens of classes the slot contains entirely added), and the outcome's id.
struct EmittedRule {
    position: usize,
    slots: [Option<Vec<u32>>; 5],
    outcome: u32,
}

impl EmittedRule {
    /// Whether the rule constrains none of its slots from `slot` on, so that it matches every continuation from there.
    fn blind_from(&self, slot: usize) -> bool {
        self.slots[slot..].iter().all(Option::is_none)
    }
}

/// The rule, slot, and class token where a deep slot rejected a class token it contains in part, after every earlier slot matched. It tells the caller to try the row member by member.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct PartialClass {
    position: usize,
    slot: usize,
    token: u32,
}

/// The emitted rules grouped by input label, each input's rules in shipped order, and for each rule's two deep slots a bitset over the configuration's classes (`class_of` gives a token's bit) marking the classes the slot contains in part, so that [`Order::admits`] can tell a partial class match from a plain miss.
struct Order {
    by_input: HashMap<u32, Vec<EmittedRule>>,
    class_of: HashMap<u32, usize>,
    words: usize,
    partial_classes: Vec<u64>,
}

impl Order {
    /// Indexes the emitted rules. Every class of the configuration is compared with every deep slot: a class the slot contains entirely is added to the slot as its token, and a class it contains in part is recorded as a partial class.
    fn new(labels: &mut Labels, rules: &[Rule], classes: &[(u32, Vec<u32>)]) -> Self {
        let mut by_input: HashMap<u32, Vec<EmittedRule>> = HashMap::default();
        let words = classes.len().div_ceil(64);
        let mut partial_classes = vec![0u64; rules.len() * 2 * words];
        for (position, rule) in rules.iter().enumerate() {
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
                for (ordinal, (token, members)) in classes.iter().enumerate() {
                    let inside = members
                        .iter()
                        .filter(|member| ids.binary_search(member).is_ok())
                        .count();
                    if inside == members.len() {
                        whole.push(*token);
                    } else if inside > 0 {
                        partial_classes[(position * 2 + index - 3) * words + ordinal / 64] |=
                            1 << (ordinal % 64);
                    }
                }
                if !whole.is_empty() {
                    ids.extend(whole);
                    ids.sort_unstable();
                }
            }
            let outcome = labels.intern(&rule.outcome);
            by_input.entry(input).or_default().push(EmittedRule {
                position,
                slots,
                outcome,
            });
        }
        Self {
            by_input,
            class_of: classes
                .iter()
                .enumerate()
                .map(|(ordinal, (token, _))| (*token, ordinal))
                .collect(),
            words,
            partial_classes,
        }
    }

    /// Whether the emitted rule at `position` contains the class `token` in part at deep `slot`.
    fn partial(&self, position: usize, slot: usize, token: u32) -> bool {
        self.class_of.get(&token).is_some_and(|ordinal| {
            self.partial_classes[(position * 2 + slot - 3) * self.words + ordinal / 64]
                & (1 << (ordinal % 64))
                != 0
        })
    }

    /// The emitted rules for `input`, in shipped order.
    fn rules(&self, input: u32) -> &[EmittedRule] {
        self.by_input.get(&input).map_or(&[][..], Vec::as_slice)
    }

    /// Whether `rule` admits `window` at every slot before `end`. When a deep slot rejects a class token it contains in part, after every earlier slot matched, this returns that [`PartialClass`] so the caller can check the row member by member.
    fn admits(
        &self,
        rule: &EmittedRule,
        window: &[u32; 5],
        end: usize,
    ) -> Result<bool, PartialClass> {
        for (index, (slot, label)) in rule.slots[..end].iter().zip(window).enumerate() {
            let Some(members) = slot else {
                continue;
            };
            if members.binary_search(label).is_ok() {
                continue;
            }
            if index >= 3 && self.partial(rule.position, index, *label) {
                return Err(PartialClass {
                    position: rule.position,
                    slot: index,
                    token: *label,
                });
            }
            return Ok(false);
        }
        Ok(true)
    }

    /// The first of `input`'s emitted rules among `candidates` (indices into [`Order::rules`], in shipped order) whose five slots match `window`, or `None` when none matches, or the [`PartialClass`] [`Order::admits`] reports.
    fn first(
        &self,
        input: u32,
        candidates: &[u32],
        window: [u32; 5],
    ) -> Result<Option<&EmittedRule>, PartialClass> {
        let rules = self.rules(input);
        for &at in candidates {
            let rule = &rules[at as usize];
            if self.admits(rule, &window, 5)? {
                return Ok(Some(rule));
            }
        }
        Ok(None)
    }
}

/// Where a row with an open slot is answered wrongly: the continuation's labels from the open slot on (`None` for a label no candidate rule names there, or the end of the run), how many of them the answer read (none when one rule or the input answers every continuation), and the emitted rule that fired there with its outcome.
#[derive(Clone, Copy, Debug)]
struct Continuation {
    path: [Option<u32>; 4],
    depth: usize,
    fired: Option<(usize, u32)>,
}

/// What every continuation of one row must reach: the row's input and outcome, and the rule slot (1 to 4) its key leaves open.
#[derive(Clone, Copy)]
struct Goal {
    input: u32,
    outcome: u32,
    from: usize,
}

/// The continuation search's scratch, reused from row to row. `lists` holds the candidate rules of each level of the search as stacked regions, `atoms` and `masks` each level's continuation labels and the rule sets they admit, and `path` the continuation being read.
#[derive(Default)]
struct Search {
    lists: Vec<u32>,
    atoms: Vec<u32>,
    masks: Vec<u64>,
    path: [Option<u32>; 4],
}

impl Search {
    /// Reads every continuation of the rules in `lists[lo..hi]` from `slot` on and returns the first whose answer is not the goal's outcome. The answer at a continuation is its first candidate's outcome once that rule constrains nothing further (the input when no candidate is left). Otherwise the candidates are cut after their first rule that constrains nothing from `slot` on, since no later rule is first on any continuation, and each label they name at `slot` (and one more for every label they do not) narrows them to the rules that admit it. Two labels that admit the same rules lead to the same answers, so only the first is read when the list fits a mask.
    fn read(
        &mut self,
        rules: &[EmittedRule],
        flags: &[u8],
        goal: Goal,
        lo: usize,
        hi: usize,
        slot: usize,
    ) -> Option<Continuation> {
        if slot == 5 || lo == hi || rules[self.lists[lo] as usize].blind_from(slot) {
            let fired = (lo < hi).then(|| &rules[self.lists[lo] as usize]);
            let answer = fired.map_or(goal.input, |rule| rule.outcome);
            return (answer != goal.outcome).then(|| Continuation {
                path: self.path,
                depth: slot - goal.from,
                fired: fired.map(|rule| (rule.position, rule.outcome)),
            });
        }
        let reach = (lo..hi)
            .find(|&at| rules[self.lists[at] as usize].blind_from(slot))
            .map_or(hi, |at| at + 1);
        let atoms_lo = self.atoms.len();
        for at in lo..reach {
            if let Some(members) = &rules[self.lists[at] as usize].slots[slot] {
                self.atoms.extend(members.iter().copied().filter(|label| {
                    flags
                        .get(*label as usize)
                        .is_none_or(|flag| flag & (CLASS | FOREIGN) == 0)
                }));
            }
        }
        self.atoms[atoms_lo..].sort_unstable();
        let mut kept = atoms_lo;
        for at in atoms_lo..self.atoms.len() {
            if at == atoms_lo || self.atoms[at] != self.atoms[kept - 1] {
                self.atoms[kept] = self.atoms[at];
                kept += 1;
            }
        }
        self.atoms.truncate(kept);
        let masks_lo = self.masks.len();
        let masked = reach - lo <= 64;
        let mut found = None;
        for at in atoms_lo..=kept {
            let atom = (at < kept).then(|| self.atoms[at]);
            let mut mask = 0u64;
            for (bit, place) in (lo..reach).enumerate() {
                let index = self.lists[place];
                let admitted = match (&rules[index as usize].slots[slot], atom) {
                    (None, _) => true,
                    (Some(members), Some(label)) => members.binary_search(&label).is_ok(),
                    (Some(_), None) => false,
                };
                if admitted {
                    self.lists.push(index);
                    if masked {
                        mask |= 1 << bit;
                    }
                }
            }
            if masked {
                if self.masks[masks_lo..].contains(&mask) {
                    self.lists.truncate(hi);
                    continue;
                }
                self.masks.push(mask);
            }
            self.path[slot - goal.from] = atom;
            let end = self.lists.len();
            found = self.read(rules, flags, goal, hi, end, slot + 1);
            self.lists.truncate(hi);
            if found.is_some() {
                break;
            }
        }
        self.atoms.truncate(atoms_lo);
        self.masks.truncate(masks_lo);
        found
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
    na: u32,
    flags: Vec<u8>,
    left_key: (u32, u32),
    by_left: Vec<u32>,
    look1_key: (u32, u32, u32),
    by_look1: Vec<u32>,
    search: Search,
}

impl<'a> Walk<'a> {
    /// A walk for `config`. `table` is the configuration's own table, read only to name the table's rule in a disagreement. `order` is every configuration's rules in shipped order. `context` holds the configuration's marker renames, deep classes and foreign labels.
    pub fn new(config: &'a str, table: &'a [Rule], order: &'a [Rule], context: &Context) -> Self {
        let mut labels = Labels::new();
        let renames: HashMap<u32, u32> = context
            .renames
            .iter()
            .map(|(raw, copy)| (labels.intern(raw), labels.intern(copy)))
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
        let foreign: Vec<u32> = context
            .foreign
            .iter()
            .map(|label| labels.intern(label))
            .collect();
        let na = labels.intern(NA_LABEL);
        let mut flags = vec![0u8; labels.len()];
        for (token, _) in &classes {
            flags[*token as usize] |= CLASS;
        }
        for label in renames.keys().chain(&foreign) {
            flags[*label as usize] |= FOREIGN;
        }
        let raw_labels: HashMap<u32, u32> =
            renames.iter().map(|(raw, copy)| (*copy, *raw)).collect();
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
            na,
            flags,
            left_key: (u32::MAX, u32::MAX),
            by_left: Vec::new(),
            look1_key: (u32::MAX, u32::MAX, u32::MAX),
            by_look1: Vec::new(),
            search: Search::default(),
        }
    }

    /// Checks every row of a windows enumeration read from `source` (the head line under [`WINDOWS_FORMAT`], the column line, then one row per line) against the shipped order, at every continuation of a row's open slot, and member by member where an emitted lookahead class contains the row's deep class only in part. A row whose shipped-order outcome differs from the table's is a disagreement, and the walk stops after [`NAMED_DISAGREEMENTS`] of them. A head line with another format, a wrong column line, a row without seven fields, or a row that carries a label its context lists as foreign is an error by itself.
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
            if let Some(foreign) = [input, right1, right2, right3, right4]
                .into_iter()
                .find(|label| self.flagged(*label, FOREIGN))
            {
                return Err(format!(
                    "{}: row {} carries {}, which its context lists as a label its stream never carries",
                    self.config,
                    self.spell_row(raw),
                    self.labels.text(foreign)
                ));
            }
            self.narrow(input, left, right1);
            let window = [left, right1, right2, right3, right4];
            match open_slot(&[raw[0], raw[1], raw[2], raw[3], raw[4], raw[5]]) {
                None => self.check_closed(raw, input, window, outcome, &by_input, &mut report)?,
                Some(open) => {
                    report.open_rows += 1;
                    let goal = Goal {
                        input,
                        outcome,
                        from: open - 1,
                    };
                    self.check_open_row(raw, goal, window, &by_input, &mut report)?;
                }
            }
            if self.disagreements.len() >= NAMED_DISAGREEMENTS {
                return Err(self.error_message());
            }
        }
        if self.disagreements.is_empty() {
            Ok(report)
        } else {
            Err(self.error_message())
        }
    }

    fn flagged(&self, label: u32, flag: u8) -> bool {
        self.flags
            .get(label as usize)
            .is_some_and(|flags| flags & flag != 0)
    }

    /// Keeps `by_left` as the indices of `input`'s emitted rules whose backtrack admits `left`, and `by_look1` as those of them whose look1 admits `right1` (all of them when `right1` is `#NA`, which a key leaves open), rebuilding each only when its labels change.
    fn narrow(&mut self, input: u32, left: u32, right1: u32) {
        if self.look1_key == (input, left, right1) {
            return;
        }
        let rules = self.order.rules(input);
        if self.left_key != (input, left) {
            self.left_key = (input, left);
            self.by_left.clear();
            self.by_left.extend(
                (0u32..)
                    .zip(rules)
                    .filter(|(_, rule)| {
                        rule.slots[0]
                            .as_ref()
                            .is_none_or(|members| members.binary_search(&left).is_ok())
                    })
                    .map(|(at, _)| at),
            );
        }
        self.look1_key = (input, left, right1);
        self.by_look1.clear();
        let open = right1 == self.na;
        self.by_look1
            .extend(self.by_left.iter().copied().filter(|&at| {
                open || rules[at as usize].slots[1]
                    .as_ref()
                    .is_none_or(|members| members.binary_search(&right1).is_ok())
            }));
    }

    /// Checks a row whose key carries every slot it reads, member by member when an emitted class contains its deep class only in part.
    fn check_closed(
        &mut self,
        raw: [&str; 7],
        input: u32,
        window: [u32; 5],
        outcome: u32,
        by_input: &HashMap<&str, Vec<(usize, &Rule)>>,
        report: &mut Report,
    ) -> Result<(), String> {
        let [left, right1, right2, right3, right4] = window;
        match self.order.first(input, &self.by_look1, window) {
            Ok(fired) => {
                let fired = fired.map(|rule| (rule.position, rule.outcome));
                if fired.map_or(input, |(_, answer)| answer) != outcome {
                    let sentence = self.disagree(raw, None, fired, None, by_input);
                    self.disagreements.push(sentence);
                }
            }
            Err(_) => {
                report.checked_per_member += 1;
                let members3 = self.members(right3);
                let members4 = self.members(right4);
                'members: for member3 in &members3 {
                    for member4 in &members4 {
                        let concrete = [left, right1, right2, *member3, *member4];
                        let fired = self
                            .order
                            .first(input, &self.by_look1, concrete)
                            .map_err(|partial| self.member_refused(partial))?;
                        let fired = fired.map(|rule| (rule.position, rule.outcome));
                        if fired.map_or(input, |(_, answer)| answer) != outcome {
                            let sentence = self.disagree(
                                raw,
                                Some((*member3, *member4)),
                                fired,
                                None,
                                by_input,
                            );
                            self.disagreements.push(sentence);
                            break 'members;
                        }
                    }
                }
            }
        }
        Ok(())
    }

    /// Checks every continuation of a row's open slot, once per member of its look3 class when an emitted class contains that class only in part (look3 is fixed only when look4 is the open slot).
    fn check_open_row(
        &mut self,
        raw: [&str; 7],
        goal: Goal,
        window: [u32; 5],
        by_input: &HashMap<&str, Vec<(usize, &Rule)>>,
        report: &mut Report,
    ) -> Result<(), String> {
        let mut continued = false;
        match self.check_open(goal, window, &mut continued) {
            Ok(None) => {}
            Ok(Some(found)) => {
                let sentence =
                    self.disagree(raw, None, found.fired, Some((goal.from, found)), by_input);
                self.disagreements.push(sentence);
            }
            Err(_) => {
                report.checked_per_member += 1;
                let [left, right1, right2, right3, right4] = window;
                let count = self.classes.get(&right3).map_or(1, Vec::len);
                for member in 0..count {
                    let member3 = self
                        .classes
                        .get(&right3)
                        .map_or(right3, |members| members[member]);
                    let concrete = [left, right1, right2, member3, right4];
                    match self.check_open(goal, concrete, &mut continued) {
                        Ok(None) => {}
                        Ok(Some(found)) => {
                            let sentence = self.disagree(
                                raw,
                                Some((member3, right4)),
                                found.fired,
                                Some((goal.from, found)),
                                by_input,
                            );
                            self.disagreements.push(sentence);
                            break;
                        }
                        Err(partial) => return Err(self.member_refused(partial)),
                    }
                }
            }
        }
        if continued {
            report.continued += 1;
        }
        Ok(())
    }

    /// The first continuation of `window`'s open slot on which the shipped order does not give the goal's outcome, or `None` when every continuation gets it. The candidates are `by_look1`. The first one that passes the slots before the open slot answers every continuation when it constrains nothing from there on (so does the input when none passes); otherwise it and the later candidates that pass those slots, up to the first that constrains nothing from there on, go to the continuation search, and `continued` is set.
    fn check_open(
        &mut self,
        goal: Goal,
        window: [u32; 5],
        continued: &mut bool,
    ) -> Result<Option<Continuation>, PartialClass> {
        let rules = self.order.rules(goal.input);
        let mut start = None;
        for (place, &at) in self.by_look1.iter().enumerate() {
            if self.order.admits(&rules[at as usize], &window, goal.from)? {
                start = Some(place);
                break;
            }
        }
        let every = |fired: Option<&EmittedRule>| {
            let answer = fired.map_or(goal.input, |rule| rule.outcome);
            (answer != goal.outcome).then(|| Continuation {
                path: [None; 4],
                depth: 0,
                fired: fired.map(|rule| (rule.position, rule.outcome)),
            })
        };
        let Some(start) = start else {
            return Ok(every(None));
        };
        let first = &rules[self.by_look1[start] as usize];
        if first.blind_from(goal.from) {
            return Ok(every(Some(first)));
        }
        *continued = true;
        self.search.lists.clear();
        for &at in &self.by_look1[start..] {
            let rule = &rules[at as usize];
            if self.order.admits(rule, &window, goal.from)? {
                self.search.lists.push(at);
                if rule.blind_from(goal.from) {
                    break;
                }
            }
        }
        self.search.atoms.clear();
        self.search.masks.clear();
        let end = self.search.lists.len();
        Ok(self
            .search
            .read(rules, &self.flags, goal, 0, end, goal.from))
    }

    /// A class's members, or the label alone when it is not a class token.
    fn members(&self, label: u32) -> Vec<u32> {
        self.classes
            .get(&label)
            .cloned()
            .unwrap_or_else(|| vec![label])
    }

    fn member_refused(&self, partial: PartialClass) -> String {
        format!(
            "{}: emitted rule {} turns {} away at its {} as a class, though it is a member label",
            self.config,
            partial.position,
            self.labels.text(partial.token),
            slot_name(partial.slot)
        )
    }

    /// Formats one disagreement: the row, the member pair it was tried at when the row was checked member by member, the continuation of its open slot it was answered wrongly at, the table's outcome and the table rule that matches the same window, and the emitted rule that fired with its outcome. The table's rule is looked up with raw labels, using the member tried (or the class's first member) and the continuation's labels mapped back through the marker renames, because the table's rules name members and raw labels where the row names a class token and the stream names a copy; a continuation label no candidate names is looked up as `#NA`, which no rule names. Where the table's rule answers that continuation with another outcome, the sentence says so, which marks a fault in the table's own rules rather than in the shipped order.
    fn disagree(
        &self,
        raw: [&str; 7],
        members: Option<(u32, u32)>,
        fired: Option<(usize, u32)>,
        continuation: Option<(usize, Continuation)>,
        by_input: &HashMap<&str, Vec<(usize, &Rule)>>,
    ) -> String {
        let spelled = |label: u32| {
            self.labels
                .text(self.raw_labels.get(&label).copied().unwrap_or(label))
        };
        let deep = |slot: usize, member: Option<u32>| -> &str {
            match member {
                Some(member) => spelled(member),
                None => self
                    .representatives
                    .get(raw[slot])
                    .map_or(raw[slot], String::as_str),
            }
        };
        let mut key = [
            raw[0],
            raw[1],
            raw[2],
            raw[3],
            deep(4, members.map(|(member3, _)| member3)),
            deep(5, members.map(|(_, member4)| member4)),
        ];
        let mut reading = String::new();
        if let Some((from, found)) = continuation {
            for (offset, slot) in (from + 1..6).enumerate() {
                key[slot] = match found.path.get(offset) {
                    Some(Some(label)) if offset < found.depth => spelled(*label),
                    _ => NA_LABEL,
                };
            }
            reading = if found.depth == 0 {
                format!(" at every continuation of its open {}", slot_name(from))
            } else {
                let labels: Vec<&str> = found.path[..found.depth]
                    .iter()
                    .map(|atom| atom.map_or("any other", |label| self.labels.text(label)))
                    .collect();
                format!(
                    " at the continuation ({}) of its open {}",
                    labels.join(", "),
                    slot_name(from)
                )
            };
        }
        let own = first_match(by_input, key).map_or_else(
            || "no rule of its own table".to_owned(),
            |position| {
                let rule = &self.table[position];
                let differs = continuation.is_some_and(|(_, found)| found.depth > 0)
                    && *rule.outcome != *raw[6];
                format!(
                    "its table's rule {position} ({}){}",
                    rule_repr(rule),
                    if differs {
                        format!(", which answers that continuation {}", rule.outcome)
                    } else {
                        String::new()
                    }
                )
            },
        );
        let shipped = match fired {
            Some((position, answer)) => format!(
                "emitted rule {position} ({}) fires first and answers {}",
                rule_repr(&self.shipped[position]),
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
            "{}: row {}{at}{reading} settles to {} by {}, but in the shipped order {shipped}",
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

    fn error_message(&self) -> String {
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

/// How many members of a slot an error message lists before it counts the rest. A committed-left block's backtrack class can be far too long to list in full.
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

    const DEFAULT_MODES: EnumerationModes = EnumerationModes {
        simulated_prospect: true,
        follower_prefer_slots: true,
        deep_classes: true,
    };

    fn table(index: &SpecIndex, features: &[Sym]) -> DecisionTable {
        let product =
            enumerate_transitions(index, features, DEFAULT_MODES).expect("the fixpoint closes");
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
            foreign: Vec::new(),
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
        assert_eq!(report.checked_per_member, 0);
        assert!(report.open_rows > 0);
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
                if let Err(error) = walk(&decision, &order, &context) {
                    found = Some(error);
                    break 'pairs;
                }
            }
        }
        let error = found.expect("some swap of the fixture's rules moves a row");
        assert!(error.contains("shipped-order disagreement"), "{error}");
        assert!(error.contains("default: row ("), "{error}");
        assert!(error.contains("emitted rule "), "{error}");
        assert!(error.contains("its table's rule "), "{error}");
    }

    /// Rows are renamed into the configuration's labels before matching: an order written with a copy's name passes once the context renames the raw label to the copy, and fails without the rename.
    #[test]
    fn a_rename_carries_the_rows_into_the_streams_labels() {
        let index = fixtures::mini();
        let decision = table(&index, &[]);
        let raw: Rc<str> = Rc::from("qsPea");
        let copy: Rc<str> = Rc::from("qsPea.ss03");
        let relabel = |label: &Rc<str>| {
            if *label == raw {
                Rc::clone(&copy)
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
            walk(&decision, &order, &context).expect_err("the raw rows miss the copy's rules");
        assert!(without.contains("qsPea"), "{without}");
        context
            .renames
            .push(("qsPea".to_owned(), "qsPea.ss03".to_owned()));
        walk(&decision, &order, &context).expect("renamed, every row is answered");
    }

    /// When an emitted lookahead class contains one of the configuration's deep classes only in part, the row is tried member by member: a rule that gives every member the row's outcome passes, and one that gives a member a different outcome is reported with that member. The rules are built by hand because the fixture's alphabet is too small to produce a multi-member class.
    #[test]
    fn a_partial_deep_class_is_tried_member_by_member() {
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
                checked_per_member: 0,
                open_rows: 1,
                continued: 0
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
                checked_per_member: 1,
                open_rows: 1,
                continued: 0
            }
        );
        let partial = [rule(&["qsTea"], "qsPea.partial"), whole[0].clone()];
        let mut walk = Walk::new("default", &whole, &partial, &context);
        let error = walk
            .walk(&mut payload.as_bytes())
            .expect_err("one member is answered differently");
        assert!(error.contains("tried at (qsTea, #NA)"), "{error}");
        assert!(error.contains("emitted rule 0"), "{error}");
        assert!(error.contains("qsPea.partial"), "{error}");
    }

    /// A rule for `qsPea` with no backtrack, the four lookahead slots given (`None` for unconstrained), and `outcome`.
    fn hand_rule(look: [Option<&[&str]>; 4], outcome: &str) -> Rule {
        let slot = |members: Option<&[&str]>| {
            members.map(|members| members.iter().map(|member| Rc::from(*member)).collect())
        };
        Rule {
            input_glyph: Rc::from("qsPea"),
            backtrack: None,
            look1: slot(look[0]),
            look2: slot(look[1]),
            look3: slot(look[2]),
            look4: slot(look[3]),
            outcome: Rc::from(outcome),
            provenance: Vec::new(),
            joint: false,
        }
    }

    /// A windows payload holding the given rows, each tab-separated.
    fn payload(rows: &[&str]) -> String {
        let mut text = format!(
            "# {WINDOWS_FORMAT}\t{{}}\ninput\tleft\tlookahead1\tlookahead2\tlookahead3\tlookahead4\toutcome\n"
        );
        for row in rows {
            text.push_str(row);
            text.push('\n');
        }
        text
    }

    fn walk_rows(
        table: &[Rule],
        order: &[Rule],
        context: &Context,
        rows: &[&str],
    ) -> Result<Report, String> {
        Walk::new("default", table, order, context).walk(&mut payload(rows).as_bytes())
    }

    const OPEN_AT_LOOK3: &str = "qsPea\t#EDGE\tqsPea\tqsPea\t#NA\t#NA\tqsPea.whole";

    /// A `#NA` after a letter stands for every label there: a rule that answers one continuation of the open slot differently fails the walk, naming that continuation, though it never matches the row's literal `#NA`. With the row's outcome, the same rule passes, and the walk counts the row as open and searched.
    #[test]
    fn an_open_slot_is_checked_at_every_continuation() {
        let catch_all = hand_rule([None; 4], "qsPea.whole");
        let table = [catch_all.clone()];
        let wrong = [
            hand_rule([None, None, Some(&["qsTea"]), None], "qsPea.other"),
            catch_all.clone(),
        ];
        let error = walk_rows(&table, &wrong, &Context::default(), &[OPEN_AT_LOOK3])
            .expect_err("the qsTea continuation is answered differently");
        assert!(error.contains("at the continuation (qsTea"), "{error}");
        assert!(error.contains("of its open look3"), "{error}");
        assert!(error.contains("emitted rule 0"), "{error}");
        assert!(error.contains("qsPea.other"), "{error}");
        let right = [
            hand_rule([None, None, Some(&["qsTea"]), None], "qsPea.whole"),
            catch_all,
        ];
        let report = walk_rows(&table, &right, &Context::default(), &[OPEN_AT_LOOK3])
            .expect("every continuation gets the row's outcome");
        assert_eq!(
            report,
            Report {
                rows: 1,
                checked_per_member: 0,
                open_rows: 1,
                continued: 1
            }
        );
    }

    /// A continuation that no rule of the input names reaches no rule when every rule constrains the open slot, so the input stands there.
    #[test]
    fn a_continuation_no_rule_names_reaches_the_input() {
        let order = [hand_rule(
            [None, None, Some(&["qsTea"]), None],
            "qsPea.whole",
        )];
        let error = walk_rows(&order, &order, &Context::default(), &[OPEN_AT_LOOK3])
            .expect_err("any other label leaves the input");
        assert!(error.contains("at the continuation (any other)"), "{error}");
        assert!(error.contains("no emitted rule matches"), "{error}");
    }

    /// A label the context lists as foreign is never a continuation, since the configuration's stream never carries it, and a row that carries one is refused outright.
    #[test]
    fn a_label_the_stream_never_carries_is_not_a_continuation() {
        let catch_all = hand_rule([None; 4], "qsPea.whole");
        let order = [
            hand_rule([None, None, Some(&["qsTea.ss03"]), None], "qsPea.other"),
            catch_all.clone(),
        ];
        let table = [catch_all];
        let foreign = read_context("foreign\tqsTea.ss03\n").expect("one record");
        walk_rows(&table, &order, &foreign, &[OPEN_AT_LOOK3])
            .expect("the copy is no continuation of this stream");
        let error = walk_rows(&table, &order, &Context::default(), &[OPEN_AT_LOOK3])
            .expect_err("unlisted, the copy is a continuation");
        assert!(error.contains("at the continuation (qsTea.ss03"), "{error}");
        let error = walk_rows(
            &table,
            &order,
            &foreign,
            &["qsPea\t#EDGE\tqsPea\tqsTea.ss03\t#NA\t#NA\tqsPea.whole"],
        )
        .expect_err("a row carrying a foreign label");
        assert!(error.contains("never carries"), "{error}");
    }

    /// A `#NA` after a boundary is a literal label, since no window reads past a boundary, so a rule that constrains the slot after it never fires on the row.
    #[test]
    fn a_slot_after_a_boundary_stays_closed() {
        let catch_all = hand_rule([None; 4], "qsPea.whole");
        let order = [
            hand_rule([None, None, Some(&["qsTea"]), None], "qsPea.other"),
            catch_all.clone(),
        ];
        let report = walk_rows(
            &[catch_all],
            &order,
            &Context::default(),
            &["qsPea\t#EDGE\tqsPea\tspace\t#NA\t#NA\tqsPea.whole"],
        )
        .expect("the row's slots after space are not continuations");
        assert_eq!(report.open_rows, 0);
    }

    /// When look4 is open and an emitted class contains the row's look3 class only in part, each member is tried with every continuation of look4. The partial rule here follows a rule that constrains look4, so only the continuation search reaches it.
    #[test]
    fn a_partial_class_behind_an_open_look4_is_tried_member_by_member() {
        let context = read_context("class\t#Cabc\tqsPea qsTea\n").expect("one class");
        let row = "qsPea\t#EDGE\tqsPea\tqsPea\t#Cabc\t#NA\tqsPea.whole";
        let catch_all = hand_rule([None; 4], "qsPea.whole");
        let order = |outcome: &str| {
            [
                hand_rule(
                    [None, None, Some(&["qsPea", "qsTea"]), Some(&["qsMay"])],
                    "qsPea.whole",
                ),
                hand_rule([None, None, Some(&["qsTea"]), None], outcome),
                catch_all.clone(),
            ]
        };
        let table = [catch_all.clone()];
        let report = walk_rows(&table, &order("qsPea.whole"), &context, &[row])
            .expect("every member's continuations get the row's outcome");
        assert_eq!(
            report,
            Report {
                rows: 1,
                checked_per_member: 1,
                open_rows: 1,
                continued: 1
            }
        );
        let error = walk_rows(&table, &order("qsPea.partial"), &context, &[row])
            .expect_err("one member's other continuations are answered differently");
        assert!(error.contains("tried at (qsTea, #NA)"), "{error}");
        assert!(error.contains("at the continuation (any other)"), "{error}");
        assert!(error.contains("emitted rule 1"), "{error}");
        assert!(error.contains("qsPea.partial"), "{error}");
    }

    /// The row's own table is checked on its continuations too: when the shipped order is the table's own and one of its rules answers a continuation differently, the walk fails, and the message says the table's own rule answers that continuation so.
    #[test]
    fn the_row_s_own_rules_answer_its_continuations() {
        let order = [
            hand_rule([None, None, Some(&["qsTea"]), None], "qsPea.other"),
            hand_rule([None; 4], "qsPea.whole"),
        ];
        let error = walk_rows(&order, &order, &Context::default(), &[OPEN_AT_LOOK3])
            .expect_err("the table's own rule answers the continuation differently");
        assert!(
            error.contains("which answers that continuation qsPea.other"),
            "{error}"
        );
    }

    /// The walk passes an order exactly when brute force does: [`first_match`] over every row of the fixture, every member of a deep class it holds, and every continuation of its open slot over every label the order names in a lookahead slot plus one it names nowhere. The orders are the table's own, swaps of two of its rules of one input, and the table's own behind a rule that copies an open row's carried slots and constrains its open slot or the next, with the row's outcome or another, so some pass, some fail, and some fail only at a continuation.
    #[test]
    fn the_walk_agrees_with_first_match_over_every_continuation() {
        const ORDERS: usize = 24;
        const OTHER: &str = "#OTHER";
        let index = fixtures::mini();
        let decision = table(&index, &[]);
        let context = context_of(&decision);
        let mut orders = vec![decision.rules.clone()];
        'pairs: for first in 0..decision.rules.len() {
            for second in first + 1..decision.rules.len() {
                let (a, b) = (&decision.rules[first], &decision.rules[second]);
                if a.input_glyph != b.input_glyph || a.outcome == b.outcome {
                    continue;
                }
                let mut order = decision.rules.clone();
                order.swap(first, second);
                orders.push(order);
                if orders.len() == ORDERS {
                    break 'pairs;
                }
            }
        }
        let opened: Vec<([&str; 6], &str, usize)> = decision
            .transitions
            .iter()
            .map(|row| (row.key(&decision.labels), &**decision.outcome(row)))
            .filter_map(|(key, outcome)| open_slot(&key).map(|open| (key, outcome, open)))
            .filter(|(key, _, open)| (1..*open).all(|slot| !key[slot].starts_with('#')))
            .take(6)
            .collect();
        for (key, outcome, open) in &opened {
            let perturbed = format!("{}.perturbed", key[0]);
            for (reading, answer) in [
                (*open, *outcome),
                (*open, &perturbed),
                (open + 1, &perturbed),
            ] {
                if reading > 5 {
                    continue;
                }
                let slot = |at: usize| -> Option<Vec<Rc<str>>> {
                    if at < *open {
                        Some(vec![Rc::from(key[at])])
                    } else if at == reading {
                        Some(vec![Rc::from(key[0])])
                    } else {
                        None
                    }
                };
                let ahead = Rule {
                    input_glyph: Rc::from(key[0]),
                    backtrack: slot(1),
                    look1: slot(2),
                    look2: slot(3),
                    look3: slot(4),
                    look4: slot(5),
                    outcome: Rc::from(answer),
                    provenance: Vec::new(),
                    joint: false,
                };
                orders.push([vec![ahead], decision.rules.clone()].concat());
            }
        }
        let classes: HashMap<&str, Vec<&str>> = decision
            .deep_classes
            .iter()
            .map(|(token, members)| (token.as_str(), members.iter().map(String::as_str).collect()))
            .collect();
        let brute = |order: &[Rule]| -> bool {
            let by_input = rules_by_input(order);
            let mut universe: Vec<&str> = order
                .iter()
                .flat_map(|rule| [&rule.look1, &rule.look2, &rule.look3, &rule.look4])
                .flatten()
                .flatten()
                .map(|label| &**label)
                .collect();
            universe.push(OTHER);
            universe.sort_unstable();
            universe.dedup();
            decision.transitions.iter().all(|row| {
                let key = row.key(&decision.labels);
                let outcome = decision.outcome(row);
                let open = open_slot(&key).unwrap_or(6);
                let choices: Vec<Vec<&str>> = (0..6)
                    .map(|slot| {
                        if slot >= open {
                            universe.clone()
                        } else {
                            classes
                                .get(key[slot])
                                .cloned()
                                .unwrap_or_else(|| vec![key[slot]])
                        }
                    })
                    .collect();
                let mut picks = [0usize; 6];
                loop {
                    let window: [&str; 6] = std::array::from_fn(|slot| choices[slot][picks[slot]]);
                    let answer = first_match(&by_input, window)
                        .map_or(window[0], |position| &*order[position].outcome);
                    if *answer != **outcome {
                        return false;
                    }
                    let Some(slot) = (0..6)
                        .rev()
                        .find(|&slot| picks[slot] + 1 < choices[slot].len())
                    else {
                        return true;
                    };
                    picks[slot] += 1;
                    for later in &mut picks[slot + 1..] {
                        *later = 0;
                    }
                }
            })
        };
        let mut verdicts = Vec::new();
        for order in &orders {
            let walked = walk(&decision, order, &context).is_ok();
            assert_eq!(walked, brute(order), "the walk and brute force disagree");
            verdicts.push(walked);
        }
        assert!(verdicts[0], "the table's own order passes");
        assert!(verdicts.contains(&false), "some swap fails");
    }

    /// The context file's three record kinds parse, and any other line is an error naming its line number.
    #[test]
    fn a_context_file_is_renames_and_classes_and_foreign_labels_and_nothing_else() {
        let context = read_context(
            "rename\tqsPea\tqsPea.ss03\nclass\t#Cabc\tqsPea qsTea\nforeign\tqsTea.ss05\n",
        )
        .expect("three records");
        assert_eq!(context.foreign, vec!["qsTea.ss05".to_owned()]);
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
        let error =
            read_context("rename\tqsPea\tqsPea.ss03\nlabel\tx\n").expect_err("a stray record");
        assert!(error.contains("line 2"), "{error}");
    }

    /// A payload that is not a windows enumeration, or a row without seven fields, is an error.
    #[test]
    fn a_malformed_payload_is_refused() {
        let index = fixtures::mini();
        let decision = table(&index, &[]);
        let context = context_of(&decision);
        let mut walk = Walk::new("default", &decision.rules, &decision.rules, &context);
        let error = walk
            .walk(&mut "# something else\n".as_bytes())
            .expect_err("not an enumeration");
        assert!(error.contains(WINDOWS_FORMAT), "{error}");
        let mut text = windows_text(&decision);
        text.push_str("qsPea\t#EDGE\n");
        let mut walk = Walk::new("default", &decision.rules, &decision.rules, &context);
        let error = walk.walk(&mut text.as_bytes()).expect_err("a short row");
        assert!(error.contains("not seven fields"), "{error}");
    }
}
