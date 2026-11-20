import GroupedSweeps.Model
import GroupedSweeps.Firsts
import GroupedSweeps.Path

/-!
What trying kept transcripts on a token comes to, measured against the token's full ask.
-/

namespace GroupedSweeps

variable {Tok Q A E R : Type} [DecidableEq A] [DecidableEq E]

omit [DecidableEq E] in
/-- A token matches a transcript exactly when it gives every question the transcript's answer. -/
theorem answerAll_matched_iff (o : Oracle Tok Q A E) (t : Tok) (qas : List (Q × A)) :
    (answerAll o t qas).matched = true ↔ ∀ qa ∈ qas, o.answer t qa.1 = qa.2 := by
  induction qas with
  | nil => simp [answerAll]
  | cons qa rest ih =>
    obtain ⟨q, a⟩ := qa
    by_cases hq : o.answer t q = a
    · simp [answerAll, hq, ih]
    · simp [answerAll, hq]

/-- When a token matches an unpeeking ask's transcript, its questions fire the first occurrences that token's full ask fires after `seen`, given `seen` holds that ask's effects outside its questions. -/
theorem firsts_walk_matched (o : Oracle Tok Q A E) (t t₁ : Tok) (p : Eval Tok Q A E R)
    (seen : List E) (unpeeked : peeks o t₁ p = false) (hout : ∀ e ∈ outside o t₁ p, e ∈ seen)
    (hm : (answerAll o t (asked o t₁ p)).matched = true) :
    firsts (seen ++ (answerAll o t (asked o t₁ p)).fired) = firsts (seen ++ fired o t p) := by
  induction p generalizing seen with
  | done r => simp [asked, answerAll, fired]
  | emit e next ih =>
    have he : e ∈ seen := hout e (by simp [outside])
    have hnext := ih seen unpeeked (fun x hx => hout x (by simp [outside, hx])) hm
    have hab := firsts_absorb seen [e] (fired o t next) (by simp [he])
    simp only [asked, fired] at hnext ⊢
    simp only [List.append_assoc, List.singleton_append] at hab
    rw [hnext, hab]
  | ask q next ih =>
    by_cases hq : o.answer t q = o.answer t₁ q
    · simp only [asked, answerAll, hq, ite_true] at hm ⊢
      have hnext := ih (o.answer t₁ q) (seen ++ o.effects t q) unpeeked
        (fun x hx => List.mem_append_left _ (hout x hx)) hm
      simp only [fired, hq]
      simp only [List.append_assoc] at hnext
      exact hnext
    · simp [asked, answerAll, hq] at hm
  | peek next => simp [peeks] at unpeeked

/-- When a token fails to match an unpeeking ask's transcript, what its questions fired adds no first occurrence ahead of that token's full ask after `seen`, given `seen` holds that ask's effects outside its questions. -/
theorem firsts_walk_failed (o : Oracle Tok Q A E) (t t₁ : Tok) (p : Eval Tok Q A E R)
    (seen : List E) (unpeeked : peeks o t₁ p = false) (hout : ∀ e ∈ outside o t₁ p, e ∈ seen)
    (hm : (answerAll o t (asked o t₁ p)).matched = false) :
    firsts (seen ++ (answerAll o t (asked o t₁ p)).fired ++ fired o t p) =
      firsts (seen ++ fired o t p) := by
  induction p generalizing seen with
  | done r => simp [asked, answerAll] at hm
  | emit e next ih =>
    have he : e ∈ seen := hout e (by simp [outside])
    have hnext := ih seen unpeeked (fun x hx => hout x (by simp [outside, hx])) hm
    have hab₁ := firsts_absorb (seen ++ (answerAll o t (asked o t₁ next)).fired) [e]
      (fired o t next) (by simp [he])
    have hab₂ := firsts_absorb seen [e] (fired o t next) (by simp [he])
    simp only [asked, fired] at hnext ⊢
    simp only [List.append_assoc, List.singleton_append] at hab₁ hab₂ hnext ⊢
    rw [hab₁, hnext, hab₂]
  | ask q next ih =>
    by_cases hq : o.answer t q = o.answer t₁ q
    · simp only [asked, answerAll, hq, ite_true] at hm ⊢
      have hnext := ih (o.answer t₁ q) (seen ++ o.effects t q) unpeeked
        (fun x hx => List.mem_append_left _ (hout x hx)) hm
      have hab := firsts_absorb
        (seen ++ o.effects t q ++ (answerAll o t (asked o t₁ (next (o.answer t₁ q)))).fired)
        (o.effects t q) (fired o t (next (o.answer t₁ q))) (fun z hz => by simp [hz])
      simp only [fired, hq]
      simp only [List.append_assoc] at hab hnext ⊢
      rw [hab, hnext]
    · have hab := firsts_absorb (seen ++ o.effects t q) (o.effects t q)
        (fired o t (next (o.answer t q))) (fun z hz => by simp [hz])
      simp only [asked, answerAll, hq, ite_false, fired]
      simp only [List.append_assoc] at hab ⊢
      exact hab
  | peek next => simp [peeks] at unpeeked

