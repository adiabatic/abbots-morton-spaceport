//! The units phase's thread pool: threads over one shared set of [`Resources`], each holding its own [`Worker`] over them and taking the next job off one queue. The corpus build's units phase drives it in process ([`crate::corpus::drive`]).
//!
//! A thread reports each job's answer, or a failure, to its owner's event queue as a [`Report`]. A failure names what failed: a worker that cannot start, a job whose run fails, or a panic inside a job, after which the thread takes no further job. A thread ends when the job queue closes, after its first failure, or once its owner's event queue is gone.

use std::panic::{AssertUnwindSafe, catch_unwind};
use std::sync::mpsc::{Receiver, Sender, channel};
use std::sync::{Arc, Mutex};

use crate::unit::{Resources, Worker};

/// What a pool thread tells its owner about one job.
#[derive(Debug, PartialEq, Eq)]
pub enum Report<A> {
    /// The job's answer.
    Answered(A),
    /// A failure that names what failed; the thread that reports it takes no further job.
    Failed(String),
}

/// The job queue of a running pool: each job [`Pool::submit`] queues is taken by the next free thread. Dropping the pool closes the queue, and each thread ends once it has finished the job it holds; the threads are not joined.
pub struct Pool<J> {
    jobs: Sender<J>,
    threads: usize,
}

impl<J: Send + 'static> Pool<J> {
    /// Start `threads` threads named `units-<index>` over `resources`, each running the jobs it takes through `run` with its own [`Worker`] and sending [`Report`]s to `events`. A thread that cannot be spawned fails the start with a message naming its index, and the threads already started end as the queue closes.
    pub fn start<A: 'static, E>(
        resources: &Arc<Resources>,
        threads: usize,
        run: fn(&mut Worker<'_>, J) -> Result<A, String>,
        events: &Sender<E>,
    ) -> Result<Self, String>
    where
        E: From<Report<A>> + Send + 'static,
    {
        let (jobs_in, jobs) = channel::<J>();
        let jobs = Arc::new(Mutex::new(jobs));
        for index in 0..threads {
            let (resources, jobs, events) =
                (Arc::clone(resources), Arc::clone(&jobs), events.clone());
            std::thread::Builder::new()
                .name(format!("units-{index}"))
                .spawn(move || {
                    match Worker::new(&resources) {
                        Ok(mut worker) => serve_jobs(&mut worker, run, &jobs, &events),
                        Err(failure) => {
                            let _ = events.send(E::from(Report::Failed(format!(
                                "the units engine cannot start: {failure}"
                            ))));
                        }
                    };
                })
                .map_err(|failure| {
                    format!("the units engine cannot start thread {index}: {failure}")
                })?;
        }
        Ok(Self {
            jobs: jobs_in,
            threads,
        })
    }

    /// Queue `job` for the next free thread; false once every thread has ended, so no thread will take it.
    pub fn submit(&self, job: J) -> bool {
        self.jobs.send(job).is_ok()
    }

    /// The number of threads the pool started with.
    pub fn threads(&self) -> usize {
        self.threads
    }
}

/// `items` cut into contiguous shares of `ceil(len / threads)`, as the verification cuts its sample among the threads.
pub fn shares<T>(mut items: Vec<T>, threads: usize) -> Vec<Vec<T>> {
    let size = items.len().div_ceil(threads.max(1)).max(1);
    let mut shares = Vec::new();
    while !items.is_empty() {
        let rest = items.split_off(size.min(items.len()));
        shares.push(std::mem::replace(&mut items, rest));
    }
    shares
}

/// The text a panic carried, or nothing when it carried neither a `&str` nor a `String`.
pub fn panic_message(panic: &(dyn std::any::Any + Send)) -> String {
    panic
        .downcast_ref::<&str>()
        .map(|text| (*text).to_owned())
        .or_else(|| panic.downcast_ref::<String>().cloned())
        .unwrap_or_default()
}

