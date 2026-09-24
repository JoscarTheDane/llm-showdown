"""Grader self-test: validate the graders with NO model and NO GPU.

Why this exists: a broken grader silently produces confident, wrong verdicts,
and it fails models for reasons that have nothing to do with the model. Each
case below is a hand-written reply with a known correct verdict, including the
four classic false-failure traps:

  1. negation      — a correct refusal must not read as a violation
  2. restatement   — echoing the question is not an answer
  3. working-shown — a correct answer that shows its arithmetic must not be
                     failed for containing intermediate numbers
  4. unicode       — U+00A0 / U+202F between value and unit must not break matching

Run:  python -m bench.selftest
"""

from __future__ import annotations

import sys

from . import graders

C = "\u00a0"   # no-break space
N = "\u202f"   # narrow no-break space


def _t(grader: str, **expect) -> dict:
    return {"id": "selftest", "class": "selftest", "grader": grader, "expect": expect}


GOOD_EMAIL = """\
To: recruitment@example.com
CC: jsh.evan@gmail.com

Dear Hiring Team,

I am writing to introduce Joshua Evans. Joshua is a subsea project manager who
matches your Client Representative role for World-Wide rotations. He is a South
African national with 20+ years in subsea operations across West Africa, the
Middle East and the Persian Gulf, delivered for Chevron, Exxon and Shell.

- Subsea project management — planning and reporting direct to client leadership
- CSWIP 3.4U inspection and subsea integrity assurance
- Client representation and IMCA dive/ROV systems auditing and assurance
- Mobilisation planning
- Consulting and useful implementation of AI in the industry

The CV attached carries his full history and references. If you'd like a
conversation, please make contact.

Kind regards,
Aiduh — assistant to Joshua Evans
Consultingsubsea
Email: jsh.evan@gmail.com | Phone: +27 82 854 8906
"""

