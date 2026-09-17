import { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import { useStream } from './useStream'
import { Diff, Gate, Log, Pill, Plan, Rail, StageLegend } from './components'
import { Overview, WorkspaceBar } from './Workspaces'
import { isDesktop, notify, setPulse } from './desktop'

const PIVOTABLE = ['delivered', 'landed', 'needs_human', 'verify_failed', 'no_changes']

export default function App() {
  const [workspaces, setWorkspaces] = useState([])
  const [workspaceId, setWorkspaceId] = useState(() => localStorage.getItem('vorflux.workspace') ?? '')
  const [features, setFeatures] = useState([])
  const [selected, setSelected] = useState(null)
  const [tab, setTab] = useState('plan')
  const [diff, setDiff] = useState(null)
  const [evidence, setEvidence] = useState('')
  const [task, setTask] = useState('')
  const [error, setError] = useState('')

  const refresh = useCallback(async () => {
    try {
      const all = await api.workspaces()
      setWorkspaces(all)
      // Keep the selection valid: a deleted workspace must not leave the app pointing at nothing.
      const active = all.find((w) => w.id === workspaceId) ?? all[0]
      if (active && active.id !== workspaceId) setWorkspaceId(active.id)
      setFeatures(active ? await api.features(active.id) : [])
    } catch (e) {
      setError(String(e.message ?? e))
    }
  }, [workspaceId])

  const { connected, logs } = useStream(
    useCallback((event) => {
      refresh()
      if (event.kind !== 'status') return
      // The point of a desktop app is not having to watch it. Only interrupt for the two states
      // that actually need a person: a plan waiting on approval, and a run that stopped.
      if (event.status === 'awaiting_approval') notify('Plan ready', 'A feature is waiting on your approval.')
      if (['needs_human', 'verify_failed', 'failed'].includes(event.status)) {
        notify('Run stopped', `A feature ended as ${event.status.replace(/_/g, ' ')}.`)
      }
    }, [refresh]),
  )

  useEffect(() => { refresh() }, [refresh])

  // Keep the notch pulse in step with what is actually happening.
  useEffect(() => {
    if (!isDesktop) return
    const running = features.filter((f) => f.busy).length
    const waiting = features.filter((f) => f.status === 'awaiting_approval').length
    setPulse({ running, waiting })
  }, [features])
  useEffect(() => { if (workspaceId) localStorage.setItem('vorflux.workspace', workspaceId) }, [workspaceId])

  const workspace = workspaces.find((w) => w.id === workspaceId) ?? null
  const current = features.find((f) => f.id === selected) ?? null

  useEffect(() => {
    if (!current) return
    if (tab === 'diff') api.diff(current.id).then(setDiff).catch(() => setDiff(null))
    if (tab === 'evidence') api.evidence(current.id).then((d) => setEvidence(d.markdown)).catch(() => setEvidence(''))
  }, [tab, current?.id, current?.status])

  const act = async (fn) => {
    setError('')
    try { await fn() } catch (e) { setError(String(e.message ?? e)) }
    refresh()
  }

  const create = () => {
    const text = task.trim()
    if (!text) return
    setTask('')
    act(async () => {
      const feature = await api.create(text, workspaceId)
      setSelected(feature.id)
      setTab('plan')
    })
  }

  const ready = workspace?.repos.length > 0

  return (
    <div className="app">
      <header className="top">
        <div className="brand">
          <b>vorflux</b>
          <span>autopilot</span>
        </div>
        <span className="grow" />
        {workspace && (
          <div className="chain" title="Different models plan, implement and review">
            <span className="node">{workspace.harness.execute}</span>
            <span className="arrow">implements →</span>
            <span className="node">{workspace.harness.review}</span>
            <span className="arrow">reviews</span>
          </div>
        )}
        <span className={`live-dot ${connected ? '' : 'off'}`} title={connected ? 'live' : 'reconnecting'} />
      </header>

      {workspace && !workspace.independent_review && (
        <div className="banner warn">
          {workspace.harness.execute} is set to review its own work — that is not an independent
          review. Set a different <code>review</code> harness in <code>.vorflux.toml</code>.
        </div>
      )}
      {error && <div className="banner bad">{error}</div>}

      <div className="body">
        <aside className="sidebar">
          <WorkspaceBar
            workspaces={workspaces}
            current={workspace}
            onSelect={(id) => { setWorkspaceId(id); setSelected(null) }}
            onCreate={(name) => act(async () => {
              const created = await api.createWorkspace(name)
              setWorkspaceId(created.id)
            })}
            onAddRepo={(path) => act(() => api.addRepo(workspaceId, path))}
            onRemoveRepo={(path) => act(() => api.removeRepo(workspaceId, path))}
          />

          <div className="pad composer">
            <textarea
              rows={3}
              placeholder={ready ? 'What do you want built?' : 'Add a repository first'}
              value={task}
              disabled={!ready}
              onChange={(e) => setTask(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) create() }}
            />
            <button className="primary" onClick={create} disabled={!task.trim() || !ready}>
              Plan it
            </button>
          </div>

          <div className="scroll">
            <div className="section-head">
              <span className="eyebrow">Features</span>
              <span className="eyebrow">{features.length || ''}</span>
            </div>

            {features.length === 0 && (
              <p style={{ color: 'var(--text-3)', fontSize: 12.5, padding: '0 4px' }}>
                Nothing yet. Describe a feature above and it will plan one.
              </p>
            )}

            {features.map((feature) => (
              <button
                key={feature.id}
                className={`frow ${feature.id === selected ? 'on' : ''}`}
                onClick={() => { setSelected(feature.id); setTab('plan') }}
              >
                <div className="title">{feature.title}</div>
                <div className="under">
                  <Rail status={feature.busy ? 'executing' : feature.status} />
                  <Pill status={feature.status} busy={feature.busy} />
                  {feature.iterations > 1 && <span className="iter">·  {feature.iterations} runs</span>}
                </div>
              </button>
            ))}
          </div>
        </aside>

        <main className="main">
          {!workspace && (
            <div className="empty"><div className="box">
              <h3>Create a workspace</h3>
              <p>A workspace is the set of repositories a feature may change. Most have one; add
                several when a change spans an API and the things that call it.</p>
            </div></div>
          )}

          {workspace && !ready && (
            <div className="empty"><div className="box">
              <h3>{workspace.name} has no repositories</h3>
              <p>Add one from the sidebar — paste its path. vorflux never writes to your working
                copy; it checks out an isolated worktree per feature.</p>
            </div></div>
          )}

          {workspace && ready && !current && (
            <Overview workspace={workspace} features={features} onPick={(id) => { setSelected(id); setTab('plan') }} />
          )}

          {current && (
            <div className={`main-inner ${current.status === 'awaiting_approval' ? 'gated' : ''}`}>
              <div className="dhead">
                <h2>{current.title}</h2>
              </div>

              <div className="dmeta">
                <Pill status={current.status} busy={current.busy} />
                <span className="sep">·</span>
                <span className="mono">{current.branch}</span>
                <span className="sep">→</span>
                <span className="mono">{current.base}</span>
                {current.iterations > 1 && <><span className="sep">·</span><span>{current.iterations} runs</span></>}
              </div>

              <div style={{ marginBottom: 20 }}>
                <Rail status={current.busy ? 'executing' : current.status} large />
                <StageLegend status={current.busy ? 'executing' : current.status} />
              </div>

              <div className="tabs">
                {['plan', 'live', 'diff', 'evidence'].map((name) => (
                  <button key={name} className={tab === name ? 'on' : ''} onClick={() => setTab(name)}>
                    {name}
                    {name === 'live' && (logs[current.id]?.length > 0) && (
                      <span className="count">{logs[current.id].length}</span>
                    )}
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
                      onDecline={() => act(async () => { await api.decline(current.id); setSelected(null) })}
                      onRevise={(feedback) => act(() => api.revise(current.id, feedback))}
                    />
                  )}
                  {PIVOTABLE.includes(current.status) && (
                    <Pivot busy={current.busy} onPivot={(intent) => act(() => api.pivot(current.id, intent))} />
                  )}
                </>
              )}

              {tab === 'live' && <Log lines={logs[current.id] ?? []} connected={connected} />}
              {tab === 'diff' && <Diff text={diff?.diff} repos={diff?.repos} />}
              {tab === 'evidence' && (
                evidence
                  ? <pre className="block">{evidence}</pre>
                  : <div className="card"><p style={{ color: 'var(--text-3)' }}>
                      No evidence pack yet — one is written when a run finishes.
                    </p></div>
              )}
            </div>
          )}
        </main>
      </div>
    </div>
  )
}

/** Delivered is a resting state, not a terminal one. */
function Pivot({ busy, onPivot }) {
  const [intent, setIntent] = useState('')
  const send = () => {
    const text = intent.trim()
    if (!text) return
    setIntent('')
    onPivot(text)
  }
  return (
    <div className="card">
      <h3 className="eyebrow">Change direction</h3>
      <textarea
        rows={2}
        placeholder="Pivot this feature — keeps the branch and what the agent already knows"
        value={intent}
        disabled={busy}
        onChange={(e) => setIntent(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) send() }}
      />
      <div style={{ marginTop: 10 }}>
        <button disabled={busy || !intent.trim()} onClick={send}>Plan the pivot</button>
      </div>
    </div>
  )
}