omit [DecidableEq A] [DecidableEq E] in
/-- A kept transcript standing after `seen` still stands after `seen` grows. -/
theorem Standing.append (o : Oracle Tok Q A E) (p : Eval Tok Q A E R) {seen : List E}
    (es : List E) {k : Kept Q A R} (h : Standing o p seen k) : Standing o p (seen ++ es) k := by
  obtain ⟨t₁, ha, hr, hp, hout⟩ := h
  exact ⟨t₁, ha, hr, hp, fun e he => List.mem_append_left _ (hout e he)⟩

/-- When a token answers a kept transcript alike, the transcript's result is the result of the token's full ask, and after `seen` the questions fire the first occurrences the full ask fires, in the same order. -/
theorem answerFirst_some (o : Oracle Tok Q A E) (p : Eval Tok Q A E R) (t : Tok)
    (kept : List (Kept Q A R)) (seen : List E) (r : R)
    (standing : ∀ k ∈ kept, Standing o p seen k)
    (h : (answerFirst o t kept).answer = some r) :
    r = result o t p ∧
    firsts (seen ++ (answerFirst o t kept).fired) = firsts (seen ++ fired o t p) := by
  induction kept generalizing seen with
  | nil => simp [answerFirst] at h
  | cons k rest ih =>
    obtain ⟨t₁, hask, hres, hpk, hout⟩ := standing k (by simp)
    by_cases hk : (answerAll o t k.asked).matched = true
    · simp only [answerFirst, hk, ite_true, Option.some.injEq] at h ⊢
      have alike := (answerAll_matched_iff o t k.asked).1 hk
      rw [hask] at alike hk ⊢
      refine ⟨?_, firsts_walk_matched o t t₁ p seen hpk hout hk⟩
      rw [← h, hres, (path_lemma o p t₁ t hpk alike).2.1]
    · have hk' : (answerAll o t k.asked).matched = false := by simpa using hk
      simp only [answerFirst, hk', Bool.false_eq_true, ite_false] at h ⊢
      have hrest := ih (seen ++ (answerAll o t k.asked).fired)
        (fun k' hk'' => (standing k' (List.mem_cons_of_mem _ hk'')).append o p _) h
      refine ⟨hrest.1, ?_⟩
      rw [← List.append_assoc, hrest.2]
      rw [hask] at hk' ⊢
      exact firsts_walk_failed o t t₁ p seen hpk hout hk'

/-- When a token answers no kept transcript alike, the questions put to it before its full ask add no first occurrence the full ask would not fire, and move none. -/
theorem answerFirst_none (o : Oracle Tok Q A E) (p : Eval Tok Q A E R) (t : Tok)
    (kept : List (Kept Q A R)) (seen : List E)
    (standing : ∀ k ∈ kept, Standing o p seen k)
    (h : (answerFirst o t kept).answer = none) :
    firsts (seen ++ (answerFirst o t kept).fired ++ fired o t p) = firsts (seen ++ fired o t p) := by
  induction kept generalizing seen with
  | nil => simp [answerFirst]
  | cons k rest ih =>
    obtain ⟨t₁, hask, -, hpk, hout⟩ := standing k (by simp)
    by_cases hk : (answerAll o t k.asked).matched = true
    · simp [answerFirst, hk] at h
    · have hk' : (answerAll o t k.asked).matched = false := by simpa using hk
      simp only [answerFirst, hk', Bool.false_eq_true, ite_false] at h ⊢
      have hrest := ih (seen ++ (answerAll o t k.asked).fired)
        (fun k' hk'' => (standing k' (List.mem_cons_of_mem _ hk'')).append o p _) h
      rw [← List.append_assoc, hrest]
      rw [hask] at hk' ⊢
      exact firsts_walk_failed o t t₁ p seen hpk hout hk'

end GroupedSweeps
