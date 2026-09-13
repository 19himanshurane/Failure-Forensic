import { STEP_ORDER, STEP_LABEL, STEP_ROLE, nodeStatus, findSpan, fmtMs } from "../constants";

function ConfBadge({ span, status }) {
  if (!span) return <span className="badge neutral">—</span>;
  if (span.confidence !== null && span.confidence !== undefined) {
    const cls = status === "critical" ? "critical" : status === "warn" ? "warn" : "neutral";
    return <span className={"badge " + cls}>conf {span.confidence}</span>;
  }
  if (!span.model) return <span className="badge neutral">no LLM</span>;
  return <span className="badge critical">no output</span>;
}

function Connector() {
  return (
    <div className="connector">
      <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
        <path d="M2 8h10M8 3l5 5-5 5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </div>
  );
}

export function FlowStrip({ trace, diagnosis, selectedStep, onSelectStep }) {
  return (
    <section className="flow" aria-label="Pipeline steps">
      {STEP_ORDER.map((step, i) => {
        const span = findSpan(trace, step);
        const status = nodeStatus(trace, diagnosis, step);
        return (
          <div key={step} style={{ display: "contents" }}>
            {i > 0 && <Connector />}
            <button
              type="button"
              className={"node " + status + (selectedStep === step ? " selected" : "")}
              disabled={!span}
              onClick={() => onSelectStep(step)}
            >
              <span className="step-name">{STEP_LABEL[step]}</span>
              <span className="step-role">{STEP_ROLE[step]}</span>
              <span className="meta-row">
                <ConfBadge span={span} status={status} />
                <span className="badge neutral">{span ? fmtMs(span.latency_ms) : "did not run"}</span>
              </span>
            </button>
          </div>
        );
      })}
    </section>
  );
}
