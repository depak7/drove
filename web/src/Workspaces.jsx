import { useState } from 'react'

/** Workspace picker plus repo management — this is how a repo gets added, from the app. */
export function WorkspaceBar({ workspaces, current, onSelect, onCreate, onAddRepo, onRemoveRepo }) {
  const [creating, setCreating] = useState(false)
  const [name, setName] = useState('')
  const [repoPath, setRepoPath] = useState('')
  const [showRepos, setShowRepos] = useState(false)

  const create = () => {
    const text = name.trim()
    if (!text) return
    setName('')
    setCreating(false)
    onCreate(text)
  }

  const addRepo = () => {
    const path = repoPath.trim()
    if (!path) return
    setRepoPath('')
    onAddRepo(path)
  }

  return (
    <div className="wsbar">
      <div className="wsrow">
        <select
          value={current?.id ?? ''}
          onChange={(e) => onSelect(e.target.value)}
          aria-label="Workspace"
        >
          {workspaces.length === 0 && <option value="">no workspaces</option>}
          {workspaces.map((w) => (
            <option key={w.id} value={w.id}>
              {w.name} ({w.repos.length} repo{w.repos.length === 1 ? '' : 's'})
            </option>
          ))}
        </select>
        <button onClick={() => setCreating((v) => !v)} title="New workspace">+</button>
      </div>

      {creating && (
        <div className="wsrow">
          <input
            autoFocus
            placeholder="Workspace name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && create()}
          />
          <button className="primary" onClick={create}>Create</button>
        </div>
      )}

      {current && (
        <>
          <button className="linkish" onClick={() => setShowRepos((v) => !v)}>
            {current.repos.length} repo{current.repos.length === 1 ? '' : 's'}
            {showRepos ? ' ▾' : ' ▸'}
          </button>

          {showRepos && (
            <div className="repolist">
              {current.repos.map((repo) => (
                <div className="repo" key={repo.path}>
                  <div>
                    <div className="mono">{repo.name}</div>
                    <div className="path mono" title={repo.path}>
                      {repo.base_branch}
                      {repo.verify.length > 0 && ` · verify: ${repo.verify.join(', ')}`}
                      {!repo.exists && ' · MISSING'}
                    </div>
                  </div>
                  <button className="danger tiny" onClick={() => onRemoveRepo(repo.path)}>
                    remove
                  </button>
                </div>
              ))}
              <div className="wsrow">
                <input
                  placeholder="/path/to/repo"
                  value={repoPath}
                  onChange={(e) => setRepoPath(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && addRepo()}
                />
                <button onClick={addRepo} disabled={!repoPath.trim()}>Add</button>
              </div>
              {current.repos.length > 1 && (
                <div className="note">
                  A feature here may change several repos at once. Their branches have to be
                  merged together — landing one without the others breaks things.
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  )
}
