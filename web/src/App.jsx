import { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import { useStream } from './useStream'
import { Diff, Gate, Log, Pill, Plan, Reviews } from './components'

export default function App() {
  const [health, setHealth] = useState(null)
  const [features, setFeatures] = useState([])
  const [selected, setSelected] = useState(null)
  const [tab, setTab] = useState('plan')
  const [diff, setDiff] = useState('')
  const [evidence, setEvidence] = useState('')
  const [task, setTask] = useState('')
  const [error, setError] = useState('')

  const refresh = useCallback(async () => {
    try {
      setFeatures(await api.features())
    } catch (e) {
      setError(String(e.message ?? e))
    }
  }, [])

  // Any status change on the server is a reason to re-read state, so the list and the detail
  // pane never drift from what actually happened.
  const { connected, logs } = useStream(refresh)

  useEffect(() => {
    api.health().then(setHealth).catch((e) => setError(String(e.message ?? e)))
    refresh()
  }, [refresh])

  const current = features.find((f) => f.id === selected) ?? null

  useEffect(() => {
    if (!current) return
    if (tab === 'diff') api.diff(current.id).then((d) => setDiff(d.diff)).catch(() => setDiff(''))
    if (tab === 'evidence')
      api.evidence(current.id).then((d) => setEvidence(d.markdown)).catch(() => setEvidence(''))
  }, [tab, current?.id, current?.status])

  const act = async (fn) => {
    setError('')
    try {
      await fn()
    } catch (e) {
      setError(String(e.message ?? e))
    }
    refresh()
  }

  const create = () => {
    const text = task.trim()
    if (!text) return
    setTask('')
    act(async () => {
      const feature = await api.create(text)
      setSelected(feature.id)
      setTab('plan')
    })
  }

  return (
    <div className="app">
      <header>
        <h1>vorflux</h1>
        <span className="repo mono">{health?.repo ?? ''}</span>
        <span className="spacer" />
        {health && (
          <span className="repo">
            {health.stages.execute} implements · {health.stages.review} reviews
          </span>
        )}
        <span className={`dot ${connected ? 'on' : 'off'}`} title={connected ? 'live' : 'reconnecting'} />
      </header>

      {health && !health.independent_review && (
        <div className="warnbar">
          {health.stages.execute} is set to review its own work — that is not an independent
          review. Set a different <code>review</code> harness in <code>.vorflux.toml</code>.
        </div>
      )}
      {error && <div className="warnbar">{error}</div>}

      <div className="body">
        <aside className="sidebar">
          <div className="newfeature">
            <textarea
              rows={3}
              placeholder="What do you want built?"
              value={task}
              onChange={(e) => setTask(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) create()
              }}
            />
            <button className="primary" onClick={create} disabled={!task.trim()}>
              Plan it
            </button>
          </div>

          <h2>Features</h2>
          {features.length === 0 && <div className="repo">nothing yet</div>}
          {features.map((feature) => (
            <button
              key={feature.id}
              className={`card ${feature.id === selected ? 'selected' : ''}`}
              onClick={() => { setSelected(feature.id); setTab('plan') }}
            >
              <div className="title">{feature.title}</div>
              <div className="meta">
                <Pill status={feature.busy ? 'running' : feature.status} />
                <span className="mono">{feature.branch}</span>
                {feature.iterations > 1 && <span>· {feature.iterations} runs</span>}
              </div>
            </button>
          ))}
        </aside>

        <main className="main">
          {!current && <div className="empty">Pick a feature, or describe one to get started.</div>}

          {current && (
            <>
              <div className="detail-head">
                <h2>{current.title}</h2>
                <Pill status={current.busy ? 'running' : current.status} />
              </div>
              <div className="sub mono">
                {current.branch} → {current.base} · {current.iterations} run
                {current.iterations === 1 ? '' : 's'}
              </div>

              <div className="tabs">
                {['plan', 'live', 'diff', 'evidence'].map((name) => (
                  <button
                    key={name}
                    className={tab === name ? 'active' : ''}
                    onClick={() => setTab(name)}
                  >
                    {name}
                  </button>
                ))}
              </div>

              {tab === 'plan' && (
                <>
                  <Plan plan={current.plan} />
                  {current.status === 'awaiting_approval' && (
                    <Gate
                      busy={current.busy}
                      onApprove={() => act(() => api.approve(current.id))}
                      onDecline={() => act(async () => {
                        await api.decline(current.id)
                        setSelected(null)
                      })}
                      onRevise={(feedback) => act(() => api.revise(current.id, feedback))}
                    />
                  )}
                  {['delivered', 'needs_human', 'verify_failed', 'no_changes'].includes(
                    current.status,
                  ) && <PivotBox busy={current.busy} onPivot={(intent) => act(() => api.pivot(current.id, intent))} />}
                </>
              )}

              {tab === 'live' && <Log lines={logs[current.id] ?? []} connected={connected} />}
              {tab === 'diff' && <Diff text={diff} />}
              {tab === 'evidence' && (
                evidence
                  ? <pre className="block mono">{evidence}</pre>
                  : <div className="empty">no evidence pack yet — it is written when a run finishes</div>
              )}
            </>
          )}
        </main>
      </div>
    </div>
  )
}

/** Delivered is a resting state, not a terminal one. */
function PivotBox({ busy, onPivot }) {
  const [intent, setIntent] = useState('')
  const send = () => {
    const text = intent.trim()
    if (!text) return
    setIntent('')
    onPivot(text)
  }
  return (
    <div className="panel">
      <h3>Pivot</h3>
      <div className="gate">
        <textarea
          rows={2}
          placeholder="Change direction — keeps the branch and what the agent already knows"
          value={intent}
          disabled={busy}
          onChange={(e) => setIntent(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) send() }}
        />
        <div className="row">
          <button disabled={busy || !intent.trim()} onClick={send}>Plan the pivot</button>
        </div>
      </div>
    </div>
  )
}
