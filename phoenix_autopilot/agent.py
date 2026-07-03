"""
agent.py — Phoenix Autopilot
5-mode autonomous LLMOps agent powered by Arize Phoenix MCP.
Run: adk web  |  adk run agent.py
"""

import os
from dotenv import load_dotenv
load_dotenv()

# ── Tracing — MUST happen before ADK imports ──────────────────────────────
from phoenix.otel import register

tracer_provider = register(project_name="phoenix-autopilot", auto_instrument=True)

# ── ADK + MCP ─────────────────────────────────────────────────────────────
from google.adk.agents import Agent
from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset, StdioServerParameters

MODEL = "gemini-2.0-flash"

def make_phoenix_mcp():
    return MCPToolset(
        connection_params=StdioServerParameters(
            command="npx",
            args=[
                "@arizeai/phoenix-mcp@latest",
                "--baseUrl", os.getenv("PHOENIX_BASE_URL", ""),
                "--apiKey",  os.getenv("PHOENIX_API_KEY",  ""),
            ],
        )
    )


# ══════════════════════════════════════════════════════════════════════════
# SUB-AGENT 1 — COST COP
# ══════════════════════════════════════════════════════════════════════════
cost_cop_agent = Agent(
    name="cost_cop",
    model=MODEL,
    description="Finds token-wasteful query patterns in Phoenix traces and suggests fixes.",
    instruction="""
You are the Cost Cop. Analyze token usage in Phoenix traces.

Steps (use Phoenix MCP tools):
1. Call list_projects, find project "phoenix-autopilot-demo".
2. Call get_traces or search_spans. Find spans with highest llm.token_count.total.
3. Group by query.type. Calculate avg input tokens, avg output tokens, ratio.
4. Identify top 3 wasteful patterns (highest input:output ratio).
5. For forecast: get total tokens used across ALL spans. Note earliest and latest
   span timestamps to calculate the observation window in days.
   - Daily token rate = total tokens / window days
   - 30-day projected tokens = daily rate × 30
   - Use Gemini 2.5 Flash pricing: $0.15 per 1M input tokens, $0.60 per 1M output tokens
   - Compute current 30-day cost and post-fix 30-day cost (apply the Save% from each pattern)

Output — strict format, each field on its own line:

════════════════════════════════
COST COP REPORT
════════════════════════════════

#1  <query.type>
    Tokens : <N> in / <N> out
    Ratio  : <N>:1
    Save   : ~<N>%
    Cause  : <5 words max>
    Fix    : <1 sentence>

#2  <query.type>
    Tokens : <N> in / <N> out
    Ratio  : <N>:1
    Save   : ~<N>%
    Cause  : <5 words max>
    Fix    : <1 sentence>

#3  <query.type>
    Tokens : <N> in / <N> out
    Ratio  : <N>:1
    Save   : ~<N>%
    Cause  : <5 words max>
    Fix    : <1 sentence>

────────────────────────────────
TOP ROI  : <query.type>
Reason   : <why this saves most per effort>

════════════════════════════════
COST FORECAST — 30-day projection
════════════════════════════════
Window          : <N> days
Daily tokens    : <N>
Current cost    : $<X>/month  (<N>M in + <N>M out tokens)
After fixes     : $<X>/month  (~<N>% reduction)
Monthly savings : $<X>/month
⚠ Unfixed trajectory reaches $<X>/month in 30 days.
════════════════════════════════

After presenting the report, ask the user:
"🔧 Proposed Fix — <query.type>
  • Current cost   : $<X>/month
  • Optimised prompt: \"<optimised_prompt>\"
  • Estimated saving: ~<N>% (~$<X>/month)
  Apply this fix? (yes / no)"

Wait for user response. If yes: call Phoenix MCP upsert_prompt with:
  - name: <query_type>
  - template: <optimised_prompt>
Do NOT skip this MCP call. After upsert_prompt completes, confirm:
"✅ Fix written to Phoenix. View it under Prompts → <query_type>."
If no: skip and move on. If upsert_prompt fails, report the exact error.
""",
    tools=[make_phoenix_mcp()],
)

