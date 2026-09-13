import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import { Sidebar } from "./components/Sidebar";
import { FlowStrip } from "./components/FlowStrip";
import { EvidencePanel } from "./components/EvidencePanel";
import { DiagnosisPanel } from "./components/DiagnosisPanel";
import { STATUS_META } from "./constants";

export default function App() {
  const [runs, setRuns] = useState([]);
  const [evalCases, setEvalCases] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [trace, setTrace] = useState(null);
  const [diagnosis, setDiagnosis] = useState(null);
  const [selectedStep, setSelectedStep] = useState(null);
  const [creating, setCreating] = useState(false);
  const [apiError, setApiError] = useState(null);

  const refreshLists = useCallback(async () => {
    const [runList, cases] = await Promise.all([api.listRuns(), api.listEvalCases()]);
    setRuns(runList);
    setEvalCases(cases);
  }, []);

  useEffect(() => {
    refreshLists().catch((e) => setApiError(e.message));
  }, [refreshLists]);

  const selectRun = useCallback(async (traceId) => {
    setSelectedId(traceId);
    setApiError(null);
    try {
      const [t, d] = await Promise.all([api.getRun(traceId), api.getDiagnosis(traceId)]);
      setTrace(t);
      setDiagnosis(d);
      setSelectedStep(d ? d.step : "summarization");
    } catch (e) {
      setApiError(e.message);
    }
  }, []);

  async function createRun(rawText, sourceName, mode, chaos) {
    setCreating(true);
    setApiError(null);
    try {
      const created = await api.createRun(rawText, sourceName, mode, chaos);
      await refreshLists();
      await selectRun(created.trace_id);
    } catch (e) {
      setApiError(e.message);
    } finally {
      setCreating(false);
    }
  }

  async function flagCurrent(category, correctedOutput) {
    await api.flagRun(selectedId, { category, corrected_output: correctedOutput });
    await refreshLists();
  }

  return (
    <div className="app">
      <Sidebar
        runs={runs}
        selectedId={selectedId}
        onSelect={selectRun}
        onCreateRun={createRun}
        creating={creating}
        evalCases={evalCases}
      />
      <main className="stage">
        {apiError && <div className="api-error">{apiError}</div>}
        {!trace && !apiError && (
          <div className="empty-stage">
            <p>Run a document through the pipeline to see its trace here.</p>
          </div>
        )}
        {trace && (
          <>
            <div className="stage-head">
              <div>
                <span className="eyebrow">{trace.source_name}</span>
                <h2>{trace.doc_id ? `Trace ${trace.trace_id.slice(0, 8)}` : "Trace"}</h2>
              </div>
              <span className={"status-chip " + (STATUS_META[trace.status]?.cls ?? "neutral")}>
                {STATUS_META[trace.status]?.label ?? trace.status}
              </span>
            </div>
            <FlowStrip trace={trace} diagnosis={diagnosis} selectedStep={selectedStep} onSelectStep={setSelectedStep} />
            <section className="workbench">
              <EvidencePanel trace={trace} step={selectedStep} />
              <DiagnosisPanel trace={trace} diagnosis={diagnosis} onFlag={flagCurrent} />
            </section>
          </>
        )}
      </main>
    </div>
  );
}
