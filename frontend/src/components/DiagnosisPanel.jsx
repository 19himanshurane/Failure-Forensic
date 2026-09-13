import { useState } from "react";
import { CATEGORY_LABEL, diffFacts } from "../constants";

const PILL_LABEL = { kept: "in summary", dropped: "missing", hallucinated: "not in source", low: "low confidence" };

export function DiagnosisPanel({ trace, diagnosis, onFlag }) {
  const [open, setOpen] = useState(false);
  const [category, setCategory] = useState(diagnosis?.category ?? Object.keys(CATEGORY_LABEL)[0]);
  const [note, setNote] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [error, setError] = useState(null);

  function startFlag() {
    setCategory(diagnosis?.category ?? Object.keys(CATEGORY_LABEL)[0]);
    setNote("");
    setError(null);
    setConfirmed(false);
    setOpen(true);
  }

  async function submit(e) {
    e.preventDefault();
    try {
      await onFlag(category, note.trim() ? { note: note.trim() } : null);
      setOpen(false);
      setConfirmed(true);
    } catch (err) {
      setError(err.message);
    }
  }

  const badgeCls = trace.status === "failure" ? "critical" : "warn";
  const rows = trace.result ? diffFacts(trace.result) : [];

  return (
    <div className="panel diagnosis">
      <div className="panel-head">
        <span className="eyebrow">Root-cause diagnosis</span>
        {diagnosis && (
          <span className={"category-badge " + badgeCls}>
            {CATEGORY_LABEL[diagnosis.category]} — step: {diagnosis.step}
          </span>
        )}
      </div>

      <p className="explanation">
        {diagnosis
          ? diagnosis.explanation
          : "No issues found — every step's output was grounded, confidently classified, and fully carried forward into the summary."}
      </p>
      {diagnosis && diagnosis.evidence.length > 0 && (
        <ul className="evidence-list">
          {diagnosis.evidence.map((e, i) => (
            <li key={i}>{e}</li>
          ))}
        </ul>
      )}

      {rows.length > 0 && (
        <div className="diff">
          <span className="eyebrow">Extraction → final summary</span>
          <div className="diff-rows">
            {rows.map((r, i) => (
              <div className="diff-row" key={i}>
                <span className="grp">{r.group.replace("_", " ")}</span>
                <span className="val">{r.value}</span>
                <span className={"state-pill " + r.state}>{PILL_LABEL[r.state]}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="flag-area">
        {!open && <button type="button" className="btn-flag" onClick={startFlag}>Flag as bad output</button>}
        {open && (
          <form className="flag-form" onSubmit={submit}>
            <label className="field">
              <span>Failure category</span>
              <select value={category} onChange={(e) => setCategory(e.target.value)}>
                {Object.entries(CATEGORY_LABEL).map(([k, v]) => (
                  <option key={k} value={k}>{v}</option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Correction notes (optional)</span>
              <textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)}
                placeholder="What should the correct output have looked like?" />
            </label>
            {error && <p className="form-error">{error}</p>}
            <div className="form-actions">
              <button type="submit" className="btn-primary">Confirm &amp; add to eval set</button>
              <button type="button" className="btn-ghost" onClick={() => setOpen(false)}>Cancel</button>
            </div>
          </form>
        )}
        {confirmed && !open && <p className="flag-confirmed">Added to the eval set.</p>}
      </div>
    </div>
  );
}
