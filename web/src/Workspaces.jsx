import { useState } from 'react'
import { chooseRepository, isDesktop } from './desktop'

/** Workspace switcher and repo management, as a sheet off the top bar. */
export function WorkspaceSheet({ workspaces, current, onClose, onSelect, onCreate, onAddRepo, onRemoveRepo }) {
  const [creating, setCreating] = useState(false)
  const [name, setName] = useState('')
  const [path, setPath] = useState('')
  const [typing, setTyping] = useState(false)

  const addRepo = async () => {
    // On the desktop, never make someone type a filesystem path.
    if (isDesktop && !typing) {
      const picked = await chooseRepository()
      if (picked) onAddRepo(picked)
      return
    }
    const text = path.trim()
    if (!text) return
    setPath('')
    setTyping(false)
    onAddRepo(text)
  }

  return (
    <>
      <div className="sheet-scrim" onClick={onClose} />
      <div className="sheet">
        <span className="eyebrow">Workspaces</span>
        {workspaces.map((w) => (
          <button
            key={w.id}
            className={`ws-item ${w.id === current?.id ? 'on' : ''}`}
            onClick={() => { onSelect(w.id); onClose() }}
          >
            <span>{w.name}</span>
            <small>{w.repos.length} repo{w.repos.length === 1 ? '' : 's'} · {w.features} feature{w.features === 1 ? '' : 's'}</small>
          </button>
        ))}

        {creating ? (
          <div className="wsrow">
            <input
              autoFocus placeholder="Workspace name" value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && name.trim()) { onCreate(name.trim()); setName(''); setCreating(false) }
                if (e.key === 'Escape') setCreating(false)
              }}
            />
            <button className="primary" disabled={!name.trim()} onClick={() => { onCreate(name.trim()); setName(''); setCreating(false) }}>Add</button>
          </div>
        ) : (
          <button className="ghost" onClick={() => setCreating(true)}>+ New workspace</button>
        )}

        {current && (
          <>
            <span className="eyebrow" style={{ marginTop: 4 }}>Repositories in {current.name}</span>
            <div className="repochips">
              {current.repos.map((repo) => (
                <span className={`chip ${repo.exists ? '' : 'missing'}`} key={repo.path} title={repo.path}>
                  {repo.name}
                  <em style={{ color: 'var(--text-3)', fontStyle: 'normal' }}>{repo.base_branch}</em>
                  <span className="x" onClick={() => onRemoveRepo(repo.path)} title="Remove">×</span>
                </span>
              ))}
            </div>
            {typing || !isDesktop ? (
              <div className="wsrow">
                <input
                  autoFocus={typing} placeholder="/path/to/repo" value={path}
                  onChange={(e) => setPath(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter') addRepo() }}
                />
                <button onClick={addRepo} disabled={!path.trim()}>Add</button>
              </div>
            ) : (
              <div className="wsrow">
                <button onClick={addRepo}>Choose folder…</button>
                <button className="ghost" onClick={() => setTyping(true)}>Type a path</button>
              </div>
            )}
            {current.repos.length > 1 && (
              <div className="note">A feature here may change several repos in one run; their
                branches must be merged together.</div>
            )}
          </>
        )}
      </div>
    </>
  )
}
