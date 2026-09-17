import { useState } from 'react'
import { chooseRepository, isDesktop } from './desktop'

/** Workspace picker plus repo chips — this is how a repo gets added, from the app. */
export function WorkspaceBar({ workspaces, current, onSelect, onCreate, onAddRepo, onRemoveRepo }) {
  const [mode, setMode] = useState(null) // 'workspace' | 'repo' | null
  const [value, setValue] = useState('')

  const submit = () => {
    const text = value.trim()
    if (!text) return
    setValue('')
    const was = mode
    setMode(null)
    if (was === 'workspace') onCreate(text)
    else onAddRepo(text)
  }

  return (
    <div className="wsbar">
      <div className="wsrow">
        <select value={current?.id ?? ''} onChange={(e) => onSelect(e.target.value)} aria-label="Workspace">
          {workspaces.length === 0 && <option value="">no workspaces</option>}
          {workspaces.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
        </select>
        <button
          className="ghost"
          onClick={() => { setMode(mode === 'workspace' ? null : 'workspace'); setValue('') }}
          title="New workspace"
        >
          +
        </button>
      </div>

      {current && (
        <div className="repochips">
          {current.repos.map((repo) => (
            <span className={`chip ${repo.exists ? '' : 'missing'}`} key={repo.path} title={repo.path}>
              {repo.name}
              <em style={{ color: 'var(--text-3)', fontStyle: 'normal' }}>{repo.base_branch}</em>
              <span className="x" onClick={() => onRemoveRepo(repo.path)} title="Remove">×</span>
            </span>
          ))}
          <span
            className="chip add"
            onClick={async () => {
              // On the desktop, never make someone type a filesystem path.
              if (isDesktop) {
                const picked = await chooseRepository()
                if (picked) onAddRepo(picked)
                return
              }
              setMode(mode === 'repo' ? null : 'repo')
              setValue('')
            }}
          >
            + repo
          </span>
        </div>
      )}

      {mode && (
        <div className="wsrow">
          <input
            autoFocus
            placeholder={mode === 'workspace' ? 'Workspace name' : '/path/to/repo'}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') submit()
              if (e.key === 'Escape') setMode(null)
            }}
          />
          <button className="primary" onClick={submit} disabled={!value.trim()}>Add</button>
        </div>
      )}

      {current?.repos.length > 1 && (
        <div className="note">A feature here may change several repos in one run.</div>
      )}
    </div>
  )
}

/** Shown when nothing is selected — the old UI wasted this space on one line of grey text. */
export function Overview({ workspace, features, onPick }) {
  const by = (s) => features.filter((f) => f.status === s).length
  const waiting = features.filter((f) => f.status === 'awaiting_approval')
  const running = features.filter((f) => f.busy || ['executing', 'planning'].includes(f.status))

  return (
    <div className="main-inner">
      <div className="dhead">
        <h2>{workspace.name}</h2>
      </div>
      <div className="dmeta">
        <span>{workspace.repos.length} repositor{workspace.repos.length === 1 ? 'y' : 'ies'}</span>
        <span className="sep">·</span>
        <span>{features.length} feature{features.length === 1 ? '' : 's'}</span>
      </div>

      <div className="stats">
        <div className="stat"><div className="k">Awaiting you</div><div className="v">{by('awaiting_approval') + by('needs_human')}</div></div>
        <div className="stat"><div className="k">Running</div><div className="v">{running.length}</div></div>
        <div className="stat"><div className="k">Ready to merge</div><div className="v">{by('delivered')}</div></div>
        <div className="stat"><div className="k">Landed</div><div className="v">{by('landed')}</div></div>
      </div>

      {waiting.length > 0 && (
        <div className="card">
          <h3 className="eyebrow">Waiting on your approval</h3>
          {waiting.map((f) => (
            <button key={f.id} className="picker" onClick={() => onPick(f.id)}>
              <span style={{ flex: 1 }}>{f.title}</span>
              <span className="go">→</span>
            </button>
          ))}
        </div>
      )}

      <div className="card">
        <h3 className="eyebrow">How a run works</h3>
        <p style={{ color: 'var(--text-2)', fontSize: 13 }}>
          You describe a feature. <b style={{ color: 'var(--text)' }}>{workspace.harness.plan}</b> reads
          the code and writes a plan for you to approve, edit or reject.{' '}
          <b style={{ color: 'var(--text)' }}>{workspace.harness.execute}</b> implements it on an
          isolated branch in every repo, then{' '}
          <b style={{ color: 'var(--text)' }}>{workspace.harness.review}</b> — a different model —
          reviews the diff without seeing how it was written. Your own tests run last. You get a
          branch and an evidence pack; your working copy is never touched.
        </p>
      </div>
    </div>
  )
}
