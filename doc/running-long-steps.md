# Running the long steps

A full `make artifact-cycle` pass outgrows a ten-minute shell window, and an agent harness's backgrounded shell is typically killed at about ten minutes regardless of progress, which strands the cycle mid-gate. A bare M1 build is shorter, and a `make review-cycle` pass's heavy gates (the rebuild suite plus the conform sweep) add a few minutes, but any pass that includes the heavy gates runs detached from the tool's lifetime. `make cycle-timings ARGS='--by-step'` is the record of what each step costs on this machine (`doc/fleet.md` names the machines); read it before picking a watcher's timeout and before deciding a run is hung.

## Detach

Run the pass under `nohup`, with `caffeinate -i` against idle sleep, and record the pid at launch:

```zsh
mkdir -p tmp
nohup caffeinate -i make artifact-cycle > tmp/cycle-pass.log 2>&1 &
pid=$!
```

When a tool harness cleans up the shell's process group on return, launch the command in a separate session with `subprocess.Popen(..., start_new_session=True)` through `uv run python`, with standard input disconnected and output redirected to the log. `nohup` ignores hangup signals but does not create a separate session.

## Where the output lands

- The redirect log under `tmp/` is scratch: it holds the same terminal output and the wrapper's `rc=` line, and the next run overwrites it. The run directory is the kept record.
- Every cycle opens a run directory under `var/build-logs/`, with `var/build-logs/latest` pointing at the newest one. It holds `plan.txt`, `terminal.log` as a byte copy of what the terminal saw, and one `<nn>-<step>.log` per spawned step with stdout and stderr merged in arrival order and the stderr lines tagged.
- Watch `terminal.log` for the completion banner: the summary table, then `Cycle complete.` on a green pass or a `CYCLE FAILED:` block of reasons on a red one. When a step goes red or falls quiet, open its own log beside it.
- A child speaks to the cycle through four line prefixes — `[t]`, `[phase]`, `[progress]`, `[warn]` — and `rebuild/tools/console.py` is the authority on both the producers and the digest that renders them.

## Judging whether a run is hung

Three traps; the first two have each killed a healthy run once:

- The pytest controller legitimately sits at 0% CPU while its xdist workers grind.
- The workers are execnet children whose argv contains no `pytest`, so grepping process names finds nothing.
- A watcher loop polling `pgrep -f <pattern>` matches its own command line and reports a finished run as live forever.

So judge liveness by summing CPU over every descendant of the recorded pid, and poll that pid rather than a pattern:

```zsh
descendants() { for c in $(pgrep -P $1); do echo $c; descendants $c; done }
ps -o %cpu= -p $(printf '%s,' $pid $(descendants $pid) | sed 's/,$//') | awk '{s+=$1} END {print s}'
until ! kill -0 $pid 2>/dev/null; do sleep 30; done
```

If a pattern is unavoidable, bracket a character so the watcher cannot match itself: `pgrep -f "artifact_cycl[e]"`.

## Stopping a pass

Every step's child stays in the process group the pass started in: `_run_step` spawns it in the cycle's group, and `test_a_step_child_stays_in_the_cycles_process_group` in `rebuild/test_artifact_cycle.py` holds that. Launched with `start_new_session=True`, the recorded pid leads that group, so one signal reaches every process in it at once — the driver, each step's child, and whatever those spawn without a group of their own, pool workers and pytest workers among them — and the loop after it waits until the last of them has exited:

```zsh
kill -TERM -- -$pid
while pgrep -g $pid > /dev/null; do sleep 1; done
```

- The driver catches SIGINT, SIGTERM and SIGHUP (`stop_signals` in `rebuild/tools/artifact_cycle.py`), terminates and reaps every child it spawned, and closes the pass as interrupted: `terminal.log` ends on a `CYCLE INTERRUPTED:` block naming the signal, and the exit status is 128 plus the signal's number. A signal the pass started with ignored stays ignored, so a pass under `nohup` still outlives its shell.
- Launched with `nohup … &` from a shell without job control, the pass shares the shell's group, so signal the recorded pid and its children: `kill -TERM $pid $(pgrep -P $pid)`. That one form stops both launch shapes.
  - In the Detach recipe above, `caffeinate` execs its utility, so `$pid` is `make`. `make` hands SIGTERM to its recipe, `uv run` hands it to the driver, and the driver's cleanup above takes its children down.
  - In a `sh -c` chain, such as the dont-bug-me-about-this-ever-again skill's launches, `$pid` is the `sh`. A SIGTERM to the `sh` alone ends it and leaves the `make` it is running at work, while `kill -0 $pid` already reports the pid gone. Signaling its children as well stops that `make`, and the `sh` starts no further command.
  - A child's own workers get no signal on that route; they exit when their pipe to the parent breaks, which can be a batch later, so only the group launch can be waited out exactly.
- A build holding gigabytes takes seconds to exit after the signal, so a process listing taken right after the kill still shows it. Wait for the group to empty before deciding anything survived, and before starting the next pass.
- The driver acts on the first stop signal and ignores every later SIGINT, SIGTERM or SIGHUP, so a repeated Ctrl-C does not cut a slow teardown short. `kill -KILL` is the way past one; it skips the driver's cleanup and leaves no interrupted record, so keep it for whatever a SIGTERM left standing.

The next pass runs from what a stopped pass leaves behind. One stopped after the surface build and before the carry leaves the verdict store stamped for the surface the build replaced; the next pass skips the build and carries that store onto the new surface by unit id rather than merging it straight in (`doc/review-cycle.md` § The verdict store).
