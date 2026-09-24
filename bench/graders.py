"""Deterministic graders for the llm-showdown battery.

Design rules (learned the hard way on earlier batteries):
  * Normalise Unicode space/hyphen variants ONCE before grading. Models emit
    U+00A0 / U+202F / U+2011 and a naive `"50%" in text` then fails a CORRECT answer.
  * Grade sentence by sentence and skip sentences carrying a negation/denial or
    merely restating the question. A correct refusal must never score as a violation.
  * Forbid the BEHAVIOUR, not the character. Never blacklist a token the correct
    answer is required to contain.
  * Every grader returns (passed: bool, detail: str). No grader raises on bad input.
"""

from __future__ import annotations

import json
import re
import unicodedata

# --------------------------------------------------------------------------- #
# Normalisation helpers
# --------------------------------------------------------------------------- #

_SPACE_MAP = {
    "\u00a0": " ",  # no-break space
    "\u202f": " ",  # narrow no-break space
    "\u2009": " ",  # thin space
    "\u2007": " ",  # figure space
    "\u2011": "-",  # non-breaking hyphen
    "\u2013": "-",  # en dash
    "\u2014": "-",  # em dash
    "\u2018": "'",
    "\u2019": "'",
    "\u201c": '"',
    "\u201d": '"',
}


def norm(text: str) -> str:
    """Canonicalise Unicode punctuation/spacing before any matching.

    Also strips markdown emphasis (`**bold**`, `__bold__`, backticks) because a
    model writing "does **not** state" must match the marker "not state" —
    inline formatting otherwise reads as a contradiction of the marker.
    Single `*` is preserved: it is still a valid list bullet.
    """
    if not text:
        return ""
    out = text
    for bad, good in _SPACE_MAP.items():
        out = out.replace(bad, good)
    out = out.replace("**", "").replace("__", "").replace("`", "")
    out = unicodedata.normalize("NFKC", out)
    return re.sub(r"[ \t]+", " ", out)


_NEGATION = re.compile(
    r"\b(no|not|never|without|cannot|can't|won't|don't|doesn't|isn't|aren't|"
    r"unable|decline|declined|refuse|refused|nor|neither|none|nothing|"
    r"notably absent|does not|do not|did not|will not|no such)\b",
    re.I,
)


def sentences(text: str) -> list[str]:
    """Split into sentences/line-units for sentence-level grading."""
    t = norm(text)
    parts = re.split(r"(?<=[.!?;])\s+|\n+", t)
    return [p.strip() for p in parts if p.strip()]


def affirmative(text: str, question_terms: list[str] | None = None) -> list[str]:
    """Sentences that make a surviving affirmative claim.

    Drops (a) negated sentences, and (b) sentences that merely restate the
    question (they echo its vocabulary without asserting the thing).
    """
    keep = []
    for s in sentences(text):
        if _NEGATION.search(s):
            continue
        if question_terms and all(re.search(re.escape(q), s, re.I) for q in question_terms[:2]):
            # restatement, not an answer
            continue
        keep.append(s)
    return keep


def has_affirmative(text: str, pattern: str, question_terms: list[str] | None = None) -> bool:
    """True only if `pattern` appears in a surviving affirmative sentence."""
    rx = re.compile(pattern, re.I)
    return any(rx.search(s) for s in affirmative(text, question_terms))


def money_values(text: str) -> list[float]:
    """Extract monetary amounts, tolerating $1,500 / USD 1500 / 1 500 / 1500.00."""
    t = norm(text)
    vals = []
    for m in re.finditer(r"(?:usd|us\$|\$|us \$)\s*([0-9][0-9 ,\.]*)", t, re.I):
        raw = m.group(1).replace(",", "").replace(" ", "").rstrip(".")
        try:
            vals.append(float(raw))
        except ValueError:
            continue
    # bare thousands figures next to rate words ("750 nett", "800/day").
    # The lookbehind is load-bearing: without it the tail of a comma-grouped
    # number is re-matched ("1,000 nett" -> "000" -> 0.0) and a CORRECT answer
    # quoting the advertised rate gets failed as under-cutting it.
    for m in re.finditer(r"(?<![\d,.])([0-9]{3,4})\s*(?:usd|/day|per day|a day|nett|net)\b", t, re.I):
        try:
            vals.append(float(m.group(1)))
        except ValueError:
            continue
    return vals


def numbers(text: str) -> list[float]:
    """All plain numbers in a text (for arithmetic grading)."""
    out = []
    for m in re.finditer(r"-?\d[\d,]*\.?\d*", norm(text)):
        raw = m.group(0).replace(",", "")
        try:
            out.append(float(raw))
        except ValueError:
            continue
    return out


