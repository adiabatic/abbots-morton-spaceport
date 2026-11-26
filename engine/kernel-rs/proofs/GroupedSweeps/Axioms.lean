import GroupedSweeps.Path
import GroupedSweeps.Sweep

/-!
The axioms the theorems rest on. Each check fails the build when the list Lean reports differs from the one written here, so a proof that comes to rest on anything else, an unfinished proof among them, does not pass.
-/

/-- info: 'GroupedSweeps.path_lemma' depends on axioms: [propext] -/
#guard_msgs in
#print axioms GroupedSweeps.path_lemma

/-- info: 'GroupedSweeps.mem_fired' depends on axioms: [propext, Quot.sound] -/
#guard_msgs in
#print axioms GroupedSweeps.mem_fired

/-- info: 'GroupedSweeps.sweep_theorem' depends on axioms: [propext, Classical.choice, Quot.sound] -/
#guard_msgs in
#print axioms GroupedSweeps.sweep_theorem

/-- info: 'GroupedSweeps.sweep_fires_the_same_set' depends on axioms: [propext, Classical.choice, Quot.sound] -/
#guard_msgs in
#print axioms GroupedSweeps.sweep_fires_the_same_set
