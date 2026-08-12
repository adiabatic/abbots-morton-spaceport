//! Canonical emission, byte-identical to Python's `json.dumps(payload, separators=(",", ":"))` under the default `ensure_ascii`.
//!
//! Compact separators, no whitespace, ASCII only. A non-ASCII code point spells out as a backslash-u escape with four lowercase hex digits, and an astral one as the UTF-16 surrogate pair Python writes; the short escapes are the seven Python uses, every other control character takes the four-digit form, and a solidus is never escaped. Integers are plain decimal and may be negative; there are no floats anywhere in the tree.
//!
//! Nothing is ever sorted. Every mapping rides in the order the model stored it, and every record spells its fields in the declaration order `rebuild/pipeline/model.py` gives them, because that is what the dump on the Python side spells and the two have to agree byte for byte.
//!
//! The emitter reads the model and only the model — there is no parse tree to fall back on — which is what makes a byte-identical echo evidence that the interned packing kept everything.

use crate::SPEC_FORMAT;
use crate::model::{
    Bitmap, BoundaryToken, CellBinding, Condition, FamilyInfo, FeatureInfo, Interner, Pairing,
    Pairings, Policy, PolicyRecord, Provenance, ResolvedSpec, Rune, ScriptRegistry, Spec, Stance,
    Stub, Surface, SurfaceRow, Sym, Table, Unlock, When,
};

/// The canonical text of one parsed dump, without the trailing newline `kernel_io.write_spec` adds.
pub fn emit_spec(spec: &Spec) -> String {
    let mut emitter = Emitter {
        out: String::with_capacity(1 << 17),
        symbols: &spec.symbols,
    };
    emitter.spec(&spec.root);
    emitter.out
}

/// The canonical spelling of one JSON string, quotes included — the escaping rule on its own, for anyone who needs to check it against `json.dumps`.
pub fn json_string(value: &str) -> String {
    let mut out = String::with_capacity(value.len() + 2);
    escape_into(&mut out, value);
    out
}

const HEX: [u8; 16] = *b"0123456789abcdef";

fn escape_into(out: &mut String, value: &str) {
    out.push('"');
    for letter in value.chars() {
        match letter {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\u{8}' => out.push_str("\\b"),
            '\t' => out.push_str("\\t"),
            '\n' => out.push_str("\\n"),
            '\u{c}' => out.push_str("\\f"),
            '\r' => out.push_str("\\r"),
            ' '..='~' => out.push(letter),
            _ => {
                let point = letter as u32;
                if let Ok(unit) = u16::try_from(point) {
                    escape_unit(out, unit);
                } else {
                    let offset = point - 0x1_0000;
                    escape_unit(out, 0xd800 + (offset >> 10) as u16);
                    escape_unit(out, 0xdc00 + (offset & 0x3ff) as u16);
                }
            }
        }
    }
    out.push('"');
}

fn escape_unit(out: &mut String, unit: u16) {
    out.push_str("\\u");
    for shift in [12, 8, 4, 0] {
        out.push(char::from(HEX[usize::from((unit >> shift) & 0xf)]));
    }
}

struct Emitter<'a> {
    out: String,
    symbols: &'a Interner,
}

