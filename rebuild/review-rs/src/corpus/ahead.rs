//! Items made ahead of the threads that consume them, in the order each consumer takes them: the write's runs ([`super::write`]) take each unit's emission from an [`Ahead`], whose helper threads ([`Ahead::serve`]) make the emissions of the units a run is about to write while the run writes the ones before. Each run's units are cut into chunks of [`CHUNK_UNITS`] consecutive units, a chunk made whole by one thread with state of its own, so a reader that reads ahead keeps its place; the helpers take the next chunk of the open run that is least far ahead, and a run whose next chunk no thread has taken makes it itself. At most [`CHUNKS_PER_HELPER`] chunks a helper are made and not yet taken across every run. A chunk stops at its first failed item, which its run reaches before it asks for any later one; a helper that panics hands over the items before the panic and what the panic carried, which its run takes in their place, and takes no further chunk.

use std::any::Any;
use std::collections::VecDeque;
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::sync::{Condvar, Mutex, MutexGuard, PoisonError};

/// The consecutive units a chunk holds, short of a class's end: tens of units, few enough that a chunk of the full recompute's emissions, each a fragment of tens of kilobytes as a value tree, is a megabyte or less, and enough that a chunk's making outweighs the lock taken to hand it over.
pub const CHUNK_UNITS: usize = 16;
/// The chunks a helper may have made ahead, across every run: the made and untaken chunks are at most this many times the helper count, and each run holds one more chunk it is taking items from, so the units made and not yet written are at most [`CHUNK_UNITS`] times twice the helpers plus the runs (304 units at 8 helpers and 3 runs, a few megabytes of the full recompute's emissions), whatever the corpus size.
pub const CHUNKS_PER_HELPER: usize = 2;

/// A made item: the unit it was made for, and what making it gave.
pub type Made<U, E> = (u32, Result<U, E>);
/// What a panic carried, as a thread's join gives it.
pub type Panic = Box<dyn Any + Send>;

/// A chunk handed out: taken by a thread and being made, or made, with what the panic that ended it short carried.
enum Slot<U, E> {
    Making,
    Made(Vec<Made<U, E>>, Option<Panic>),
}

/// One run's units and how far they have been handed out: its classes' units in order, the class and the place in it of the next unit no thread has taken, whether the run is open, and the chunks handed out and not yet taken by the run, oldest first.
struct Run<'s, U, E> {
    classes: Vec<&'s [u32]>,
    class: usize,
    offset: usize,
    open: bool,
    taken: usize,
    slots: VecDeque<Slot<U, E>>,
}

impl<'s, U, E> Run<'s, U, E> {
    /// The run's next chunk, which no thread has taken: up to [`CHUNK_UNITS`] consecutive units of one class, None once every unit is taken.
    fn next_chunk(&mut self) -> Option<&'s [u32]> {
        while let Some(&units) = self.classes.get(self.class) {
            if self.offset < units.len() {
                let end = units.len().min(self.offset + CHUNK_UNITS);
                let chunk = &units[self.offset..end];
                self.offset = end;
                return Some(chunk);
            }
            self.class += 1;
            self.offset = 0;
        }
        None
    }

    /// Whether a unit of the run is left for a thread to take.
    fn untaken(&self) -> bool {
        self.classes[self.class.min(self.classes.len())..]
            .iter()
            .enumerate()
            .any(|(index, units)| {
                let from = if index == 0 { self.offset } else { 0 };
                from < units.len()
            })
    }
}

/// The state the threads share under the lock: each run's, the chunks made or being made and not yet taken across every run, and whether every run is done.
struct Board<'s, U, E> {
    runs: Vec<Run<'s, U, E>>,
    ahead: usize,
    closed: bool,
}

/// The items of runs of units made ahead by helper threads and taken by each run's own thread in its units' order. See the module's doc.
pub struct Ahead<'s, U, E> {
    board: Mutex<Board<'s, U, E>>,
    made: Condvar,
    room: Condvar,
    limit: usize,
}

/// Make the items of `units` in order through `make` with `state`, stopping after the first that fails: how a maker of chunks given to [`Ahead::serve`] and [`Feed::next`] makes a chunk's items.
pub fn make_chunk<S, U, E>(
    units: &[u32],
    state: &mut S,
    make: &impl Fn(&mut S, u32) -> Result<U, E>,
    made: &mut Vec<Made<U, E>>,
) {
    for &unit in units {
        let item = make(state, unit);
        let failed = item.is_err();
        made.push((unit, item));
        if failed {
            return;
        }
    }
}

