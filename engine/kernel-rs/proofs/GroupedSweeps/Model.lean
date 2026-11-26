/-!
The model of the fourth-slot prospect probe's sweep that the theorems in this directory are about.

Everything here is a definition. The theorems are in `Path.lean` and `Sweep.lean`, and `README.md` says in plain words what each definition stands for in the crate and what the model leaves out.
-/

namespace GroupedSweeps

/-- One ask of a probe with everything fixed except the swept token: a decision procedure that fires effects, puts questions about the token to an oracle and branches on the answers, and may read the token directly. `peek` is the direct read, which the crate's recorder calls poisoning. -/
inductive Eval (Tok Q A E R : Type) where
  | done (result : R) : Eval Tok Q A E R
  | emit (effect : E) (next : Eval Tok Q A E R) : Eval Tok Q A E R
  | ask (question : Q) (next : A → Eval Tok Q A E R) : Eval Tok Q A E R
  | peek (next : Tok → Eval Tok Q A E R) : Eval Tok Q A E R

/-- What a question about a token answers, and what answering it fires. Both may depend on the token. -/
structure Oracle (Tok Q A E : Type) where
  answer : Tok → Q → A
  effects : Tok → Q → List E

variable {Tok Q A E R : Type}

/-- The result of the ask for token `t`. -/
def result (o : Oracle Tok Q A E) (t : Tok) : Eval Tok Q A E R → R
  | .done r => r
  | .emit _ next => result o t next
  | .ask q next => result o t (next (o.answer t q))
  | .peek next => result o t (next t)

/-- The questions the ask for token `t` puts to the oracle, in order, each with its answer: the ask's transcript. -/
def asked (o : Oracle Tok Q A E) (t : Tok) : Eval Tok Q A E R → List (Q × A)
  | .done _ => []
  | .emit _ next => asked o t next
  | .ask q next => (q, o.answer t q) :: asked o t (next (o.answer t q))
  | .peek next => asked o t (next t)

/-- The effects the ask for token `t` fires outside its questions, in order. -/
def outside (o : Oracle Tok Q A E) (t : Tok) : Eval Tok Q A E R → List E
  | .done _ => []
  | .emit e next => e :: outside o t next
  | .ask q next => outside o t (next (o.answer t q))
  | .peek next => outside o t (next t)

/-- Every effect the ask for token `t` fires, in order: those outside its questions and those of each question. -/
def fired (o : Oracle Tok Q A E) (t : Tok) : Eval Tok Q A E R → List E
  | .done _ => []
  | .emit e next => e :: fired o t next
  | .ask q next => o.effects t q ++ fired o t (next (o.answer t q))
  | .peek next => fired o t (next t)

/-- Whether the ask for token `t` reads the token directly. -/
def peeks (o : Oracle Tok Q A E) (t : Tok) : Eval Tok Q A E R → Bool
  | .done _ => false
  | .emit _ next => peeks o t next
  | .ask q next => peeks o t (next (o.answer t q))
  | .peek _ => true

/-- What putting one transcript's questions to a token comes to: whether every answer matched, and what the questions fired. -/
structure Walk (E : Type) where
  matched : Bool
  fired : List E

/-- Put a transcript's questions to token `t` in order, stopping after the first whose answer differs from the transcript's. -/
def answerAll [DecidableEq A] (o : Oracle Tok Q A E) (t : Tok) : List (Q × A) → Walk E
  | [] => { matched := true, fired := [] }
  | (q, a) :: rest =>
    if o.answer t q = a then
      { matched := (answerAll o t rest).matched, fired := o.effects t q ++ (answerAll o t rest).fired }
    else
      { matched := false, fired := o.effects t q }

/-- A transcript a grouped sweep keeps: the questions an earlier ask put to its token with their answers, and that ask's result. -/
structure Kept (Q A R : Type) where
  asked : List (Q × A)
  result : R

/-- What trying the kept transcripts on a token comes to: the result of the first transcript the token answers alike, if there is one, and what every question put to the token fired. -/
structure Tried (E R : Type) where
  answer : Option R
  fired : List E

/-- Try the kept transcripts on token `t` in the order they were kept, stopping at the first the token answers alike. -/
def answerFirst [DecidableEq A] (o : Oracle Tok Q A E) (t : Tok) : List (Kept Q A R) → Tried E R
  | [] => { answer := none, fired := [] }
  | k :: rest =>
    if (answerAll o t k.asked).matched then
      { answer := some k.result, fired := (answerAll o t k.asked).fired }
    else
      { answer := (answerFirst o t rest).answer,
        fired := (answerAll o t k.asked).fired ++ (answerFirst o t rest).fired }

/-- One step of a sweep: its result, what it fired, and the sweep's state after it. -/
structure Step (E R S : Type) where
  result : R
  fired : List E
  state : S

/-- What a sweep comes to: the step it ended at with that step's result, or `none` when it ran out of tokens, and what each step it ran fired, in order. -/
structure Outcome (E R : Type) where
  stop : Option (Nat × R)
  steps : List (List E)

