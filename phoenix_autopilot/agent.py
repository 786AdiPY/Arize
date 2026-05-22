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
from openinference.instrumentation.google_adk import GoogleADKInstrumentor

tracer_provider = register(project_name="phoenix-autopilot", auto_instrument=True)
GoogleADKInstrumentor().instrument(tracer_provider=tracer_provider)

# ── ADK + MCP ─────────────────────────────────────────────────────────────
from google.adk.agents import Agent
from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset, StdioServerParameters

MODEL = "gemini-2.5-flash"

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

Output format — be terse, numbers only, no prose explanation:
COST COP REPORT
───────────────
#1  <query.type>
    Tokens: <N> in / <N> out  |  Ratio: <N>:1  |  Save: ~<N>%
    Cause: <5 words max>
    Fix:   <1 sentence, actionable>

#2  <query.type>
    Tokens: <N> in / <N> out  |  Ratio: <N>:1  |  Save: ~<N>%
    Cause: <5 words max>
    Fix:   <1 sentence, actionable>

#3  <query.type>
    Tokens: <N> in / <N> out  |  Ratio: <N>:1  |  Save: ~<N>%
    Cause: <5 words max>
    Fix:   <1 sentence, actionable>

TOP ROI: <query.type> — fix this first, saves most tokens per effort.

COST FORECAST (30-day projection)
──────────────────────────────────
Observation window: <N> days  |  Daily tokens: <N>
Current trajectory:  $<X>/month  (<N>M input tokens + <N>M output tokens)
After fixes applied: $<X>/month  (~<N>% reduction)
Monthly savings:     $<X>/month
⚠ At current growth rate, token spend reaches $<X>/month in 30 days if unfixed.
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
You are the Prompt Doctor. Diagnose prompt version regression using Phoenix trace data.

Steps (use Phoenix MCP tools):
1. Retrieve traces from "phoenix-autopilot-demo".
2. Group spans by prompt.version. Compute average eval.score per version.
3. PREVIOUS = version with highest average score. CURRENT = version with lowest average score.
4. Retrieve 2 representative output examples from each version.
5. Identify the single instruction change or omission responsible for the score drop.
6. Prescribe one precise, clause-level correction. Full rewrites are not acceptable.

Output format — one value per field, no elaboration, no hedging:
PROMPT DOCTOR REPORT
────────────────────
Previous version: <version>   Average score: <score>
Current version:  <version>   Average score: <score>   Regression: <delta> (<N>%)

Previous (passing)
  ✓ Input: "<input>"  →  Output: "<output>"
  ✓ Input: "<input>"  →  Output: "<output>"

Current (failing)
  ✗ Input: "<input>"  →  Output: "<output>"
  ✗ Input: "<input>"  →  Output: "<output>"

Root Cause
  Instruction removed or weakened: "<exact phrase>"
  Resulting failure mode: "<one sentence — specific, observable behaviour>"

Required Fix
  Remove:  "<exact phrase that must be changed>"
  Replace: "<precise replacement — one clause, directly addresses the failure mode>"

Projected score after fix: <score>
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
5. Use Phoenix MCP to create a dataset named "golden-eval-set-v1" if it doesn't exist.
6. Add the examples to the dataset with labels: "good" or "bad".

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
# ROOT ORCHESTRATOR AGENT
# ══════════════════════════════════════════════════════════════════════════
root_agent = Agent(
    name="phoenix_autopilot",
    model=MODEL,
    description="Autonomous LLMOps agent. Runs 5 diagnostic modes against Arize Phoenix production data.",
    instruction="""
You are Phoenix Autopilot — an autonomous LLMOps orchestrator.

When triggered (e.g. "something is wrong", "run diagnostics", "fix it"):
1. Delegate to cost_cop          → get cost analysis
2. Delegate to prompt_doctor     → get prompt fix
3. Delegate to failure_fingerprint → get failure clusters
4. Delegate to incident_summarizer → get incident report
5. Delegate to dataset_builder   → build golden dataset

After all 5 complete, output a single CONSOLIDATED REPORT:

════════════════════════════════════
PHOENIX AUTOPILOT — FULL DIAGNOSIS
════════════════════════════════════
[paste Cost Cop findings]
[paste Prompt Doctor findings]
[paste Failure Fingerprint findings]
[paste Incident Report]
[paste Dataset Builder status]

PRIORITY ACTIONS:
1. <most urgent fix>
2. <second fix>
3. <third fix>
════════════════════════════════════

If user asks about only one mode (e.g. "check costs"), delegate to that agent only.
Never fabricate data — all numbers must come from Phoenix MCP queries.
""",
    tools=[make_phoenix_mcp()],
    sub_agents=[
        cost_cop_agent,
        prompt_doctor_agent,
        fingerprint_agent,
        incident_agent,
        dataset_agent,
    ],
)