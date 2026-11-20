import GroupedSweeps.Model

/-!
The path lemma: two tokens that answer an ask's questions alike take the same path through it.
-/

namespace GroupedSweeps

variable {Tok Q A E R : Type}

/-- The path lemma. If the ask for `t₁` never reads its token directly, and `t₂` gives every question that ask puts the answer `t₁` gave, then the ask for `t₂` puts the same questions and reads the same answers, returns the same result, fires the same effects outside its questions in the same order, and never reads its token directly either. -/
theorem path_lemma (o : Oracle Tok Q A E) (p : Eval Tok Q A E R) (t₁ t₂ : Tok)
    (unpeeked : peeks o t₁ p = false)
    (alike : ∀ qa ∈ asked o t₁ p, o.answer t₂ qa.1 = qa.2) :
    asked o t₂ p = asked o t₁ p ∧
    result o t₂ p = result o t₁ p ∧
    outside o t₂ p = outside o t₁ p ∧
    peeks o t₂ p = false := by
  induction p with
  | done r => simp [asked, result, outside, peeks]
  | emit e next ih =>
    simp only [asked, result, outside, peeks] at *
    obtain ⟨h1, h2, h3, h4⟩ := ih unpeeked alike
    exact ⟨h1, h2, by rw [h3], h4⟩
  | ask q next ih =>
    simp only [asked, result, outside, peeks] at *
    have hq : o.answer t₂ q = o.answer t₁ q := alike (q, o.answer t₁ q) (List.mem_cons_self ..)
    rw [hq]
    obtain ⟨h1, h2, h3, h4⟩ :=
      ih (o.answer t₁ q) unpeeked (fun qa hqa => alike qa (List.mem_cons_of_mem _ hqa))
    exact ⟨by rw [h1], h2, h3, h4⟩
  | peek next ih =>
    simp [peeks] at unpeeked

/-- An ask fires exactly its effects outside its questions and the effects of the questions it puts. -/
theorem mem_fired (o : Oracle Tok Q A E) (p : Eval Tok Q A E R) (t : Tok) (e : E) :
    e ∈ fired o t p ↔ e ∈ outside o t p ∨ ∃ qa ∈ asked o t p, e ∈ o.effects t qa.1 := by
  induction p with
  | done r => simp [fired, outside, asked]
  | emit e' next ih =>
    simp only [fired, outside, asked, List.mem_cons]
    rw [ih]
    exact or_assoc.symm
  | ask q next ih =>
    simp only [fired, outside, asked, List.mem_cons, List.mem_append]
    rw [ih]
    constructor
    · rintro (h | h | ⟨qa, hqa, he⟩)
      · exact Or.inr ⟨_, Or.inl rfl, h⟩
      · exact Or.inl h
      · exact Or.inr ⟨qa, Or.inr hqa, he⟩
    · rintro (h | ⟨qa, (rfl | hqa), he⟩)
      · exact Or.inr (Or.inl h)
      · exact Or.inl he
      · exact Or.inr (Or.inr ⟨qa, hqa, he⟩)
  | peek next ih =>
    simp only [fired, outside, asked]
    exact ih t

/-- An ask fires its effects outside its questions. -/
theorem outside_subset_fired (o : Oracle Tok Q A E) (p : Eval Tok Q A E R) (t : Tok) :
    ∀ e ∈ outside o t p, e ∈ fired o t p := by
  intro e he
  exact (mem_fired o p t e).2 (Or.inl he)

end GroupedSweeps
