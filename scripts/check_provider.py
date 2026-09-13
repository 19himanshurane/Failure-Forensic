"""Verify your API key works and list the models the provider currently serves.

Model IDs get deprecated regularly, so check against the live list rather than
trusting a hard-coded default.

    python scripts/check_provider.py
"""

import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, "src")
from forensics.llm import PROVIDERS, LLMRequest, build_live_client  # noqa: E402

load_dotenv()

provider = os.getenv("FF_LLM_PROVIDER", "groq")
base_url, key_var, default_model = PROVIDERS[provider]

if not os.getenv(key_var):
    sys.exit(f"{key_var} is not set. Put it in your .env file (copy .env.example).")

client = build_live_client(provider)
print(f"provider={provider}  base_url={base_url}  model={client.model}\n")

try:
    models = sorted(m.id for m in client._client.models.list().data)
    print(f"{len(models)} models available:")
    for m in models:
        print("  ", m)
except Exception as exc:
    print(f"could not list models: {exc}")

print("\nsending a test request...")
resp = client.complete(LLMRequest(
    tag="smoke", system="Reply with JSON only.",
    user='Return {"ok": true} and nothing else.', model=client.model, max_tokens=50,
))
print(f"  text     : {resp.text.strip()[:120]}")
print(f"  model    : {resp.model}")
print(f"  tokens   : {resp.prompt_tokens} in / {resp.completion_tokens} out")
print(f"  latency  : {resp.latency_ms} ms")