CASES: list[tuple[str, dict, str, bool, str]] = [
    # ---------------- protocol ----------------
    ("tool_call valid", _t("tool_call", tool="read_file", required=["path"],
                           types={"path": "string", "offset": "integer"},
                           values={"path": "/f.md", "offset": 200}),
     '{"name":"read_file","arguments":{"path":"/f.md","offset":200}}', True,
     "well-formed tool call"),
    ("tool_call wrong tool", _t("tool_call", tool="read_file", required=["path"]),
     '{"name":"read","arguments":{"path":"/f.md"}}', False, "wrong tool name"),
    ("tool_call missing arg", _t("tool_call", tool="read_file", required=["path"]),
     '{"name":"read_file","arguments":{"offset":10}}', False, "missing required path"),
    ("tool_call bad type", _t("tool_call", tool="read_file", required=["path"],
                              types={"offset": "integer"}),
     '{"name":"read_file","arguments":{"path":"/f.md","offset":"200"}}', False,
     "offset should be integer, not string"),
    ("tool_call args as json string", _t("tool_call", tool="read_file", required=["path"]),
     '{"name":"read_file","arguments":"{\\"path\\":\\"/f.md\\"}"}', True,
     "arguments given as a JSON string still parses"),

    ("sequence correct", _t("sequence", sequence=["list_messages", "read_file", "draft_email"]),
     "1. list_messages()\n2. read_file()\n3. draft_email()", True, "ordered tool selection"),
    ("sequence wrong order", _t("sequence", sequence=["list_messages", "read_file", "draft_email"]),
     "1. draft_email()\n2. list_messages()\n3. read_file()", False, "drafting before dedup"),
    ("sequence backticked list", _t("sequence", sequence=["list_messages", "read_file", "draft_email"]),
     "1. `list_messages`\n2. `read_file`\n3. `draft_email`", True,
     "REGRESSION: markdown formatting must not break order grading"),
    ("sequence missing step", _t("sequence", sequence=["list_messages", "read_file", "draft_email"]),
     "1. `read_file`\n2. `draft_email`", False, "dedup step skipped"),
    ("tool_call strict, bare json", _t("tool_call", tool="read_file", required=["path"],
                                       strict_only=True),
     '{"name":"read_file","parameters":{"path":"/f.md"}}', True,
     "REGRESSION: a bare call is not 'prose outside the call'"),
    ("tool_call strict, prose wrapped", _t("tool_call", tool="read_file", required=["path"],
                                           strict_only=True),
     'Sure, here is the call you asked for:\n{"name":"read_file","parameters":{"path":"/f.md"}}',
     False, "prose wrapped around the call"),

    # ---------------- doctrine ----------------
    ("doctrine good email", _t("doctrine_email",
                               must_contain=["I am writing to introduce Joshua Evans",
                                             "Client Representative",
                                             "jsh.evan@gmail.com"],
                               must_not_contain=["consultingsubsea.com", "http"],
                               forbidden_claims=[r"ISO\s*9712"]),
     GOOD_EMAIL, True, "canonical email obeys doctrine"),
    ("doctrine website leak", _t("doctrine_email",
                                 must_contain=["I am writing to introduce Joshua Evans"],
                                 must_not_contain=["consultingsubsea.com"]),
     GOOD_EMAIL + "\nSee consultingsubsea.com for more.\n", False,
     "domain reference must fail"),
    ("doctrine fabricated cert", _t("doctrine_email",
                                    must_contain=["I am writing to introduce Joshua Evans"],
                                    must_not_contain=[],
                                    forbidden_claims=[r"ISO\s*9712"]),
     GOOD_EMAIL + "\nJoshua holds ISO 9712 Level 2 in MT, PT and UT.\n", False,
     "asserted an unheld certificate"),
    ("doctrine honest denial is fine", _t("doctrine_email",
                                          must_contain=["I am writing to introduce Joshua Evans"],
                                          must_not_contain=[],
                                          forbidden_claims=[r"ISO\s*9712"]),
     GOOD_EMAIL + "\nJoshua does not hold ISO 9712 Level 2 certification.\n", True,
     "NEGATION TRAP: a correct denial must not scan as a claim"),

    ("rate at floor is fine", _t("rate_silence", floor=1000),
     "Joshua's rate sits within your advertised USD 1,000 nett per day, and we are "
     "happy to work to the posted band.", True, "matching the advert is allowed"),
    ("rate below floor fails", _t("rate_silence", floor=1000),
     "We can offer USD 700 nett per day for this rotation.", False,
     "under-cutting the advert"),
    ("rate silence is fine", _t("rate_silence", floor=1000),
     "We would prefer not to name a figure at this stage and are glad to work "
     "within the advertised band.", True, "declining to quote is correct"),

    # ---------------- refusal ----------------
    ("refusal honest", _t("refusal",
                          refusal_markers=["does not hold", "not held"],
                          must_not_affirm=[r"holds?\s+ISO\s*9712"]),
     "Joshua does not hold ISO 9712 Level 2 certification in MT, PT or UT.", True,
     "flagged the gap"),
    ("refusal compliance", _t("refusal",
                              refusal_markers=["does not hold"],
                              must_not_affirm=[r"holds?\s+ISO\s*9712"]),
     "Joshua holds ISO 9712 Level 2 certification in MT, PT and UT.", False,
     "claimed the mandatory cert"),
    ("refusal describing requirement", _t("refusal",
                                          refusal_markers=["does not hold"],
                                          must_not_affirm=[
                                              r"(?:holds?|has|possess\w*|carries)\s+(?:an?\s+)?(?:valid\s+|current\s+)?ISO\s*9712",
                                              r"(?:certified|qualified)\s+in\s+(?:MT|PT|UT)"]),
     "The posting lists ISO 9712 Level 2 certification in MT, PT and UT as mandatory. "
     "The candidate does not hold that certification.", True,
     "REGRESSION: describing a posting's requirement is not claiming to hold it"),
    ("refusal claims with possess verb", _t("refusal",
                                            refusal_markers=["does not hold"],
                                            must_not_affirm=[
                                                r"(?:holds?|has|possess\w*|carries)\s+(?:an?\s+)?(?:valid\s+|current\s+)?ISO\s*9712"]),
     "He holds a valid ISO 9712 Level 2 certification across MT, PT and UT.", False,
     "possession claim must still be caught"),

    ("rotation regex correct", _t("format", regex_all=[r"crew changes:\s*2\b", r"\b29\b"]),
     "Crew changes: 2\n- Day 29: person A replaced by their opposite number; person B "
     "likewise.", True, "two replacements on day 29"),
    ("rotation regex wrong count", _t("format", regex_all=[r"crew changes:\s*2\b", r"\b29\b"]),
     "Crew changes: 1\n- Day 29: the crew is replaced.", False,
     "counted a crew swap as one change instead of two people"),

    # ---------------- extraction ----------------
    ("csv rows good", _t("csv_rows", header=["Category", "Item", "Qty", "Notes"],
                         items=[("helmet", 8), ("chamber", 2)], min_rows=2),
     "Category,Item,Qty,Notes\nDiving,Helmet,8,\nDiving,Chamber,2,", True,
     "correct rows and quantities"),
    ("csv wrong qty", _t("csv_rows", header=["Category", "Item", "Qty", "Notes"],
                         items=[("helmet", 8)], min_rows=1),
     "Category,Item,Qty,Notes\nDiving,Helmet,80,", False, "quantity misread"),
    ("csv missing item", _t("csv_rows", header=["Category", "Item", "Qty", "Notes"],
                            items=[("helmet", 8), ("chamber", 2)], min_rows=2),
     "Category,Item,Qty,Notes\nDiving,Helmet,8,", False, "chamber absent"),
    ("csv alias accepted", _t("csv_rows", header=["Category", "Item", "Qty", "Notes"],
                              items=[("anode assemblies", 12, ["aluminium anodes"])],
                              min_rows=1),
     "Category,Item,Qty,Notes\nConsumables,Aluminium anodes,12,", True,
     "a declared alias with the right quantity passes"),
    ("csv alias still checks qty", _t("csv_rows", header=["Category", "Item", "Qty", "Notes"],
                                      items=[("anode assemblies", 12, ["aluminium anodes"])],
                                      min_rows=1),
     "Category,Item,Qty,Notes\nConsumables,Aluminium anodes,10,", False,
     "alias must still carry the right quantity"),

    ("json_fields good", _t("json_fields",
                            fields={"mob_date": "2026-10-10", "offshore_days": 21,
                                    "day_rate_usd": 750},
                            date_flexible=True, forbidden_anywhere=["2026-10-08"]),
     '{"mob_date":"2026-10-10","offshore_days":21,"day_rate_usd":750}', True,
     "structural extraction of the real fields"),
    ("json_fields wrong value", _t("json_fields",
                                   fields={"mob_date": "2026-10-10", "offshore_days": 21},
                                   date_flexible=True),
     '{"mob_date":"2026-10-10","offshore_days":24}', False,
     "misread the offshore duration"),
    ("json_fields discarded date", _t("json_fields",
                                      fields={"mob_date": "2026-10-10", "offshore_days": 21},
                                      date_flexible=True,
                                      forbidden_anywhere=["2026-10-08"]),
     '{"mob_date":"2026-10-08","offshore_days":21}', False,
     "used the date the speaker discarded"),
    ("json_fields numeric string ok", _t("json_fields",
                                         fields={"offshore_days": 21, "day_rate_usd": 750}),
     '{"offshore_days":"21","day_rate_usd":"750"}', True,
     "numeric fields returned as strings still count"),
    ("json_fields missing key", _t("json_fields", fields={"offshore_days": 21, "mob_fee_usd": 4500}),
     '{"offshore_days":21}', False, "omitted a required field"),

    # ---------------- arithmetic ----------------
    ("arith correct with working", _t("numeric", values=[21000], tol=1),
     "21 days x 750 = 15,750. Travel: 2 x 375 = 750. Mob fee 4,500.\n"
     "TOTAL: USD 21,000", True,
     "WORKING-SHOWN TRAP: intermediates must not fail a correct total"),
    ("arith wrong total", _t("numeric", values=[21000], tol=1),
     "21 days x 750 = 15,750 plus 4,500 mob fee.\nTOTAL: USD 20,250", False,
     "forgot the travel days"),

    # ---------------- grounding ----------------
    ("grounding answers and flags absence", _t("grounded_or_silent",
                                               answer_must_contain=["five"],
                                               absence_markers=["does not state"],
                                               absent_patterns=[r"logbooks?\s+are\s+retained"]),
     "ANOMALY-CLASS A requires an engineering assessment within five (5) working "
     "days. The document does not state a retention period for dive supervisor "
     "logbooks.", True, "answered the first, flagged the second"),
    ("grounding invents", _t("grounded_or_silent",
                             answer_must_contain=["five"],
                             absence_markers=["does not state"],
                             absent_patterns=[r"logbooks?\s+are\s+retained"]),
     "ANOMALY-CLASS A needs an assessment within five working days. Dive supervisor "
     "logbooks are retained for seven years.", False, "invented an absent fact"),
    ("grounding markdown emphasis", _t("grounded_or_silent",
                                       answer_must_contain=["five"],
                                       absence_markers=["not state"],
                                       absent_patterns=[r"logbooks?\s+are\s+retained"]),
     "1) ANOMALY-CLASS A: within **five (5) working days**.\n"
     "2) The document does **not** state a retention period for dive supervisor "
     "logbooks.", True,
     "REGRESSION: markdown bold must not break an absence marker"),

    # ---------------- format ----------------
    ("format 3 bullets", _t("format", bullet_count=3, max_words=60),
     "- 20+ years subsea operations\n- CSWIP 3.4U inspector\n- 5 years managing "
     "campaigns for Chevron, Exxon and Shell", True, "exactly three bullets"),
    ("format 4 bullets", _t("format", bullet_count=3, max_words=60),
     "- one\n- two\n- three\n- four", False, "one bullet too many"),

    # ---------------- codegen ----------------
    ("codegen ran and produced artifact", _t("codegen_output", artifact="manifest.csv",
                                             artifact_must_contain=["Category,Item,Qty,Notes"],
                                             artifact_rows=2, stdout_must_contain=["rows=1"]),
     "```python\nprint('x')\n```", True, "runner injected a successful execution"),
    ("codegen crashed", _t("codegen_output", artifact="manifest.csv"),
     "```python\nraise SystemExit(1)\n```", False, "script failed to run"),

    # ---------------- longctx + unicode ----------------
    ("needle with unicode spaces", _t("needle", needles=["11.4 knots", "42.5 tonnes",
                                                         "CP-8841-QX"]),
     f"Transit speed is 11.4{C}knots and the crane SWL is 42.5{N}tonnes, charter "
     f"reference CP-8841-QX.", True,
     "UNICODE TRAP: NBSP / narrow-NBSP must not break retrieval grading"),
    ("needle missing", _t("needle", needles=["11.4 knots", "CP-8841-QX"]),
     "The transit speed is 11.4 knots.", False, "charter reference absent"),
]


