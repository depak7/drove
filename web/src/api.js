const json = async (res) => {
  if (!res.ok) throw new Error((await res.text()) || res.statusText)
  return res.json()
}

/**
 * GET with a short retry.
 *
 * The engine is a separate process. It is slow to boot, and it can be restarted underneath a
 * window that stays open. A connection refused a second after launch is not an error worth
 * showing anyone — it is the app being early.
 */
const get = async (url, attempts = 5) => {
  for (let i = 0; i < attempts; i += 1) {
    try {
      return await fetch(url).then(json)
    } catch (err) {
      const refused = err instanceof TypeError || /fetch|network|refused/i.test(String(err.message))
      if (!refused || i === attempts - 1) throw err
      await new Promise((r) => setTimeout(r, 400 * (i + 1)))
    }
  }
  return undefined
}

const post = (url, body) =>
  fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body ?? {}),
  }).then(json)

export const api = {
  health: () => get('/api/health'),
  browseRepos: (path) => fetch(`/api/repos/browse${path ? `?path=${encodeURIComponent(path)}` : ''}`).then(json),

  workspaces: () => get('/api/workspaces'),
  harnesses: () => get('/api/harnesses'),
  runs: (ws) => get(`/api/workspaces/${ws}/runs`),
  agents: (ws) => get(`/api/workspaces/${ws}/agents`),
  saveSettings: (id, harness, models) =>
    fetch(`/api/workspaces/${id}/settings`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ harness, models }),
    }).then(json),
  createWorkspace: (name, repos = []) => post('/api/workspaces', { name, repos }),
  addRepo: (id, path) => post(`/api/workspaces/${id}/repos`, { path }),
  removeRepo: (id, path) =>
    fetch(`/api/workspaces/${id}/repos?path=${encodeURIComponent(path)}`, {
      method: 'DELETE',
    }).then(json),

  features: (workspaceId) =>
    get(workspaceId ? `/api/features?workspace_id=${workspaceId}` : '/api/features'),
  feature: (id) => get(`/api/features/${id}`),
  diff: (id) => get(`/api/features/${id}/diff`),
  evidence: (id) => get(`/api/features/${id}/evidence`),
  log: (id) => get(`/api/features/${id}/log`),
  browser: (id) => get(`/api/features/${id}/browser`),
  review: (id) => get(`/api/features/${id}/review`),
  source: (id) => get(`/api/features/${id}/source`),
  push: (id) => post(`/api/features/${id}/push`),
  retry: (id) => post(`/api/features/${id}/retry`),
  cancel: (id) => post(`/api/features/${id}/cancel`),
  create: (task, workspaceId) =>
    post('/api/features', { task, workspace_id: workspaceId }),
  revise: (id, feedback) => post(`/api/features/${id}/revise`, { feedback }),
  approve: (id) => post(`/api/features/${id}/approve`),
  decline: (id) => post(`/api/features/${id}/decline`),
  pivot: (id, intent) => post(`/api/features/${id}/pivot`, { intent }),
}