impl<'s, U, E> Ahead<'s, U, E> {
    /// The hand-off over `runs`, each its classes' units in order, for `helpers` helper threads.
    pub fn new(runs: Vec<Vec<&'s [u32]>>, helpers: usize) -> Self {
        Self {
            board: Mutex::new(Board {
                runs: runs
                    .into_iter()
                    .map(|classes| Run {
                        classes,
                        class: 0,
                        offset: 0,
                        open: false,
                        taken: 0,
                        slots: VecDeque::new(),
                    })
                    .collect(),
                ahead: 0,
                closed: false,
            }),
            made: Condvar::new(),
            room: Condvar::new(),
            limit: CHUNKS_PER_HELPER * helpers.max(1),
        }
    }

    fn lock(&self) -> MutexGuard<'_, Board<'s, U, E>> {
        self.board.lock().unwrap_or_else(PoisonError::into_inner)
    }

    /// Wait on `on` with the lock `board` holds: a run thread for a chunk being made (`made`), a helper for a chunk to make (`room`).
    fn wait<'g>(
        on: &Condvar,
        board: MutexGuard<'g, Board<'s, U, E>>,
    ) -> MutexGuard<'g, Board<'s, U, E>> {
        on.wait(board).unwrap_or_else(PoisonError::into_inner)
    }

    /// Every run done: the helpers return once they have handed over the chunks they hold.
    pub fn close(&self) {
        self.lock().closed = true;
        self.room.notify_all();
    }

    /// Open run `run` for its own thread, which takes its items through the [`Feed`] in its units' order; dropping the feed closes the run and drops the items made for it and not taken.
    pub fn feed(&self, run: usize) -> Feed<'_, 's, U, E> {
        self.lock().runs[run].open = true;
        self.room.notify_all();
        Feed {
            ahead: self,
            run,
            items: Vec::new().into_iter(),
            panicked: None,
        }
    }

    /// Make chunks of the open runs through `make` with `state`, which makes a chunk's items in order into the list it is given, stopping after the first that fails ([`make_chunk`]), until [`Ahead::close`], each the next untaken chunk of the run least far ahead (the first such run in run order on a tie) while fewer than the limit are made and untaken. A panic in `make` hands over the chunk's items before it with what the panic carried and returns.
    pub fn serve<S>(&self, state: &mut S, make: impl Fn(&mut S, &[u32], &mut Vec<Made<U, E>>)) {
        let mut board = self.lock();
        loop {
            if board.closed {
                return;
            }
            let chosen = if board.ahead < self.limit {
                board
                    .runs
                    .iter()
                    .enumerate()
                    .filter(|(_, run)| run.open && run.untaken())
                    .min_by_key(|(_, run)| run.slots.len())
                    .map(|(index, _)| index)
            } else {
                None
            };
            let Some(index) = chosen else {
                board = Self::wait(&self.room, board);
                continue;
            };
            let run = &mut board.runs[index];
            let Some(units) = run.next_chunk() else {
                continue;
            };
            let chunk = run.taken + run.slots.len();
            run.slots.push_back(Slot::Making);
            board.ahead += 1;
            drop(board);
            let mut made = Vec::with_capacity(units.len());
            let panicked = catch_unwind(AssertUnwindSafe(|| {
                make(state, units, &mut made);
            }))
            .err();
            board = self.lock();
            let ended = panicked.is_some();
            let run = &mut board.runs[index];
            match run.slots.get_mut(chunk.wrapping_sub(run.taken)) {
                Some(slot) if run.open => {
                    *slot = Slot::Made(made, panicked);
                    self.made.notify_all();
                }
                _ => {
                    board.ahead -= 1;
                    self.room.notify_one();
                }
            }
            if ended {
                return;
            }
        }
    }
}

/// A run's items as its own thread takes them, in its units' order: the items of the chunk it is taking, and what the panic that ended that chunk short carried.
pub struct Feed<'a, 's, U, E> {
    ahead: &'a Ahead<'s, U, E>,
    run: usize,
    items: std::vec::IntoIter<Made<U, E>>,
    panicked: Option<Panic>,
}

