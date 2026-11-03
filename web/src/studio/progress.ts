// Hackathon-built (Meta Global AI Developer Hackathon).
// The original analysis pipeline reports the real stage of a job in the `progress` field of /prysm/analyse/status/{job}.
// These helpers turn that into labels and counts for the processing screen (only used when VITE_STUDIO_MODE=1).

export type StageState = 'pending' | 'running' | 'done' | 'skipped'

export const STUDIO_STAGES: { id: string; label: string; core: boolean }[] = [
  { id: 'frames', label: 'Preparing frames and audio', core: true },
  { id: 'vision', label: 'Watching the video', core: true },
  { id: 'speech', label: 'Transcribing the speech', core: true },
  { id: 'consensus', label: 'Cross-checking the transcript', core: false },
  { id: 'subjects', label: 'Spotting people and objects', core: false },
  { id: 'audio', label: 'Listening to the audio', core: true },
  { id: 'gpu_signals', label: 'Measuring deeper visual and audio signals', core: false },
  { id: 'music_id', label: 'Identifying the music', core: true },
  { id: 'signals', label: 'Reading visual and audio signals', core: true },
  { id: 'trends', label: "Checking what's trending now", core: true },
  { id: 'scoring', label: 'Scoring the 10 parameters', core: true },
  { id: 'report', label: 'Writing your report and fixes', core: true },
]

/** The steps to list: the always-present ones, plus optional ones (extra evidence) only when the backend reports them. */
export function visibleStages(progress: Progress | null | undefined) {
  return STUDIO_STAGES.filter(s => s.core || (progress && progress[s.id] !== undefined))
}

export type Progress = Record<string, string>

export function stageState(progress: Progress | null | undefined, id: string): StageState {
  const v = progress?.[id]
  if (v === 'running') return 'running'
  if (v === 'ok' || v === 'cached') return 'done'
  if (v === 'pending') return 'pending'
  if (v) return 'skipped' // error, skipped: the stage did not contribute but the job carries on
  return 'pending'
}

export function doneCount(progress: Progress | null | undefined): number {
  return visibleStages(progress).filter(s => ['done', 'skipped'].includes(stageState(progress, s.id))).length
}

/** The sentence for the status line: what is running now (or the next stage while one finishes). */
export function currentLabel(progress: Progress | null | undefined): string {
  const running = visibleStages(progress).filter(s => stageState(progress, s.id) === 'running')
  if (running.length) return running.map(s => s.label).join(' · ') + '...'
  const next = visibleStages(progress).find(s => stageState(progress, s.id) === 'pending')
  return next ? next.label + '...' : 'Finishing up...'
}
