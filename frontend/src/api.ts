export type Status = 'supported' | 'conflicting' | 'insufficient'

export interface Evidence {
  id: string
  source_id: string
  excerpt: string
  start_offset: number
  end_offset: number
  page_number?: number | null
}

export interface Source {
  id: string
  canonical_url: string
  title: string
  domain: string
  retrieved_at: string
  published_at?: string | null
  content_type: string
  synthetic: boolean
  extraction_status: string
}

export interface Assessment {
  id: string
  claim_id: string
  text: string
  status: Status
  supporting_evidence_ids: string[]
  contradicting_evidence_ids: string[]
  explanation: string
}

export interface Finding {
  heading: string
  text: string
  claim_ids: string[]
  kind: 'evidence' | 'synthesis' | 'recommendation'
  premise_claim_ids: string[]
}

export interface Report {
  overview: string
  findings: Finding[]
  comparison: Finding[]
  conflicts: Finding[]
  gaps: string[]
  source_ids: string[]
  demo_label?: string | null
}

export interface Result {
  run_id: string
  status: string
  request: { question: string; depth: 'quick' | 'standard'; preferred_domains: string[] }
  plan?: { overview: string; tasks: { id: string; question: string }[] } | null
  sources: Source[]
  evidence: Evidence[]
  claims: Assessment[]
  report?: Report | null
  usage: { model_requests: number; scripted_model_steps?: number; metrics_scope?: 'live' | 'demo'; search_requests: number; fetch_attempts: number; cached_calls?: number; elapsed_seconds: number; ended_by_limit?: string | null }
  error?: string | null
}

export interface RunSummary {
  id: string
  status: string
  question: string
  depth: string
  created_at: string
}

export interface EventItem {
  id: number
  created_at: string
  kind: string
  message: string
  data: Record<string, unknown>
}

const base = '/api'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${base}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  })
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    throw new Error(body?.detail?.message ?? `Request failed (${response.status})`)
  }
  return response.json() as Promise<T>
}

export const api = {
  config: () => request<{ mode: 'demo' | 'live'; providers_available: Record<string, boolean> }>('/config'),
  history: () => request<RunSummary[]>('/research'),
  create: (payload: { question: string; depth: 'quick' | 'standard'; preferred_domains: string[] }) =>
    request<{ run_id: string; status: string }>('/research', { method: 'POST', body: JSON.stringify(payload) }),
  detail: (id: string) => request<{ status: string; result: Result }>('/research/' + id),
  events: (id: string, cursor: number) => request<{ events: EventItem[]; next_cursor: number }>(`/research/${id}/events?cursor=${cursor}`),
  cancel: (id: string) => request(`/research/${id}/cancel`, { method: 'POST' }),
  exportUrl: (id: string, format: 'markdown' | 'json') => `${base}/research/${id}/export?format=${format}`,
}
