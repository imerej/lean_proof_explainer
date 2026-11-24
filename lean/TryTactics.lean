/-
Try alternative tactics on the goals a proof passes through.

    lake env lean --run TryTactics.lean <file.lean> <probes.json>

`probes.json` is `{"positions": [[line, col], ...], "tactics": ["omega", "simp", ...],
"maxHeartbeats": 50000}` (lines 1-based, columns 0-based, as in Lean's messages). The file is
elaborated with info trees enabled; for every tactic that starts at one of the positions, each
alternative is run on the first goal *before* that tactic, in a fresh copy of the proof state.
One JSON object per (position, tactic) is printed:

    {"line": 12, "col": 4, "tactic": "omega", "goal": "x : ℕ\n⊢ ...",
     "status": "closed" | "progress" | "unchanged" | "failed" | "unavailable",
     "goals": ["..."], "error": "..."}

`progress` means the tactic succeeded but left goals (listed in `goals`); `unchanged` means it
succeeded without changing the goal; `unavailable` means the tactic does not parse in this file
(typically a Mathlib tactic the file does not import, which a proof in this file cannot use either).
A result that only "succeeds" through error recovery
(an elaboration error, or a proof term containing `sorry`) counts as `failed`.
Several info-tree nodes can start at the same position (a tactic and its wrapper syntax, or a
case alternative and the tactic it encloses) and their goals may differ; every distinct goal at a
position is probed once, and the caller picks the one it wants by its text. Used by `lpe/contrast.py`.
-/
import Lean

open Lean Elab Meta

structure Probe where
  positions : List (Nat × Nat)
  tactics : List String
  maxHeartbeats : Option Nat := none  -- in thousands, as the `maxHeartbeats` option; default 50000
  deriving FromJson

def ppGoals (goals : List MVarId) : MetaM (List String) :=
  goals.mapM fun g => return toString (← ppGoal g)

/-- Run `tac` on `goal`; returns (status, remaining goals, error). -/
def tryOne (goal : MVarId) (tac : String) (heartbeats : Nat) : MetaM (String × List String × String) := do
  match Parser.runParserCategory (← getEnv) `tactic tac with
  | .error e => return ("unavailable", [], s!"parse error: {e}")
  | .ok stx =>
    let before ← ppGoal goal
    let msgs ← Core.getMessageLog
    Core.resetMessageLog
    let res ← withTheReader Core.Context (fun c => { c with maxHeartbeats := heartbeats * 1000 }) <|
      withCurrHeartbeats <| tryCatchRuntimeEx
        (do
          let gs ← (Term.withoutErrToSorry <| Tactic.run goal (Tactic.evalTactic stx)).run'
          let log ← Core.getMessageLog
          let proof ← instantiateMVars (mkMVar goal)
          if log.hasErrors then
            let errs ← log.toList.filter (·.severity == .error) |>.mapM (·.data.toString)
            return Except.error (String.intercalate "\n" errs)
          if proof.hasSorry then
            return Except.error "the result contains `sorry`"
          return Except.ok gs)
        (fun e => return Except.error (← e.toMessageData.toString))
    Core.setMessageLog msgs
    match res with
    | .error e => return ("failed", [], e)
    | .ok [] => return ("closed", [], "")
    | .ok gs =>
      let after ← ppGoals gs
      if after == [toString before] then return ("unchanged", after, "")
      return ("progress", after, "")

partial def collect (fileMap : FileMap) (wanted : List (Nat × Nat))
    (out : IO.Ref (Array (Nat × Nat × ContextInfo × TacticInfo))) :
    Option ContextInfo → InfoTree → IO Unit
  | ctx?, .context pci t => collect fileMap wanted out (pci.mergeIntoOuter? ctx?) t
  | ctx?, .node info children => do
    let ctx? := info.updateContext? ctx?
    if let (some ctx, .ofTacticInfo ti) := (ctx?, info) then
      if let some pos := ti.stx.getPos? (canonicalOnly := true) then
        let p := fileMap.toPosition pos
        if wanted.contains (p.line, p.column) && !ti.goalsBefore.isEmpty then
          out.modify (·.push (p.line, p.column, ctx, ti))
    for c in children do
      collect fileMap wanted out ctx? c
  | _, .hole _ => pure ()

unsafe def main (args : List String) : IO UInt32 := do
  let [path, probePath] := args
    | IO.eprintln "usage: TryTactics <file.lean> <probes.json>"; return 2
  let probe ← match Json.parse (← IO.FS.readFile probePath) >>= fromJson? with
    | .ok (p : Probe) => pure p
    | .error e => IO.eprintln s!"bad probes file: {e}"; return 2
  initSearchPath (← findSysroot)
  enableInitializersExecution
  let input ← IO.FS.readFile path
  let inputCtx := Parser.mkInputContext input path
  let (header, parserState, messages) ← Parser.parseHeader inputCtx
  let (env, messages) ← processHeader header {} messages inputCtx
  let commandState := { Command.mkState env messages {} with infoState := { enabled := true } }
  let s ← IO.processCommands inputCtx parserState commandState
  for msg in s.commandState.messages.toList do
    if msg.severity == .error then
      IO.eprintln (← msg.toString (includeEndPos := true))
  let found ← IO.mkRef #[]
  for t in s.commandState.infoState.trees do
    collect inputCtx.fileMap probe.positions found none t
  let mut seen : Std.HashSet (Nat × Nat × String) := {}
  for (line, col, ctx, ti) in (← found.get) do
    let goal := ti.goalsBefore.head!
    let ctx : ContextInfo := { ctx with mctx := ti.mctxBefore }
    let shown ← ctx.runMetaM {} (return toString (← ppGoal goal))
    if seen.contains (line, col, shown) then continue
    seen := seen.insert (line, col, shown)
    for tac in probe.tactics do
      let (status, goals, err) ← ctx.runMetaM {} (tryOne goal tac (probe.maxHeartbeats.getD 50000))
      IO.println <| Json.compress <| Json.mkObj [
        ("line", line), ("col", col), ("tactic", tac), ("goal", shown),
        ("status", status), ("goals", toJson goals), ("error", err)]
  return 0
