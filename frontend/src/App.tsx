import { useEffect, useRef, useState } from 'react'
import { Activity, AlertCircle, ArrowDownToLine, BookOpenText, Check, ChevronRight, CircleHelp, Clock3, FileSearch, FlaskConical, LoaderCircle, Plus, Search, ShieldCheck, Square, X } from 'lucide-react'
import { api, type Assessment, type EventItem, type Result, type RunSummary } from './api'

type ViewState = 'idle' | 'submitting' | 'running' | 'completed' | 'failed' | 'cancelled'

export function statusLabel(status: string) {
  if (status === 'supported') return 'Supported by retrieved evidence'
  if (status === 'conflicting') return 'Conflicting evidence'
  if (status === 'insufficient') return 'Insufficient evidence'
  if (status === 'failed') return 'Research failed'
  if (status === 'interrupted') return 'Interrupted'
  return status
}

export function evidenceForClaim(claim: Assessment, result: Result) {
  const ids = new Set([...claim.supporting_evidence_ids, ...claim.contradicting_evidence_ids])
  return result.evidence.filter((item) => ids.has(item.id))
}

function App() {
  const selection = useRef(0)
  const [mode, setMode] = useState<'demo' | 'live'>('demo')
  const [question, setQuestion] = useState('How does graph-based LLM orchestration compare with a linear workflow?')
  const [domains, setDomains] = useState('')
  const [depth, setDepth] = useState<'quick' | 'standard'>('quick')
  const [viewState, setViewState] = useState<ViewState>('idle')
  const [result, setResult] = useState<Result | null>(null)
  const [selectedClaimId, setSelectedClaimId] = useState<string | null>(null)
  const [events, setEvents] = useState<EventItem[]>([])
  const [history, setHistory] = useState<RunSummary[]>([])
  const [error, setError] = useState('')
  const [cursor, setCursor] = useState(0)

  useEffect(() => {
    api.config().then((value) => setMode(value.mode)).catch(() => setError('The API is not reachable. Start the backend, then refresh.'))
    api.history().then(setHistory).catch(() => undefined)
  }, [])

  useEffect(() => {
    if (!result || !['queued', 'running'].includes(result.status)) return
    let active = true
    const poll = async () => {
      try {
        const [detail, eventPage] = await Promise.all([api.detail(result.run_id), api.events(result.run_id, cursor)])
        if (!active) return
        const nextResult = detail.result
        setResult(nextResult)
        setEvents((current) => Array.from(new Map([...current, ...eventPage.events].map((item) => [item.id, item])).values()).sort((a, b) => a.id - b.id))
        setCursor(eventPage.next_cursor)
        if (['completed', 'partial'].includes(nextResult.status)) setViewState('completed')
        else if (nextResult.status === 'cancelled') setViewState('cancelled')
        else if (['failed', 'interrupted'].includes(nextResult.status)) {
          setViewState('failed')
          setError(nextResult.error ?? 'The run stopped before a report was produced.')
        }
      } catch (cause) {
        if (active) setError(cause instanceof Error ? cause.message : 'Could not refresh this run.')
      }
    }
    const timer = window.setInterval(poll, 900)
    void poll()
    return () => { active = false; window.clearInterval(timer) }
  }, [result?.run_id, result?.status, cursor])

  const selectedClaim = result?.claims.find((claim) => claim.claim_id === selectedClaimId) ?? null
  const selectedEvidence = selectedClaim && result ? evidenceForClaim(selectedClaim, result) : []

  async function startResearch(event: React.FormEvent) {
    event.preventDefault()
    const selected = ++selection.current
    setError('')
    setViewState('submitting')
    setResult(null)
    setEvents([])
    setCursor(0)
    setSelectedClaimId(null)
    try {
      const started = await api.create({ question, depth, preferred_domains: domains.split(',').map((item) => item.trim()).filter(Boolean) })
      if (selected !== selection.current) return
      setViewState('running')
      setResult({ mode, run_id: started.run_id, status: started.status, request: { question, depth, preferred_domains: [] }, sources: [], evidence: [], claims: [], usage: { model_requests: 0, search_requests: 0, fetch_attempts: 0, elapsed_seconds: 0 } })
      api.history().then(setHistory)
    } catch (cause) {
      setViewState('failed')
      setError(cause instanceof Error ? cause.message : 'Unable to start research.')
    }
  }

  async function openRun(id: string) {
    const selected = ++selection.current
    setResult(null)
    setEvents([])
    try {
      setError('')
      const [detail, eventPage] = await Promise.all([api.detail(id), api.events(id, 0)])
      if (selected !== selection.current) return
      setResult(detail.result)
      setQuestion(detail.result.request.question)
      setDepth(detail.result.request.depth)
      setDomains(detail.result.request.preferred_domains.join(', '))
      setError(detail.result.error ?? '')
      setEvents(eventPage.events)
      setCursor(eventPage.next_cursor)
      setViewState(['queued', 'running'].includes(detail.result.status) ? 'running' : detail.result.status === 'failed' ? 'failed' : 'completed')
      setSelectedClaimId(detail.result.claims[0]?.claim_id ?? null)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not open saved research.')
    }
  }

  async function cancelRun() {
    if (!result) return
    try { await api.cancel(result.run_id); setViewState('cancelled') } catch (cause) { setError(cause instanceof Error ? cause.message : 'Could not cancel the run.') }
  }

  const statusCounts = result?.claims.reduce((counts, claim) => ({ ...counts, [claim.status]: counts[claim.status] + 1 }), { supported: 0, conflicting: 0, insufficient: 0 })

  return (
    <div className="app-shell">
      <aside className="rail">
        <a className="brand" href="#top" aria-label="Evidence Desk home"><span className="brand-mark"><FileSearch size={19} /></span><span>evidence<span className="brand-light">desk</span></span></a>
        <div className="rail-label">WORKSPACE</div>
        <button className="rail-action" onClick={() => { selection.current++; setViewState('idle'); setResult(null); setEvents([]); setError('') }}><Plus size={16} /> New investigation</button>
        <div className="rail-label history-label">RECENT RUNS <span>{history.length.toString().padStart(2, '0')}</span></div>
        <nav className="history-list" aria-label="Research history">
          {history.map((run) => <button className={`history-item ${result?.run_id === run.id ? 'active' : ''}`} key={run.id} onClick={() => void openRun(run.id)}><span className="history-dot" data-status={run.status} /><span className="history-question">[{run.mode ?? 'unknown'}] {run.question}</span><span className="history-date">{new Date(run.created_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}</span></button>)}
          {history.length === 0 && <p className="history-empty">Your completed and active runs will appear here.</p>}
        </nav>
        <div className="rail-bottom"><div className="mode-badge"><span className="mode-light" />{(result?.mode ?? mode).toUpperCase()} MODE</div><p>Local single-user workspace</p><a href="../docs/limitations.md">Limitations <ChevronRight size={13} /></a></div>
      </aside>

      <main className="main-content" id="top">
        <header className="topbar"><div className="breadcrumb">Research <ChevronRight size={14} /> <span>{result ? 'Investigation' : 'New investigation'}</span></div><div className="topbar-right"><span className="local-chip"><ShieldCheck size={14} /> LOCAL ONLY</span><button className="icon-button" title="Project limitations" onClick={() => window.open('../docs/limitations.md', '_blank', 'noopener,noreferrer')}><CircleHelp size={17} /></button></div></header>

        <div className="workspace">
          <section className="work-column">
            <div className="section-kicker"><FlaskConical size={15} /> TECHNICAL EVIDENCE REVIEW</div>
            <h1>What are you<br /><em>trying to establish?</em></h1>
            <p className="intro-copy">A bounded investigation with inspectable evidence for every assessed claim.</p>

            {result?.mode && result.mode !== 'unknown' && <p>Selected run: {result.mode} mode</p>}
            <form className="research-form" onSubmit={startResearch}>
              <label htmlFor="research-question">Research question</label>
              <textarea id="research-question" value={question} onChange={(e) => setQuestion(e.target.value)} minLength={12} maxLength={700} required placeholder="Compare approaches, tools, or technical decisions..." />
              <div className="form-meta"><span>Be specific about version, context, or evaluation criteria.</span><span>{question.length}/700</span></div>
              <div className="form-row">
                <fieldset className="depth-select"><legend>Depth</legend><div className="segmented-control"><button type="button" aria-pressed={depth === 'quick'} onClick={() => setDepth('quick')}>Quick</button><button type="button" aria-pressed={depth === 'standard'} onClick={() => setDepth('standard')}>Standard</button></div></fieldset>
                <label className="domains-field" htmlFor="preferred-domains">Preferred domains <span>OPTIONAL</span><input id="preferred-domains" value={domains} onChange={(e) => setDomains(e.target.value)} placeholder="docs.langchain.com, arxiv.org" /></label>
                <button className="submit-button" type="submit" disabled={viewState === 'submitting' || viewState === 'running'}>{viewState === 'submitting' ? <LoaderCircle className="spin" size={16} /> : <Search size={16} />}{viewState === 'submitting' ? 'Starting' : 'Research'}<span>↗</span></button>
              </div>
            </form>

            {error && <div className="error-banner" role="alert"><AlertCircle size={17} /><span>{error}</span><button title="Dismiss error" onClick={() => setError('')}><X size={15} /></button></div>}

            <section className="activity-section" aria-label="Research activity">
              <div className="section-heading"><div><span className="micro-label">INVESTIGATION</span><h2>{viewState === 'running' || viewState === 'submitting' ? 'Research in progress' : result?.report ? 'Investigation complete' : 'Activity'}</h2></div>{viewState === 'running' && <button className="cancel-button" onClick={() => void cancelRun()}><Square size={13} /> Cancel</button>}</div>
              {(viewState === 'running' || viewState === 'submitting') && <div className="progress-strip"><span className="progress-indicator"><Activity size={15} /></span><div><strong>Working within the run budget</strong><p>Events below are workflow milestones, not private model reasoning.</p></div><LoaderCircle className="spin progress-spinner" size={16} /></div>}
              <ol className="event-list">{events.map((item) => <li key={item.id}><span className="event-mark"><Check size={12} /></span><div><span className="event-kind">{item.kind}</span><p>{item.message}</p></div><time>{new Date(item.created_at).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })}</time></li>)}{events.length === 0 && viewState === 'idle' && <li className="event-placeholder"><span className="event-mark muted"><Clock3 size={13} /></span><div><span className="event-kind">READY</span><p>Shape a question to begin. Demo mode uses synthetic evidence only.</p></div></li>}</ol>
            </section>

            {result?.report && <section className="report-section" aria-label="Research report">
              <div className="report-header"><div><span className="micro-label">STRUCTURED REPORT</span><h2>Evidence summary</h2></div><div className="export-actions"><a className="icon-button" title="Download Markdown" href={api.exportUrl(result.run_id, 'markdown')}><ArrowDownToLine size={16} /><span>.md</span></a><a className="icon-button" title="Download JSON" href={api.exportUrl(result.run_id, 'json')}><ArrowDownToLine size={16} /><span>.json</span></a></div></div>
              {result.report.demo_label && <div className="demo-notice"><span>DEMO</span>{result.report.demo_label}</div>}
              <p className="report-overview">{result.report.overview}</p>
              {result.usage.ended_by_limit && <div className="limit-note"><AlertCircle size={15} /> Run stopped at the {result.usage.ended_by_limit} limit. Findings are partial.</div>}
              <div className="report-stats">{(['supported', 'conflicting', 'insufficient'] as const).map((status) => <div key={status}><strong>{statusCounts?.[status] ?? 0}</strong><span>{statusLabel(status)}</span></div>)}</div>
              <div className="finding-list">{[...result.report.findings, ...result.report.comparison].map((finding, index) => <article className="finding" key={`${finding.heading}-${index}`}><span className="finding-type">{finding.kind === 'evidence' ? 'EVIDENCE' : finding.kind.toUpperCase()}</span><h3>{finding.heading}</h3><p>{finding.text}</p><div className="finding-claims">{finding.claim_ids.map((id) => <button key={id} onClick={() => setSelectedClaimId(id)}>Claim <span>{id.slice(-5)}</span><ChevronRight size={13} /></button>)}</div></article>)}{result.report.gaps.map((gap) => <p className="gap-line" key={gap}><CircleHelp size={14} />{gap}</p>)}</div>
              <div className="usage-line"><span><Activity size={14} /> {result.usage.metrics_scope === 'demo' ? `${result.usage.scripted_model_steps ?? 0} scripted model steps · not live usage` : `${result.usage.model_requests} model requests`}</span><span>{result.usage.search_requests} searches</span><span>{result.usage.fetch_attempts} fetch attempts</span><span>{result.usage.elapsed_seconds}s elapsed</span></div>
            </section>}
          </section>

          <aside className="evidence-column" aria-label="Evidence explorer">
            <div className="evidence-title"><div><span className="micro-label">TRACEABILITY</span><h2>Claims &amp; evidence</h2></div><BookOpenText size={19} /></div>
            {!result?.claims.length ? <div className="empty-evidence"><div className="empty-icon"><FileSearch size={22} /></div><h3>Evidence appears here</h3><p>Choose an assessed claim to inspect its exact passage, source, and limitations.</p></div> : <div className="claim-list">{result.claims.map((claim) => <button key={claim.claim_id} className={`claim-item ${selectedClaimId === claim.claim_id ? 'selected' : ''}`} onClick={() => setSelectedClaimId(claim.claim_id)}><span className={`status-symbol ${claim.status}`} aria-hidden="true">{claim.status === 'supported' ? <Check size={14} /> : claim.status === 'conflicting' ? <AlertCircle size={14} /> : <CircleHelp size={14} />}</span><span className="claim-content"><span className="claim-status">{statusLabel(claim.status)}</span><span className="claim-text">{claim.text}</span></span><ChevronRight size={15} className="claim-arrow" /></button>)}</div>}
            {selectedClaim && result && <div className="evidence-detail" aria-live="polite"><div className="detail-topline"><span>SELECTED CLAIM</span><button className="icon-button close-detail" aria-label="Close claim details" onClick={() => setSelectedClaimId(null)}><X size={15} /></button></div><h3>{selectedClaim.text}</h3><p className="assessment-reason">{selectedClaim.explanation}</p><div className="detail-rule" /><div className="passage-label">INSPECTED PASSAGES <span>{selectedEvidence.length}</span></div>{selectedEvidence.length === 0 ? <p className="no-passage">No usable passage was attached to this assessment.</p> : selectedEvidence.map((passage) => { const source = result.sources.find((item) => item.id === passage.source_id); return <article className="passage" key={passage.id}><blockquote>“{passage.excerpt}”</blockquote><div className="passage-source"><strong>{source?.title ?? 'Source unavailable'}</strong><span>{source?.published_at ? `Published ${new Date(source.published_at).toLocaleDateString()}` : 'Publication date unknown'} · Retrieved {source ? new Date(source.retrieved_at).toLocaleDateString() : 'date unavailable'}</span>{source?.synthetic && <span className="synthetic-tag">SYNTHETIC FIXTURE</span>}{source?.canonical_url.startsWith('http') && !source.synthetic && <a href={source.canonical_url} target="_blank" rel="noreferrer">Open inspected source <ChevronRight size={13} /></a>}{passage.page_number && <span>PDF page {passage.page_number}</span>}</div></article> })}</div>}
            {result?.sources.length ? <section className="sources-panel"><div className="sources-heading"><h3>Inspected sources</h3><span>{result.sources.length}</span></div>{result.sources.map((source) => <div className="source-row" key={source.id}><span className="source-pin" /><div><strong>{source.title}</strong><span>{source.domain} · {source.synthetic ? 'Synthetic' : source.extraction_status}</span></div></div>)}</section> : null}
            {viewState === 'failed' && <div className="side-state error-side"><AlertCircle size={16} />{error || 'This run did not complete.'}</div>}
            {viewState === 'cancelled' && <div className="side-state"><Square size={14} />Run cancelled. Any in-flight provider request may still complete.</div>}
          </aside>
        </div>
        <footer className="page-footer"><span>Evidence describes retrieved sources, not absolute truth.</span><span>SQLite · local only</span></footer>
      </main>
    </div>
  )
}

export default App
