import { useEffect, useState } from "react";
import { STEP_LABEL, findSpan } from "../constants";

export function EvidencePanel({ trace, step }) {
  const span = findSpan(trace, step);
  const [tab, setTab] = useState("output");

  const tabs = span
    ? [
        { id: "input", label: "Input", has: !!span.input },
        { id: "prompt", label: "Prompt", has: !!span.prompt },
        { id: "output", label: "Output", has: span.output !== null && span.output !== undefined },
        { id: "raw", label: "Raw response", has: !!span.raw_response },
      ]
    : [];

  useEffect(() => {
    if (!tabs.some((t) => t.id === tab && t.has)) {
      const first = tabs.find((t) => t.has);
      setTab(first ? first.id : "input");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step, trace.trace_id]);

  if (!span) return null;

  let content = "";
  if (tab === "input") content = span.input ? JSON.stringify(span.input, null, 2) : "(no input captured for this step)";
  else if (tab === "prompt") content = span.prompt || "(no LLM call for this step)";
  else if (tab === "output")
    content =
      span.output !== null && span.output !== undefined
        ? JSON.stringify(span.output, null, 2)
        : span.error_message || "(no output — step failed)";
  else if (tab === "raw") content = span.raw_response || "(no raw response)";

  const metaParts = [];
  if (span.model) metaParts.push(["model", span.model]);
  if (span.source) metaParts.push(["source", span.source]);
  metaParts.push(["latency", (span.latency_ms < 1 ? span.latency_ms.toFixed(2) : span.latency_ms.toFixed(1)) + "ms"]);
  if (span.prompt_tokens || span.completion_tokens) metaParts.push(["tokens", `${span.prompt_tokens} in / ${span.completion_tokens} out`]);
  if (span.cache_key) metaParts.push(["cache_key", span.cache_key]);

  return (
    <div className="panel evidence">
      <div className="panel-head">
        <span className="eyebrow">{STEP_LABEL[step]}{!span.ok && " — failed"}</span>
        <div className="tabs">
          {tabs.map((t) => (
            <button
              key={t.id}
              type="button"
              className={"tab-btn" + (tab === t.id ? " active" : "")}
              disabled={!t.has}
              onClick={() => setTab(t.id)}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>
      <div className="meta-strip">
        {metaParts.map(([k, v]) => (
          <span key={k}>{k} <b>{v}</b></span>
        ))}
      </div>
      <pre className="mono-block">{content}</pre>
    </div>
  );
}
