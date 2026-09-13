// Every call goes through /api, which vite.config.js proxies to the FastAPI
// backend in dev; a production build behind a real reverse proxy would map
// /api the same way. No client ever needs to know the backend's own port.
const BASE = "/api";

async function request(path, options) {
  const res = await fetch(BASE + path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `${res.status} ${res.statusText}`);
  }
  if (res.status === 204) return null;
  return res.json();
}

export const api = {
  health: () => request("/health"),
  listRuns: () => request("/runs"),
  createRun: (raw_text, source_name, mode, chaos) =>
    request("/runs", { method: "POST", body: JSON.stringify({ raw_text, source_name, mode, chaos }) }),
  getRun: (traceId) => request(`/runs/${traceId}`),
  getDiagnosis: (traceId) => request(`/runs/${traceId}/diagnosis`),
  flagRun: (traceId, { category, corrected_output } = {}) =>
    request(`/runs/${traceId}/flag`, {
      method: "POST",
      body: JSON.stringify({ category: category ?? null, corrected_output: corrected_output ?? null }),
    }),
  listEvalCases: () => request("/eval-cases"),
  analytics: () => request("/analytics"),
};
