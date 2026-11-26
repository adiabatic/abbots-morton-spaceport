# Proofs for the grouped sweep

`build-tables` answers most asks of a fourth-slot prospect probe from the transcript of an earlier ask instead of running them, unless given `--no-grouped-sweeps` (`Engine::grouped_probe_prospect`, `fourth_class_live` in `src/liveness.rs`). The Lean files here prove, about a model of that sweep, that grouping changes neither where the sweep ends nor what it fires.

A proof shows only that the stated theorem holds of the stated model. Whether the model is the crate, and whether the theorem is the one the design needs, is for a reader to judge, and this file is written for that reader: it gives each theorem in plain words, says what each definition stands for in the crate, and lists what the model assumes.

## Checking the proofs

```sh
make kernel-proofs
```

It needs [`elan`](https://github.com/leanprover/elan) on the path, which fetches the Lean release that `lean-toolchain` pins. The proofs use core Lean only. No local gate runs this target, and no build needs Lean installed. GitHub Actions runs it, beside `make kernel-check`, on a push to `master` or a pull request that touches `engine/kernel-rs/` (`.github/workflows/kernel.yml`).

The target fails on any warning, so it fails on a proof left unfinished. `GroupedSweeps/Axioms.lean` fails the build if a theorem comes to rest on an axiom beyond the ones it lists.

## Checking the model against the crate

`modeled-sources.tsv` pins the Rust items the model stands for most directly: the fourth-slot sweep, the grouped ask and its replay of a transcript, every function of `src/engine.rs` that calls `token::enter` (where a call becomes a recorded nested question or the recording ask itself), and `src/token.rs` outside its tests. Each line holds the source's path in the crate, the item (`fn Type::name` for a method, `module` for the file), and the SHA-256 of the item's text with its blank lines and comment lines left out.

The crate's test `the_modeled_sources_match_their_pinned_digests`, in `src/token.rs`, fails in `make kernel-gate` when a pinned item's text changes, and when a function of `src/engine.rs` calls `token::enter` but has no line in the file. When it fails, read [What each definition stands for](#what-each-definition-stands-for) and [What the model assumes](#what-the-model-assumes) against the new code, change the model and its proofs here where what they say no longer holds, and then replace the item's digest with the one the failure prints. The test needs no Lean.

## What is proved

### The path lemma

`path_lemma` in `GroupedSweeps/Path.lean`.

Take one ask and two tokens. Suppose the ask for the first token never reads its token directly, and the second token gives every question that ask puts the answer the first token gave. Then the ask for the second token:

- puts the same questions, in the same order, and reads the same answers;
- returns the same result;
- fires the same effects outside its questions, in the same order;
- never reads its token directly either.

What a question fires while it is being answered may differ between the two tokens. `mem_fired` says that an ask fires exactly its effects outside its questions and the effects of the questions it puts.

### The sweep theorem

`sweep_theorem` in `GroupedSweeps/Sweep.lean`.

Take any probe, any list of tokens, any rule for which asks may be grouped, and any rule for which full asks leave a transcript. Run the full sweep and the grouped sweep. Then:

- both end at the same step with the same result, or both run out of tokens;
- both run the same number of steps;
- after every step, list what each sweep has fired so far, dropping every repeat of an effect already listed. The two lists are equal.

`sweep_fires_the_same_set` is the weaker reading the crate's output needs: after every step, the two sweeps have fired the same set of effects.

A single step of the grouped sweep does not fire what the same step of the full sweep fires. A token answered from a transcript fires only what its questions fire, and the effects its ask would fire outside them are ones the transcript's own ask fired at an earlier step. That is why the theorem speaks of everything fired so far.

`GroupedSweeps/Example.lean` works one small probe through both sweeps.

## What each definition stands for

`GroupedSweeps/Model.lean` holds every definition the theorems mention.

| Model                               | Crate                                                                                                                                                          |
| ----------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| a token                             | the `RightToken` in the probe's fourth slot                                                                                                                    |
| `Eval`                              | one ask: `Engine::probe_prospect` on `(right1, right2, right3, token)` with everything but the token fixed                                                     |
| `Eval.ask`                          | a question put to the swept token: a `token::Question`, or `kind()`, which every grouped token answers alike because only letters are grouped                  |
| `Oracle.answer`                     | a question's answer as its caller reads it, an error as its kind                                                                                               |
| `Oracle.effects`                    | the pointers a nested question fires while it is answered                                                                                                      |
| `Eval.emit`                         | a pointer the ask fires outside its nested questions                                                                                                           |
| `Eval.peek`                         | a read that poisons a recording ask: a rune accessor on the swept token, or comparing, ordering or hashing it against a token that is not the same swept token |
| `asked`                             | the questions of a `Transcript`                                                                                                                                |
| `answerAll`                         | `Engine::answers`                                                                                                                                              |
| `answerFirst`, `groupedAsk`         | `Engine::grouped_probe_prospect`                                                                                                                               |
| `Grouping.groupable`                | the run takes every ask (`whole()`), grouped sweeps are on, the token is a letter, the engine keeps a fired journal, and no capture is open                    |
| `Grouping.keeps`                    | the recording ask returns a value its own prospect computed, and no memo answers its own prospect or window                                                    |
| `Probe.base`                        | `EDGE`, the baseline's fourth slot                                                                                                                             |
| `Probe.ends`                        | at the baseline, whether its ask raises; at a later step, whether the ask raises or its result differs from the baseline's                                     |
| `loop`, `fullSweep`, `groupedSweep` | `fourth_class_live` under an `Asks` run for which `whole()` holds, with grouping off and on                                                                    |
| `Outcome.stop`                      | the step at which the probe finds its slot live or raises; `none` is the dead verdict                                                                          |
| everything fired so far             | what the sweep adds to `Engine::fired`                                                                                                                         |

## What the model assumes

These are the places where the model could differ from the crate. Each names what holds the crate to it.

- **An ask reads its token only through questions.** The model's ask is one decision procedure for every token, and a direct read is a `peek`. `RightToken`'s representation is private to `src/token.rs`, and the crate forbids unsafe code. `key_ordinal()` returns the token's identity for memo keys, and no memo lookup lets it steer an ask that leaves a transcript: a memo that answers the ask's own prospect or window marks the ask as answered from a memo, which leaves none, and every other memo keyed on the token is reached inside a nested question. `token.rs`'s tests pin the files that call `key_ordinal()`, not the call sites, so a new memo keyed on it needs the same care.
- **A memo changes no answer.** In the model a question's answer and effects depend only on the question and the token. In the crate a memo may answer a nested question, replaying the pointers its first computation fired. Every memo in the crate rests on this, and the crate's memo identity tests check it. `Engine::answers` makes each nested call as the ask makes it, with replay errors discarded as they are inside the ask's own prospect.
- **A recorded answer is all the caller reads.** The ask branches on a nested call's result only as far as the `Question` records it.
- **Nothing else varies during a probe.** The ask's other arguments and the specification are fixed from the baseline to the last token.
- **Only what is fired is observed.** A grouped sweep runs only where no capture is open and no verdict is published, so nothing reads one ask's firings or reads by themselves. The build's output reads `Engine::fired` as a set, which is less than the theorem gives.

The model says nothing about what the memos hold. A grouped build's prospect memo lacks the entry of an ask a transcript answered, and the crate's documentation of `Engine::grouped_probe_prospect` says what else can differ there.

`build-tables --check-grouped-sweeps` checks the crate against the theorem's conclusion as it runs: it also runs every answered ask in full and raises when the result differs or the full ask fires a pointer the build had not.
