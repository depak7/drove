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
  fixing:            { at: 1, state: 'running' },
  reviewing:         { at: 2, state: 'running' },
  verifying:         { at: 3, state: 'running' },
  needs_human:       { at: 2, state: 'stopped' },
  verify_failed:     { at: 3, state: 'failed' },
  no_changes:        { at: 1, state: 'stopped' },
  failed:            { at: 1, state: 'failed' },
  interrupted:       { at: 1, state: 'failed' },
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
  fixing:            ['running', 'fixing review issues'],
  reviewing:         ['running', 'in review'],
  verifying:         ['running', 'running your checks'],
  needs_human:       ['attn',    'needs you'],
  verify_failed:     ['bad',     'checks failed'],
  no_changes:        ['attn',    'no changes'],
  failed:            ['bad',     'failed'],
  interrupted:       ['attn',    'interrupted'],
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


/**
 * States where the system has stopped and is waiting on a person.
 *
 * An approval is the obvious one, but a run that deadlocked in review, failed your tests, errored
 * or changed nothing is equally stuck — and used to look identical to finished work. Anything
 * here is counted, badged and surfaced the same way, because to you they are the same thing: the
 * machine cannot proceed without a decision.
 */
export const NEEDS_YOU = {
  awaiting_approval: { verb: 'Approve the plan', why: 'A plan is ready for your decision.' },
  needs_human: {
    verb: 'Settle the disagreement',
    why: 'The reviewer still blocked the change after two fix rounds. That usually means the request was ambiguous.',
  },
  verify_failed: {
    verb: 'Decide what to do',
    why: "Your own project checks failed on the branch. The code was reviewed, but it does not pass.",
  },
  failed: { verb: 'Decide what to do', why: 'The run errored before it finished.' },
  interrupted: {
    verb: 'Resume it',
    why: 'Drove was stopped before this finished — the app quit, the machine slept or shut down. Nothing is lost: work already committed is on the branch.',
  },
  no_changes: { verb: 'Decide what to do', why: 'The agent made no changes. The request may already be satisfied, or it was too vague to act on.' },
}

export const needsYou = (feature) => !feature.busy && Boolean(NEEDS_YOU[feature.status])
