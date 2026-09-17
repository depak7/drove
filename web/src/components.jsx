import { useEffect, useRef, useState } from 'react'

export const Pill = ({ status }) => (
  <span className={`pill ${status}`}>{status.replace(/_/g, ' ')}</span>
)

export function Plan({ plan }) {
  if (!plan) return null
  return (
    <>
      <div className="panel">
        <h3>Plan</h3>
        <p style={{ margin: 0 }}>{plan.summary}</p>
      </div>

      {plan.steps?.length > 0 && (
        <div className="panel">
          <h3>Steps</h3>
          {plan.steps.map((step, i) => (
            <div className="step" key={i}>
              {i + 1}. {step.title}
              {step.files?.length > 0 && <div className="files mono">{step.files.join('  ')}</div>}
            </div>
          ))}
        </div>
      )}

      {plan.acceptance_criteria?.length > 0 && (
        <div className="panel">
          <h3>Done when</h3>
          <ul>
            {plan.acceptance_criteria.map((c, i) => <li key={i}>{c}</li>)}
          </ul>
        </div>
      )}

      {plan.risks?.length > 0 && (
        <div className="panel">
          <h3>Risks</h3>
          <ul>
            {plan.risks.map((r, i) => <li className="risk" key={i}>{r}</li>)}
          </ul>
        </div>
      )}
    </>
  )
}

/** The approval gate. Feedback revises the plan; the planner keeps its session. */
export function Gate({ busy, onApprove, onDecline, onRevise }) {
  const [feedback, setFeedback] = useState('')
  const send = () => {
    const text = feedback.trim()
    if (!text) return
    setFeedback('')
    onRevise(text)
  }
  return (
    <div className="panel">
      <h3>Your call</h3>
      <div className="gate">
        <textarea
          rows={3}
          placeholder="Type feedback to revise the plan — e.g. “use a token bucket, skip the middleware”"
          value={feedback}
          disabled={busy}
          onChange={(e) => setFeedback(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) send()
          }}
        />
        <div className="row">
          <button className="primary" disabled={busy} onClick={onApprove}>
            Approve &amp; run
          </button>
          <button disabled={busy || !feedback.trim()} onClick={send}>
            Revise
          </button>
          <span style={{ flex: 1 }} />
          <button className="danger" disabled={busy} onClick={onDecline}>
            Decline
          </button>
        </div>
      </div>
    </div>
  )
}

export function Log({ lines, connected }) {
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
    <div className="panel">
      <h3>Live {!connected && '· disconnected'}</h3>
      <div className="log mono" ref={ref} onScroll={onScroll}>
        {lines.length === 0 && <div className="dim">nothing yet</div>}
        {lines.map((line, i) => (
          <div className={line.tone} key={i}>{line.text}</div>
        ))}
      </div>
    </div>
  )
}

export function Diff({ text }) {
  if (!text) return <div className="empty">no changes yet</div>
  return (
    <pre className="block diff mono">
      {text.split('\n').map((line, i) => {
        const cls = line.startsWith('+++') || line.startsWith('---') || line.startsWith('diff ')
          ? 'meta'
          : line.startsWith('@@')
            ? 'hunk'
            : line.startsWith('+')
              ? 'add'
              : line.startsWith('-')
                ? 'del'
                : ''
        return <div className={cls} key={i}>{line || ' '}</div>
      })}
    </pre>
  )
}

export function Reviews({ runs, reviews }) {
  if (!reviews?.length) return null
  return (
    <div className="panel">
      <h3>Review</h3>
      {reviews.map((review, i) => (
        <div key={i} style={{ marginBottom: 10 }}>
          <strong>Round {i + 1} — {review.verdict === 'pass' ? 'pass' : 'changes requested'}</strong>
          <div className="dim">{review.summary}</div>
          <ul>
            {review.blocking?.map((issue, j) => (
              <li key={j}>
                <code>{issue.file}{issue.line ? `:${issue.line}` : ''}</code> [{issue.severity}] {issue.why}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  )
}
