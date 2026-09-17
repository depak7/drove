const json = async (res) => {
  if (!res.ok) throw new Error((await res.text()) || res.statusText)
  return res.json()
}

const post = (url, body) =>
  fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body ?? {}),
  }).then(json)

export const api = {
  health: () => fetch('/api/health').then(json),
  features: () => fetch('/api/features').then(json),
  feature: (id) => fetch(`/api/features/${id}`).then(json),
  diff: (id) => fetch(`/api/features/${id}/diff`).then(json),
  evidence: (id) => fetch(`/api/features/${id}/evidence`).then(json),
  create: (task) => post('/api/features', { task }),
  revise: (id, feedback) => post(`/api/features/${id}/revise`, { feedback }),
  approve: (id) => post(`/api/features/${id}/approve`),
  decline: (id) => post(`/api/features/${id}/decline`),
  pivot: (id, intent) => post(`/api/features/${id}/pivot`, { intent }),
}