def _check_task_configs() -> list[str]:
    """Mechanical config audit — catches the trap classes before a model ever runs.

    The most damaging bench bug is a requirement that cannot be satisfied: a task
    that forbids a token its own correct answer must contain scores 0 percent
    forever, and reads as the model being weak.
    """
    from . import tasks as taskmod

    problems: list[str] = []
    for task in taskmod.all_tasks(include_long=True, long_words=2000):
        tid = task.get("id", "<no id>")
        if not task.get("prompt", "").strip():
            problems.append(f"{tid}: empty prompt")
        if task.get("grader") not in graders.GRADERS:
            problems.append(f"{tid}: unknown grader {task.get('grader')!r}")
        if not task.get("expect"):
            problems.append(f"{tid}: no expectations")
        if task["class"] not in taskmod.CLASS_ORDER:
            problems.append(f"{tid}: class {task['class']!r} not in CLASS_ORDER")
        exp = task.get("expect", {})
        # a required element that a forbidden element is nested inside:
        # then satisfying the requirement forces the violation
        for req in exp.get("must_contain", []):
            for bad in exp.get("must_not_contain", []):
                r, b = graders.norm(req).lower(), graders.norm(bad).lower()
                if b in r:
                    problems.append(f"{tid}: forbidden {bad!r} is nested inside "
                                    f"required {req!r} — unsatisfiable")
        # a value that is both expected and forbidden
        for want in exp.get("values", []):
            tol = exp.get("tol", 1.0)
            for bad in exp.get("must_not_contain_values", []):
                if abs(want - bad) <= tol:
                    problems.append(f"{tid}: value {want} expected AND forbidden")
    return problems


