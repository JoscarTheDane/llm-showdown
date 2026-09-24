"""llm-showdown — a personal model battery and 1v1 stand-off rig.

Modules:
    tasks     the battery (our real work, phrased as tasks)
    graders   deterministic graders (+ the traps they guard against)
    runner    suite execution, raw capture, code execution
    speed     prefill / TTFT / decode rig
    swap      single-GPU llama.cpp handover manager
    report    1v1 head-to-head scoring and Markdown report
    cli       entrypoints
    selftest  offline grader validation
"""

__version__ = "1.0.0"
