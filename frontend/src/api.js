// Local dev and the nginx-fronted Docker setup both route same-origin /api
// requests to the backend (see vite.config.js's proxy and frontend/nginx.conf),
// so no env var is needed there. A static host (Render, Netlify, ...) serves
// only the built files with nothing to proxy through, so VITE_API_BASE -- set
// at build time -- points straight at the deployed API's own URL instead.
const BASE = import.meta.env.VITE_API_BASE || "/api";

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
