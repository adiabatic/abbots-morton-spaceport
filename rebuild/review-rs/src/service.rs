//! The `units` subcommand (§ The process of `rebuild/CORPUS-UNITS-PORT-PLAN.md`): `_corpus_worker`'s message loop over request frames on standard input and reply frames on standard output, served by a pool of threads as `_RecomputeRunner` serves it with a pool of worker processes.
//!
//! One reader thread drains the input into a queue and one writer thread writes the replies, so the parent never blocks writing a batch while the engine blocks writing a reply. The [`Resources`] are opened once and shared by the `--threads` threads of a [`Pool`], which builds each batch's reply frame on the thread that ran it. A coordinator reads the requests and the pool's reports from one event queue: a `batch` becomes one job, answered with a `batch` reply whose deflated fragments follow its line; a `verify` is cut into one share per thread, as `_RecomputeRunner.verify` cuts the sample, and answered with one `verify` reply holding the shares' keys in order; and `stop` is answered, once every job handed out is answered, with `stopped` and the process's peak footprint. A failure in any thread, a pool thread failing to start among them, is answered with an `error` frame that names it, and the service ends; so it does when the input closes without `stop`.

use std::collections::VecDeque;
use std::io::{BufRead, Write};
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::sync::mpsc::{Receiver, Sender, channel};

use serde_json::{Map, Value};

use crate::ffi::peak_footprint;
use crate::pool::{Pool, Report, panic_message, shares};
use crate::unit::{Batch, Resources, VerifyKey, Worker, deltas_json};
use crate::wire::{Injection, Request, UnitRecord, error_frame};

/// How the service ended.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Ending {
    /// It answered `stop`.
    Stopped,
    /// It wrote an `error` frame, or could not write a reply.
    Failed,
    /// Its input closed before `stop`.
    Closed,
}

enum Incoming {
    Request(Request),
    Refused(String),
    Closed,
}

/// One piece of work for the pool: a whole batch, or one share of a verification sample.
enum Job {
    Batch {
        batch: i64,
        units: Vec<UnitRecord>,
    },
    Verify {
        request: usize,
        share: usize,
        items: Vec<(UnitRecord, Injection)>,
    },
}

/// A pool thread's answer to one job: a batch's whole reply frame, or one share's keys.
enum Answer {
    Batch(Vec<u8>),
    Verify {
        request: usize,
        share: usize,
        keys: Vec<VerifyKey>,
    },
}

/// What the coordinator reads: the reader's requests and the pool's answers and failures, in the order they arrive.
enum Event {
    Incoming(Incoming),
    Answered(Answer),
    Failed(String),
}

impl From<Report<Answer>> for Event {
    fn from(report: Report<Answer>) -> Self {
        match report {
            Report::Answered(answer) => Event::Answered(answer),
            Report::Failed(failure) => Event::Failed(failure),
        }
    }
}

/// A verification sample whose shares are still being answered.
struct PendingVerify {
    request: usize,
    shares: Vec<Option<Vec<VerifyKey>>>,
}

/// The `batch` reply: the projections and the contract errors on one line, and the deflated fragments after it, end to end in projection order.
pub fn batch_frame(batch: i64, answer: &Batch) -> Vec<u8> {
    let payload: usize = answer
        .projections
        .iter()
        .map(|projection| projection.packed.len())
        .sum();
    let mut frame = Map::new();
    frame.insert("op".to_owned(), Value::from("batch"));
    frame.insert("batch".to_owned(), Value::from(batch));
    frame.insert(
        "projections".to_owned(),
        Value::Array(
            answer
                .projections
                .iter()
                .map(|projection| projection.to_json())
                .collect(),
        ),
    );
    frame.insert(
        "contract_errors".to_owned(),
        Value::Array(
            answer
                .contract_errors
                .iter()
                .map(|error| Value::from(error.as_str()))
                .collect(),
        ),
    );
    frame.insert("payload".to_owned(), Value::from(payload));
    let mut bytes = Value::Object(frame).to_string().into_bytes();
    bytes.reserve(payload + 1);
    bytes.push(b'\n');
    for projection in &answer.projections {
        bytes.extend_from_slice(&projection.packed);
    }
    bytes
}

/// The `verify` reply: each sampled unit's recomputed id, content key and ink deltas.
pub fn verify_frame(keys: &[VerifyKey]) -> Vec<u8> {
    let keys = keys
        .iter()
        .map(|key| {
            Value::Array(vec![
                Value::from(key.unit_id.as_str()),
                Value::from(key.content_key.as_str()),
                deltas_json(&key.ink_deltas),
            ])
        })
        .collect();
    let mut frame = Map::new();
    frame.insert("op".to_owned(), Value::from("verify"));
    frame.insert("keys".to_owned(), Value::Array(keys));
    line(Value::Object(frame).to_string())
}

