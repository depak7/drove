/**
 * The pipeline, and where a given status sits in it.
 *
 * This mapping is the whole reason the UI is legible: a status string like "verify_failed" means
 * nothing on its own, but "stopped at stage 4 of 5" is instantly readable.
 */
export const STAGES = ['plan', 'execute', 'review', 'verify', 'deliver']

const AT = {
  planning:          { at: 0, state: 'running' },
  awaiting_approval: { at: 0, state: 'gate' },
  approved:          { at: 1, state: 'running' },
  executing:         { at: 1, state: 'running' },
  reviewing:         { at: 2, state: 'running' },
  needs_human:       { at: 2, state: 'stopped' },
  verify_failed:     { at: 3, state: 'failed' },
  no_changes:        { at: 1, state: 'stopped' },
  failed:            { at: 1, state: 'failed' },
  delivered:         { at: 5, state: 'done' },
  landed:            { at: 5, state: 'landed' },
  declined:          { at: 0, state: 'stopped' },
  abandoned:         { at: 0, state: 'stopped' },
}

export const progress = (status) => AT[status] ?? { at: 0, state: 'stopped' }

/** Status → pill tone + the words a person would actually use. */
export const LABEL = {
  planning:          ['running', 'planning'],
  awaiting_approval: ['gate',    'needs your approval'],
  approved:          ['running', 'starting'],
  executing:         ['running', 'implementing'],
  reviewing:         ['running', 'in review'],
  needs_human:       ['attn',    'needs you'],
  verify_failed:     ['bad',     'checks failed'],
  no_changes:        ['attn',    'no changes'],
  failed:            ['bad',     'failed'],
  delivered:         ['good',    'ready to merge'],
  landed:            ['neutral', 'landed'],
  abandoned:         ['neutral', 'abandoned'],
}

export const label = (status) => LABEL[status] ?? ['neutral', String(status).replace(/_/g, ' ')]

export const STAGE_COLOR = {
  plan: 'var(--st-plan)',
  execute: 'var(--st-execute)',
  review: 'var(--st-review)',
  verify: 'var(--st-verify)',
  deliver: 'var(--st-deliver)',
}
