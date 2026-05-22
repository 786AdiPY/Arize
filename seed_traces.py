"""
seed_traces_final.py — Phoenix Autopilot demo seeder
Sends spans one-by-one with a small delay — stable on free tier.
Run ONCE before demo: python seed_traces_final.py
"""

import os
import time
import random
import uuid
from dotenv import load_dotenv
load_dotenv()

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # one-by-one, no batch gateway drop
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource

PHOENIX_ENDPOINT = os.getenv("PHOENIX_COLLECTOR_ENDPOINT")
PHOENIX_API_KEY  = os.getenv("PHOENIX_API_KEY")

# OpenInference attribute keys — hardcoded, no semconv dep needed
INPUT_VALUE                = "input.value"
OUTPUT_VALUE               = "output.value"
LLM_MODEL_NAME             = "llm.model_name"
LLM_TOKEN_COUNT_PROMPT     = "llm.token_count.prompt"
LLM_TOKEN_COUNT_COMPLETION = "llm.token_count.completion"
LLM_TOKEN_COUNT_TOTAL      = "llm.token_count.total"

def build_provider(project: str) -> TracerProvider:
    resource = Resource({
        "service.name": project,
        "openinference.project.name": project,
    })
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(
        endpoint=PHOENIX_ENDPOINT,
        headers={
            "Authorization": f"Bearer {PHOENIX_API_KEY}",
            "api_key": PHOENIX_API_KEY,
        },
        timeout=30,                   # generous timeout per span
    )
    provider.add_span_processor(SimpleSpanProcessor(exporter))  # one-by-one export
    return provider

def r(a, b):
    return random.randint(a, b)

# Reduced counts — enough for demo, won't hammer free tier
SCENARIOS = [
    {
        "name": "healthy_baseline",
        "count": 20,                  # baseline healthy traces
        "prompt_version": "v1.0.0",
        "query_type": "general_qa",
        "input_tokens": (80, 150),
        "output_tokens": (60, 120),
        "eval_score": (0.78, 0.95),
        "note": "baseline",
    },
    {
        "name": "order_status_bloat",
        "count": 15,                  # MODE 1 — Cost Cop
        "prompt_version": "v1.2.0",
        "query_type": "order_status",
        "input_tokens": (2800, 3400),
        "output_tokens": (40, 80),
        "eval_score": (0.75, 0.88),
        "note": "cost_cop_target",
    },
    {
        "name": "bad_prompt_v2_3_1",
        "count": 15,                  # MODE 2 — Prompt Doctor
        "prompt_version": "v2.3.1",
        "query_type": "product_summary",
        "input_tokens": (200, 400),
        "output_tokens": (100, 200),
        "eval_score": (0.18, 0.42),
        "note": "prompt_doctor_target",
    },
    {
        "name": "date_range_failures",
        "count": 15,                  # MODE 3 — Failure Fingerprint
        "prompt_version": "v1.2.0",
        "query_type": "date_range_query",
        "input_tokens": (150, 300),
        "output_tokens": (80, 160),
        "eval_score": (0.08, 0.28),
        "note": "fingerprint_target",
    },
    {
        "name": "incident_spike",
        "count": 10,                  # MODE 4 — Incident Summarizer
        "prompt_version": "v2.3.1",
        "query_type": "financial_report",
        "input_tokens": (300, 600),
        "output_tokens": (150, 300),
        "eval_score": (0.05, 0.25),
        "note": "incident_target",
    },
    {
        "name": "golden_examples",
        "count": 15,                  # MODE 5 — Dataset Builder
        "prompt_version": "v1.0.0",
        "query_type": "general_qa",
        "input_tokens": (100, 200),
        "output_tokens": (80, 160),
        "eval_score": (0.88, 0.98),
        "note": "dataset_builder_target",
    },
]

