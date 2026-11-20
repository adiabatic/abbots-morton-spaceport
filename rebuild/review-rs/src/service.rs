//! The `units` subcommand (§ The process of `rebuild/CORPUS-UNITS-PORT-PLAN.md`): `_corpus_worker`'s message loop over request frames on standard input and reply frames on standard output.
//!
//! One reader thread drains the input into a queue and one writer thread writes the replies, so the parent never blocks writing a batch while the engine blocks writing a reply; one worker ([`Worker`]) answers the requests in order. A `batch` is answered with a `batch` reply whose deflated fragments follow its line, a `verify` with a `verify` reply, and `stop` with `stopped` and the process's peak footprint. A failure, the worker failing to start among them, is answered with an `error` frame that names it, and the service ends; so it does when the input closes without `stop`.

use std::io::{BufRead, Write};
use std::path::{Path, PathBuf};
use std::sync::mpsc::{Receiver, Sender, channel};

use serde_json::{Map, Value};

use crate::ffi::peak_footprint;
use crate::unit::{Batch, Resources, VerifyKey, Worker, deltas_json};
use crate::wire::{Request, error_frame};

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

fn read_requests(input: impl BufRead, requests: &Sender<Incoming>) {
    for line in input.lines() {
        let incoming = match line {
            Ok(line) => match Request::parse(&line) {
                Ok(request) => Incoming::Request(request),
                Err(failure) => Incoming::Refused(failure.to_string()),
            },
            Err(failure) => Incoming::Refused(format!("cannot read a request: {failure}")),
        };
        let refused = matches!(incoming, Incoming::Refused(_));
        if requests.send(incoming).is_err() || refused {
            return;
        }
    }
    let _ = requests.send(Incoming::Closed);
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

/// Answer the requests one at a time with one worker over the init at `init`. `threads` must be 1: one worker serves every request.
fn work(
    init: &Path,
    threads: usize,
    requests: &Receiver<Incoming>,
    replies: &Sender<Vec<u8>>,
) -> Ending {
    let fail = |message: String| {
        let _ = replies.send(line(error_frame(&message)));
        Ending::Failed
    };
    if threads != 1 {
        return fail(format!(
            "the units engine serves one thread, and --threads={threads} asks for {threads}"
        ));
    }
    let resources = match Resources::open(init) {
        Ok(resources) => resources,
        Err(failure) => return fail(format!("the units engine cannot start: {failure}")),
    };
    let mut worker = match Worker::new(&resources) {
        Ok(worker) => worker,
        Err(failure) => return fail(format!("the units engine cannot start: {failure}")),
    };
    loop {
        let reply = match requests.recv() {
            Ok(Incoming::Request(Request::Batch { batch, units })) => {
                match worker.run_batch(units) {
                    Ok(answer) => batch_frame(batch, &answer),
                    Err(failure) => return fail(format!("batch {batch}: {failure}")),
                }
            }
            Ok(Incoming::Request(Request::Verify { units })) => match worker.verify(units) {
                Ok(keys) => verify_frame(&keys),
                Err(failure) => return fail(format!("verify: {failure}")),
            },
            Ok(Incoming::Request(Request::Stop)) => {
                return if replies.send(stopped_frame(peak_footprint())).is_ok() {
                    Ending::Stopped
                } else {
                    Ending::Failed
                };
            }
            Ok(Incoming::Refused(failure)) => return fail(failure),
            Ok(Incoming::Closed) | Err(_) => return Ending::Closed,
        };
        if replies.send(reply).is_err() {
            return Ending::Failed;
        }
    }
}

/// Serve the requests `input` carries with the init at `init`, writing the replies to `output`, until `stop`, a failure or the input's end. The reader thread is left running when the service ends, since it may be blocked on an input the parent still holds open; the process exits after this returns.
pub fn serve(
    init: PathBuf,
    threads: usize,
    input: impl BufRead + Send + 'static,
    output: impl Write + Send + 'static,
) -> Ending {
    let (requests_in, requests) = channel();
    let (replies, replies_out) = channel();
    std::thread::spawn(move || read_requests(input, &requests_in));
    let writer = std::thread::spawn(move || write_replies(output, &replies_out));
    let panicked = replies.clone();
    let ending = std::thread::spawn(move || work(&init, threads, &requests, &replies))
        .join()
        .unwrap_or_else(|panic| {
            let message = panic
                .downcast_ref::<&str>()
                .map(|text| (*text).to_owned())
                .or_else(|| panic.downcast_ref::<String>().cloned())
                .unwrap_or_default();
            let _ = panicked.send(line(error_frame(&format!(
                "the units engine's worker panicked: {message}"
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
    use std::sync::{Arc, Mutex};

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
            1,
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
    fn the_engine_serves_one_thread() {
        let output = Shared::default();
        let ending = serve(
            PathBuf::from("init.json"),
            2,
            Cursor::new(Vec::new()),
            output.clone(),
        );
        assert_eq!(ending, Ending::Failed);
        let written = String::from_utf8(output.0.lock().expect("the lock").clone()).expect("UTF-8");
        assert!(written.contains("--threads=2 asks for 2"));
    }
}