impl<'a> Emitter<'a> {
    fn resolve(&self, symbol: Sym) -> &'a str {
        self.symbols.resolve(symbol)
    }

    fn item(&mut self) {
        if !(self.out.ends_with('{') || self.out.ends_with('[')) {
            self.out.push(',');
        }
    }

    fn name(&mut self, key: &str) {
        self.item();
        escape_into(&mut self.out, key);
        self.out.push(':');
    }

    fn key(&mut self, key: Sym) {
        let key = self.resolve(key);
        self.item();
        escape_into(&mut self.out, key);
        self.out.push(':');
    }

    fn text(&mut self, value: &str) {
        escape_into(&mut self.out, value);
    }

    fn symbol(&mut self, value: Sym) {
        let value = self.resolve(value);
        escape_into(&mut self.out, value);
    }

    fn number(&mut self, value: i64) {
        self.out.push_str(&value.to_string());
    }

    fn flag(&mut self, value: bool) {
        self.out.push_str(if value { "true" } else { "false" });
    }

    fn null(&mut self) {
        self.out.push_str("null");
    }

    fn object<T>(&mut self, table: &Table<T>, emit: impl Fn(&mut Self, &T)) {
        self.out.push('{');
        for (key, value) in table {
            self.key(*key);
            emit(self, value);
        }
        self.out.push('}');
    }

    fn array<T>(&mut self, items: &[T], emit: impl Fn(&mut Self, &T)) {
        self.out.push('[');
        for item in items {
            self.item();
            emit(self, item);
        }
        self.out.push(']');
    }

    fn maybe<T>(&mut self, value: Option<&T>, emit: impl FnOnce(&mut Self, &T)) {
        match value {
            Some(item) => emit(self, item),
            None => self.null(),
        }
    }

    fn symbols(&mut self, items: &[Sym]) {
        self.out.push('[');
        for symbol in items {
            self.item();
            self.symbol(*symbol);
        }
        self.out.push(']');
    }

    fn numbers(&mut self, items: &[i64]) {
        self.out.push('[');
        for value in items {
            self.item();
            self.number(*value);
        }
        self.out.push(']');
    }

    fn prose_object(&mut self, table: &Table<String>) {
        self.out.push('{');
        for (key, value) in table {
            self.key(*key);
            escape_into(&mut self.out, value);
        }
        self.out.push('}');
    }

    fn symbol_object(&mut self, table: &Table<Sym>) {
        self.out.push('{');
        for (key, value) in table {
            self.key(*key);
            self.symbol(*value);
        }
        self.out.push('}');
    }

    fn symbols_object(&mut self, table: &Table<Vec<Sym>>) {
        self.out.push('{');
        for (key, value) in table {
            self.key(*key);
            self.symbols(value);
        }
        self.out.push('}');
    }

    fn number_object(&mut self, table: &Table<i64>) {
        self.out.push('{');
        for (key, value) in table {
            self.key(*key);
            self.number(*value);
        }
        self.out.push('}');
    }

    fn maybe_symbol(&mut self, value: Option<Sym>) {
        match value {
            Some(symbol) => self.symbol(symbol),
            None => self.null(),
        }
    }

    fn maybe_number(&mut self, value: Option<i64>) {
        match value {
            Some(number) => self.number(number),
            None => self.null(),
        }
    }

    fn maybe_prose(&mut self, value: Option<&str>) {
        match value {
            Some(prose) => self.text(prose),
            None => self.null(),
        }
    }

    fn maybe_symbols(&mut self, value: Option<&[Sym]>) {
        match value {
            Some(items) => self.symbols(items),
            None => self.null(),
        }
    }

    fn maybe_symbol_object(&mut self, value: Option<&Table<Sym>>) {
        match value {
            Some(table) => self.symbol_object(table),
            None => self.null(),
        }
    }

    fn maybe_integer_pair(&mut self, value: Option<(i64, i64)>) {
        match value {
            Some((first, second)) => {
                self.out.push('[');
                self.number(first);
                self.item();
                self.number(second);
                self.out.push(']');
            }
            None => self.null(),
        }
    }

    fn maybe_against(&mut self, value: Option<(Sym, Option<Sym>)>) {
        match value {
            Some((rune, record)) => {
                self.out.push('[');
                self.symbol(rune);
                self.item();
                self.maybe_symbol(record);
                self.out.push(']');
            }
            None => self.null(),
        }
    }

    fn provenance(&mut self, provenance: &Provenance) {
        self.out.push('[');
        self.symbol(provenance.file);
        self.item();
        self.symbol(provenance.path);
        self.out.push(']');
    }

    fn spec(&mut self, spec: &ResolvedSpec) {
        self.out.push('{');
        self.name("format");
        self.text(SPEC_FORMAT);
        self.name("runes");
        self.object(&spec.runes, Self::rune);
        self.name("registry");
        self.registry(&spec.registry);
        self.out.push('}');
    }

    fn rune(&mut self, rune: &Rune) {
        self.out.push('{');
        self.name("name");
        self.symbol(rune.name);
        self.name("codepoint");
        self.maybe_number(rune.codepoint);
        self.name("sequence");
        self.maybe_symbols(rune.sequence.as_deref());
        self.name("ductus");
        self.prose_object(&rune.ductus);
        self.name("notes");
        self.maybe_prose(rune.notes.as_deref());
        self.name("mono");
        self.maybe(rune.mono.as_ref(), Self::bitmap);
        self.name("stances");
        self.object(&rune.stances, Self::stance);
        self.name("policy");
        self.policy(&rune.policy);
        self.out.push('}');
    }

    fn policy(&mut self, policy: &Policy) {
        self.out.push('{');
        self.name("order");
        self.symbols(&policy.order);
        self.name("refuse");
        self.array(&policy.refuse, Self::policy_record);
        self.name("prefer");
        self.array(&policy.prefer, Self::policy_record);
        self.name("extend");
        self.array(&policy.extend, Self::policy_record);
        self.name("contract");
        self.array(&policy.contract, Self::policy_record);
        self.name("resolve");
        self.array(&policy.resolve, Self::policy_record);
        self.name("groups");
        self.symbols_object(&policy.groups);
        self.out.push('}');
    }

    fn policy_record(&mut self, record: &PolicyRecord) {
        self.out.push('{');
        self.name("kind");
        self.symbol(record.kind);
        self.name("when");
        self.when(&record.when);
        self.name("id");
        self.maybe_symbol(record.id);
        self.name("stance");
        self.maybe_symbol(record.stance);
        self.name("entry");
        self.maybe_symbol(record.entry);
        self.name("exit");
        self.maybe_symbol(record.exit);
        self.name("cell");
        self.maybe_symbol_object(record.cell.as_ref());
        self.name("over");
        self.maybe_symbol_object(record.over.as_ref());
        self.name("mode");
        self.maybe_symbol(record.mode);
        self.name("by");
        self.maybe_number(record.by);
        self.name("ok");
        self.maybe_integer_pair(record.ok);
        self.name("bind");
        self.maybe_symbol(record.bind);
        self.name("trim");
        self.maybe_number(record.trim);
        self.name("split");
        self.maybe_integer_pair(record.split);
        self.name("against");
        self.maybe_against(record.against);
        self.name("pick");
        self.maybe_symbol_object(record.pick.as_ref());
        self.name("migrated");
        self.maybe_symbol(record.migrated);
        self.name("why");
        self.maybe_prose(record.why.as_deref());
        self.name("provenance");
        self.maybe(record.provenance.as_ref(), Self::provenance);
        self.out.push('}');
    }

    fn when(&mut self, when: &When) {
        self.out.push('{');
        self.name("left");
        self.maybe(when.left.as_ref(), Self::condition);
        self.name("right");
        self.maybe(when.right.as_ref(), Self::condition);
        self.name("self_entry");
        self.maybe_symbol(when.self_entry);
        self.name("self_exit");
        self.maybe_symbol(when.self_exit);
        self.name("word");
        self.maybe_symbol(when.word);
        self.name("feature");
        self.maybe_symbol(when.feature);
        self.out.push('}');
    }

    fn condition(&mut self, condition: &Condition) {
        self.out.push('{');
        self.name("family");
        self.symbols(&condition.family);
        self.name("klass");
        self.symbols(&condition.klass);
        self.name("stance");
        self.symbols(&condition.stance);
        self.name("joined_at");
        self.maybe_symbol(condition.joined_at);
        self.name("stroke");
        self.maybe_symbol(condition.stroke);
        self.name("is_token");
        self.maybe_symbol(condition.is_token);
        self.name("except_");
        self.array(&condition.except_, Self::condition);
        self.name("then");
        self.maybe(condition.then.as_deref(), Self::condition);
        self.out.push('}');
    }

    fn stance(&mut self, stance: &Stance) {
        self.out.push('{');
        self.name("name");
        self.symbol(stance.name);
        self.name("motion");
        self.symbol(stance.motion);
        self.name("traits");
        self.symbols(&stance.traits);
        self.name("bitmap");
        self.bitmap(&stance.bitmap);
        self.name("bitmaps");
        self.object(&stance.bitmaps, Self::bitmap);
        self.name("surface");
        self.surface(&stance.surface);
        self.out.push('}');
    }

    fn bitmap(&mut self, bitmap: &Bitmap) {
        self.out.push('{');
        self.name("rows");
        self.symbols(&bitmap.rows);
        self.name("y_offset");
        self.number(bitmap.y_offset);
        self.out.push('}');
    }

    fn surface(&mut self, surface: &Surface) {
        self.out.push('{');
        self.name("entries");
        self.object(&surface.entries, Self::surface_row);
        self.name("exits");
        self.object(&surface.exits, Self::surface_row);
        self.name("pairings");
        self.pairings(&surface.pairings);
        self.name("cells");
        self.array(&surface.cells, Self::cell_binding);
        self.name("unlocks");
        self.array(&surface.unlocks, Self::unlock);
        self.name("require");
        self.symbols(&surface.require);
        self.out.push('}');
    }

    fn surface_row(&mut self, row: &SurfaceRow) {
        self.out.push('{');
        self.name("height");
        self.symbol(row.height);
        self.name("x");
        self.number(row.x);
        self.name("stroke");
        self.maybe_symbol(row.stroke);
        self.name("joined");
        self.maybe_symbol(row.joined);
        self.name("joined_x");
        self.maybe_number(row.joined_x);
        self.name("withdrawal");
        self.maybe_symbol(row.withdrawal);
        self.name("stub");
        self.maybe(row.stub.as_ref(), Self::stub);
        self.name("scope");
        self.array(&row.scope, Self::condition);
        self.name("selectable");
        self.flag(row.selectable);
        self.name("ink_y");
        self.maybe_number(row.ink_y);
        self.name("x_off_convention");
        self.flag(row.x_off_convention);
        self.name("provenance");
        self.maybe(row.provenance.as_ref(), Self::provenance);
        self.out.push('}');
    }

    fn stub(&mut self, stub: &Stub) {
        self.out.push('{');
        self.name("cols");
        self.numbers(&stub.cols);
        self.name("inks_when");
        self.symbol(stub.inks_when);
        self.out.push('}');
    }

    fn pairing(&mut self, pairing: &Pairing) {
        self.out.push('{');
        self.name("entry");
        self.symbol(pairing.entry);
        self.name("exit");
        self.symbol(pairing.exit);
        self.out.push('}');
    }

    fn pairings(&mut self, pairings: &Pairings) {
        self.out.push('{');
        self.name("never");
        self.array(&pairings.never, Self::pairing);
        self.name("only");
        match &pairings.only {
            Some(only) => self.array(only, Self::pairing),
            None => self.null(),
        }
        self.out.push('}');
    }

    fn cell_binding(&mut self, binding: &CellBinding) {
        self.out.push('{');
        self.name("entry");
        self.symbol(binding.entry);
        self.name("exit");
        self.symbol(binding.exit);
        self.name("bitmap");
        self.symbol(binding.bitmap);
        self.name("entry_x");
        self.maybe_number(binding.entry_x);
        self.name("exit_x");
        self.maybe_number(binding.exit_x);
        self.name("provenance");
        self.maybe(binding.provenance.as_ref(), Self::provenance);
        self.out.push('}');
    }

    fn unlock(&mut self, unlock: &Unlock) {
        self.out.push('{');
        self.name("feature");
        self.symbol(unlock.feature);
        self.name("entry");
        self.maybe_symbol(unlock.entry);
        self.name("exit");
        self.maybe_symbol(unlock.exit);
        self.name("pairing");
        self.maybe(unlock.pairing.as_ref(), Self::pairing);
        self.name("when");
        self.maybe(unlock.when.as_ref(), Self::when);
        self.name("why");
        self.maybe_prose(unlock.why.as_deref());
        self.name("provenance");
        self.maybe(unlock.provenance.as_ref(), Self::provenance);
        self.out.push('}');
    }

    fn registry(&mut self, registry: &ScriptRegistry) {
        self.out.push('{');
        self.name("heights");
        self.number_object(&registry.heights);
        self.name("boundary_tokens");
        self.object(&registry.boundary_tokens, Self::boundary_token);
        self.name("features");
        self.object(&registry.features, Self::feature_info);
        self.name("interactions");
        self.out.push('[');
        for interaction in &registry.interactions {
            self.item();
            self.symbols(interaction);
        }
        self.out.push(']');
        self.name("predicate_classes");
        self.symbols_object(&registry.predicate_classes);
        self.name("families");
        self.object(&registry.families, Self::family_info);
        self.out.push('}');
    }

    fn boundary_token(&mut self, token: &BoundaryToken) {
        self.out.push('{');
        self.name("codepoint");
        self.number(token.codepoint);
        self.name("splits_runs");
        self.flag(token.splits_runs);
        self.out.push('}');
    }

    fn feature_info(&mut self, feature: &FeatureInfo) {
        self.out.push('{');
        self.name("kind");
        self.symbol(feature.kind);
        self.name("description");
        self.text(&feature.description);
        self.name("overlay");
        self.maybe_symbol(feature.overlay);
        self.out.push('}');
    }

    fn family_info(&mut self, family: &FamilyInfo) {
        self.out.push('{');
        self.name("codepoint");
        self.maybe_number(family.codepoint);
        self.name("sequence");
        self.maybe_symbols(family.sequence.as_deref());
        self.out.push('}');
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_plain_ascii_string_rides_through_untouched() {
        assert_eq!(json_string("qsZoo"), "\"qsZoo\"");
        assert_eq!(json_string(""), "\"\"");
        assert_eq!(json_string("a/b"), "\"a/b\"");
        assert_eq!(json_string(" ~"), "\" ~\"");
    }

    #[test]
    fn the_seven_short_escapes_are_the_ones_python_writes() {
        assert_eq!(json_string("\u{8}\t\n\u{c}\r"), "\"\\b\\t\\n\\f\\r\"");
        assert_eq!(json_string("q\"w\\e"), "\"q\\\"w\\\\e\"");
    }

    #[test]
    fn every_other_control_character_takes_the_four_digit_form() {
        assert_eq!(json_string("\u{1}"), "\"\\u0001\"");
        assert_eq!(json_string("\u{b}"), "\"\\u000b\"");
        assert_eq!(json_string("\u{1f}"), "\"\\u001f\"");
        assert_eq!(json_string("\u{7f}"), "\"\\u007f\"");
    }

    #[test]
    fn the_escapes_the_live_dump_carries_are_lowercase_hex() {
        assert_eq!(json_string("\u{b7}"), "\"\\u00b7\"");
        assert_eq!(json_string("\u{2019}"), "\"\\u2019\"");
        assert_eq!(json_string("\u{201c}"), "\"\\u201c\"");
        assert_eq!(json_string("\u{201d}"), "\"\\u201d\"");
        assert_eq!(json_string("\u{2014}"), "\"\\u2014\"");
        assert_eq!(json_string("\u{e9}"), "\"\\u00e9\"");
        assert_eq!(json_string("\u{80}"), "\"\\u0080\"");
        assert_eq!(json_string("\u{d7ff}"), "\"\\ud7ff\"");
    }

    #[test]
    fn an_astral_code_point_becomes_a_surrogate_pair() {
        assert_eq!(json_string("\u{1d11e}"), "\"\\ud834\\udd1e\"");
        assert_eq!(json_string("\u{10000}"), "\"\\ud800\\udc00\"");
        assert_eq!(json_string("\u{10ffff}"), "\"\\udbff\\udfff\"");
    }

    #[test]
    fn a_mixed_string_escapes_only_what_needs_it() {
        assert_eq!(
            json_string("The \u{b7}Zoo\u{2014}\u{b7}Bay seam,\nas \"drawn\"."),
            "\"The \\u00b7Zoo\\u2014\\u00b7Bay seam,\\nas \\\"drawn\\\".\""
        );
    }
}
