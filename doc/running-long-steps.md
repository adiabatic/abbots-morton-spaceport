# Running the long steps

A full `make artifact-cycle` pass takes longer than ten minutes. An agent harness caps a foreground shell command at about ten minutes and can kill a backgrounded one whatever its progress, which stops the cycle partway through a gate. A bare M1 build is shorter, and the heavy gates (the rebuild suite and the conform sweep) add a few minutes to a `make review-cycle` pass. Run any pass that includes the heavy gates detached from the tool's lifetime. `make cycle-timings ARGS='--by-step'` records what each step costs on this machine (`doc/fleet.md` names the machines); read it before choosing a watcher's timeout and before deciding a run is hung.

## Detach

Run the pass under `nohup`, with `caffeinate -i` to prevent idle sleep, and record the pid at launch:

```zsh
mkdir -p tmp
nohup caffeinate -i make artifact-cycle > tmp/cycle-pass.log 2>&1 &
pid=$!
```

If the tool harness kills the shell's process group when the command returns, launch the command in a new session with `subprocess.Popen(..., start_new_session=True)` through `uv run python`, with standard input disconnected and output redirected to the log. `nohup` ignores hangup signals but does not create a new session.

A Python wrapper that calls M1 guards its entry point with `if __name__ == "__main__":`, because multiprocessing spawn workers import the wrapper and otherwise start the build again.

## Where the output lands

- The redirect log under `tmp/` is scratch. It holds the same terminal output, plus an `rc=` line when the launch command echoes one (the skills' `sh -c` chains run `echo "rc=$?"`), and the next run overwrites it. The run directory is the lasting record.
- Every cycle creates a run directory under `var/build-logs/`, and `var/build-logs/latest` points at the newest one. It holds `plan.txt`, `terminal.log` (a byte copy of what the terminal showed), and one `<nn>-<step>.log` per spawned step, with stdout and stderr merged in arrival order and stderr lines prefixed with `stderr|` and a space.
- Watch `terminal.log` for the closing block: the summary table, then `Cycle complete.` on a passing run or a `CYCLE FAILED:` block of reasons on a failing one. When a step fails or stops printing, open its own log in the same directory.
- A child reports to the cycle through four line prefixes: `[t]`, `[phase]`, `[progress]`, and `[warn]`. `rebuild/tools/console.py` defines them and `CycleConsole`, the terminal renderer that shows them.

## Judging whether a run is hung

Four things make a healthy run look hung or a finished run look alive:

- A pass started while another pass runs waits for it at 0% CPU with no children, having printed `waiting for pass <pid> to finish`, and starts when that pass ends (`doc/review-cycle.md` § What a pass does).
- The pytest controller sits at 0% CPU while its xdist workers run.
- The workers are execnet children whose argv does not contain `pytest`, so searching process names for `pytest` finds nothing.
- A watcher loop polling `pgrep -f <pattern>` matches its own command line and reports a finished run as still running.

Judge liveness by summing CPU over every descendant of the recorded pid, and poll that pid instead of a pattern:

```zsh
descendants() { for c in $(pgrep -P $1); do echo $c; descendants $c; done }
ps -o %cpu= -p $(printf '%s,' $pid $(descendants $pid) | sed 's/,$//') | awk '{s+=$1} END {print s}'
until ! kill -0 $pid 2>/dev/null; do sleep 30; done
```

If you must match a pattern, put one character in brackets so the watcher cannot match itself: `pgrep -f "artifact_cycl[e]"`.

## Stopping a pass

Every step's child but the land stays in the process group the pass started in: `_run_step` spawns it without a group of its own, and `test_a_step_child_stays_in_the_cycles_process_group` in `rebuild/test_artifact_cycle.py` checks this. When the pass was launched with `start_new_session=True`, the recorded pid leads that group, so one signal reaches every process in it at once: the driver, each step's child, and whatever those spawn without a group of their own, including pool workers and pytest workers. The loop after the kill waits until the last of them has exited:

```zsh
kill -TERM -- -$pid
while pgrep -g $pid > /dev/null; do sleep 1; done
```

- The driver catches SIGINT, SIGTERM, and SIGHUP (`stop_signals` in `rebuild/tools/artifact_cycle.py`), terminates and reaps every child it spawned, and ends the pass as interrupted: `terminal.log` ends with a `CYCLE INTERRUPTED:` block naming the signal, and the exit status is 128 plus the signal's number. A signal that was ignored when the pass started stays ignored, so a pass under `nohup` still outlives its shell.
- When the pass is launched with `nohup … &` from a shell without job control, it shares the shell's process group, so signal the recorded pid and its children: `kill -TERM $pid $(pgrep -P $pid)`. This form stops a pass launched either way.
  - In the Detach recipe above, `caffeinate` execs its utility, so `$pid` is `make`. `make` passes SIGTERM to its recipe, `uv run` passes it to the driver, and the driver's cleanup stops the driver's children.
  - In an `sh -c` chain, such as the ones the dont-bug-me-about-this-ever-again skill launches, `$pid` is the `sh`. A SIGTERM to the `sh` alone ends it but leaves the `make` it started running, and `kill -0 $pid` already reports the pid gone. Signaling the `sh`'s children too stops that `make`, and the `sh` starts no further command.
  - On this route, a step child's own workers get no signal. They exit when their pipe to the parent closes, which can take until the end of a batch, so only the group launch lets you wait until every process has exited.
- The land, which moves the new corpus and the verdict store into place together, runs in its own session, so neither the group signal nor Ctrl-C reaches it, and the driver waits for it without signaling it, printing `waiting for the land to finish`, before it ends the pass. It holds the store's lock for a few seconds. It also shares the pass lock (`var/cycle/pass.lock`), so a driver killed with `kill -KILL` while its land runs leaves the next pass waiting until the land exits. The group-emptying loop above does not see the land, because it has left the group; before concluding a stopped pass is gone, also wait until `pgrep -f "rebuild.review.landin[g]"` finds nothing. `kill -9` of the land itself is the only way to cut it short; the review server's next request, the next pass, or a merge then finishes or drops the land it began (`var/cycle/land.json`), and the tabs meanwhile retry their saves from their outbox.
- A build holding gigabytes takes seconds to exit after the signal, so a process listing taken right after the kill still shows it. Wait for the group to empty before concluding that anything survived, and before starting the next pass.
- The driver acts on the first stop signal and ignores every later SIGINT, SIGTERM, or SIGHUP, so pressing Ctrl-C again does not cut a slow teardown short. `kill -KILL` does, but it skips the driver's cleanup and leaves no interrupted record, so use it only on processes a SIGTERM left running.

The next pass starts from whatever a stopped pass left behind. A pass stopped before its land leaves the served corpus and the verdict store as they were, and its build, when it finished, as a complete `rebuild/out/review.next`: the next pass lands that tree without a rebuild when it still reproduces the inputs, and otherwise builds over it as its seed. A partial `review.next` is deleted (`doc/review-cycle.md` § The server and the pass).
