// Built during the Meta Global AI Developer Hackathon (new work).
// Shapes returned by the /instagram API (see backend/app/studio/instagram/router.py).
export type Fingerprint = {
  hook_type: string; format: string; pacing: string; audio_type: string; on_screen_text: boolean; topic: string; hook_words?: string | null
}
export type VsUsual = { pct: number; direction: 'above' | 'below' | 'same'; label: string }
export type Reel = {
  n: number; id: string; media_id?: string; permalink: string | null; postedAt: string | null; caption: string | null; duration_s: number | null
  performance_index: number | null; performance_basis: string | null; watch_through: number | null; fingerprint: Fingerprint | null
  frames_used: number; note: string | null; like_count?: number | null; comments_count?: number | null
  analysis_level?: 'full' | 'thumbnail_caption' | 'caption_only'; partial?: boolean; partial_reason?: string | null
  thumbnailUrl?: string | null; insights?: Record<string, number | null>; vs_usual?: VsUsual | null
  hook_words?: string | null; hook_words_status?: 'transcribed' | 'no_speech' | 'unknown' | null
}
export type Claim = { claim: string; reels: number[]; media_ids?: string[] }
export type DnaData = {
  mode: 'full' | 'descriptive' | 'breakdown'; reason?: string; strongest: Claim[]; weakest: Claim[]; keep: Claim[]; change: Claim[]
  shared?: Claim[]; outlier?: boolean; partial_reels?: number[]; top?: number[]; bottom?: number[]
  grounding?: { checked: number; dropped: number; regenerated: boolean }
}
export type DnaResp = {
  connected: boolean; isFixture?: boolean; username?: string; profilePictureUrl?: string | null; lastImportAt?: string | null
  lastImport?: { imported: number; found: number; requested: number; missing_metrics: string[]; rate_limited: boolean; token_invalid: boolean; partial?: number } | null
  dna: null | { summary: string; sampleSize: number; costUsd: number; model: string; data: DnaData; metricsAvailable: Record<string, number>; medians?: Record<string, number | null> }
  reels: Reel[]; metricLabels: Record<string, string>
}
export type Status = {
  configured: boolean; fixtureAvailable: boolean; connected: boolean; scopes: string[]
  limits: { min: number; max: number; default: number }
  account: null | {
    username: string | null; isFixture: boolean; profilePictureUrl?: string | null; tokenExpiresAt: string | null; lastImportAt: string | null
    lastImportRequested: number | null
    lastImport: null | { imported: number; found: number; requested: number; missing_metrics: string[]; rate_limited: boolean; token_invalid: boolean; partial?: number }
  }
}
export type Job = {
  status: 'running' | 'done' | 'error'; found?: number; requested: number; imported?: number; error?: string; progress: Record<string, string>
  missing_metrics?: string[]; rate_limited?: boolean; token_invalid?: boolean; cost_usd?: number; partial?: number
}

export type Advice = { id: string; text: string }
export type Suggestion = { n: number; media_id: string; score: number; reasons: string[]; likely: boolean }
export type AnalysisItem = {
  id: string; createdAt: string | null; score: number | null; summary: string; durationS: number | null; hasEditedVideo: boolean
  advice: Advice[]; linked: boolean; suggestions: Suggestion[]
}
export type ResultRow = {
  key: string; label: string; value: number | null; usual: number | null; direction: 'better' | 'worse' | 'same' | 'none'
  valueText: string | null; usualText: string | null; plainText?: string; note: string | null; delta?: number
}
export type LinkResult = { basedOn: number; rows: ResultRow[]; lagging: boolean; better: number; worse: number; same: number; compared: number; notes: string[] }
export type FixLink = {
  id: string; analysisId: string; mediaId: string; applied: string[]; usedAutoEdit: boolean
  analysis: { createdAt?: string | null; score?: number | null; summary?: string; duration?: number | null }
  reel: null | { n: number; postedAt: string | null; duration_s: number | null; permalink: string | null; caption: string }
  result: LinkResult | null
}