/// The `stopped` reply, with the process's peak footprint.
pub fn stopped_frame(peak: u64) -> Vec<u8> {
    line(format!("{{\"op\":\"stopped\",\"peak_footprint\":{peak}}}"))
}

fn line(mut text: String) -> Vec<u8> {
    text.push('\n');
    text.into_bytes()
}

fn read_requests(input: impl BufRead, events: &Sender<Event>) {
    for line in input.lines() {
        let incoming = match line {
            Ok(line) => match Request::parse(&line) {
                Ok(request) => Incoming::Request(request),
                Err(failure) => Incoming::Refused(failure.to_string()),
            },
            Err(failure) => Incoming::Refused(format!("cannot read a request: {failure}")),
        };
        let refused = matches!(incoming, Incoming::Refused(_));
        if events.send(Event::Incoming(incoming)).is_err() || refused {
            return;
        }
    }
    let _ = events.send(Event::Incoming(Incoming::Closed));
}

fn write_replies(mut output: impl Write, replies: &Receiver<Vec<u8>>) -> bool {
    for reply in replies {
        if output
            .write_all(&reply)
            .and_then(|()| output.flush())
            .is_err()
        {
            return false;
        }
    }
    true
}

/// One job through `worker`: its answer, or a failure naming the batch.
fn run_job(worker: &mut Worker<'_>, job: Job) -> Result<Answer, String> {
    match job {
        Job::Batch { batch, units } => worker
            .run_batch(units)
            .map(|answer| Answer::Batch(batch_frame(batch, &answer)))
            .map_err(|failure| format!("batch {batch}: {failure}")),
        Job::Verify {
            request,
            share,
            items,
        } => worker
            .verify(items)
            .map(|keys| Answer::Verify {
                request,
                share,
                keys,
            })
            .map_err(|failure| format!("verify: {failure}")),
    }
}

/// Answer the requests `events` carries with a pool of `threads` workers over the init at `init`, forwarding each answer to `replies` as it arrives. The pool threads are left to end once the job queue and the event queue close, which they do when this returns.
fn work(
    init: &Path,
    threads: usize,
    events_in: &Sender<Event>,
    events: &Receiver<Event>,
    replies: &Sender<Vec<u8>>,
) -> Ending {
    let fail = |message: String| {
        let _ = replies.send(line(error_frame(&message)));
        Ending::Failed
    };
    if threads == 0 {
        return fail(
            "the units engine needs at least one thread, and --threads=0 asks for none".to_owned(),
        );
    }
    let resources = match Resources::open(init) {
        Ok(resources) => Arc::new(resources),
        Err(failure) => return fail(format!("the units engine cannot start: {failure}")),
    };
    let pool = match Pool::start(&resources, threads, run_job, events_in) {
        Ok(pool) => pool,
        Err(failure) => return fail(failure),
    };
    let mut outstanding = 0usize;
    let mut verifies: VecDeque<PendingVerify> = VecDeque::new();
    let mut requested = 0usize;
    let mut stopping = false;
    loop {
        while verifies
            .front()
            .is_some_and(|pending| pending.shares.iter().all(Option::is_some))
        {
            let pending = verifies.pop_front().expect("a front verify");
            let keys: Vec<VerifyKey> = pending.shares.into_iter().flatten().flatten().collect();
            if replies.send(verify_frame(&keys)).is_err() {
                return Ending::Failed;
            }
        }
        if stopping && outstanding == 0 {
            return if replies.send(stopped_frame(peak_footprint())).is_ok() {
                Ending::Stopped
            } else {
                Ending::Failed
            };
        }
        let Ok(event) = events.recv() else {
            return Ending::Closed;
        };
        match event {
            Event::Incoming(_) if stopping => {}
            Event::Incoming(Incoming::Request(Request::Batch { batch, units })) => {
                if !pool.submit(Job::Batch { batch, units }) {
                    return fail(format!("batch {batch}: the pool has stopped"));
                }
                outstanding += 1;
            }
            Event::Incoming(Incoming::Request(Request::Verify { units })) => {
                let request = requested;
                requested += 1;
                let shares = shares(units, pool.threads());
                verifies.push_back(PendingVerify {
                    request,
                    shares: shares.iter().map(|_| None).collect(),
                });
                for (share, items) in shares.into_iter().enumerate() {
                    if !pool.submit(Job::Verify {
                        request,
                        share,
                        items,
                    }) {
                        return fail("verify: the pool has stopped".to_owned());
                    }
                    outstanding += 1;
                }
            }
            Event::Incoming(Incoming::Request(Request::Stop)) => stopping = true,
            Event::Incoming(Incoming::Refused(failure)) => return fail(failure),
            Event::Incoming(Incoming::Closed) => return Ending::Closed,
            Event::Answered(Answer::Batch(reply)) => {
                outstanding -= 1;
                if replies.send(reply).is_err() {
                    return Ending::Failed;
                }
            }
            Event::Answered(Answer::Verify {
                request,
                share,
                keys,
            }) => {
                outstanding -= 1;
                if let Some(slot) = verifies
                    .iter_mut()
                    .find(|pending| pending.request == request)
                    .and_then(|pending| pending.shares.get_mut(share))
                {
                    *slot = Some(keys);
                }
            }
            Event::Failed(failure) => return fail(failure),
        }
    }
}

