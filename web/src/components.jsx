import { useEffect, useRef, useState } from 'react'
import { STAGES, STAGE_COLOR, label, progress } from './stages'

export function Pill({ status }) {
  // The status now names the stage the run is in; overriding it with "executing" whenever a job
  // was attached made every running feature claim to be implementing, mid-plan and mid-review.
  const [tone, text] = label(status)
  return (
    <span className={`pill ${tone}`}>
      <i />
      {text}
    </span>
  )
}

/**
 * The signature element: five segments, one per pipeline stage.
 * Done is green, the current stage pulses in its own hue, a failure is red.
 * You can read a feature's state from three metres away.
 */
export function Rail({ status, large = false }) {
  const { at, state } = progress(status)
  return (
    <div className={`rail ${large ? 'lg' : ''}`}>
      {STAGES.map((stage, i) => {
        let cls = ''
        if (i < at) cls = 'done'
        else if (i === at) cls = state === 'failed' ? 'fail' : state === 'running' ? 'now' : ''
        return (
          <span
            key={stage}
            className={`seg ${cls}`}
            style={{
              '--seg': STAGE_COLOR[stage],
              ...(i === at && state === 'gate' ? { background: 'var(--accent)' } : null),
              ...(i === at && state === 'stopped' ? { background: 'var(--line-strong)' } : null),
            }}
            title={stage}
          />
        )
      })}
    </div>
  )
}

export function StageLegend({ status }) {
  const { at, state } = progress(status)
  return (
    <div className="stagerow">
      {STAGES.map((stage, i) => (
        <span key={stage} className={`s ${i < at ? 'done' : i === at && state !== 'stopped' ? 'now' : ''}`}>
          {stage}
        </span>
      ))}
    </div>
  )
}

export function Plan({ plan }) {
  if (!plan) return <div className="card"><p className="mono" style={{ color: 'var(--text-3)' }}>no plan yet</p></div>
  return (
    <>
      <div className="card">
        <h3 className="eyebrow">Approach</h3>
        <p>{plan.summary}</p>
      </div>

      {plan.steps?.length > 0 && (
        <div className="card">
          <h3 className="eyebrow">Steps</h3>
          <div className="steps">
            {plan.steps.map((step, i) => (
              <div className="step" key={i}>
                <span className="n">{i + 1}</span>
                <div>
                  <div>{step.title}</div>
                  {step.files?.length > 0 && <div className="files mono">{step.files.join('  ·  ')}</div>}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {plan.acceptance_criteria?.length > 0 && (
        <div className="card">
          <h3 className="eyebrow">Done when</h3>
          <ul>{plan.acceptance_criteria.map((c, i) => <li key={i}>{c}</li>)}</ul>
        </div>
      )}

      {plan.risks?.length > 0 && (
        <div className="card">
          <h3 className="eyebrow">Risks &amp; assumptions</h3>
          {plan.risks.map((r, i) => <div className="risk" key={i}><span /><span>{r}</span></div>)}
        </div>
      )}
    </>
  )
}

/**
 * The gate: the one decision the product asks of you.
 *
 * Pinned to the bottom of the pane rather than placed after the plan. A plan runs to several
 * screens — approach, steps, criteria, risks — and burying the only action under all of it means
 * the thing the product exists to ask never stays in view.
 */
export function Gate({ busy, onApprove, onDecline, onRevise }) {
  const [feedback, setFeedback] = useState('')
  const [open, setOpen] = useState(false)

  const send = () => {
    const text = feedback.trim()
    if (!text) return
    setFeedback('')
    setOpen(false)
    onRevise(text)
  }

  return (
    <div className="gatebar">
      {open && (
        <textarea
          autoFocus
          rows={2}
          placeholder="What should change? — “use a token bucket, skip the middleware”"
          value={feedback}
          disabled={busy}
          onChange={(e) => setFeedback(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) send()
            if (e.key === 'Escape') setOpen(false)
          }}
        />
      )}
      <div className="row">
        <span className="eyebrow">Your call</span>
        <span className="grow" />
        <button className="ghost danger" disabled={busy} onClick={onDecline}>Discard</button>
        {open ? (
          <button disabled={busy || !feedback.trim()} onClick={send}>Send feedback</button>
        ) : (
          <button disabled={busy} onClick={() => setOpen(true)}>Revise…</button>
        )}
        <button className="primary" disabled={busy} onClick={onApprove}>Approve &amp; run</button>
      </div>
    </div>
  )
}

export function Log({ lines, replay, connected }) {
  const ref = useRef(null)
  const pinned = useRef(true)

  // Follow the tail, but stop fighting the user the moment they scroll up to read something.
  const onScroll = () => {
    const el = ref.current
    if (!el) return
    pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40
  }
  useEffect(() => {
    if (pinned.current && ref.current) ref.current.scrollTop = ref.current.scrollHeight
  }, [lines])

  return (
    <div className="card">
      <h3 className="eyebrow">
        {lines.length > 0 ? (connected ? 'Live' : 'Reconnecting…') : 'Recorded'}
      </h3>
      <div className="log" ref={ref} onScroll={onScroll}>
        {lines.map((line, i) => <div className={line.tone} key={i}>{line.text}</div>)}

        {/* Nothing is streaming, so replay what the run recorded. The live view only exists while
            a tab is open, which left a finished or failed run with an empty panel. */}
        {lines.length === 0 && replay?.stages?.map((s) => (
          <div key={s.stage}>
            <div className="stage">{s.stage} · {s.harness}</div>
            {s.lines.map((line, i) => <div className={line.tone} key={i}>{line.text}</div>)}
          </div>
        ))}

        {lines.length === 0 && !replay?.stages?.length && (
          <div className="dim">nothing recorded for this run</div>
        )}
      </div>
    </div>
  )
}

export function Diff({ text, repos }) {
  if (!text) return <div className="card"><p style={{ color: 'var(--text-3)' }}>No changes yet.</p></div>
  return (
    <>
      {repos?.length > 1 && (
        <div className="banner warn" style={{ borderRadius: 'var(--radius)', marginBottom: 12, border: '1px solid #E8B33940' }}>
          This change spans {repos.length} repositories. Their branches must be merged together —
          landing one without the others breaks things.
        </div>
      )}
      <pre className="block diff">
        {text.split('\n').map((line, i) => {
          if (line.startsWith('===== repo:')) {
            return <span className="repo" key={i}>{line.replace(/=====/g, '').trim()}</span>
          }
          const cls = line.startsWith('+++') || line.startsWith('---') || line.startsWith('diff ')
            ? 'meta'
            : line.startsWith('@@') ? 'hunk'
            : line.startsWith('+') ? 'add'
            : line.startsWith('-') ? 'del'
            : ''
          return <div className={cls} key={i}>{line || ' '}</div>
        })}
      </pre>
    </>
  )
}
