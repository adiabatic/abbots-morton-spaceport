import GroupedSweeps.Model
import GroupedSweeps.Firsts
import GroupedSweeps.Path
import GroupedSweeps.Answer

/-!
The sweep theorem: a grouped sweep ends where the full sweep ends and fires what it fires.
-/

namespace GroupedSweeps

variable {Tok Q A E R : Type} [DecidableEq A] [DecidableEq E]

/-- One grouped ask returns the full ask's result, extends the first occurrences as the full ask does, and leaves only standing transcripts. -/
theorem groupedAsk_step (p : Probe Tok Q A E R) (g : Grouping Tok) (step : Nat)
    (kept : List (Kept Q A R)) (t : Tok) (sg sf : List E) (hf : firsts sg = firsts sf)
    (hs : ∀ k ∈ kept, Standing p.oracle p.eval sf k) :
    (groupedAsk p g step kept t).result = result p.oracle t p.eval ∧
    firsts (sg ++ (groupedAsk p g step kept t).fired) = firsts (sf ++ fired p.oracle t p.eval) ∧
    ∀ k ∈ (groupedAsk p g step kept t).state,
      Standing p.oracle p.eval (sf ++ fired p.oracle t p.eval) k := by
  have widen : ∀ k ∈ kept, Standing p.oracle p.eval (sf ++ fired p.oracle t p.eval) k :=
    fun k hk => (hs k hk).append p.oracle p.eval _
  unfold groupedAsk
  cases hg : g.groupable step t
  · exact ⟨rfl, firsts_append_congr _ hf, widen⟩
  · cases h : (answerFirst p.oracle t kept).answer with
    | some r =>
      obtain ⟨hr, hfir⟩ := answerFirst_some p.oracle p.eval t kept sf r hs h
      exact ⟨hr, (firsts_append_congr _ hf).trans hfir, widen⟩
    | none =>
      refine ⟨rfl, ?_, ?_⟩
      · show firsts (sg ++ ((answerFirst p.oracle t kept).fired ++ fired p.oracle t p.eval)) = _
        rw [firsts_append_congr _ hf, ← List.append_assoc]
        exact answerFirst_none p.oracle p.eval t kept sf hs h
      · intro k hk
        by_cases hc : (g.keeps step t && !peeks p.oracle t p.eval) = true
        · simp only [hc, ite_true] at hk
          rcases List.mem_append.mp hk with hk | hk
          · exact widen k hk
          · rw [List.mem_singleton] at hk
            subst hk
            simp only [Bool.and_eq_true, Bool.not_eq_true'] at hc
            exact ⟨t, rfl, rfl, hc.2, fun e he =>
              List.mem_append_right _ (outside_subset_fired p.oracle p.eval t e he)⟩
        · simp only [hc] at hk
          exact widen k hk

/-- The grouped loop and the full loop stop alike, run the same number of steps, and after every step extend matching first occurrences into matching first occurrences. -/
theorem loop_grouped (p : Probe Tok Q A E R) (g : Grouping Tok) (ends : R → Bool)
    (tokens : List Tok) :
    ∀ (step : Nat) (kept : List (Kept Q A R)) (sg sf : List E),
      firsts sg = firsts sf → (∀ k ∈ kept, Standing p.oracle p.eval sf k) →
      (loop ends (groupedAsk p g) step kept tokens).stop =
          (loop ends (fullAsk p) step () tokens).stop ∧
        (loop ends (groupedAsk p g) step kept tokens).steps.length =
          (loop ends (fullAsk p) step () tokens).steps.length ∧
        ∀ n, firsts (sg ++ ((loop ends (groupedAsk p g) step kept tokens).steps.take n).flatten) =
          firsts (sf ++ ((loop ends (fullAsk p) step () tokens).steps.take n).flatten) := by
  induction tokens with
  | nil =>
    intro step kept sg sf hf _
    simp [loop, hf]
  | cons t rest ih =>
    intro step kept sg sf hf hs
    obtain ⟨h1, h2, h3⟩ := groupedAsk_step p g step kept t sg sf hf hs
    have hfr : (fullAsk p step () t).result = result p.oracle t p.eval := rfl
    have hff : (fullAsk p step () t).fired = fired p.oracle t p.eval := rfl
    simp only [loop, h1, hfr, hff]
    cases he : ends (result p.oracle t p.eval)
    · simp only [Bool.false_eq_true, ite_false]
      obtain ⟨i1, i2, i3⟩ := ih (step + 1) _ _ _ h2 h3
      refine ⟨i1, by simp [i2], ?_⟩
      intro n
      cases n with
      | zero => simpa using hf
      | succ n =>
        simp only [List.take_succ_cons, List.flatten_cons, ← List.append_assoc]
        exact i3 n
    · simp only [ite_true, true_and]
      refine ⟨rfl, ?_⟩
      intro n
      cases n with
      | zero => simpa using hf
      | succ n => simpa using h2

/-- The sweep theorem. For every probe, every choice of which asks are grouped and which leave transcripts, and every list of tokens: the grouped sweep and the full sweep end at the same step with the same result, or both run out; they run the same number of steps; and after every step, the effects fired so far, each at its first occurrence, are the same list. -/
theorem sweep_theorem (p : Probe Tok Q A E R) (g : Grouping Tok) (tokens : List Tok) :
    (groupedSweep p g tokens).stop = (fullSweep p tokens).stop ∧
    (groupedSweep p g tokens).steps.length = (fullSweep p tokens).steps.length ∧
    ∀ n, firsts ((groupedSweep p g tokens).steps.take n).flatten =
      firsts ((fullSweep p tokens).steps.take n).flatten := by
  unfold groupedSweep fullSweep sweep
  cases hb : p.ends none (result p.oracle p.base p.eval)
  · obtain ⟨i1, i2, i3⟩ := loop_grouped p g (p.ends (some (result p.oracle p.base p.eval))) tokens
      1 [] (fired p.oracle p.base p.eval) (fired p.oracle p.base p.eval) rfl (by simp)
    refine ⟨by simpa using i1, by simpa using i2, ?_⟩
    intro n
    cases n with
    | zero => simp
    | succ n => simpa using i3 n
  · exact ⟨rfl, rfl, fun _ => rfl⟩

/-- After every step, the grouped sweep and the full sweep have fired the same set of effects. -/
theorem sweep_fires_the_same_set (p : Probe Tok Q A E R) (g : Grouping Tok) (tokens : List Tok)
    (n : Nat) (e : E) :
    e ∈ ((groupedSweep p g tokens).steps.take n).flatten ↔
      e ∈ ((fullSweep p tokens).steps.take n).flatten := by
  rw [← mem_firsts, (sweep_theorem p g tokens).2.2 n, mem_firsts]

end GroupedSweeps
