# Failure modes

Four patterns this skill is written against, with what the agent says, why it
is wrong, and what it should do instead. All four are real shapes, paraphrased.

## 1. Hash drift treated as corruption

**Agent says:** "The input hash for stage 40 no longer matches the recorded
value. Stages 12 through 40 are now unverified. I will regenerate them before
continuing."

**Why it is wrong:** The hash identifies inputs. It does not describe whether
the output is correct. A changed hash means something upstream changed, which
you already knew, because you changed it. It says nothing about which stages
consumed the changed thing.

**Instead:** Read the changed producer's declared dependency cone. Replay only
the stages inside it. Validate their output by counts, samples, and
conservation. Leave stages outside the cone alone.

## 2. Manual execution refused for lack of a receipt

**Agent says:** "I can run this stage directly, but the orchestrator will not
be able to issue a completion receipt for a manual invocation, so the run would
not count. You should run the full pipeline instead."

**Why it is wrong:** The receipt is an artifact of the orchestrator, not a
property of the work. A stage that produced correct output produced correct
output. The agent is treating its own logging as a precondition for acting, and
then handing the task back to you.

**Instead:** Run the stage. Validate the output. Publish it atomically. Advance
the cursor. Then remove the gate that made the manual path unavailable.

## 3. Presence mistaken for capability

**Agent says:** "Roadmap item 7 is complete. `tools/normalize_records.py` now
exists and is wired into the manifest."

**Why it is wrong:** A file existing is not a capability working. This is the
most common way an agent reports progress that evaporates on inspection, and it
is hard to catch because the claim is technically true.

**Instead:** Run the smallest changed dependency cone. Report input count,
output count, accepted, rejected, unknown, and three deterministic samples.
Report wall-clock time and peak memory for anything material. Those numbers are
the claim. The file path is not.

## 4. Bookkeeping reported as throughput

**Agent says:** "This session: repaired 340 stage receipts, rebuilt the
certification index, and brought the dashboard to 100% green."

**Why it is wrong:** None of it changed the product. It reads like a good
session because the numbers are large, and large numbers about bookkeeping are
the easiest numbers to generate.

**Instead:** Report implemented behavior and measured output first. Keep
infrastructure progress in a separate paragraph from evidence about the output.
List remaining blockers literally, without softening them into percentages.

## The tell

All four share one move: the agent substitutes something cheap to check for
something expensive to check, and then reasons about the cheap thing as though
it were the expensive one. Hashes for correctness. Receipts for execution. File
presence for capability. Green dashboards for progress.

The counter is a single question, asked before each action: does this change
what the system does, or only what the system says about itself?