impl<U, E> Feed<'_, '_, U, E> {
    /// The run's next item, made by a helper or, when no thread has taken its chunk, made here through `make` with `state`; what a helper's panic carried where that helper's chunk ended short, and a text for a request past the run's last unit.
    pub fn next<S>(
        &mut self,
        state: &mut S,
        make: impl Fn(&mut S, &[u32], &mut Vec<Made<U, E>>),
    ) -> Result<Made<U, E>, Panic> {
        loop {
            if let Some(item) = self.items.next() {
                return Ok(item);
            }
            if let Some(panicked) = self.panicked.take() {
                return Err(panicked);
            }
            let ahead = self.ahead;
            let mut board = ahead.lock();
            loop {
                let run = &mut board.runs[self.run];
                match run.slots.pop_front() {
                    Some(Slot::Made(items, panicked)) => {
                        run.taken += 1;
                        board.ahead -= 1;
                        drop(board);
                        ahead.room.notify_one();
                        self.items = items.into_iter();
                        self.panicked = panicked;
                        break;
                    }
                    Some(Slot::Making) => {
                        run.slots.push_front(Slot::Making);
                        board = Ahead::wait(&ahead.made, board);
                    }
                    None => {
                        let Some(units) = run.next_chunk() else {
                            return Err(Box::new(format!(
                                "run {} was asked for a unit past its last",
                                self.run
                            )));
                        };
                        run.taken += 1;
                        drop(board);
                        let mut made = Vec::with_capacity(units.len());
                        make(state, units, &mut made);
                        self.items = made.into_iter();
                        break;
                    }
                }
            }
        }
    }
}

impl<U, E> Drop for Feed<'_, '_, U, E> {
    fn drop(&mut self) {
        let mut board = self.ahead.lock();
        let run = &mut board.runs[self.run];
        run.open = false;
        let made = run
            .slots
            .iter()
            .filter(|slot| matches!(slot, Slot::Made(..)))
            .count();
        run.taken += run.slots.len();
        run.slots.clear();
        board.ahead -= made;
        drop(board);
        self.ahead.room.notify_all();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::pool::panic_message;
    use std::sync::atomic::{AtomicUsize, Ordering};

    /// What a unit makes: its square, a failure at a unit divisible by `fail`, a panic at `panic`, each thread counting what it made in its state.
    fn make(fail: u32, panic: u32) -> impl Fn(&mut Vec<u32>, u32) -> Result<u64, String> {
        move |seen: &mut Vec<u32>, unit: u32| {
            seen.push(unit);
            if unit == panic {
                panic!("unit {unit} panicked");
            }
            if fail != 0 && unit.is_multiple_of(fail) {
                return Err(format!("unit {unit} failed"));
            }
            Ok(u64::from(unit) * u64::from(unit))
        }
    }

    /// The items a test chunk is made into.
    type Items = Vec<Made<u64, String>>;

    /// The maker of chunks over `make`, one item a unit ([`make_chunk`]).
    fn each<F: Fn(&mut Vec<u32>, u32) -> Result<u64, String>>(
        make: &F,
    ) -> impl Fn(&mut Vec<u32>, &[u32], &mut Items) + '_ {
        move |state, units, made| make_chunk(units, state, make, made)
    }

    /// The runs of `classes`, each a list of class sizes, numbered on from `first` across them.
    fn units(runs: &[&[usize]]) -> Vec<Vec<Vec<u32>>> {
        let mut next = 1;
        runs.iter()
            .map(|sizes| {
                sizes
                    .iter()
                    .map(|&size| {
                        let class: Vec<u32> = (next..next + size as u32).collect();
                        next += size as u32;
                        class
                    })
                    .collect()
            })
            .collect()
    }

    /// Each run's items as the serial write takes them: in order, up to and with its first failure.
    fn serial(
        runs: &[Vec<Vec<u32>>],
        make: &impl Fn(&mut Vec<u32>, u32) -> Result<u64, String>,
    ) -> Vec<Vec<Made<u64, String>>> {
        runs.iter()
            .map(|classes| {
                let mut taken = Vec::new();
                for &unit in classes.iter().flatten() {
                    let item = make(&mut Vec::new(), unit);
                    let failed = item.is_err();
                    taken.push((unit, item));
                    if failed {
                        break;
                    }
                }
                taken
            })
            .collect()
    }

    /// What one run's thread took: its items, and the text of the panic that ended it short.
    type Taken = (Vec<Made<u64, String>>, Option<String>);

