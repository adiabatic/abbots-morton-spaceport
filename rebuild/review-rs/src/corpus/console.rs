//! The console lines the build prints on standard error, in the forms `rebuild/tools/console.py` writes and the cycle reads (`[phase]`, `[t]`, `[progress]`, `[warn]` and a plain line), and the memory tokens `rebuild/tools/peak_rss.py` puts on a `[t]` line (`rss_gb=`, `rss_now_gb=`). Each line is written whole in one call and flushed, as `console.say` writes it, so lines from several threads never split.

use std::io::{self, Write};

use crate::ffi;
use crate::pyfmt::is_space;

/// The prefix of a line that closes a stretch of work, `console.TIMING`.
pub const TIMING: &str = "[t] ";
/// The prefix of a line that opens one, `console.PHASE`.
pub const PHASE: &str = "[phase] ";
/// The prefix of a counter line, `console.PROGRESS`.
pub const PROGRESS: &str = "[progress] ";
/// The prefix of a line that always reaches the terminal, `console.WARN`.
pub const WARN: &str = "[warn] ";

/// `console.phase`'s line.
pub fn phase_line(name: &str) -> String {
    format!("{PHASE}{name}")
}

/// `console.progress`'s line: `done/total unit`, `?` for an unknown total, with trailing whitespace stripped as `str.rstrip` strips it.
pub fn progress_line(done: u64, total: Option<u64>, unit: &str) -> String {
    let total = total.map_or_else(|| "?".to_owned(), |total| total.to_string());
    format!("{PROGRESS}{done}/{total} {unit}")
        .trim_end_matches(is_space)
        .to_owned()
}

/// `console.warn`'s line.
pub fn warn_line(text: &str) -> String {
    format!("{WARN}{text}")
}

/// `console.timing`'s line: the label, the seconds to one place as `f"{seconds:.1f}"` writes them, and the tail after a space when there is one.
pub fn timing_line(label: &str, seconds: f64, tail: &str) -> String {
    let suffix = if tail.is_empty() {
        String::new()
    } else {
        format!(" {tail}")
    };
    format!("{TIMING}{label} {seconds:.1}s{suffix}")
}

/// A byte count in decimal gigabytes to two places, `peak_rss.format_gb`.
pub fn format_gb(bytes: u64) -> String {
    format!("{:.2}", bytes as f64 / 1e9)
}

/// The peak-RSS token of a `[t]` phase line, `peak_rss.rss_token`.
pub fn rss_token(bytes: u64) -> String {
    format!("rss_gb={}", format_gb(bytes))
}

/// The current-RSS token beside it, `peak_rss.rss_now_token`.
pub fn rss_now_token(bytes: u64) -> String {
    format!("rss_now_gb={}", format_gb(bytes))
}

/// The tail of the `[t]` line that closes a `review.build` phase, as `build._phase_timing` builds it: the peak token, the current token when the resident set could be read, and the note after a tab when there is one.
pub fn phase_tail(peak: u64, now: Option<u64>, note: &str) -> String {
    let mut tail = rss_token(peak);
    if let Some(now) = now {
        tail.push(' ');
        tail.push_str(&rss_now_token(now));
    }
    if !note.is_empty() {
        tail.push('\t');
        tail.push_str(note);
    }
    tail
}

/// This process's resident peak in bytes, as `peak_rss.peak_rss_self_bytes` reads it, which the `rss_gb=` token carries.
pub fn peak_rss_bytes() -> u64 {
    ffi::peak_rss_self_bytes()
}

/// This process's resident set now in bytes, as `peak_rss.current_rss_bytes` reads it, or None when it cannot be read.
pub fn current_rss_bytes() -> Option<u64> {
    ffi::resident_bytes()
}

/// Write one line and its newline in a single call and flush, `console.say`.
pub fn say(out: &mut impl Write, text: &str) -> io::Result<()> {
    let mut line = String::with_capacity(text.len() + 1);
    line.push_str(text);
    line.push('\n');
    out.write_all(line.as_bytes())?;
    out.flush()
}

/// Write `console.phase`'s line.
pub fn phase(out: &mut impl Write, name: &str) -> io::Result<()> {
    say(out, &phase_line(name))
}

/// Write `console.progress`'s line.
pub fn progress(out: &mut impl Write, done: u64, total: Option<u64>, unit: &str) -> io::Result<()> {
    say(out, &progress_line(done, total, unit))
}

/// Write `console.warn`'s line.
pub fn warn(out: &mut impl Write, text: &str) -> io::Result<()> {
    say(out, &warn_line(text))
}

/// Write `console.timing`'s line and return it as written.
pub fn timing(out: &mut impl Write, label: &str, seconds: f64, tail: &str) -> io::Result<String> {
    let line = timing_line(label, seconds, tail);
    say(out, &line)?;
    Ok(line)
}

