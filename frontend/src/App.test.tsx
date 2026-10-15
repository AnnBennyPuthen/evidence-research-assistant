import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App, { evidenceForClaim, statusLabel } from './App'
import type { Assessment, Result } from './api'

afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('evidence inspection', () => {
  it('selects only passages linked from an assessment', () => {
    const claim: Assessment = { id: 'a', claim_id: 'c1', text: 'A claim', status: 'supported', supporting_evidence_ids: ['e1'], contradicting_evidence_ids: [], explanation: 'Reason' }
    const result = { evidence: [{ id: 'e1' }, { id: 'e2' }] } as Result
    expect(evidenceForClaim(claim, result).map((item) => item.id)).toEqual(['e1'])
  })

  it('uses explicit evidence status language', () => {
    expect(statusLabel('supported')).toBe('Supported by retrieved evidence')
    expect(statusLabel('conflicting')).toBe('Conflicting evidence')
    expect(statusLabel('insufficient')).toBe('Insufficient evidence')
  })
})

describe('status presentation', () => {
  it('shows API connectivity errors to the user', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')))
    render(<App />)
    expect(await screen.findByRole('alert')).toHaveTextContent('The API is not reachable')
  })

  it('supports choosing a claim from its report reference', () => {
    vi.stubGlobal('fetch', vi.fn().mockImplementation((url: string) => {
      if (url.endsWith('/config')) return Promise.resolve({ ok: true, json: async () => ({ mode: 'demo' }) })
      return Promise.resolve({ ok: true, json: async () => [] })
    }))
    render(<App />)
    fireEvent.click(screen.getByRole('button', { name: /New investigation/i }))
    expect(screen.getByLabelText('Research question')).toBeInTheDocument()
  })
})
