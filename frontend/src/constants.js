export const STEP_ORDER = ["intake", "extraction", "classification", "summarization"];

export const STEP_LABEL = {
  intake: "Intake",
  extraction: "Extraction",
  classification: "Classification",
  summarization: "Summarization",
};

export const STEP_ROLE = {
  intake: "raw text → Document",
  extraction: "Document → entities",
  classification: "entities → document type",
  summarization: "everything → summary",
};

export const CATEGORY_LABEL = {
  extraction_hallucination: "Extraction hallucination",
  misclassification: "Misclassification",
  propagation_error: "Propagation error",
  prompt_failure: "Prompt failure",
  context_loss: "Context loss",
};

export const STATUS_META = {
  success: { label: "Success", cls: "good" },
  degraded: { label: "Degraded", cls: "warn" },
  failure: { label: "Failure", cls: "critical" },
};

export function findSpan(trace, step) {
  return trace.spans.find((s) => s.step === step) ?? null;
}

export function nodeStatus(trace, diagnosis, step) {
  const span = findSpan(trace, step);
  if (!span) return "pending";
  if (!span.ok) return "critical";
  if (diagnosis && diagnosis.step === step) return "critical";
  if (diagnosis && diagnosis.category === "propagation_error") {
    const idx = STEP_ORDER.indexOf(diagnosis.step);
    if (idx > 0 && STEP_ORDER[idx - 1] === step) return "warn";
  }
  return "good";
}

function norm(s) {
  return (s || "").split(/\s+/).join(" ").toLowerCase();
}

export function diffFacts(result) {
  const haystack = norm(result.summary.headline + " " + result.summary.bullets.join(" "));
  const doc = norm(result.document.raw_text);
  const groups = ["people", "organizations", "dates", "amounts", "key_terms"];
  const rows = [];
  for (const g of groups) {
    for (const e of result.extraction[g] || []) {
      const grounded = doc.includes(norm(e.source_quote));
      const kept = haystack.includes(norm(e.value));
      let state;
      if (!grounded) state = "hallucinated";
      else if (kept) state = "kept";
      else if (e.confidence >= 3) state = "dropped";
      else state = "low";
      rows.push({ group: g, value: e.value, confidence: e.confidence, state });
    }
  }
  return rows;
}

export function fmtMs(ms) {
  if (ms === null || ms === undefined) return "—";
  return (ms < 1 ? ms.toFixed(2) : ms.toFixed(1)) + "ms";
}