    /// The hand-off over `runs` for `helpers` helpers.
    fn board(runs: &[Vec<Vec<u32>>], helpers: usize) -> Ahead<'_, u64, String> {
        Ahead::new(
            runs.iter()
                .map(|classes| classes.iter().map(Vec::as_slice).collect())
                .collect(),
            helpers,
        )
    }

    /// Each run's items as its own thread takes them from an [`Ahead`] with `helpers` helpers making them through `make`, `threads` run threads taking the runs in order and making a chunk no helper took through `own`: up to and with its first failure or a helper's panic, which ends the run with the panic's text.
    fn ahead(
        runs: &[Vec<Vec<u32>>],
        helpers: usize,
        threads: usize,
        make: &(impl Fn(&mut Vec<u32>, u32) -> Result<u64, String> + Sync),
        own: &(impl Fn(&mut Vec<u32>, u32) -> Result<u64, String> + Sync),
    ) -> Vec<Taken> {
        let ahead = board(runs, helpers);
        let outcomes: Vec<Mutex<Taken>> = runs
            .iter()
            .map(|_| Mutex::new((Vec::new(), None)))
            .collect();
        let next = AtomicUsize::new(0);
        std::thread::scope(|scope| {
            let helpers: Vec<_> = (0..helpers)
                .map(|_| scope.spawn(|| ahead.serve(&mut Vec::new(), each(make))))
                .collect();
            let workers: Vec<_> = (0..threads)
                .map(|_| {
                    scope.spawn(|| {
                        loop {
                            let index = next.fetch_add(1, Ordering::Relaxed);
                            let Some(classes) = runs.get(index) else {
                                return;
                            };
                            let mut feed = ahead.feed(index);
                            let mut seen = Vec::new();
                            let mut taken = Vec::new();
                            let mut panicked = None;
                            for &unit in classes.iter().flatten() {
                                match feed.next(&mut seen, each(own)) {
                                    Ok((at, item)) => {
                                        assert_eq!(at, unit);
                                        let failed = item.is_err();
                                        taken.push((at, item));
                                        if failed {
                                            break;
                                        }
                                    }
                                    Err(text) => {
                                        panicked = Some(panic_message(text.as_ref()));
                                        break;
                                    }
                                }
                            }
                            *outcomes[index].lock().expect("the outcome") = (taken, panicked);
                        }
                    })
                })
                .collect();
            for worker in workers {
                worker.join().expect("a run thread");
            }
            ahead.close();
            for helper in helpers {
                helper.join().expect("a helper");
            }
        });
        let board = ahead.lock();
        assert_eq!(board.ahead, 0);
        assert!(
            board
                .runs
                .iter()
                .all(|run| run.slots.is_empty() && !run.open)
        );
        drop(board);
        outcomes
            .into_iter()
            .map(|outcome| outcome.into_inner().expect("the outcome"))
            .collect()
    }

    #[test]
    fn a_chunk_is_consecutive_units_of_one_class_and_the_run_ends_after_its_last() {
        let classes = [vec![1, 2, 3], Vec::new(), (4..40).collect::<Vec<u32>>()];
        let mut run: Run<'_, (), ()> = Run {
            classes: classes.iter().map(Vec::as_slice).collect(),
            class: 0,
            offset: 0,
            open: true,
            taken: 0,
            slots: VecDeque::new(),
        };
        let mut chunks = Vec::new();
        while run.untaken() {
            chunks.push(run.next_chunk().expect("a chunk").to_vec());
        }
        assert_eq!(run.next_chunk(), None);
        assert_eq!(
            chunks,
            vec![
                vec![1, 2, 3],
                (4..20).collect(),
                (20..36).collect(),
                (36..40).collect()
            ]
        );
    }

    #[test]
    fn every_run_takes_the_serial_items_in_order_at_any_helper_count() {
        let runs = units(&[&[40, 3, 0, 70], &[100], &[1, 1, 1, 20], &[]]);
        let make = make(0, 0);
        let expected: Vec<_> = serial(&runs, &make)
            .into_iter()
            .map(|taken| (taken, None))
            .collect();
        for helpers in [1, 2, 5] {
            for threads in [1, 2, 4] {
                assert_eq!(
                    ahead(&runs, helpers, threads, &make, &make),
                    expected,
                    "{helpers} helpers, {threads} run threads"
                );
            }
        }
    }

    #[test]
    fn a_failed_item_ends_its_run_where_the_serial_write_ends_it() {
        let runs = units(&[&[30, 30], &[50], &[9]]);
        let make = make(37, 0);
        let expected: Vec<_> = serial(&runs, &make)
            .into_iter()
            .map(|taken| (taken, None))
            .collect();
        assert_eq!(expected[0].0.last().map(|item| item.0), Some(37));
        assert_eq!(expected[1].0.last().map(|item| item.0), Some(74));
        for helpers in [1, 3] {
            for threads in [1, 3] {
                assert_eq!(ahead(&runs, helpers, threads, &make, &make), expected);
            }
        }
    }

    #[test]
    fn the_items_helpers_made_ahead_come_in_order_without_the_run_making_any() {
        let runs = units(&[&[10, 50, 7]]);
        let make = make(0, 0);
        let refuse = |_: &mut Vec<u32>, unit: u32| -> Result<u64, String> {
            panic!("the run made unit {unit} itself")
        };
        let ahead = board(&runs, 2);
        let limit = CHUNKS_PER_HELPER * 2;
        std::thread::scope(|scope| {
            let helpers: Vec<_> = (0..2)
                .map(|_| scope.spawn(|| ahead.serve(&mut Vec::new(), each(&make))))
                .collect();
            let mut feed = ahead.feed(0);
            let made = || {
                let board = ahead.lock();
                board.ahead == limit
                    && board.runs[0]
                        .slots
                        .iter()
                        .all(|slot| matches!(slot, Slot::Made(..)))
            };
            while !made() {
                std::thread::yield_now();
            }
            for unit in runs[0].iter().flatten().take(10 + 16 + 16 + 16) {
                assert_eq!(
                    feed.next(&mut Vec::new(), each(&refuse)).ok(),
                    Some((*unit, Ok(u64::from(*unit) * u64::from(*unit))))
                );
            }
            drop(feed);
            ahead.close();
            for helper in helpers {
                helper.join().expect("a helper");
            }
        });
    }

    #[test]
    fn a_helpers_panic_ends_its_run_with_the_panics_text_and_hangs_no_run() {
        let runs = units(&[&[200], &[200]]);
        for helpers in [1, 4] {
            let outcomes = ahead(&runs, helpers, 2, &make(0, 150), &make(0, 0));
            let (taken, panicked) = &outcomes[0];
            assert!(taken.iter().all(|(_, item)| item.is_ok()));
            assert!(
                taken
                    .iter()
                    .map(|item| item.0)
                    .eq(1..1 + taken.len() as u32),
                "the items before the panic come in order"
            );
            match panicked {
                Some(text) => {
                    assert_eq!(text, "unit 150 panicked");
                    assert_eq!(taken.len(), 149);
                }
                None => assert_eq!(taken.len(), 200),
            }
            assert_eq!(outcomes[1].0.len(), 200);
            assert_eq!(outcomes[1].1, None);
        }
    }

    #[test]
    fn a_failure_before_a_panic_wins_and_the_items_before_it_come_whole() {
        let runs = units(&[&[100]]);
        let make = make(20, 21);
        for helpers in [1, 2] {
            let outcomes = ahead(&runs, helpers, 1, &make, &make);
            assert_eq!(outcomes[0].0, serial(&runs, &make)[0]);
            assert_eq!(outcomes[0].1, None);
        }
    }

    #[test]
    fn a_run_closed_early_drops_what_was_made_for_it_and_frees_its_helpers() {
        let runs = units(&[&[500], &[500]]);
        let ahead = Ahead::new(
            runs.iter()
                .map(|classes| classes.iter().map(Vec::as_slice).collect())
                .collect(),
            2,
        );
        let make = make(0, 0);
        std::thread::scope(|scope| {
            let helpers: Vec<_> = (0..2)
                .map(|_| scope.spawn(|| ahead.serve(&mut Vec::new(), each(&make))))
                .collect();
            let mut feed = ahead.feed(0);
            for unit in 1..=10 {
                assert_eq!(
                    feed.next(&mut Vec::new(), each(&make)).expect("an item").0,
                    unit
                );
            }
            drop(feed);
            let mut feed = ahead.feed(1);
            for unit in 501..=1000 {
                assert_eq!(
                    feed.next(&mut Vec::new(), each(&make)).expect("an item").0,
                    unit
                );
            }
            assert!(feed.next(&mut Vec::new(), each(&make)).is_err());
            drop(feed);
            ahead.close();
            for helper in helpers {
                helper.join().expect("a helper");
            }
        });
        let board = ahead.lock();
        assert_eq!(board.ahead, 0);
        assert!(board.runs[0].untaken());
    }
}
