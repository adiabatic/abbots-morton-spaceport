# Running the long steps

A full `make artifact-cycle` pass outgrows a ten-minute shell window, and an agent harness's backgrounded shell is typically killed at about ten minutes regardless of progress, which strands the cycle mid-gate. A bare M1 build is shorter, and a `make review-cycle` pass's heavy gates (the rebuild suite plus the conform sweep) add a few minutes, but any pass that includes the heavy gates runs detached from the tool's lifetime. `make cycle-timings ARGS='--by-step'` is the record of what each step costs on this machine (`doc/fleet.md` names the machines); read it before picking a watcher's timeout and before deciding a run is hung.

## Detach

Run the pass under `nohup`, with `caffeinate -i` against idle sleep, and record the pid at launch:

```zsh
mkdir -p tmp
nohup caffeinate -i make artifact-cycle > tmp/cycle-pass.log 2>&1 &
pid=$!
```

## Where the output lands

- Every cycle opens a run directory under `tmp/build-logs/`, with `tmp/build-logs/latest` pointing at the newest one. It holds `plan.txt`, `terminal.log` as a byte copy of what the terminal saw, and one `<nn>-<step>.log` per spawned step with stdout and stderr merged in arrival order and the stderr lines tagged.
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