/// Write the `[t]` line that closes a `review.build` phase after `seconds`, with this process's resident peak and resident set read now and the note (`build._phase_timing`), and return it as written.
pub fn phase_timing(
    out: &mut impl Write,
    label: &str,
    seconds: f64,
    note: &str,
) -> io::Result<String> {
    timing(
        out,
        label,
        seconds,
        &phase_tail(peak_rss_bytes(), current_rss_bytes(), note),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn written(write: impl FnOnce(&mut Vec<u8>) -> io::Result<()>) -> Vec<String> {
        let mut sink = Vec::new();
        write(&mut sink).expect("written");
        String::from_utf8(sink)
            .expect("UTF-8")
            .lines()
            .map(str::to_owned)
            .collect()
    }

    #[test]
    fn the_writers_print_the_four_protocol_prefixes() {
        let lines = written(|sink| {
            phase(sink, "build_tables")?;
            progress(sink, 3, Some(6), "configurations")?;
            progress(sink, 8192, None, "units")?;
            warn(sink, "green, but this pass recorded no green")?;
            timing(sink, "build_tables", 243.05, "").map(drop)
        });
        assert_eq!(
            lines,
            [
                "[phase] build_tables",
                "[progress] 3/6 configurations",
                "[progress] 8192/? units",
                "[warn] green, but this pass recorded no green",
                "[t] build_tables 243.1s",
            ]
        );
    }

    #[test]
    fn a_timing_tail_follows_a_space_whatever_it_starts_with() {
        assert_eq!(
            timing_line("build_tables", 243.05, &rss_token(8_940_000_000)),
            "[t] build_tables 243.1s rss_gb=8.94"
        );
        assert_eq!(
            timing_line("review.build enrich", 12.0, "\t(4 shards)"),
            "[t] review.build enrich 12.0s \t(4 shards)"
        );
        assert_eq!(
            timing_line("enrich", 12.5, "(4 shards)"),
            "[t] enrich 12.5s (4 shards)"
        );
    }

    #[test]
    fn seconds_round_to_one_place_as_python_formats_them() {
        for (seconds, text) in [
            (0.25, "0.2"),
            (0.05, "0.1"),
            (2.25, "2.2"),
            (243.05, "243.1"),
            (12.0, "12.0"),
            (9.95, "9.9"),
            (0.0, "0.0"),
            (59.95, "60.0"),
            (1e-9, "0.0"),
            (1_234_567.85, "1234567.9"),
        ] {
            assert_eq!(timing_line("x", seconds, ""), format!("[t] x {text}s"));
        }
    }

    #[test]
    fn a_progress_line_without_a_unit_ends_at_its_count() {
        assert_eq!(progress_line(3, Some(6), ""), "[progress] 3/6");
        assert_eq!(
            progress_line(3, Some(6), "units \u{1c}"),
            "[progress] 3/6 units"
        );
        assert_eq!(
            progress_line(12, Some(40), "units enriched"),
            "[progress] 12/40 units enriched"
        );
    }

    #[test]
    fn gb_means_decimal_gigabytes_to_two_places() {
        assert_eq!(rss_token(8_940_000_000), "rss_gb=8.94");
        assert_eq!(rss_now_token(4_020_000_000), "rss_now_gb=4.02");
        for (bytes, text) in [
            (5_280_000_000, "5.28"),
            (5_000_000, "0.01"),
            (0, "0.00"),
            (12_345_678_901, "12.35"),
            (1_005_000_000, "1.00"),
            (2_675_000_000, "2.67"),
        ] {
            assert_eq!(format_gb(bytes), text);
        }
    }

    #[test]
    fn both_rss_tokens_sit_side_by_side_before_the_note() {
        assert_eq!(
            timing_line(
                "review.build plan",
                9.9,
                &phase_tail(5_280_000_000, Some(4_020_000_000), "")
            ),
            "[t] review.build plan 9.9s rss_gb=5.28 rss_now_gb=4.02"
        );
        assert_eq!(
            phase_tail(5_280_000_000, None, "build=rust kept 3"),
            "rss_gb=5.28\tbuild=rust kept 3"
        );
        assert_eq!(
            phase_tail(5_280_000_000, Some(4_020_000_000), "jobs=4 engine=rust"),
            "rss_gb=5.28 rss_now_gb=4.02\tjobs=4 engine=rust"
        );
    }

    #[test]
    fn the_closing_line_reads_this_processs_memory_now() {
        let lines = written(|sink| phase_timing(sink, "review.build load", 1.0, "note").map(drop));
        let [line] = lines.as_slice() else {
            panic!("one line: {lines:?}");
        };
        assert!(
            line.starts_with("[t] review.build load 1.0s rss_gb="),
            "{line}"
        );
        assert!(line.contains(" rss_now_gb="), "{line}");
        assert!(line.ends_with("\tnote"), "{line}");
        let now = current_rss_bytes().expect("the resident set reads");
        assert!(0 < now && now <= peak_rss_bytes());
    }

    #[test]
    fn say_writes_the_text_and_its_newline_whole() {
        let lines = written(|sink| say(sink, "unit cache: no store"));
        assert_eq!(lines, ["unit cache: no store"]);
    }
}
