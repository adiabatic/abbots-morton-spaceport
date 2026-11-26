import GroupedSweeps.Model

/-!
One small probe, worked by the definitions, to show what the two sweeps fire and that the model is not empty.

A token is a number. Question `n` asks whether `n` divides the token, and answering it for token `t` fires `100 * n + t`. The ask fires `1`, asks whether 2 divides the token, and when it does fires `2`; otherwise it asks whether 3 divides the token, and when it does fires `3`. An ask of an even token returns `7` when the token is at least 100 and `0` otherwise, and every other ask returns `0`.
-/

namespace GroupedSweeps.Example

def oracle : Oracle Nat Nat Bool Nat where
  answer t n := t % n == 0
  effects t n := [100 * n + t]

def eval : Eval Nat Nat Bool Nat Nat :=
  .emit 1 (.ask 2 fun even =>
    if even then .emit 2 (.peek fun t => .done (if t ≥ 100 then 7 else 0))
    else .ask 3 fun third => if third then .emit 3 (.done 0) else .done 0)

/-- The baseline's token is 1, and a step ends the sweep when its result differs from the baseline's. -/
def probe : Probe Nat Nat Bool Nat Nat where
  oracle := oracle
  eval := eval
  base := 1
  ends
    | none, _ => false
    | some b, r => r != b

def everything : Grouping Nat where
  groupable _ _ := true
  keeps _ _ := true

/-- The full sweep of five tokens runs every ask. -/
example : (fullSweep probe [5, 3, 9, 2, 4]).steps =
    [[1, 201, 301], [1, 205, 305], [1, 203, 303, 3], [1, 209, 309, 3], [1, 202, 2], [1, 204, 2]] := by
  decide

/-- The grouped sweep answers 9 from 3's transcript, after 5's fails at its second question. Token 2's ask reads its token directly, so it leaves no transcript and token 4's ask runs in full too. -/
example : (groupedSweep probe everything [5, 3, 9, 2, 4]).steps =
    [[1, 201, 301], [1, 205, 305], [203, 303, 1, 203, 303, 3], [209, 309, 209, 309],
      [202, 202, 1, 202, 2], [204, 204, 1, 204, 2]] := by
  decide

/-- Both sweeps run out of tokens. -/
example : (groupedSweep probe everything [5, 3, 9, 2, 4]).stop = none ∧
    (fullSweep probe [5, 3, 9, 2, 4]).stop = none := by
  decide

/-- Both sweeps end at step 3, where token 100's ask returns 7. -/
example : (groupedSweep probe everything [5, 3, 100, 9]).stop = some (3, 7) ∧
    (fullSweep probe [5, 3, 100, 9]).stop = some (3, 7) := by
  decide

end GroupedSweeps.Example