# ══════════════════════════════════════════════════════════════════════════
# SUB-AGENT 2 — PROMPT DOCTOR
# ══════════════════════════════════════════════════════════════════════════
prompt_doctor_agent = Agent(
    name="prompt_doctor",
    model=MODEL,
    description="Finds which prompt version caused eval score degradation and proposes a fix.",
    instruction="""
You are the Prompt Doctor. No thinking out loud. No explanations. Output ONLY the report format below, then the fix question. Nothing else.

Steps (use Phoenix MCP tools — do not narrate):
1. Get traces from "phoenix-autopilot-demo".
2. Group by prompt.version. Compute avg eval.score per version.
3. PREVIOUS = highest score version. CURRENT = lowest score version.
4. Get 2 examples from each. Find the single broken instruction.

PROMPT DOCTOR REPORT
────────────────────
Previous: <version>  score <score>
Current:  <version>  score <score>  drop <delta> (<N>%)

✓ "<input>" → "<output>"
✓ "<input>" → "<output>"
✗ "<input>" → "<output>"
✗ "<input>" → "<output>"

Cause:  <exact phrase removed or weakened>
Effect: <bad behaviour in 8 words>
Remove:  "<phrase>"
Replace: "<one clause fix>"
Score after fix: <score>

🔧 Apply this fix? (yes / no)

If yes: call upsert_prompt with name=<prompt_name>, template=<full prompt with fix applied>. Then output only: "✅ Written to Phoenix → Prompts → <prompt_name>"
If no: revise and re-present report only. No explanation.
""",
    tools=[make_phoenix_mcp()],
)

# ══════════════════════════════════════════════════════════════════════════
# SUB-AGENT 3 — FAILURE FINGERPRINT
# ══════════════════════════════════════════════════════════════════════════
fingerprint_agent = Agent(
    name="failure_fingerprint",
    model=MODEL,
    description="Clusters low-scoring traces into named failure patterns with guardrail suggestions.",
    instruction="""
You are the Failure Fingerprinter. Find systematic failure patterns in Phoenix traces.

Steps (use Phoenix MCP tools):
1. Get traces from "phoenix-autopilot-demo".
2. Filter for spans where eval.score < 0.35.
3. Group these bad spans by query.type attribute.
4. For each group: count total, calculate failure rate vs all spans of that type.
5. Pick 2 example inputs from each failing group.
6. Name the failure pattern (e.g. "Date-range queries always fail").

Output format:
FAILURE FINGERPRINT REPORT
──────────────────────────
Cluster 1: "<pattern name>"
  Query type: <query.type value>
  Failure rate: <X>% (<N> of <total> spans)
  Example inputs:
    - <input 1>
    - <input 2>
  Why it fails: <analysis>
  Guardrail: <specific prompt addition or input validation rule to prevent this>

Repeat for each cluster found.
Total bad spans: <N> across <N> clusters.
""",
    tools=[make_phoenix_mcp()],
)

# ══════════════════════════════════════════════════════════════════════════
# SUB-AGENT 4 — INCIDENT SUMMARIZER
# ══════════════════════════════════════════════════════════════════════════
incident_agent = Agent(
    name="incident_summarizer",
    model=MODEL,
    description="Detects anomaly time windows and produces a plain-English incident report.",
    instruction="""
You are the Incident Summarizer. Find the production incident in Phoenix traces.

Steps (use Phoenix MCP tools):
1. Get traces from "phoenix-autopilot-demo".
2. Look for spans with alert.triggered = true OR eval.score < 0.25.
3. Find the time window where these cluster (start timestamp, end timestamp).
4. Identify: which prompt.version was live during the incident.
5. Count unique session.id values affected.
6. Pull the 3 worst spans (lowest eval.score) as evidence.

Output format:
INCIDENT REPORT
───────────────
Severity: HIGH
Incident window: <start time> → <end time>
Duration: <N> minutes
Prompt version live: <version>
Sessions affected: <N>
Worst eval score seen: <score>

Evidence (worst spans):
  1. Input: <input> | Score: <score>
  2. Input: <input> | Score: <score>
  3. Input: <input> | Score: <score>

Root cause: <what caused this — prompt version, query type, etc.>
Recommended action: Roll back to <version> immediately.
Status: OPEN — awaiting rollback confirmation.
""",
    tools=[make_phoenix_mcp()],
)

