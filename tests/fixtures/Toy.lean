/-
A tiny core-Lean file used by the tests, so the pipeline can be exercised without Mathlib.
-/

namespace Toy

variable {a b : Nat}

/-- Addition is commutative (proved by induction, to give the tree some depth). -/
theorem add_comm' (m n : Nat) : m + n = n + m := by
  induction n with
  | zero => simp
  | succ k ih =>
    have h : m + (k + 1) = (m + k) + 1 := by
      rfl
    rw [h, ih]
    omega

theorem le_add_of_le (h : a ≤ b) (c : Nat) : a ≤ b + c := by
  have hb : b ≤ b + c := Nat.le_add_right b c
  exact Nat.le_trans h hb

theorem term_mode (n : Nat) : n = n := rfl

end Toy
