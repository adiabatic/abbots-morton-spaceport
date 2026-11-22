#![forbid(unsafe_code)]

use std::io::Read;
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

// A build whose input is a FIFO nobody writes blocks opening it, so it is still running when its standard input closes.
#[test]
fn a_build_whose_standard_input_closes_mid_build_exits_1_with_no_result_line() {
    let scratch =
        std::env::temp_dir().join(format!("ams-review-build-stdin-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&scratch);
    std::fs::create_dir_all(&scratch).expect("scratch");
    let input = scratch.join("input.json");
    let made = Command::new("mkfifo").arg(&input).status().expect("mkfifo");
    assert!(made.success(), "mkfifo exited {made}");
    let mut child = Command::new(env!("CARGO_BIN_EXE_ams-review"))
        .arg("build")
        .arg(format!("--input={}", input.display()))
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("spawn");
    std::thread::sleep(Duration::from_millis(300));
    let running = child.try_wait().expect("try_wait").is_none();
    drop(child.stdin.take());
    let started = Instant::now();
    let status = loop {
        if let Some(status) = child.try_wait().expect("try_wait") {
            break Some(status);
        }
        if started.elapsed() > Duration::from_secs(30) {
            let _ = child.kill();
            let _ = child.wait();
            break None;
        }
        std::thread::sleep(Duration::from_millis(20));
    };
    let mut stdout = String::new();
    let mut stderr = String::new();
    child
        .stdout
        .take()
        .expect("stdout")
        .read_to_string(&mut stdout)
        .expect("read stdout");
    child
        .stderr
        .take()
        .expect("stderr")
        .read_to_string(&mut stderr)
        .expect("read stderr");
    let _ = std::fs::remove_dir_all(&scratch);
    assert!(
        running,
        "the build ended before its standard input closed: {stderr}"
    );
    let status = status.expect("the build did not stop when its standard input closed");
    assert_eq!(status.code(), Some(1), "{stderr}");
    assert_eq!(stdout, "");
    assert_eq!(
        stderr,
        "ams-review: build: the standard input closed, so the build stops\n"
    );
}