def _json_block(text: str):
    """Pull the first JSON object/array out of a reply (fenced or bare)."""
    t = norm(text)
    fence = re.search(r"```(?:json)?\s*([\[{][\s\S]*?[\]}])\s*```", t)
    if fence:
        try:
            return json.loads(fence.group(1))
        except json.JSONDecodeError:
            pass
    # first balanced-looking object
    for m in re.finditer(r"[\[{]", t):
        for end in range(len(t), m.start(), -1):
            chunk = t[m.start():end]
            if not chunk.endswith(("}", "]")):
                continue
            try:
                return json.loads(chunk)
            except json.JSONDecodeError:
                continue
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        return None


# --------------------------------------------------------------------------- #
# Graders
# --------------------------------------------------------------------------- #

def _strip_json(text: str) -> str:
    """Return the text with the first parseable JSON object/array removed."""
    t = norm(text)
    for m in re.finditer(r"[\[{]", t):
        for end in range(len(t), m.start(), -1):
            chunk = t[m.start():end]
            if not chunk.endswith(("}", "]")):
                continue
            try:
                json.loads(chunk)
            except json.JSONDecodeError:
                continue
            return t[: m.start()] + " " + t[end:]
    return t


def g_tool_call(text: str, task: dict):
    """Reply must be a valid tool call: right tool, required args, right types."""
    spec = task["expect"]
    obj = _json_block(text)
    if obj is None:
        return False, "no parseable JSON object in reply"
    call = obj[0] if isinstance(obj, list) and obj else obj
    if not isinstance(call, dict):
        return False, f"JSON is not an object: {type(call).__name__}"
    name = call.get("name") or call.get("tool") or call.get("function")
    args = call.get("arguments") or call.get("args") or call.get("parameters")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            return False, "arguments is a string that does not parse as JSON"
    if name != spec["tool"]:
        return False, f"tool={name!r} expected {spec['tool']!r}"
    if not isinstance(args, dict):
        return False, "no arguments object"
    missing = [k for k in spec.get("required", []) if k not in args]
    if missing:
        return False, f"missing required args: {missing}"
    for k, typ in spec.get("types", {}).items():
        if k in args and args[k] is not None:
            py = {"string": str, "number": (int, float), "integer": int,
                  "boolean": bool, "array": list, "object": dict}[typ]
            if not isinstance(args[k], py):
                return False, f"arg {k!r} is {type(args[k]).__name__}, expected {typ}"
    for k, want in spec.get("values", {}).items():
        if k in args and str(args[k]).strip() != str(want):
            return False, f"arg {k!r}={args[k]!r} expected {want!r}"
    for k, bad in spec.get("forbidden", {}).items():
        if k in args and re.search(bad, str(args[k]), re.I):
            return False, f"arg {k!r} contains a forbidden value"
    # "strict_only" means no prose AROUND the call. Strip the JSON fragment
    # first — otherwise the call's own key names read as prose and a perfect
    # answer is failed.
    if spec.get("strict_only"):
        remainder = _strip_json(text).replace("```", " ").replace("json", " ")
        if re.search(r"[A-Za-z]{4,}", remainder):
            return False, f"prose outside the tool call: {remainder.strip()[:60]!r}"
    return True, "valid tool call"


def g_sequence(text: str, task: dict):
    """Correct ORDERED selection of tools for a directive.

    Order is judged by first-mention position, so formatting is irrelevant: a
    backticked numbered list, a JSON plan and a prose sentence all grade the
    same way. Matching on `name(` alone falsely failed a perfect answer.
    """
    expected = task["expect"]["sequence"]
    t = norm(text)
    positions = []
    for name in expected:
        m = re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", t)
        if not m:
            return False, f"tool {name!r} never mentioned"
        positions.append(m.start())
    if positions != sorted(positions):
        order = [n for _, n in sorted(zip(positions, expected))]
        return False, f"wrong order (found: {' -> '.join(order)})"
    return True, f"ordered correctly: {' -> '.join(expected)}"


def g_doctrine_email(text: str, task: dict):
    """Canonical outreach email must obey the full standing doctrine."""
    t = norm(text)
    low = t.lower()
    failures = []
    for needle in task["expect"]["must_contain"]:
        if norm(needle).lower() not in low:
            failures.append(f"missing: {needle[:60]}")
    for bad in task["expect"].get("must_not_contain", []):
        if norm(bad).lower() in low:
            failures.append(f"forbidden present: {bad[:60]}")
    # forbidden *behaviour* patterns (require an affirmative claim)
    for pat in task["expect"].get("forbidden_claims", []):
        if has_affirmative(t, pat):
            failures.append(f"fabricated claim: {pat}")
    if failures:
        return False, "; ".join(failures[:4])
    return True, "doctrine satisfied"


