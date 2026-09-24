# Methodology

## 1. Task design

Tasks are taken from the real workload, phrased the way the work actually
arrives. The value of a battery is in its hard cases, so the cheap cases are
included only to separate a broken setup from a weak model — the score that
matters is on the hard ones.

Rules applied to every task:

- **The input must contain everything the grader requires.** A task that demands
  a value it never supplied scores zero forever and reads as the model being
  weak. (Found in practice: a voice-note transcript with no year, graded for a
  specific date.)
- **One defensible answer.** If two readings of the wording give two different
  correct answers, the task is rewritten until only one does. (Found in practice:
  "how many crew changes" in a 28/28 rotation for a two-person team.)
- **Negation and restatement awareness.** A model that correctly says "the
  document does not state X" is right, and must not score as if it asserted X.
- **Forbid the behaviour, not the character.** Never blacklist a token a correct
  answer must contain, and never forbid terminology that a correct answer needs
  in order to *describe a requirement*. (Found in practice: forbidding the words
  `ISO 9712` failed a model that correctly described the posting as demanding it.)
- **Grade by execution where possible.** Codegen tasks are run: the script is
  written to a throwaway directory with the task's fixtures, executed, and its
  artifacts are compared. Prose that looks like a solution scores nothing.
- **Structural over textual.** Provenance answers are parsed as CSV and JSON and
  the fields compared, because substring matching is defeated by coincidence —
  requiring the bare string `2` passes on `2026`.

## 2. Trials, temperature, and honesty about variance

Every task runs multiple trials across a temperature ladder (0.2 / 0.7 / 1.0 by
default) and the reported figure is a pass rate, never a single run.

One suite pass is one sample. Re-running the same model over the same fixtures
moves individual class verdicts, so:

- raw replies are persisted per trial, with usage and timing
- a verdict can be re-graded offline without a GPU
- results are published with their variance, not as fixed capability

## 3. Output budget and the empty reply

A reasoning model emits its thinking **first**, and the thinking is billed as
output. With too small a budget the reply arrives **empty** with
`finish_reason=length`.

That is a harness error, not a low score. The runner therefore:

- floors the output budget at 3000 tokens
- on an empty-and-truncated reply, retries at 4x the budget, up to 32768
- records the escalation and the budget actually used on the trial

Only an escalation that still returns nothing is treated as a failure, and the
raw record says so.

## 4. Cost accounting

Computed from actual per-call usage, never from a price list plus an assumed
reply length:

- median completion tokens per call, and per **correct** answer
- reasoning tokens, which on a reasoning model dominate the bill
- median, p90 and max wall time, plus wall time per **correct** answer

The tail matters: a fine median with a 20 second tail is experienced as "broken".

## 5. The threat model of a self-authored bench

The same author writes the tasks, the graders and the weights. Every grader bug
found in a run was failing a model unfairly, and the failures were invisible
without reading the raw replies. So:

- graders are validated offline against hand-written replies with known verdicts
  (`bench/selftest.py`), including the classic false-failure traps
- the task configurations are audited mechanically for unsatisfiable
  requirements and for values that are both expected and forbidden
- a smoke run against the incumbent model is done before any serious run — a
  harness bug discovered after the swap costs the whole night
- the grader-correction history is published in the README rather than quietly
  cleaned up

## 6. What is deliberately not measured

- vision, voice, and any non-text modality
- long unattended multi-step autonomy (single-turn only here)
- cost in money (tokens are measured; a token's price is a separate decision)
- throughput under concurrent load (this rig measures a single stream, which is
  what a personal agent experiences)