QUERIES = {
    "order_status":     ["Where is my order #{id}?", "Track order #{id}", "Order #{id} status?"],
    "product_summary":  ["Summarize product {id}", "What is product {id}?"],
    "date_range_query": ["Sales from Jan 2024 to Mar 2024", "Data from 01/01/2024 to 03/31/2024"],
    "financial_report": ["Generate Q3 earnings summary", "Revenue breakdown last quarter"],
    "general_qa":       ["What is the return policy?", "How do I reset my password?"],
}

RESPONSES = {
    "order_status":     "Your order is processing and ships in 3-5 days.",
    "product_summary":  "This product features advanced specs for professional use.",
    "date_range_query": "Sales data shows overall growth trajectory.",
    "financial_report": "Financial report shows revenue of $X million.",
    "general_qa":       "Returns accepted within 30 days with receipt.",
}

def seed():
    print("\nPhoenix Autopilot — Demo Trace Seeder")
    print("=" * 42)
    print(f"Endpoint : {PHOENIX_ENDPOINT}")
    print(f"API Key  : {PHOENIX_API_KEY[:16]}..." if PHOENIX_API_KEY else "API Key  : MISSING")

    if not PHOENIX_ENDPOINT or not PHOENIX_API_KEY:
        print("\nERROR: Check .env file — both vars required.")
        return

    provider = build_provider("phoenix-autopilot-demo")
    tracer   = trace.get_tracer("seed_tracer", tracer_provider=provider)
    total    = sum(s["count"] for s in SCENARIOS)
    done     = 0

    for scenario in SCENARIOS:
        qtype = scenario["query_type"]
        print(f"\n[{scenario['name']}] sending {scenario['count']} spans...")

        for i in range(scenario["count"]):
            query    = random.choice(QUERIES.get(qtype, ["Generic query"])).replace("{id}", str(r(1000, 9999)))
            response = RESPONSES.get(qtype, "Response generated.")
            in_tok   = r(*scenario["input_tokens"])
            out_tok  = r(*scenario["output_tokens"])
            score    = round(random.uniform(*scenario["eval_score"]), 2)

            with tracer.start_as_current_span(f"llm.{qtype}") as span:
                span.set_attribute(INPUT_VALUE,                query)
                span.set_attribute(OUTPUT_VALUE,               response)
                span.set_attribute(LLM_MODEL_NAME,             "gemini-2.0-flash")
                span.set_attribute(LLM_TOKEN_COUNT_PROMPT,     in_tok)
                span.set_attribute(LLM_TOKEN_COUNT_COMPLETION, out_tok)
                span.set_attribute(LLM_TOKEN_COUNT_TOTAL,      in_tok + out_tok)
                span.set_attribute("prompt.version",           scenario["prompt_version"])
                span.set_attribute("query.type",               qtype)
                span.set_attribute("eval.score",               score)
                span.set_attribute("scenario.note",            scenario["note"])
                span.set_attribute("session.id",               str(uuid.uuid4()))
                if score < 0.3:
                    span.set_attribute("alert.triggered", True)
                    span.set_attribute("alert.reason",    "low_eval_score")

            done += 1
            print(f"  {done}/{total} — {qtype} | tokens: {in_tok+out_tok} | score: {score}")
            time.sleep(0.15)          # 150ms between spans — prevents gateway overload

    provider.shutdown()

    print(f"\nDone. {done}/{total} traces in project: phoenix-autopilot-demo")
    print(f"Dashboard: {os.getenv('PHOENIX_BASE_URL')}/projects")
    print("\nExpect to see in Phoenix:")
    print("  order_status   — 2800-3400 token bloat   [Cost Cop]")
    print("  v2.3.1 traces  — eval score 0.18-0.42    [Prompt Doctor]")
    print("  date_range     — eval score 0.08-0.28    [Failure Fingerprint]")
    print("  financial      — eval score 0.05-0.25    [Incident]")
    print("  general_qa     — eval score 0.88-0.98    [Golden Dataset]")

if __name__ == "__main__":
    seed()