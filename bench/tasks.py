"""The personal battery: the real work this rig is used for.

Every task is phrased as a thing that actually gets done on this machine —
campaign outreach under standing doctrine, equipment manifests from a SOW,
cron-style tool calls, IMCA/procedure grounding, campaign arithmetic,
scripts that must run, retrieval from long context.

Classes
  protocol   tool-call JSON and ordered tool selection (the cron failure mode)
  doctrine   outreach email obeying the standing email doctrine
  refusal    flagging a directive that violates the standing rules
  extraction equipment manifest / structured pull from messy input
  grounding  answer only from the supplied document
  arithmetic campaign cost and rotation maths
  codegen    a script that must EXECUTE and produce correct artifacts
  longctx    needle retrieval at depth inside a long document
  format     hard format constraints
"""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# Fixtures from the real world
# --------------------------------------------------------------------------- #

SOW_EXCERPT = """\
SCOPE OF WORK - SUBSEA INSPECTION CAMPAIGN, BLOCK X (extract)

3.1  Diving spread
     The CONTRACTOR shall provide a surface supplied diving spread comprising a
     minimum of eight (8) diver helmets with associated umbilicals, two (2)
     decompression chambers, and one (1) dive control console with redundant
     communications. One (1) air diving supervisor is required per shift, with
     two (2) shifts planned per day.

3.2  ROV support
     One (1) work class ROV with a minimum 250 hp rating shall be mobilised,
     together with two (2) complete spare tether sections and one (1) spare
     manipulator arm.

3.3  Survey and positioning
     The CONTRACTOR shall supply two (2) USBL transceivers, one (1) multibeam
     echosounder, and four (4) acoustic release units. A single (1) INS unit is
     acceptable where dual-redundant positioning is not required by the CLIENT.

3.4  Consumables
     The following consumables shall be provided for the duration of the
     campaign: 400 litres of hydraulic fluid, 60 litres of compressor oil,
     12 anode assemblies (aluminium, sacrificial), and 250 metres of spiral wrap.
"""

MESSY_TRANSCRIPT = """\
[voice note transcript]
ja so uh we need to sort the mob date out - it's the eighth of October, oh wait
no, the eight is a sunday... let me think, we're looking at the tenth of October
2026 for mob to the vessel, and the client wants us offshore for twenty one days
total. the day rate is seven hundred and fifty dollars a day nett, and travel
days are paid at fifty percent, there's two travel days. also don't forget the
mob fee, that's four thousand five hundred dollars once off. the certification
on the spread is due for renewal on the third of november 2026 so that's cutting
it close.
"""

PROCEDURE_DOC = """\
PROCEDURE CS-PM-014 - SUBSEA INSPECTION REPORT REVIEW (extract, rev 4)

5.2  Review sequence
     5.2.1  The reviewer confirms the inspection report against the approved
            work scope before any client issue.
     5.2.2  Any anomaly classified as ANOMALY-CLASS A requires an engineering
            assessment within five (5) working days of report receipt.
     5.2.3  ANOMALY-CLASS B items are logged and reviewed at the next routine
            inspection interval, which shall not exceed twelve (12) months.
     5.2.4  Photography of any anomaly shall include a scale reference in every
            frame. Frames without a scale reference are rejected at review.

5.3  Retention
     5.3.1  Inspection records are retained for a period of seven (7) years
            from the date of the inspection campaign close-out.
     5.3.2  Records relating to welding procedures are retained for the life of
            the asset.
"""

JOB_POSTING = """\
CLIENT REPRESENTATIVE - SUBSEA INSTALLATION (ROTATIONAL)
Location: World-Wide / Multiple Rotations
Rate: USD 1,000 nett per day
Duration: 12 months, rotational

Requirements:
  - Minimum 10 years offshore subsea installation experience
  - CSWIP 3.4U or equivalent senior inspection certification
  - ISO 9712 Level 2 certification in MT, PT and UT (mandatory)
  - Valid offshore medical, BOSIET/FOET, seaman's book
  - Experience representing the client on diving and ROV installation scopes
"""

# --------------------------------------------------------------------------- #
# Long-context document builder (token-ish scale, deterministic)
# --------------------------------------------------------------------------- #

_LONG_FILLER = [
    "The campaign tracker is reviewed each morning before the operations call.",
    "Vessel mobilisation is contingent on the certification pack being complete.",
    "Diving operations are suspended where current exceeds the stated limit.",
    "The contractor shall maintain a register of all personnel on board.",
    "Any deviation from the approved procedure requires written client approval.",
    "Weather downtime is recorded separately from standby and repair time.",
    "Equipment is inspected on arrival and again before each deployment.",
    "The operations log is signed by the supervisor at the end of each shift.",
]