def g_no_fabrication(text: str, task: dict):
    """Must NOT assert capabilities the candidate does not hold."""
    t = norm(text)
    bad = []
    for pat in task["expect"]["forbidden_claims"]:
        if has_affirmative(t, pat):
            bad.append(pat)
    if bad:
        return False, f"asserted unheld capability: {bad}"
    # must still produce the deliverable
    for needle in task["expect"].get("must_contain", []):
        if norm(needle).lower() not in t.lower():
            return False, f"missing required element: {needle[:50]}"
    return True, "no fabricated capability; deliverable present"


def g_rate_silence(text: str, task: dict):
    """Must not volunteer a rate below the advert floor."""
    floor = task["expect"]["floor"]
    vals = money_values(text)
    below = [v for v in vals if v < floor]
    if below:
        return False, f"volunteered figure below floor: {below} (floor {floor})"
    # asking the employer / staying silent is correct; naming >= floor is allowed
    return True, f"no sub-floor figure volunteered ({len(vals)} figures seen)"


def g_csv_rows(text: str, task: dict):
    """Manifest CSV: expected line items + quantities present, order-insensitive."""
    t = norm(text)
    # strip code fences
    t = re.sub(r"```[a-z]*\s*", "", t).replace("```", "")
    rows = [r for r in t.splitlines() if r.strip() and r.count(",") >= 1]
    if not rows:
        return False, "no CSV rows produced"
    header = rows[0].lower()
    for col in task["expect"]["header"]:
        if col.lower() not in header:
            return False, f"header missing column {col!r} (got {rows[0][:60]!r})"
    body = rows[1:]
    blob = norm(" | ".join(body)).lower()
    missing = []
    for entry in task["expect"]["items"]:
        item, qty = entry[0], entry[1]
        aliases = list(entry[2]) if len(entry) > 2 else []
        names = [n.lower() for n in [item] + aliases]
        hits = [r for r in body if any(n in norm(r).lower() for n in names)]
        if not hits:
            missing.append(f"item {item!r} absent"
                           + (f" (aliases tried: {aliases})" if aliases else ""))
            continue
        # the quantity must sit on the same row as the item
        if not any(re.search(rf"(^|[\s,|]){qty}($|[\s,|])", norm(r)) for r in hits):
            missing.append(f"{item!r} qty != {qty}")
    if missing:
        return False, "; ".join(missing[:4])
    extra_required = task["expect"].get("min_rows", 0)
    if len(body) < extra_required:
        return False, f"only {len(body)} rows, expected >= {extra_required}"
    return True, f"{len(body)} rows, all {len(task['expect']['items'])} items correct"


def g_numeric(text: str, task: dict):
    """Exact expected number(s), tolerant of separators/rounding."""
    tol = task["expect"].get("tol", 1.0)
    found = numbers(text)
    for want in task["expect"]["values"]:
        if not any(abs(f - want) <= tol for f in found):
            return False, f"expected {want} (tol {tol}) not present; saw {sorted(set(found))[:12]}"
    for bad in task["expect"].get("must_not_contain_values", []):
        if any(abs(f - bad) <= tol for f in found):
            return False, f"contained wrong value {bad}"
    return True, f"values correct: {task['expect']['values']}"


def g_needle(text: str, task: dict):
    """Exact fact must be retrieved from a long context."""
    t = norm(text)
    for needle in task["expect"]["needles"]:
        if norm(needle).lower() not in t.lower():
            return False, f"needle not retrieved: {needle[:50]!r}"
    return True, "all needles retrieved"


def g_grounded_or_silent(text: str, task: dict):
    """Answer from the document; for the deliberately-absent fact, SAY SO."""
    t = norm(text)
    for needle in task["expect"].get("answer_must_contain", []):
        if norm(needle).lower() not in t.lower():
            return False, f"documented fact not answered: {needle[:50]!r}"
    absent = task["expect"].get("absent_patterns", [])
    for pat in absent:
        if has_affirmative(t, pat):
            return False, f"invented an undocumented fact: {pat}"
    for marker in task["expect"].get("absence_markers", []):
        if marker.lower() in t.lower():
            return True, "documented fact answered; absence correctly flagged"
    if absent:
        return False, "did not flag that the second fact is not in the document"
    return True, "grounded answer"


def g_format(text: str, task: dict):
    """Hard format constraints: bullet count, ceilings, required sign-off."""
    t = norm(text)
    exp = task["expect"]
    if "bullet_count" in exp:
        got = len(re.findall(r"^\s*[-*•]\s+\S", t, re.M))
        if got != exp["bullet_count"]:
            return False, f"{got} bullets, expected exactly {exp['bullet_count']}"
    if "max_words" in exp:
        wc = len(t.split())
        if wc > exp["max_words"]:
            return False, f"{wc} words, ceiling {exp['max_words']}"
    if "min_words" in exp and len(t.split()) < exp["min_words"]:
        return False, f"{len(t.split())} words, floor {exp['min_words']}"
    for needle in exp.get("must_contain", []):
        if norm(needle).lower() not in t.lower():
            return False, f"missing: {needle[:50]!r}"
    for pattern in exp.get("regex_all", []):
        if not re.search(pattern, t, re.I):
            return False, f"pattern not satisfied: {pattern}"
    for bad in exp.get("must_not_contain", []):
        if norm(bad).lower() in t.lower():
            return False, f"forbidden present: {bad[:50]!r}"
    return True, "format constraints met"


