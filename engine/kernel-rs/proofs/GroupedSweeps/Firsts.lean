import GroupedSweeps.Model

/-!
Facts about `firsts`, the first occurrence of each element of a list.
-/

namespace GroupedSweeps

variable {E : Type} [DecidableEq E]

/-- An element survives `firstsAfter seen` exactly when it is in the list and not in `seen`. -/
theorem mem_firstsAfter (seen l : List E) (e : E) :
    e ∈ firstsAfter seen l ↔ e ∈ l ∧ e ∉ seen := by
  induction l generalizing seen with
  | nil => simp [firstsAfter]
  | cons x rest ih =>
    by_cases hx : x ∈ seen
    · simp only [firstsAfter, hx, ↓reduceIte, ih]
      grind
    · simp only [firstsAfter, hx, ↓reduceIte, List.mem_cons, ih]
      grind

/-- `firstsAfter` reads `seen` only through membership. -/
theorem firstsAfter_congr {seen seen' : List E} (h : ∀ e, e ∈ seen ↔ e ∈ seen') (l : List E) :
    firstsAfter seen l = firstsAfter seen' l := by
  induction l generalizing seen seen' with
  | nil => rfl
  | cons x rest ih =>
    by_cases hx : x ∈ seen
    · have hx' : x ∈ seen' := (h x).1 hx
      simp only [firstsAfter, hx, hx', ↓reduceIte]
      exact ih h
    · have hx' : x ∉ seen' := fun h' => hx ((h x).2 h')
      simp only [firstsAfter, hx, hx', ↓reduceIte]
      exact congrArg (x :: ·) (ih (by simp [h]))

/-- `firstsAfter` over an append splits into the first part and the rest after everything seen in it. -/
theorem firstsAfter_append (seen xs ys : List E) :
    firstsAfter seen (xs ++ ys) = firstsAfter seen xs ++ firstsAfter (xs ++ seen) ys := by
  induction xs generalizing seen with
  | nil => rfl
  | cons x rest ih =>
    by_cases hx : x ∈ seen
    · simp only [List.cons_append, firstsAfter, hx, ↓reduceIte, ih]
      exact congrArg (_ ++ ·) (firstsAfter_congr (by intro e; simp; grind) ys)
    · simp only [List.cons_append, firstsAfter, hx, ↓reduceIte, ih]
      exact congrArg (x :: _ ++ ·) (firstsAfter_congr (by intro e; simp; grind) ys)

/-- A prefix of already-seen elements adds nothing to `firstsAfter`. -/
theorem firstsAfter_absorb (seen zs ys : List E) (h : ∀ z ∈ zs, z ∈ seen) :
    firstsAfter seen (zs ++ ys) = firstsAfter seen ys := by
  induction zs with
  | nil => rfl
  | cons z rest ih =>
    simp only [List.cons_append, firstsAfter, h z (List.mem_cons_self ..), ↓reduceIte]
    exact ih (fun w hw => h w (List.mem_cons_of_mem _ hw))

/-- Dropping repeats keeps every element. -/
theorem mem_firsts (l : List E) (e : E) : e ∈ firsts l ↔ e ∈ l := by
  simp [firsts, mem_firstsAfter]

/-- What follows a list reads that list only through its first occurrences. -/
theorem firsts_append_congr {xs xs' : List E} (ys : List E) (h : firsts xs = firsts xs') :
    firsts (xs ++ ys) = firsts (xs' ++ ys) := by
  have hmem : ∀ e, e ∈ xs ++ [] ↔ e ∈ xs' ++ [] := by
    intro e
    rw [List.append_nil, List.append_nil, ← mem_firsts xs, ← mem_firsts xs', h]
  unfold firsts at h ⊢
  rw [firstsAfter_append [] xs ys, firstsAfter_append [] xs' ys, h, firstsAfter_congr hmem ys]

/-- Elements already seen add no first occurrence, wherever they are repeated. -/
theorem firsts_absorb (xs zs ys : List E) (h : ∀ z ∈ zs, z ∈ xs) :
    firsts (xs ++ zs ++ ys) = firsts (xs ++ ys) := by
  unfold firsts
  rw [List.append_assoc, firstsAfter_append [] xs (zs ++ ys), firstsAfter_append [] xs ys,
    firstsAfter_absorb _ _ _ (by simpa using h)]

end GroupedSweeps
