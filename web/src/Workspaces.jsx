import { useEffect, useState } from 'react'
import { api } from './api'
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
            {isDesktop && !typing ? (
              <div className="wsrow">
                <button onClick={addRepo}>Choose folder…</button>
                <button className="ghost" onClick={() => setTyping(true)}>Type a path</button>
              </div>
            ) : typing ? (
              <div className="wsrow">
                <input
                  autoFocus placeholder="/path/to/repo" value={path}
                  onChange={(e) => setPath(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter') addRepo() }}
                />
                <button onClick={addRepo} disabled={!path.trim()}>Add</button>
                <button className="ghost tiny" onClick={() => setTyping(false)}>browse</button>
              </div>
            ) : (
              <FolderBrowser
                onPick={(p) => onAddRepo(p)}
                onType={() => setTyping(true)}
              />
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


/** Show the tail of a long path; the full one is in the tooltip. */
function shorten(path, keep = 3) {
  const parts = String(path).split('/').filter(Boolean)
  return (parts.length > keep ? '…/' : '/') + parts.slice(-keep).join('/')
}

/**
 * Folder picker for a plain browser tab, where there is no native dialog.
 *
 * Typing an absolute path from memory is a miserable way to add a repository, and it is the one
 * step between installing this and using it. The daemon serves a read-only listing under your
 * home directory; folders that are already Git worktrees are marked so you can see the target.
 */
function FolderBrowser({ onPick, onType }) {
  const [at, setAt] = useState(null)
  const [data, setData] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    api.browseRepos(at).then(setData).catch((e) => setError(String(e.message ?? e)))
  }, [at])

  if (error) {
    return (
      <div className="wsrow">
        <span className="note bad">{error}</span>
        <button className="ghost tiny" onClick={onType}>type a path</button>
      </div>
    )
  }
  if (!data) return <div className="note">loading…</div>

  return (
    <div className="browser">
      <div className="crumbs">
        <button className="ghost tiny" disabled={!data.parent} onClick={() => setAt(data.parent)}>
          ↑ up
        </button>
        <span className="mono path" title={data.path}>{shorten(data.path)}</span>
        <button className="ghost tiny" onClick={onType}>type</button>
      </div>
      <div className="entries">
        {data.entries.length === 0 && <div className="note">no folders here</div>}
        {data.entries.map((entry) => (
          <div className="entry" key={entry.path}>
            <button className="name" onClick={() => setAt(entry.path)}>
              {entry.is_repo ? '◆' : '▸'} {entry.name}
            </button>
            {entry.is_repo && (
              <button className="tiny primary" onClick={() => onPick(entry.path)}>add</button>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
