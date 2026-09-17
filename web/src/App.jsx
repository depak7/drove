import { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import { useStream } from './useStream'
import { Diff, Gate, Log, Pill, Plan, Rail, StageLegend } from './components'
import { WorkspaceSheet } from './Workspaces'
import { SettingsSheet } from './Settings'
import { Mark, Wordmark } from './Logo'
import { isDesktop, notify, setPulse } from './desktop'

const PIVOTABLE = ['delivered', 'landed', 'needs_human', 'verify_failed', 'no_changes']
const ACTIVE = ['planning', 'awaiting_approval', 'approved', 'executing', 'reviewing']

export default function App() {
  const [workspaces, setWorkspaces] = useState([])
  const [workspaceId, setWorkspaceId] = useState(() => localStorage.getItem('drove.workspace') ?? '')
  const [features, setFeatures] = useState([])
  const [selected, setSelected] = useState(null)
  const [sheet, setSheet] = useState(false)
  const [settings, setSettings] = useState(false)
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
      // The point of a desktop app is not having to watch it. Interrupt only for the two states
      // that actually need a person.
      if (event.status === 'awaiting_approval') notify('Plan ready', 'A feature is waiting on your approval.')
      if (['needs_human', 'verify_failed', 'failed'].includes(event.status)) {
        notify('Run stopped', `A feature ended as ${event.status.replace(/_/g, ' ')}.`)
      }
    }, [refresh]),
  )

  useEffect(() => { refresh() }, [refresh])
  useEffect(() => { if (workspaceId) localStorage.setItem('drove.workspace', workspaceId) }, [workspaceId])

  const workspace = workspaces.find((w) => w.id === workspaceId) ?? null
  const current = features.find((f) => f.id === selected) ?? null
  const ready = workspace?.repos.length > 0

  useEffect(() => {
    if (!isDesktop) return
    setPulse({
      running: features.filter((f) => f.busy).length,
      waiting: features.filter((f) => f.status === 'awaiting_approval').length,
    })
  }, [features])

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

  const active = features.filter((f) => f.busy || ACTIVE.includes(f.status))
  const settled = features.filter((f) => !active.includes(f))

  return (
    <div className="shell">
      <header className="topbar">
        <Wordmark />

        {workspace && (
          <button className="ws" onClick={() => setSheet(true)}>
            {workspace.name}
            <span className="caret">▾</span>
          </button>
        )}

        {workspace && (
          <div className="repos">
            {workspace.repos.slice(0, 4).map((r) => (
              <span className={`chip ${r.exists ? '' : 'missing'}`} key={r.path} title={r.path}>{r.name}</span>
            ))}
            {workspace.repos.length > 4 && <span className="chip">+{workspace.repos.length - 4}</span>}
            {workspace.repos.length === 0 && (
              <button className="ghost tiny" onClick={() => setSheet(true)}>+ add a repository</button>
            )}
          </div>
        )}

        <span className="grow" />

        {workspace && (
          <button
            className="chain"
            onClick={() => setSettings(true)}
            title="Choose which harness and model runs each stage"
          >
            <span className="node">
              {workspace.harness.execute}
              {workspace.models?.execute && <em>{workspace.models.execute}</em>}
            </span>
            <span className="arrow">builds</span>
            <span className="node">
              {workspace.harness.review}
              {workspace.models?.review && <em>{workspace.models.review}</em>}
            </span>
            <span className="arrow">reviews</span>
          </button>
        )}
        <span className={`live-dot ${connected ? '' : 'off'}`} title={connected ? 'live' : 'reconnecting'} />
      </header>

      {workspace && !workspace.independent_review && (
        <div className="banner warn">
          {workspace.harness.execute} is set to review its own work — that is not an independent
          review. Set a different <code>review</code> harness in <code>.drove.toml</code>.
        </div>
      )}
      {error && <div className="banner bad">{error}</div>}

      {settings && workspace && (
        <SettingsSheet
          workspace={workspace}
          onClose={() => setSettings(false)}
          onSave={(harness, models) => act(() => api.saveSettings(workspace.id, harness, models))}
        />
      )}

      {sheet && (
        <WorkspaceSheet
          workspaces={workspaces}
          current={workspace}
          onClose={() => setSheet(false)}
          onSelect={(id) => { setWorkspaceId(id); setSelected(null) }}
          onCreate={(name) => act(async () => {
            const created = await api.createWorkspace(name)
            setWorkspaceId(created.id)
          })}
          onAddRepo={(path) => act(() => api.addRepo(workspaceId, path))}
          onRemoveRepo={(path) => act(() => api.removeRepo(workspaceId, path))}
        />
      )}

      <div className="stage">
        {!workspace ? (
          <FirstRun onCreate={(name) => act(async () => {
            const created = await api.createWorkspace(name)
            setWorkspaceId(created.id)
            setSheet(true)
          })} />
        ) : current ? (
          <Detail
            feature={current}
            logs={logs[current.id] ?? []}
            connected={connected}
            tab={tab} setTab={setTab}
            diff={diff} evidence={evidence}
            onBack={() => setSelected(null)}
            act={act}
            onDiscard={() => { setSelected(null) }}
          />
        ) : (
          <div className="stage-inner">
            <div className="hero">
              <h1>What do you want built?</h1>
              {!ready && <p className="sub">Add a repository first — {workspace.name} has none.</p>}
              <div className="prompt">
                <textarea
                  rows={2}
                  placeholder={ready ? "Describe it the way you would to a colleague…" : 'Add a repository to begin'}
                  value={task}
                  disabled={!ready}
                  onChange={(e) => setTask(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) create() }}
                />
                <div className="foot">
                  <span className="kbd">Cmd + Enter</span>
                  <span className="grow" />
                  <button className="primary" onClick={create} disabled={!task.trim() || !ready}>
                    Plan it
                  </button>
                </div>
              </div>
            </div>

            {active.length > 0 && (
              <>
                <div className="strip"><span className="eyebrow">In flight</span><span className="rule" /></div>
                {active.map((f) => <FeatureCard key={f.id} feature={f} onOpen={setSelected} />)}
              </>
            )}

            {settled.length > 0 && (
              <>
                <div className="strip"><span className="eyebrow">Done</span><span className="rule" /></div>
                {settled.map((f) => <FeatureCard key={f.id} feature={f} onOpen={setSelected} />)}
              </>
            )}

            {features.length === 0 && ready && (
              <p style={{ color: 'var(--text-3)', fontSize: 13, textAlign: 'center', marginTop: 8 }}>
                Nothing yet. {workspace.harness.plan} will read your code and write a plan for you
                to approve before anything is changed.
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

function FeatureCard({ feature, onOpen }) {
  const gate = feature.status === 'awaiting_approval'
  return (
    <button className={`fcard ${gate ? 'gate' : ''}`} onClick={() => onOpen(feature.id)}>
      <div className="top">
        <span className="title">{feature.title}</span>
        <Pill status={feature.status} busy={feature.busy} />
      </div>
      <div className="under">
        <Rail status={feature.busy ? 'executing' : feature.status} />
        <span className="iter mono">{feature.branch}</span>
        {feature.iterations > 1 && <span className="iter">· {feature.iterations} runs</span>}
      </div>
    </button>
  )
}

function FirstRun({ onCreate }) {
  const [name, setName] = useState('')
  return (
    <div className="stage-inner">
      <div className="hero">
        <Mark size={40} />
        <h1 style={{ marginTop: 18 }}>Name your first workspace</h1>
        <p className="sub">
          A workspace is the set of repositories a feature may change. Most have one; add several
          when a change spans an API and the things that call it.
        </p>
        <div className="prompt">
          <input
            autoFocus placeholder="product" value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && name.trim()) onCreate(name.trim()) }}
            style={{ background: 'transparent', border: 'none', fontSize: 15, padding: 0 }}
          />
          <div className="foot">
            <span className="grow" />
            <button className="primary" disabled={!name.trim()} onClick={() => onCreate(name.trim())}>
              Create
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

function Detail({ feature, logs, connected, tab, setTab, diff, evidence, onBack, act, onDiscard }) {
  const gated = feature.status === 'awaiting_approval'
  return (
    <div className={`stage-inner wide ${gated ? 'gated' : ''}`}>
      <button className="back" onClick={onBack}>← all features</button>

      <div className="dhead"><h2>{feature.title}</h2></div>
      <div className="dmeta">
        <Pill status={feature.status} busy={feature.busy} />
        <span className="sep">·</span>
        <span className="mono">{feature.branch}</span>
        <span className="sep">→</span>
        <span className="mono">{feature.base}</span>
        {feature.iterations > 1 && <><span className="sep">·</span><span>{feature.iterations} runs</span></>}
      </div>

      <div style={{ marginBottom: 20 }}>
        <Rail status={feature.busy ? 'executing' : feature.status} large />
        <StageLegend status={feature.busy ? 'executing' : feature.status} />
      </div>

      <div className="tabs">
        {['plan', 'live', 'diff', 'evidence'].map((name) => (
          <button key={name} className={tab === name ? 'on' : ''} onClick={() => setTab(name)}>
            {name}
            {name === 'live' && logs.length > 0 && <span className="count">{logs.length}</span>}
          </button>
        ))}
      </div>

      {tab === 'plan' && (
        <>
          <Plan plan={feature.plan} />
          {gated && (
            <Gate
              busy={feature.busy}
              onApprove={() => act(() => api.approve(feature.id))}
              onDecline={() => act(async () => { await api.decline(feature.id); onDiscard() })}
              onRevise={(feedback) => act(() => api.revise(feature.id, feedback))}
            />
          )}
          {PIVOTABLE.includes(feature.status) && (
            <Pivot busy={feature.busy} onPivot={(intent) => act(() => api.pivot(feature.id, intent))} />
          )}
        </>
      )}
      {tab === 'live' && <Log lines={logs} connected={connected} />}
      {tab === 'diff' && <Diff text={diff?.diff} repos={diff?.repos} />}
      {tab === 'evidence' && (
        evidence
          ? <pre className="block">{evidence}</pre>
          : <div className="card"><p style={{ color: 'var(--text-3)' }}>
              No evidence pack yet — one is written when a run finishes.
            </p></div>
      )}
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
        value={intent} disabled={busy}
        onChange={(e) => setIntent(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) send() }}
      />
      <div style={{ marginTop: 10 }}>
        <button disabled={busy || !intent.trim()} onClick={send}>Plan the pivot</button>
      </div>
    </div>
  )
}