/// One pool thread's loop: answer the jobs off `jobs` through `run` with `worker`, reporting each to `events`, until the queue closes, a job fails or panics, or `events` is gone.
fn serve_jobs<W, J, A, E>(
    worker: &mut W,
    run: impl Fn(&mut W, J) -> Result<A, String>,
    jobs: &Mutex<Receiver<J>>,
    events: &Sender<E>,
) where
    E: From<Report<A>>,
{
    loop {
        let job = match jobs.lock() {
            Ok(queue) => match queue.recv() {
                Ok(job) => job,
                Err(_) => return,
            },
            Err(_) => return,
        };
        let report = match catch_unwind(AssertUnwindSafe(|| run(worker, job))) {
            Ok(Ok(answer)) => Report::Answered(answer),
            Ok(Err(failure)) => Report::Failed(failure),
            Err(panic) => Report::Failed(format!(
                "the units engine's worker panicked: {}",
                panic_message(panic.as_ref())
            )),
        };
        let failed = matches!(report, Report::Failed(_));
        if events.send(E::from(report)).is_err() || failed {
            return;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn doubled(seen: &mut Vec<i64>, job: i64) -> Result<i64, String> {
        match job {
            -1 => Err("job -1 failed".to_owned()),
            -2 => panic!("job -2 panicked"),
            _ => {
                seen.push(job);
                Ok(job * 2)
            }
        }
    }

    fn served(queued: &[i64]) -> (Vec<Report<i64>>, Vec<i64>, usize) {
        let (jobs_in, jobs) = channel();
        for job in queued {
            jobs_in.send(*job).expect("the queue");
        }
        drop(jobs_in);
        let jobs = Mutex::new(jobs);
        let (events, reports) = channel();
        let mut seen = Vec::new();
        serve_jobs(&mut seen, doubled, &jobs, &events);
        drop(events);
        let left = jobs.lock().expect("the lock").iter().count();
        (reports.iter().collect(), seen, left)
    }

    #[test]
    fn a_thread_answers_every_job_in_queue_order_until_the_queue_closes() {
        assert_eq!(
            served(&[1, 2, 3]),
            (
                vec![
                    Report::Answered(2),
                    Report::Answered(4),
                    Report::Answered(6)
                ],
                vec![1, 2, 3],
                0
            )
        );
    }

    #[test]
    fn a_thread_reports_a_failed_job_and_takes_no_further_job() {
        assert_eq!(
            served(&[1, -1, 3]),
            (
                vec![
                    Report::Answered(2),
                    Report::Failed("job -1 failed".to_owned())
                ],
                vec![1],
                1
            )
        );
    }

    #[test]
    fn a_thread_reports_a_panic_with_its_message_and_takes_no_further_job() {
        assert_eq!(
            served(&[-2, 3]),
            (
                vec![Report::Failed(
                    "the units engine's worker panicked: job -2 panicked".to_owned()
                )],
                Vec::new(),
                1
            )
        );
    }

    #[test]
    fn a_thread_ends_once_its_owner_is_gone() {
        let (jobs_in, jobs) = channel();
        for job in [1, 2, 3] {
            jobs_in.send(job).expect("the queue");
        }
        let jobs = Mutex::new(jobs);
        let (events, reports) = channel::<Report<i64>>();
        drop(reports);
        let mut seen = Vec::new();
        serve_jobs(&mut seen, doubled, &jobs, &events);
        assert_eq!(seen, vec![1]);
    }

    #[test]
    fn a_verify_sample_is_cut_into_contiguous_shares_a_thread() {
        assert_eq!(
            shares((0..10).collect(), 4),
            vec![vec![0, 1, 2], vec![3, 4, 5], vec![6, 7, 8], vec![9]]
        );
        assert_eq!(shares((0..3).collect(), 8), vec![vec![0], vec![1], vec![2]]);
        assert_eq!(shares((0..4).collect(), 1), vec![vec![0, 1, 2, 3]]);
        assert!(shares(Vec::<i32>::new(), 4).is_empty());
    }

    #[test]
    fn a_panic_message_is_its_text_or_nothing() {
        assert_eq!(panic_message(&"static"), "static");
        assert_eq!(panic_message(&"owned".to_owned()), "owned");
        assert_eq!(panic_message(&42), "");
    }
}
