/-
Print the goal state before and after every tactic in a range of lines of a Lean file.

    lake env lean --run GoalStates.lean <file.lean> <firstLine> <lastLine>

The file is elaborated with info trees enabled (the same data the editor's infoview shows),
and every `TacticInfo` node whose syntax starts inside [firstLine, lastLine] is printed as one
JSON object per line:

    {"line": 12, "col": 4, "endLine": 13, "endCol": 9, "kind": "Lean.Parser.Tactic.tacticHave_",
     "before": ["x : ℕ\n⊢ ..."], "after": ["..."]}

Lines and columns are 1-based / 0-based as in Lean's messages. Used by `lpe/goals.py`.
-/
import Lean

open Lean Elab

def goalStrings (ctx : ContextInfo) (mctx : MetavarContext) (goals : List MVarId) : IO (List String) :=
  goals.mapM fun g => do
    let fmt ← ({ ctx with mctx } : ContextInfo).runMetaM {} (Meta.ppGoal g)
    return toString fmt

partial def visit (fileMap : FileMap) (first last : Nat) (out : IO.Ref (Array Json)) :
    Option ContextInfo → InfoTree → IO Unit
  | ctx?, .context pci t => visit fileMap first last out (pci.mergeIntoOuter? ctx?) t
  | ctx?, .node info children => do
    let ctx? := info.updateContext? ctx?
    if let (some ctx, .ofTacticInfo ti) := (ctx?, info) then
      if let (some pos, some endPos) := (ti.stx.getPos? (canonicalOnly := true), ti.stx.getTailPos? (canonicalOnly := true)) then
        let p := fileMap.toPosition pos
        let e := fileMap.toPosition endPos
        if first ≤ p.line && p.line ≤ last then
          let before ← goalStrings ctx ti.mctxBefore ti.goalsBefore
          let after ← goalStrings ctx ti.mctxAfter ti.goalsAfter
          out.modify (·.push <| Json.mkObj [
            ("line", p.line), ("col", p.column), ("endLine", e.line), ("endCol", e.column),
            ("kind", toString ti.stx.getKind), ("before", toJson before), ("after", toJson after)])
    for c in children do
      visit fileMap first last out ctx? c
  | _, .hole _ => pure ()

unsafe def main (args : List String) : IO UInt32 := do
  let [path, firstS, lastS] := args
    | IO.eprintln "usage: GoalStates <file.lean> <firstLine> <lastLine>"; return 2
  let some first := firstS.toNat? | IO.eprintln "bad firstLine"; return 2
  let some last := lastS.toNat? | IO.eprintln "bad lastLine"; return 2
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
  let out ← IO.mkRef #[]
  for t in s.commandState.infoState.trees do
    visit inputCtx.fileMap first last out none t
  for j in (← out.get) do
    IO.println j.compress
  return 0