# ══════════════════════════════════════════════════════════════════════════
# SUB-AGENT 5 — GOLDEN DATASET BUILDER
# ══════════════════════════════════════════════════════════════════════════
dataset_agent = Agent(
    name="dataset_builder",
    model=MODEL,
    description="Mines production traces to build a golden eval dataset in Phoenix.",
    instruction="""
You are the Dataset Builder. Build a golden eval dataset from production traces.

Steps (use Phoenix MCP tools):
1. Get traces from "phoenix-autopilot-demo".
2. Filter GOOD examples: eval.score > 0.85. Take up to 20.
3. Filter BAD examples: eval.score < 0.25. Take up to 10.
4. For each example extract: input.value, output.value, eval.score, query.type.
5. Call Phoenix MCP create_dataset with name "golden-eval-set-v1". Do NOT skip this call.
6. Call Phoenix MCP add_examples with the dataset name and all collected examples,
   each labelled "good" or "bad". Do NOT skip this call.
7. After both MCP calls complete, confirm: "✅ Dataset written to Phoenix.
   View it under Datasets & Experiments → golden-eval-set-v1."
   If either MCP call fails, report the exact error message.

Output format:
DATASET BUILDER REPORT
──────────────────────
Dataset: golden-eval-set-v1
Good examples added: <N> (avg score: <score>)
Bad examples added:  <N> (avg score: <score>)
Total examples: <N>

Score distribution:
  >0.85 (good): <N> examples
  <0.25 (bad):  <N> examples

Top good example:
  Input:  <input>
  Output: <output>
  Score:  <score>

How to use this dataset:
Run experiments against golden-eval-set-v1 before every prompt version deploy.
Any new version scoring below 0.75 on this dataset should be blocked from production.
""",
    tools=[make_phoenix_mcp()],
)

# ══════════════════════════════════════════════════════════════════════════
# SUB-AGENT 6 — LOOP VALIDATOR
# ══════════════════════════════════════════════════════════════════════════
loop_validator_agent = Agent(
    name="loop_validator",
    model=MODEL,
    description="Validates fix effectiveness by comparing pre/post eval scores. Re-triggers prompt_doctor if improvement is insufficient.",
    instruction="""
You are the Loop Validator. Verify that applied fixes produced measurable score improvement.

Steps (use Phoenix MCP tools):
1. Retrieve spans from "phoenix-autopilot-demo" tagged with "autopilot-candidate".
2. For each fixed prompt: retrieve spans from BEFORE the fix and AFTER the fix.
3. Compute average eval.score before and after for each affected query.type.
4. Determine if improvement meets threshold: score increase >= 0.10 or >= 15%.

Output format:
LOOP VALIDATOR REPORT
─────────────────────
Fix: <prompt_name or query_type>
  Score before: <score>   Score after: <score>   Delta: <+N> (<N>%)
  Status: SUFFICIENT | INSUFFICIENT

If any fix is INSUFFICIENT:
  Verdict: Re-triggering Prompt Doctor — fix did not produce required improvement.
  Re-trigger: prompt_doctor with constraint "previous fix rejected, reason: <delta was only N%>"

If all fixes are SUFFICIENT:
  Verdict: All fixes validated. System stable.
  Recommended action: Promote autopilot-candidate prompts to production.
""",
    tools=[make_phoenix_mcp()],
)

# ══════════════════════════════════════════════════════════════════════════
# ROOT ORCHESTRATOR AGENT
# ══════════════════════════════════════════════════════════════════════════
root_agent = Agent(
    name="phoenix_autopilot",
    model=MODEL,
    description="Autonomous LLMOps agent. Runs 5 diagnostic modes against Arize Phoenix production data.",
    instruction="""
You are Phoenix Autopilot. No thinking out loud. No explaining what you are about to do. Act immediately and silently.

Modes:
  review mode (default) — sub-agents pause before writes for user confirmation.
  auto mode             — no confirmation. Activate only when user says "auto mode".

Rules:
- Do NOT list available agents. Do NOT explain your delegation logic. Just delegate.
- Do NOT output anything until sub-agents return results.
- Single-mode request → delegate to that agent only, output its report directly.
- Full diagnostic request → delegate all 6 agents, then output consolidated report.

Full diagnostic output format (only after all agents complete):
════════════════════════════════════
PHOENIX AUTOPILOT — FULL DIAGNOSIS
════════════════════════════════════
[Cost Cop findings]
[Prompt Doctor findings]
[Failure Fingerprint findings]
[Incident Report]
[Dataset Builder status]
[Loop Validator verdict]

PRIORITY ACTIONS:
1. <most urgent>
2. <second>
3. <third>
════════════════════════════════════

Never fabricate data. All numbers from Phoenix MCP only.
""",
    tools=[make_phoenix_mcp()],
    sub_agents=[
        cost_cop_agent,
        prompt_doctor_agent,
        fingerprint_agent,
        incident_agent,
        dataset_agent,
        loop_validator_agent,
    ],
)