/-- Run one step per token, numbering from `step`, until a step's result ends the sweep. -/
def loop {S : Type} (ends : R → Bool) (ask : Nat → S → Tok → Step E R S) :
    Nat → S → List Tok → Outcome E R
  | _, _, [] => { stop := none, steps := [] }
  | step, s, t :: rest =>
    if ends (ask step s t).result then
      { stop := some (step, (ask step s t).result), steps := [(ask step s t).fired] }
    else
      { stop := (loop ends ask (step + 1) (ask step s t).state rest).stop,
        steps := (ask step s t).fired :: (loop ends ask (step + 1) (ask step s t).state rest).steps }

/-- A probe: the oracle, the ask, the baseline's token, and the test that ends the sweep. `ends none r` is the test on the baseline's own result `r`, and `ends (some b) r` the test on a later step's result `r` against the baseline's result `b`. -/
structure Probe (Tok Q A E R : Type) where
  oracle : Oracle Tok Q A E
  eval : Eval Tok Q A E R
  base : Tok
  ends : Option R → R → Bool

/-- The baseline as step zero, then `ask` once per token from step one. -/
def sweep {S : Type} (p : Probe Tok Q A E R) (ask : Nat → S → Tok → Step E R S) (start : S)
    (tokens : List Tok) : Outcome E R :=
  if p.ends none (result p.oracle p.base p.eval) then
    { stop := some (0, result p.oracle p.base p.eval), steps := [fired p.oracle p.base p.eval] }
  else
    { stop := (loop (p.ends (some (result p.oracle p.base p.eval))) ask 1 start tokens).stop,
      steps := fired p.oracle p.base p.eval ::
        (loop (p.ends (some (result p.oracle p.base p.eval))) ask 1 start tokens).steps }

/-- A full ask: the ask run for the token. -/
def fullAsk (p : Probe Tok Q A E R) (_ : Nat) (_ : Unit) (t : Tok) : Step E R Unit :=
  { result := result p.oracle t p.eval, fired := fired p.oracle t p.eval, state := () }

/-- The sweep that runs every ask in full. -/
def fullSweep (p : Probe Tok Q A E R) (tokens : List Tok) : Outcome E R :=
  sweep p (fullAsk p) () tokens

/-- Which asks a grouped sweep may group, and which full asks leave a transcript. Both are arbitrary: the theorems hold for every choice. A full ask that peeks never leaves one, whatever `keeps` says. -/
structure Grouping (Tok : Type) where
  groupable : Nat → Tok → Bool
  keeps : Nat → Tok → Bool

/-- A grouped ask. A token that may be grouped is tried on the kept transcripts: when it answers one alike it takes that transcript's result and fires only what the questions fired; otherwise the ask runs in full after the questions already put to the token, and leaves its own transcript when `keeps` allows and it did not peek. A token that may not be grouped runs in full. -/
def groupedAsk [DecidableEq A] (p : Probe Tok Q A E R) (g : Grouping Tok) (step : Nat)
    (kept : List (Kept Q A R)) (t : Tok) : Step E R (List (Kept Q A R)) :=
  if g.groupable step t then
    match (answerFirst p.oracle t kept).answer with
    | some r => { result := r, fired := (answerFirst p.oracle t kept).fired, state := kept }
    | none =>
      { result := result p.oracle t p.eval,
        fired := (answerFirst p.oracle t kept).fired ++ fired p.oracle t p.eval,
        state :=
          if g.keeps step t && !peeks p.oracle t p.eval then
            kept ++ [{ asked := asked p.oracle t p.eval, result := result p.oracle t p.eval }]
          else kept }
  else
    { result := result p.oracle t p.eval, fired := fired p.oracle t p.eval, state := kept }

/-- The sweep that groups its asks, starting with no transcripts. -/
def groupedSweep [DecidableEq A] (p : Probe Tok Q A E R) (g : Grouping Tok) (tokens : List Tok) :
    Outcome E R :=
  sweep p (groupedAsk p g) [] tokens

/-- The elements of a list that are not in `seen`, each at its first occurrence, in order. -/
def firstsAfter [DecidableEq E] (seen : List E) : List E → List E
  | [] => []
  | e :: rest => if e ∈ seen then firstsAfter seen rest else e :: firstsAfter (e :: seen) rest

/-- A list with every repeat of an element dropped: each element at its first occurrence, in order. -/
def firsts [DecidableEq E] (l : List E) : List E :=
  firstsAfter [] l

/-- A kept transcript stands for the ask of some token `t₁` whose effects outside its questions are all in `seen`: it holds that ask's transcript and result, and that ask does not peek. -/
def Standing (o : Oracle Tok Q A E) (p : Eval Tok Q A E R) (seen : List E) (k : Kept Q A R) : Prop :=
  ∃ t₁, k.asked = asked o t₁ p ∧ k.result = result o t₁ p ∧ peeks o t₁ p = false ∧
    ∀ e ∈ outside o t₁ p, e ∈ seen

end GroupedSweeps