/// Serve the requests `input` carries with the init at `init` and a pool of `threads` workers, writing the replies to `output`, until `stop`, a failure or the input's end. The reader thread is left running when the service ends, since it may be blocked on an input the parent still holds open, and so are pool threads still inside a job; the process exits after this returns.
pub fn serve(
    init: PathBuf,
    threads: usize,
    input: impl BufRead + Send + 'static,
    output: impl Write + Send + 'static,
) -> Ending {
    let (events_in, events) = channel();
    let (replies, replies_out) = channel();
    let reader_events = events_in.clone();
    std::thread::spawn(move || read_requests(input, &reader_events));
    let writer = std::thread::spawn(move || write_replies(output, &replies_out));
    let panicked = replies.clone();
    let ending = std::thread::spawn(move || work(&init, threads, &events_in, &events, &replies))
        .join()
        .unwrap_or_else(|panic| {
            let _ = panicked.send(line(error_frame(&format!(
                "the units engine's coordinator panicked: {}",
                panic_message(panic.as_ref())
            ))));
            Ending::Failed
        });
    drop(panicked);
    let written = writer.join().unwrap_or(false);
    if written { ending } else { Ending::Failed }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;
    use std::sync::Mutex;

    #[derive(Clone, Default)]
    struct Shared(Arc<Mutex<Vec<u8>>>);

    impl Write for Shared {
        fn write(&mut self, data: &[u8]) -> std::io::Result<usize> {
            self.0.lock().expect("the lock").extend_from_slice(data);
            Ok(data.len())
        }

        fn flush(&mut self) -> std::io::Result<()> {
            Ok(())
        }
    }

    #[test]
    fn a_batch_reply_counts_its_payload_and_carries_it_after_the_line() {
        let frame = batch_frame(3, &Batch::default());
        assert_eq!(
            frame,
            b"{\"op\":\"batch\",\"batch\":3,\"projections\":[],\"contract_errors\":[],\"payload\":0}\n"
        );
        assert_eq!(
            stopped_frame(42),
            b"{\"op\":\"stopped\",\"peak_footprint\":42}\n"
        );
        assert_eq!(
            verify_frame(&[VerifyKey {
                unit_id: "u-1".to_owned(),
                content_key: "c".to_owned(),
                ink_deltas: vec![("ss10".to_owned(), "d".to_owned())],
            }]),
            b"{\"op\":\"verify\",\"keys\":[[\"u-1\",\"c\",{\"ss10\":\"d\"}]]}\n"
        );
    }

    #[test]
    fn an_engine_that_cannot_start_answers_with_an_error_frame() {
        let output = Shared::default();
        let ending = serve(
            PathBuf::from("/nonexistent/init.json"),
            4,
            Cursor::new(b"{\"op\":\"stop\"}\n".to_vec()),
            output.clone(),
        );
        assert_eq!(ending, Ending::Failed);
        let written = String::from_utf8(output.0.lock().expect("the lock").clone()).expect("UTF-8");
        assert!(written.starts_with(
            "{\"op\":\"error\",\"message\":\"the units engine cannot start: cannot read the init"
        ));
        assert!(written.ends_with("\n"));
    }

    #[test]
    fn the_engine_needs_a_thread() {
        let output = Shared::default();
        let ending = serve(
            PathBuf::from("init.json"),
            0,
            Cursor::new(Vec::new()),
            output.clone(),
        );
        assert_eq!(ending, Ending::Failed);
        let written = String::from_utf8(output.0.lock().expect("the lock").clone()).expect("UTF-8");
        assert!(written.contains("--threads=0 asks for none"));
    }
}