def build_long_doc(target_words: int = 24000, needles: dict | None = None) -> str:
    """Build a long document with needles planted at ~10%, 50% and 90% depth.

    needles: {"10": "text", "50": "text", "90": "text"}
    """
    needles = needles or {}
    blocks = [
        "PROJECT EXECUTION AND OPERATIONS MANUAL (consolidated, controlled copy)",
        "Section 1 - Governance",
    ]
    i = 0
    depth_targets = {int(k): v for k, v in needles.items()}
    placed: set[int] = set()
    total_blocks = max(1, target_words // 12)
    while len(" ".join(blocks).split()) < target_words:
        pct = int(100 * i / max(1, total_blocks))
        for mark in depth_targets:
            if mark not in placed and pct >= mark:
                blocks.append(f"RECORDED VALUE (reference {mark}): {depth_targets[mark]}")
                placed.add(mark)
        blocks.append(_LONG_FILLER[i % len(_LONG_FILLER)])
        if i % 40 == 0:
            blocks.append(f"Section {i // 40 + 1} - Routine provisions")
        i += 1
    for mark, val in depth_targets.items():
        if mark not in placed:
            blocks.append(f"RECORDED VALUE (reference {mark}): {val}")
    return "\n".join(blocks)


def long_ctx_task(target_words: int = 24000) -> dict:
    """One long-context task with three needles and a buried constraint."""
    needles = {
        "10": "the approved transit speed is 11.4 knots",
        "50": "the crane SWL for this campaign is 42.5 tonnes",
        "90": "the charter party reference is CP-8841-QX",
    }
    doc = build_long_doc(target_words, needles)
    doc += (
        "\nSTANDING INSTRUCTION (buried, Section 99): any summary produced from this "
        "manual must state the charter party reference in the first line.\n"
    )
    return {
        "id": "longctx_needles",
        "class": "longctx",
        "grader": "needle",
        "prompt": (
            "Read the document below and answer with the four facts it records: "
            "the approved transit speed, the crane SWL for the campaign, the charter "
            "party reference, and the standing instruction about summaries.\n\n"
            + doc
        ),
        "expect": {
            "needles": [
                "11.4 knots",
                "42.5 tonnes",
                "CP-8841-QX",
                "charter party reference in the first line",
            ]
        },
        "max_tokens": 2000,
        "trials": 2,
    }


# --------------------------------------------------------------------------- #
# The battery
# --------------------------------------------------------------------------- #

TASKS: list[dict] = [
    # ---------------- protocol ----------------
    {
        "id": "tool_single_read",
        "class": "protocol",
        "grader": "tool_call",
        "prompt": (
            "You are an agent with tools. Emit ONLY a single JSON tool call, no prose.\n\n"
            'Available tool schema:\n{"name":"read_file","parameters":'
            '{"path":"string (required)","offset":"integer (optional)","limit":"integer (optional)"}}\n\n'
            "Directive: read the operations tracker at /srv/projects/operations_tracker.md "
            "starting from line 200, at most 50 lines."
        ),
        "expect": {
            "tool": "read_file",
            "required": ["path"],
            "types": {"path": "string", "offset": "integer", "limit": "integer"},
            "values": {"path": "/srv/projects/operations_tracker.md", "offset": 200, "limit": 50},
            "strict_only": True,
        },
        "max_tokens": 600,
    },
    {
        "id": "tool_chain_dedup",
        "class": "protocol",
        "grader": "sequence",
        "prompt": (
            "An agent must: first check the agentmail send log for an existing thread with the "
            "target, then read the campaign tracker, and only then draft the email. "
            "Reproduce that ordered tool-selection as a numbered list using the tool names: "
            "list_messages, read_file, draft_email."
        ),
        "expect": {"sequence": ["list_messages", "read_file", "draft_email"]},
        "max_tokens": 400,
    },
    # ---------------- doctrine ----------------
    {
        "id": "doctrine_email_straight",
        "class": "doctrine",
        "grader": "doctrine_email",
        "prompt": (
            "Draft the outreach email for this posting.\n\n"
            "STANDING EMAIL DOCTRINE — follow it exactly:\n"
            "- The email is written BY the assistant Aiduh, introducing Joshua Evans in "
            "the third person. It is not written by Joshua.\n"
            "- Open with this line, verbatim: \"I am writing to introduce Joshua Evans.\"\n"
            "- Follow it with a role-match line naming the target role and its location.\n"
            "- Then capability bullets drawn ONLY from this approved list; invent nothing "
            "and claim no certification or qualification that is not listed:\n"
            "    * Subsea project management — planning and reporting direct to client leadership\n"
            "    * CSWIP 3.4U subsea inspection, integrity and asset management\n"
            "    * AutoCAD certified, with a strong understanding of P&IDs and technical drawings\n"
            "    * UWILDs and FPSO husbandry — campaign planning and delivery in the field\n"
            "    * Mobilisation planning\n"
            "    * Consulting and useful implementation of AI in the industry\n"
            "- Close with this line, verbatim: \"The CV attached carries his full history "
            "and references. If you'd like a conversation, please make contact.\"\n"
            "- Sign off with this block, verbatim:\n"
            "Kind regards,\n"
            "Aiduh — assistant to Joshua Evans\n"
            "Consultingsubsea\n"
            "Email: jsh.evan@gmail.com | Phone: +27 82 854 8906\n"
            "- Include the header line \"CC: jsh.evan@gmail.com\".\n"
            "- Never reference a website or a domain name.\n\n"
            "Candidate background: Joshua Evans, subsea project manager and CSWIP 3.4U "
            "inspector; 20+ years subsea operations in West Africa, the Middle East and "
            "the Persian Gulf; delivered for Chevron, Exxon and Shell; 5 years planning "
            "and managing marine/subsea operations remotely from home, plus on-deck "
            "execution before that.\n\n"
            + JOB_POSTING
        ),
        "expect": {
            "must_contain": [
                "I am writing to introduce Joshua Evans",
                "subsea project manager",
                "CSWIP 3.4U",
                "Chevron",
                "The CV attached carries his full history and references",
                "If you'd like a conversation, please make contact",
                "Kind regards",
                "Aiduh",
                "jsh.evan@gmail.com",
                "+27 82 854 8906",
                "jsh.evan@gmail.com",  # CC presence
                "Client Representative",  # role match named
            ],
            "must_not_contain": ["consultingsubsea.com", "www.", "http"],
            "forbidden_claims": [
                r"ISO\s*9712",
                r"(?:holds?|have|has|possess\w*)\s+(?:the\s+)?(?:MT|PT|UT|NDT)\s*(?:level|L)?" ,
                r"(?:willing|happy|prepared|able)\s+to\s+relocat",
                r"\b3\.[12]U\b",
                r"(?:our\s+)?website",
            ],
        },
        "max_tokens": 3000,
    },
    {
        "id": "doctrine_rate_silence",
        "class": "doctrine",
        "grader": "rate_silence",
        "prompt": (
            "The employer asks: 'What is your expected day rate for this role?' "
            "The posting advertises USD 1,000 nett per day.\n\n"
            "Draft the rate paragraph for the reply. Standing rule: never volunteer a figure "
            "below the advertised rate, and do not name a lower number than the advert."
        ),
        "expect": {"floor": 1000},
        "max_tokens": 1200,
    },
    # ---------------- refusal ----------------
    {
        "id": "refusal_no_cert_fabrication",
        "class": "refusal",
        "grader": "refusal",
        "prompt": (
            "Draft the email for this posting. The candidate does NOT hold ISO 9712 Level 2 "
            "in MT/PT/UT.\n\n" + JOB_POSTING + "\n\n"
            "Write the capability paragraph and note anything the posting requires that the "
            "candidate does not hold. Do not claim certifications he does not have."
        ),
        "expect": {
            "refusal_markers": [
                "does not hold", "not held", "do not hold", "does not include",
                "not currently certified", "without", "lacks", "no ISO 9712", "not in possession",
            ],
            # Regex form, because literal markers are brittle: an answer that
            # correctly says "does not CURRENTLY hold" is still a refusal, and a
            # bare substring list scored it as a failure.
            "refusal_patterns": [
                r"(?:does|do)\s+not\s+(?:currently\s+)?(?:hold|have|possess)",
                r"not\s+(?:currently\s+)?(?:certified|qualified|held|in\s+possession)",
                r"\blacks?\b",
                r"\bno\s+ISO\s*9712\b",
                r"\bwithout\s+(?:the\s+)?ISO\s*9712",
                r"non-?conformance",
                r"not\s+a\s+compliant\s+fit",
            ],
            # These must forbid a POSSESSION CLAIM, not the words "ISO 9712".
            # A sentence describing the posting's mandatory requirement is correct
            # behaviour, and an earlier version of this pattern failed exactly that.
            "must_not_affirm": [
                r"(?:holds?|has|possess\w*|carries)\s+(?:an?\s+)?(?:valid\s+|current\s+)?ISO\s*9712",
                r"(?:certified|qualified)\s+in\s+(?:MT|PT|UT)",
                r"(?:his|my|Joshua'?s)\s+ISO\s*9712",
            ],
        },
        "max_tokens": 2000,
    },
    # ---------------- extraction ----------------
    {
        "id": "extract_manifest_sow",
        "class": "extraction",
        "grader": "csv_rows",
        "prompt": (
            "Produce the equipment manifest as CSV from this scope of work. "
            "One line item per row with columns: Category,Item,Qty,Notes. "
            "Use the quantities as stated. Output only the CSV.\n\n" + SOW_EXCERPT
        ),
        "expect": {
            "header": ["Category", "Item", "Qty", "Notes"],
            "items": [
                ("diver helmet", 8, ["helmet"]),
                ("decompression chamber", 2, ["chamber"]),
                ("dive control console", 1, ["control console"]),
                # The SOW says "one (1) per shift, two (2) shifts per day". Both
                # "1, per shift" and the campaign total "2" are honest manifest
                # lines, so either quantity is accepted.
                ("air diving supervisor", [1, 2], ["diving supervisor"]),
                ("work class ROV", 1, ["work-class ROV"]),
                ("spare tether", 2, ["tether"]),
                ("manipulator arm", 1, ["manipulator"]),
                ("USBL transceiver", 2, ["USBL"]),
                ("multibeam echosounder", 1, ["multibeam"]),
                ("acoustic release", 4, []),
                ("INS unit", 1, ["INS"]),
                ("hydraulic fluid", 400, []),
                ("compressor oil", 60, []),
                ("anode assemblies", 12, ["aluminium anodes", "anodes"]),
                ("spiral wrap", 250, []),
            ],
            "min_rows": 15,
        },
        "max_tokens": 3000,
    },
    {
        "id": "extract_messy_transcript",
        "class": "extraction",
        "grader": "json_fields",
        "prompt": (
            "From this voice-note transcript, extract a JSON object with keys: mob_date "
            "(ISO date), offshore_days (integer), day_rate_usd (number), travel_days "
            "(integer), mob_fee_usd (number), cert_renewal_date (ISO date). "
            "Ignore the discarded dates. Output only JSON.\n\n" + MESSY_TRANSCRIPT
        ),
        "expect": {
            "fields": {
                "mob_date": "2026-10-10",
                "offshore_days": 21,
                "day_rate_usd": 750,
                "travel_days": 2,
                "mob_fee_usd": 4500,
                "cert_renewal_date": "2026-11-03",
            },
            "date_flexible": True,
            "forbidden_anywhere": ["2026-10-08"],
        },
        "max_tokens": 1200,
    },
    # ---------------- grounding ----------------
    {
        "id": "grounding_procedure",
        "class": "grounding",
        "grader": "grounded_or_silent",
        "prompt": (
            "Answer ONLY from the document below. Two questions:\n"
            "1) Within how many working days must ANOMALY-CLASS A receive an engineering "
            "assessment?\n"
            "2) What is the retention period for dive supervisor logbooks?\n"
            "If the document does not state something, say so explicitly.\n\n" + PROCEDURE_DOC
        ),
        "expect": {
            "answer_must_contain": ["five", "5"],
            "absence_markers": ["not state", "does not state", "not specified",
                                "no retention period for dive supervisor", "not mentioned",
                                "does not mention", "not in the document", "does not specify",
                                "not covered", "no information"],
            "absent_patterns": [
                r"dive supervisor logbooks?\s+(?:are|is)\s+retained",
                r"retention period for dive supervisor logbooks is",
            ],
        },
        "max_tokens": 1500,
    },
    # ---------------- arithmetic ----------------
    {
        "id": "arith_campaign_cost",
        "class": "arithmetic",
        "grader": "numeric",
        "prompt": (
            "Calculate the total campaign value and answer with the figure.\n"
            "Day rate USD 750 nett per day. Offshore duration 21 days. "
            "Travel days: 2, paid at 50% of day rate. Mob fee USD 4,500 once off.\n\n"
            "Show the arithmetic briefly, then state the TOTAL in USD."
        ),
        # 21*750 = 15750 ; 2*375 = 750 ; + 4500 = 21000
        # NOTE 15750 must NOT be forbidden: a correct answer shows its working,
        # and the first line of that working is 15,750.
        "expect": {"values": [21000], "tol": 1, "must_not_contain_values": [20250]},
        "max_tokens": 1500,
    },
    {
        "id": "arith_rotation_crews",
        "class": "arithmetic",
        "grader": "format",
        "prompt": (
            "A campaign runs offshore for 45 days and needs two supervisory positions "
            "manned SIMULTANEOUSLY at all times — Position 1 (diving supervisor) and "
            "Position 2 (ROV supervisor). On day 1, supervisor A takes Position 1 and "
            "supervisor B takes Position 2. Each of the two works 28 days, and at the "
            "end of those 28 days each is replaced by a relief who covers the rest of "
            "the campaign.\n"
            "State the total number of crew changes (one change = one person being "
            "replaced) and the day numbers on which they occur. "
            "Use the format 'Crew changes: N' and then list the day numbers."
        ),
        # Two people, each replaced once, both on day 29.
        # Phrased around two simultaneously-manned positions on purpose: an
        # earlier wording ("a two-person team on a 28/28 rotation") read
        # legitimately as one person on and one off, which changes the answer.
        "expect": {"regex_all": [r"crew changes:\s*2\b", r"\b29\b"]},
        "max_tokens": 2000,
    },
    # ---------------- codegen ----------------
    {
        "id": "codegen_manifest_csv",
        "class": "codegen",
        "grader": "codegen_output",
        "prompt": (
            "Write a complete, runnable Python 3 script to a file named mkm.py that reads "
            "the file 'items.txt' in the current directory. Each line is 'Category,Item,Qty'. "
            "Write 'manifest.csv' with header Category,Item,Qty,Notes where Notes is empty, "
            "sorted by Category then Item. Print 'rows=N' where N is the number of data rows. "
            "Output ONLY a single python code block."
        ),
        "expect": {
            "artifact": "manifest.csv",
            "artifact_must_contain": ["Category,Item,Qty,Notes", "Diving,Helmet,8"],
            "artifact_rows": 5,
            "stdout_must_contain": ["rows=4"],
        },
        "fixtures": {
            "items.txt": "Diving,Helmet,8\nDiving,Chamber,2\nSurvey,USBL,2\nROV,Tether,2\n",
        },
        "run_file": "mkm.py",
        "run_cmd": ["python3", "mkm.py"],
        "max_tokens": 2500,
    },
    {
        "id": "codegen_fix_script",
        "class": "codegen",
        "grader": "codegen_output",
        "prompt": (
            "This script is meant to count non-empty lines in data.txt and print 'count=N', "
            "but it is broken. Fix it and output ONLY the corrected python code block.\n\n"
            "```python\n"
            "def main():\n"
            "    with open('data.txt') as f:\n"
            "        lines = f.read().split('\\n')\n"
            "    n = 0\n"
            "    for line in lines:\n"
            "        if line:\n"
            "            n += 1\n"
            "    print('count=' + n)\n"
            "main()\n"
            "```"
        ),
        "expect": {
            "stdout_must_contain": ["count=3"],
        },
        "fixtures": {"data.txt": "alpha\n\nbeta\ngamma\n\n"},
        "run_file": "fixed.py",
        "run_cmd": ["python3", "fixed.py"],
        "max_tokens": 2000,
    },
    # ---------------- format ----------------
    {
        "id": "format_bullets_ceiling",
        "class": "format",
        "grader": "format",
        "prompt": (
            "Summarise the subsea project manager's profile in EXACTLY 3 bullet points, "
            "each starting with '- ', no more than 60 words total, no emoji. "
            "Facts: 20+ years subsea operations; CSWIP 3.4U inspector; 5 years managing "
            "subsea campaigns remotely; delivered for Chevron, Exxon and Shell; "
            "UWILDs and FPSO husbandry."
        ),
        "expect": {"bullet_count": 3, "max_words": 60, "must_contain": ["- "]},
        "max_tokens": 800,
    },
]

# sort so the cheap protocol tasks run first and the long one last
CLASS_ORDER = {
    "protocol": 0, "format": 1, "refusal": 2, "grounding": 3,
    "extraction": 4, "arithmetic": 5, "doctrine": 6, "codegen": 7, "longctx": 8,
}


def all_tasks(include_long: bool = True, long_words: int = 24000) -> list[dict]:
    tasks = [dict(t) for t in TASKS]
    if include_long:
        tasks.append(long_ctx_task(long_words))
    tasks.sort(key=lambda t: CLASS_ORDER.get(t["class"], 99))
    return tasks
