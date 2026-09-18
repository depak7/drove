import { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import { useStream } from './useStream'
import { BrowserChecks, Diff, Gate, Log, Pill, Plan, Rail, StageLegend } from './components'
import { WorkspaceSheet } from './Workspaces'
import { SettingsSheet } from './Settings'
import { Mark, Wordmark } from './Logo'
import { PipelineStrip } from './Pipeline'
import { Board } from './Board'
import { NEEDS_YOU, needsYou } from './stages'
import { Agents, Blank, Runs } from './Agents'
import { isDesktop, notify, setPulse } from './desktop'

const PIVOTABLE = ['delivered', 'landed', 'needs_human', 'verify_failed', 'no_changes']
const ACTIVE = ['planning', 'awaiting_approval', 'approved', 'executing', 'fixing', 'reviewing', 'verifying']

export default function App() {
  const [workspaces, setWorkspaces] = useState([])
  const [workspaceId, setWorkspaceId] = useState(() => localStorage.getItem('drove.workspace') ?? '')
  const [features, setFeatures] = useState([])
  const [selected, setSelected] = useState(null)
  const [sheet, setSheet] = useState(false)
  const [screen, setScreen] = useState('overview')
  const [harnesses, setHarnesses] = useState([])
  const [runs, setRuns] = useState([])
  const [agents, setAgents] = useState([])
  const [settings, setSettings] = useState(false)
  const [tab, setTab] = useState('plan')
  const [diff, setDiff] = useState(null)
  const [evidence, setEvidence] = useState('')
  const [replay, setReplay] = useState(null)
  const [browser, setBrowser] = useState(null)
  const [task, setTask] = useState('')
  const [error, setError] = useState('')

  const refresh = useCallback(async () => {
    try {
      const all = await api.workspaces()
      setWorkspaces(all)
      // Keep the selection valid: a deleted workspace must not leave the app pointing at nothing.
      const active = all.find((w) => w.id === workspaceId) ?? all[0]
      if (active && active.id !== workspaceId) setWorkspaceId(active.id)
      if (active) {
        const [f, r, a] = await Promise.all([
          api.features(active.id), api.runs(active.id), api.agents(active.id),
        ])
        setFeatures(f); setRuns(r); setAgents(a)
      } else {
        setFeatures([]); setRuns([]); setAgents([])
      }
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
      if (event.status === 'awaiting_approval') {
        notify('Plan ready', 'A feature is waiting on your approval.')
      } else if (NEEDS_YOU[event.status]) {
        notify('Needs you', NEEDS_YOU[event.status].why)
      }
    }, [refresh]),
  )

  useEffect(() => { refresh() }, [refresh])

  // Warm the harness capabilities in the background. Gathering them spawns processes, so doing it
  // when the settings sheet opens showed a blank panel for about a second.
  useEffect(() => { api.harnesses().then(setHarnesses).catch(() => {}) }, [])
  useEffect(() => { if (workspaceId) localStorage.setItem('drove.workspace', workspaceId) }, [workspaceId])

  const workspace = workspaces.find((w) => w.id === workspaceId) ?? null
  const current = features.find((f) => f.id === selected) ?? null
  const ready = workspace?.repos.length > 0

  useEffect(() => {
    if (!isDesktop) return
    setPulse({
      running: features.filter((f) => f.busy).length,
      waiting: features.filter(needsYou).length,
    })
  }, [features])

  useEffect(() => {
    if (!current) return
    if (tab === 'diff') api.diff(current.id).then(setDiff).catch(() => setDiff(null))
    if (tab === 'evidence') api.evidence(current.id).then((d) => setEvidence(d.markdown)).catch(() => setEvidence(''))
    if (tab === 'live') api.log(current.id).then(setReplay).catch(() => setReplay(null))
    if (tab === 'browser') api.browser(current.id).then(setBrowser).catch(() => setBrowser(null))
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
  const running = features.filter((f) => f.busy).length
  const blocked = features.filter(needsYou)
  const waiting = blocked.length
  const spend = runs.reduce((sum, r) => sum + (r.cost_usd ?? 0), 0)

  return (
    <div className="shell">
      <header className="topbar">
        <Wordmark />

        {workspace && (
          <>
            <span className="hdiv" />
            <button className="ws" onClick={() => setSheet(true)} title="Switch workspace">
              {workspace.name}
              <span className="caret">▾</span>
            </button>

            <div className="repos" title="Repositories a feature here may change">
              {workspace.repos.length === 0 ? (
                <button className="ghost tiny" onClick={() => setSheet(true)}>+ add a repository</button>
              ) : (
                <>
                  {workspace.repos.slice(0, 4).map((r) => (
                    <span className={`chip ${r.exists ? '' : 'missing'}`} key={r.path} title={r.path}>
                      {r.name}
                    </span>
                  ))}
                  {workspace.repos.length > 4 && (
                    <button className="chip add" onClick={() => setSheet(true)}>
                      +{workspace.repos.length - 4}
                    </button>
                  )}
                </>
              )}
            </div>
          </>
        )}

        <span className="grow" />

        {workspace && (
          <div className="stats-centre">
            <span className={running > 0 ? 'sb-live' : ''}><b>{running}</b> running</span>
            <span className={waiting > 0 ? 'sb-gate' : ''}><b>{waiting}</b> needs you</span>
            <span><b>{features.length}</b> {features.length === 1 ? 'feature' : 'features'}</span>
            <span><b>{runs.length}</b> {runs.length === 1 ? 'run' : 'runs'}</span>
            {spend > 0 && <span><b>${spend.toFixed(2)}</b> spent</span>}
          </div>
        )}

        {workspace && (
          <button className="settings-btn" onClick={() => setSettings(true)} title="Choose the harness and model for each stage">
            <span className="gear">⚙</span> Stages
          </button>
        )}
        <span className={`live-dot ${connected ? '' : 'off'}`} title={connected ? 'engine connected' : 'reconnecting'} />
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
          harnesses={harnesses}
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

      <div className="work">
        <nav className="nav">
          <div className="group">
            <span className="eyebrow">{workspace?.name ?? 'Workspace'}</span>
            {[
              ['overview', '◉', 'Overview'],
              ['board', '▦', 'Board'],
              ['agents', '◆', 'Agents'],
              ['runs', '▶', 'Runs'],
            ].map(([key, ico, label]) => (
              <button
                key={key}
                className={screen === key && !current ? 'on' : ''}
                onClick={() => { setScreen(key); setSelected(null) }}
              >
                <span className="ico">{ico}</span>
                {label}
              </button>
            ))}
          </div>

          {blocked.length > 0 && (
            <div className="group needs">
              {/* Always in view, on every screen. Something stuck is the one thing that must not
                  wait for you to navigate back to Overview to be noticed. */}
              <span className="eyebrow">
                Needs you <span className="count">{blocked.length}</span>
              </span>
              {blocked.map((f) => (
                <button
                  key={f.id}
                  className={`nyitem ${f.id === selected ? 'on' : ''}`}
                  onClick={() => setSelected(f.id)}
                  title={`${f.title} — ${NEEDS_YOU[f.status]?.why ?? ''}`}
                >
                  <span className={`dot ${f.status}`} />
                  <span className="t">{f.title}</span>
                </button>
              ))}
            </div>
          )}

          <div className="group">
            <span className="eyebrow">Configure</span>
            <button onClick={() => setSettings(true)}><span className="ico">⚙</span>Stages</button>
            <button onClick={() => setSheet(true)}><span className="ico">▤</span>Workspace</button>
          </div>
        </nav>

        <div className="pane">
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
              onDiscard={() => setSelected(null)}
            />
          ) : screen === 'board' ? (
            <div className="pane-inner">
              <div className="pane-head">
                <h2>Board</h2>
                <span className="sub">Cards move themselves — the column is where the run actually got to.</span>
              </div>
              {features.length === 0
                ? <Blank title="Nothing on the board" body="Describe a feature on Overview and it appears here." />
                : <Board features={features} onOpen={(id) => setSelected(id)} />}
            </div>
          ) : screen === 'agents' ? (
            <div className="pane-inner mid">
              <div className="pane-head">
                <h2>Agents</h2>
                <span className="sub">Every session Drove has run, and how to reopen it.</span>
              </div>
              <Agents agents={agents} />
            </div>
          ) : screen === 'runs' ? (
            <div className="pane-inner mid">
              <div className="pane-head">
                <h2>Runs</h2>
                <span className="sub">What Drove actually did, and what it cost.</span>
              </div>
              <Runs runs={runs} onOpen={(id) => setSelected(id)} />
            </div>
          ) : (
            <div className="pane-inner narrow">
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

              <PipelineStrip workspace={workspace} onConfigure={() => setSettings(true)} />

              {active.length > 0 && (
                <>
                  <div className="strip"><span className="eyebrow">In flight</span><span className="rule" /></div>
                  {active.map((f) => <FeatureCard key={f.id} feature={f} onOpen={setSelected} />)}
                </>
              )}

              {settled.length > 0 && (
                <>
                  <div className="strip"><span className="eyebrow">Done</span><span className="rule" /></div>
                  {settled.slice(0, 5).map((f) => <FeatureCard key={f.id} feature={f} onOpen={setSelected} />)}
                  {settled.length > 5 && (
                    <button className="ghost tiny" onClick={() => setScreen('board')}>
                      see all {features.length} on the board →
                    </button>
                  )}
                </>
              )}
            </div>
          )}
        </div>
      </div>

    </div>
  )
}

function FeatureCard({ feature, onOpen }) {
  const gate = feature.status === 'awaiting_approval'
  return (
    <button
      className={`fcard ${gate ? 'gate' : ''} ${needsYou(feature) && !gate ? 'blocked' : ''}`}
      onClick={() => onOpen(feature.id)}
    >
      <div className="top">
        <span className="title">{feature.title}</span>
        <Pill status={feature.status} busy={feature.busy} />
      </div>
      <div className="under">
        <Rail status={feature.status} />
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

function Detail({
  feature, logs, replay, browser, connected, tab, setTab, diff, evidence, onBack, act, onDiscard,
}) {
  const gated = feature.status === 'awaiting_approval'
  const stopped = !gated && Boolean(NEEDS_YOU[feature.status])

  // Only offer a tab when there is something behind it. An empty panel reads as broken; a tab
  // that is simply absent reads as "this run has not got there yet", which is the truth.
  const has = feature.has ?? {}
  const tabs = ['plan', 'live', 'diff', 'browser', 'evidence'].filter((name) => {
    if (name === 'plan') return Boolean(feature.plan)
    if (name === 'live') return logs.length > 0 || has.log
    return has[name]
  })

  useEffect(() => {
    if (tabs.length && !tabs.includes(tab)) setTab(tabs[0])
  }, [tabs.join(), tab])
  return (
    <div className={`stage-inner wide ${gated || stopped ? 'gated' : ''}`}>
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
        <Rail status={feature.status} large />
        <StageLegend status={feature.status} />
      </div>

      <div className="tabs">
        {tabs.map((name) => (
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
          {!gated && NEEDS_YOU[feature.status] && (
            <Stopped
              feature={feature}
              busy={feature.busy}
              onPivot={(intent) => act(() => api.pivot(feature.id, intent))}
              onRetry={feature.plan ? () => act(() => api.retry(feature.id)) : null}
              onDiscard={() => act(async () => { await api.decline(feature.id); onDiscard() })}
            />
          )}
          {!NEEDS_YOU[feature.status] && PIVOTABLE.includes(feature.status) && (
            <Pivot busy={feature.busy} onPivot={(intent) => act(() => api.pivot(feature.id, intent))} />
          )}
        </>
      )}
      {tab === 'live' && <Log lines={logs} replay={replay} connected={connected} />}
      {tab === 'diff' && <Diff text={diff?.diff} repos={diff?.repos} />}
      {tab === 'browser' && <BrowserChecks featureId={feature.id} data={browser} />}
      {tab === 'evidence' && <pre className="block">{evidence}</pre>}
    </div>
  )
}

/**
 * A run that stopped needs a decision as much as a plan does, so it gets the same pinned bar.
 * Previously these looked like finished work with an unusual label.
 */
function Stopped({ feature, busy, onPivot, onRetry, onDiscard }) {
  const [intent, setIntent] = useState('')
  const info = NEEDS_YOU[feature.status]
  const send = () => {
    const text = intent.trim()
    if (!text) return
    setIntent('')
    onPivot(text)
  }
  return (
    <div className="gatebar stopped">
      <div className="reason">{info.why}</div>
      {feature.error && <pre className="failure">{feature.error}</pre>}
      <textarea
        rows={2}
        placeholder="Tell it what to do differently, and it will re-plan on the same branch…"
        value={intent}
        disabled={busy}
        onChange={(e) => setIntent(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) send() }}
      />
      <div className="row">
        <span className="eyebrow">{info.verb}</span>
        <span className="grow" />
        <button className="ghost danger" disabled={busy} onClick={onDiscard}>Discard</button>
        {onRetry && (
          <button disabled={busy} onClick={onRetry} title="Run the same plan again, unchanged">
            Try again
          </button>
        )}
        <button className="primary" disabled={busy || !intent.trim()} onClick={send}>
          Re-plan with this
        </button>
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
