import { useState } from "react";
import { STATUS_META, CATEGORY_LABEL } from "../constants";

const SAMPLE = {
  raw_text:
    "INVOICE 2201\nFrom: Acme Corp\nBill to: Globex Ltd\n\nConsulting services, Q1 2026.\n" +
    "Amount due: $4,500.00\nPayment terms: net 30. Due 2026-04-01.\n\nRegards,\nPriya Sharma\n",
  source_name: "invoice.txt",
};

export function Sidebar({ runs, selectedId, onSelect, onCreateRun, creating, evalCases }) {
  const [rawText, setRawText] = useState(SAMPLE.raw_text);
  const [sourceName, setSourceName] = useState(SAMPLE.source_name);
  const [mode, setMode] = useState("mock");
  const [chaos, setChaos] = useState("");

  function submit(e) {
    e.preventDefault();
    onCreateRun(rawText, sourceName || "document.txt", mode, chaos);
  }

  return (
    <aside className="rail">
      <div className="rail-head">
        <span className="eyebrow">Failure Forensics</span>
        <h1>Trace Explorer</h1>
        <p className="sub">Live against the FastAPI backend: run any document through the real pipeline.</p>
      </div>

      <form className="new-run" onSubmit={submit}>
        <span className="eyebrow">New run</span>
        <textarea
          value={rawText}
          onChange={(e) => setRawText(e.target.value)}
          rows={5}
          placeholder="Paste a document to run through the pipeline..."
        />
        <div className="new-run-row">
          <input
            value={sourceName}
            onChange={(e) => setSourceName(e.target.value)}
            placeholder="source_name.txt"
          />
          <select value={mode} onChange={(e) => setMode(e.target.value)}>
            <option value="mock">mock</option>
            <option value="replay">replay</option>
            <option value="live">live</option>
          </select>
        </div>
        {mode === "mock" && (
          <select value={chaos} onChange={(e) => setChaos(e.target.value)} className="chaos-select">
            <option value="">no chaos</option>
            <option value="hallucinate">hallucinate</option>
            <option value="misclassify">misclassify</option>
            <option value="drop_context">drop_context</option>
            <option value="bad_json">bad_json</option>
          </select>
        )}
        <button type="submit" className="btn-primary" disabled={creating || !rawText.trim()}>
          {creating ? "Running…" : "Run pipeline"}
        </button>
      </form>

      <div className="case-list">
        {runs.length === 0 && <p className="empty-note">No runs yet. Submit a document above.</p>}
        {runs.map((r) => {
          const meta = STATUS_META[r.status] ?? { label: r.status, cls: "neutral" };
          return (
            <button
              key={r.trace_id}
              type="button"
              className={"case-row" + (r.trace_id === selectedId ? " selected" : "")}
              onClick={() => onSelect(r.trace_id)}
            >
              <span className={"dot " + meta.cls} />
              <span className="txt">
                <span className="name">{r.source_name}</span>
                <span className="note">{r.category ? CATEGORY_LABEL[r.category] : meta.label}</span>
              </span>
            </button>
          );
        })}
      </div>

      <div className="rail-foot">
        <div className="rail-foot-head">
          <span className="eyebrow">Flagged into eval set</span>
          <span className="count-pill">{evalCases.length}</span>
        </div>
        <div className="flag-log">
          {evalCases.length === 0 && <p className="empty-note">No cases flagged yet.</p>}
          {evalCases.slice().reverse().map((c) => (
            <div className="flag-item" key={c.case_id}>
              <span className="cat">{CATEGORY_LABEL[c.category] ?? c.category}</span>
              <span className="src">{c.source_name}</span>
            </div>
          ))}
        </div>
      </div>
    </aside>
  );
}