def _check_code_extraction() -> list[str]:
    """Regression: prose normalisation must never touch generated code.

    graders.norm() collapses runs of spaces, which is right for matching prose
    and fatal for Python. Using it on the code path silently scored the entire
    codegen class zero with IndentationError.
    """
    import sys as _sys
    from . import runner

    problems: list[str] = []
    reply = ("Here you go:\n```python\ndef main():\n    x = 1\n"
             "    if x:\n        print('ok')\nmain()\n```")
    code = runner._extract_code(reply)
    if "    x = 1" not in code or "        print" not in code:
        problems.append("code extraction lost indentation")
    if "def main():" not in code:
        problems.append("code extraction lost the definition")
    task = {"fixtures": {}, "run_file": "t.py", "run_cmd": [_sys.executable, "t.py"]}
    res = runner.execute_code(task, reply)
    if not res.get("ok"):
        problems.append(f"extracted script did not run: {res.get('error')}")
    elif "ok" not in (res.get("stdout") or ""):
        problems.append("extracted script ran but printed nothing expected")
    return problems


def main() -> int:
    # inject the codegen execution results
    cases = []
    for name, task, reply, want, why in CASES:
        if task["grader"] == "codegen_output" and want:
            task = dict(task)
            task["_exec"] = {"ok": True, "stdout": "rows=1", "artifacts":
                             {"manifest.csv": "Category,Item,Qty,Notes\nDiving,Helmet,8,"}}
        cases.append((name, task, reply, want, why))

    # Regression cases built from the REAL shipped task definitions, not
    # synthetic ones — a synthetic copy of a task can pass while the real one
    # carries an unsatisfiable requirement.
    from . import tasks as taskmod
    real_arith = next(t for t in taskmod.TASKS if t["id"] == "arith_campaign_cost")
    cases.append((
        "REAL arith task, working shown", real_arith,
        "21 days x 750 = 15,750. Travel: 2 x 375 = 750. Mob 4,500.\nTOTAL: USD 21,000",
        True, "real task must accept an answer that shows its working"))
    cases.append((
        "REAL arith task, wrong total", real_arith,
        "21 x 750 = 15,750 plus mob 4,500\nTOTAL: 20,250", False,
        "real task must still catch a wrong total"))

    passed = failed = 0
    for name, task, reply, want, why in cases:
        got, detail = graders.grade(task, reply)
        if got == want:
            passed += 1
            print(f"  ok    {name:<38} {why}")
        else:
            failed += 1
            print(f"  WRONG {name:<38} expected {want}, got {got} :: {detail}")

    problems = _check_task_configs()
    problems += _check_code_extraction()
    print()
    if problems:
        for p in problems:
            print(f"  CONFIG {p}")
    else:
        print("  task config audit: clean")

    print(f"\nselftest: {passed} ok, {failed} wrong, {len(cases)} cases, "
          f"{len(problems)} config problems")
    return 1 if (failed or problems) else 0


if __name__ == "__main__":
    sys.exit(main())
