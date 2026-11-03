// Built during the Meta Global AI Developer Hackathon (new work).
// A parameter's "howToDoIt" arrives from the backend as "Step 1 — ... Step 2 — ..." (one marker per step, quote-aware, normalised in
// backend/app/studio/steps.py). Splitting at those markers only keeps a quoted phrase, a decimal or an ellipsis from ever being cut
// into two bullets (the older sentence splitter in Results.tsx did that), and keeps an unnumbered line from turning into a step.
const MARKER = /(?:^|\s)Step\s*\d+\s*[—–:\-]\s*/gi

/** The steps of a text that uses "Step N —" markers, or null when it does not (the caller then falls back to its sentence splitter). */
export function parseMarkedSteps(text: string): string[] | null {
  if (!text || !/(?:^|\s)Step\s*\d+\s*[—–:\-]/i.test(text)) return null
  const parts = text.split(MARKER).map(s => s.trim()).filter(Boolean)
  return parts.length ? parts : null
}