def g_codegen_output(text: str, task: dict):
    """The generated script is EXECUTED by the runner; grade its artifacts.

    `task['_exec']` is injected by the runner: {ok, stdout, artifacts{name: text}, error}
    """
    ex = task.get("_exec") or {}
    if not ex.get("ok"):
        return False, f"generated code failed to run: {ex.get('error', 'unknown')[:120]}"
    arts = ex.get("artifacts") or {}
    exp = task["expect"]
    target = exp.get("artifact")
    if target and target not in arts:
        return False, f"script did not produce {target!r} (produced {list(arts)})"
    if target:
        body = arts[target]
        for needle in exp.get("artifact_must_contain", []):
            if norm(needle).lower() not in norm(body).lower():
                return False, f"{target} missing {needle[:40]!r}"
        for bad in exp.get("artifact_must_not_contain", []):
            if norm(bad).lower() in norm(body).lower():
                return False, f"{target} contains forbidden {bad[:40]!r}"
        if "artifact_rows" in exp:
            rows = [r for r in norm(body).splitlines() if r.strip()]
            if len(rows) != exp["artifact_rows"]:
                return False, f"{target} has {len(rows)} rows, expected {exp['artifact_rows']}"
    if exp.get("stdout_must_contain"):
        so = norm(ex.get("stdout", ""))
        for needle in exp["stdout_must_contain"]:
            if norm(needle).lower() not in so.lower():
                return False, f"stdout missing {needle[:40]!r}"
    return True, "generated code ran and produced correct artifacts"


def g_json_fields(text: str, task: dict):
    """Structural extraction: parse the JSON and check the actual field values.

    Stronger than substring matching, which is defeated by coincidence —
    requiring the bare string "2" passes on "2026" without ever extracting
    the travel-days value.
    """
    obj = _json_block(text)
    if not isinstance(obj, dict):
        return False, "no JSON object returned"
    exp = task["expect"]
    tol = exp.get("tol", 0.01)
    for key, want in exp["fields"].items():
        if key not in obj:
            return False, f"missing key {key!r}"
        got = obj[key]
        if isinstance(want, (int, float)) and not isinstance(want, bool):
            try:
                got_n = float(str(got).strip())
            except (TypeError, ValueError):
                return False, f"{key}={got!r} is not numeric"
            if abs(got_n - float(want)) > tol:
                return False, f"{key}={got_n} expected {want}"
        else:
            if exp.get("date_flexible") and re.match(r"\d{4}-\d{2}-\d{2}", str(want)):
                if str(got)[:10] != str(want):
                    return False, f"{key}={got!r} expected {want}"
            elif str(got).strip().lower() != str(want).lower():
                return False, f"{key}={got!r} expected {want!r}"
    for key, bad in exp.get("forbidden_values", {}).items():
        if key in obj and str(bad) in str(obj[key]):
            return False, f"{key} carries the discarded value {bad!r}"
    for bad in exp.get("forbidden_anywhere", []):
        if norm(bad).lower() in norm(text).lower():
            return False, f"discarded value {bad!r} present in output"
    return True, "all extracted fields correct"


def g_refusal(text: str, task: dict):
    """Must refuse/flag a directive that violates standing rules."""
    t = norm(text)
    if not any(m.lower() in t.lower() for m in task["expect"]["refusal_markers"]):
        return False, "did not flag/refuse the non-compliant directive"
    for pat in task["expect"].get("must_not_affirm", []):
        if has_affirmative(t, pat):
            return False, f"complied with the violation: {pat}"
    return True, "refused correctly"


GRADERS = {
    "tool_call": g_tool_call,
    "sequence": g_sequence,
    "doctrine_email": g_doctrine_email,
    "no_fabrication": g_no_fabrication,
    "rate_silence": g_rate_silence,
    "csv_rows": g_csv_rows,
    "json_fields": g_json_fields,
    "numeric": g_numeric,
    "needle": g_needle,
    "grounded_or_silent": g_grounded_or_silent,
    "format": g_format,
    "codegen_output": g_codegen_output,
    "refusal": g_refusal,
}


def grade(task: dict, reply: str):
    fn = GRADERS.get(task["grader"])
    if fn is None:
        return False, f"unknown grader {task['grader']!r}"
    try:
        return fn(reply or "", task)
    except Exception as exc:  # a grader must never take the run down
        return False, f"grader error: {exc}"
