# Running the long steps

A full `make artifact-cycle` pass takes longer than ten minutes, and an agent harness usually kills a backgrounded shell after about ten minutes whatever its progress, which stops the cycle partway through a gate. A bare M1 build is shorter, and the heavy gates (the rebuild suite and the conform sweep) add a few minutes to a `make review-cycle` pass. Run any pass that includes the heavy gates detached from the tool's lifetime. `make cycle-timings ARGS='--by-step'` records what each step costs on this machine (`doc/fleet.md` names the machines); read it before choosing a watcher's timeout and before deciding a run is hung.

## Detach

Run the pass under `nohup`, with `caffeinate -i` to prevent idle sleep, and record the pid at launch:

```zsh
mkdir -p tmp
nohup caffeinate -i make artifact-cycle > tmp/cycle-pass.log 2>&1 &
pid=$!
```

If the tool harness kills the shell's process group when the command returns, launch the command in a new session with `subprocess.Popen(..., start_new_session=True)` through `uv run python`, with standard input disconnected and output redirected to the log. `nohup` ignores hangup signals but does not create a new session.

## Where the output lands

- The redirect log under `tmp/` is scratch. It holds the same terminal output, plus an `rc=` line when the launch command echoes one (the skills' `sh -c` chains run `echo "rc=$?"`), and the next run overwrites it. The run directory is the lasting record.
- Every cycle creates a run directory under `var/build-logs/`, and `var/build-logs/latest` points at the newest one. It holds `plan.txt`, `terminal.log` (a byte copy of what the terminal showed), and one `<nn>-<step>.log` per spawned step, with stdout and stderr merged in arrival order and stderr lines prefixed with `stderr|` and a space.
- Watch `terminal.log` for the closing block: the summary table, then `Cycle complete.` on a passing run or a `CYCLE FAILED:` block of reasons on a failing one. When a step fails or stops printing, open its own log in the same directory.
- A child reports to the cycle through four line prefixes: `[t]`, `[phase]`, `[progress]`, and `[warn]`. `rebuild/tools/console.py` defines them and the digest that renders them.

## Judging whether a run is hung

Three things make a healthy run look hung or a finished run look alive:

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

Every step's child stays in the process group the pass started in: `_run_step` spawns it without a group of its own, and `test_a_step_child_stays_in_the_cycles_process_group` in `rebuild/test_artifact_cycle.py` checks this. When the pass was launched with `start_new_session=True`, the recorded pid leads that group, so one signal reaches every process in it at once: the driver, each step's child, and whatever those spawn without a group of their own, including pool workers and pytest workers. The loop after the kill waits until the last of them has exited:

```zsh
kill -TERM -- -$pid
while pgrep -g $pid > /dev/null; do sleep 1; done
```

- The driver catches SIGINT, SIGTERM, and SIGHUP (`stop_signals` in `rebuild/tools/artifact_cycle.py`), terminates and reaps every child it spawned, and ends the pass as interrupted: `terminal.log` ends with a `CYCLE INTERRUPTED:` block naming the signal, and the exit status is 128 plus the signal's number. A signal that was ignored when the pass started stays ignored, so a pass under `nohup` still outlives its shell.
- When the pass is launched with `nohup … &` from a shell without job control, it shares the shell's process group, so signal the recorded pid and its children: `kill -TERM $pid $(pgrep -P $pid)`. This form stops a pass launched either way.
  - In the Detach recipe above, `caffeinate` execs its utility, so `$pid` is `make`. `make` passes SIGTERM to its recipe, `uv run` passes it to the driver, and the driver's cleanup stops the driver's children.
  - In an `sh -c` chain, such as the ones the dont-bug-me-about-this-ever-again skill launches, `$pid` is the `sh`. A SIGTERM to the `sh` alone ends it but leaves the `make` it started running, and `kill -0 $pid` already reports the pid gone. Signaling the `sh`'s children too stops that `make`, and the `sh` starts no further command.
  - On this route, a step child's own workers get no signal. They exit when their pipe to the parent closes, which can take until the end of a batch, so only the group launch lets you wait until every process has exited.
- A build holding gigabytes takes seconds to exit after the signal, so a process listing taken right after the kill still shows it. Wait for the group to empty before concluding that anything survived, and before starting the next pass.
- The driver acts on the first stop signal and ignores every later SIGINT, SIGTERM, or SIGHUP, so pressing Ctrl-C again does not cut a slow teardown short. `kill -KILL` does, but it skips the driver's cleanup and leaves no interrupted record, so use it only on processes a SIGTERM left running.

The next pass starts from whatever a stopped pass left behind. A pass stopped after the surface build and before the carry leaves the verdict store stamped for the surface the build replaced. The next pass skips the build and carries that store onto the new surface by unit id instead of merging it in directly (`doc/review-cycle.md` § The verdict store).
