import { useEffect, useRef, useState } from 'react'

/**
 * Live run events over SSE.
 *
 * Log lines are kept per feature and capped: a long run emits thousands, and an unbounded array
 * makes the tab crawl long before the run finishes. EventSource reconnects on its own, so there
 * is no retry logic here — only a status flag so the UI can say when it is not connected.
 */
const MAX_LINES = 400

export function useStream(onStatus) {
  const [connected, setConnected] = useState(false)
  const [logs, setLogs] = useState({})
  const statusRef = useRef(onStatus)
  statusRef.current = onStatus

  useEffect(() => {
    const source = new EventSource('/api/events')
    source.addEventListener('ready', () => setConnected(true))
    source.onerror = () => setConnected(false)
    source.onmessage = (message) => {
      const event = JSON.parse(message.data)
      const { feature_id: id, kind } = event

      if (kind === 'status' || kind === 'done' || kind === 'plan') statusRef.current?.(event)

      const line = describe(event)
      if (!line) return
      setLogs((prev) => {
        const next = [...(prev[id] ?? []), line]
        return { ...prev, [id]: next.slice(-MAX_LINES) }
      })
    }
    return () => source.close()
  }, [])

  return { connected, logs }
}

function describe(event) {
  switch (event.kind) {
    case 'stage':
      return { tone: 'stage', text: `${event.stage}: ${event.message}` }
    case 'tool':
      return { tone: 'dim', text: `${event.name} ${event.hint ?? ''}` }
    case 'text':
      return { tone: 'text', text: event.text }
    case 'drift':
      return { tone: 'warn', text: `${event.base} advanced ${event.commits} commit(s)` }
    case 'rate_limit':
      return event.utilization > 0.8
        ? { tone: 'warn', text: `rate limit window ${Math.round(event.utilization * 100)}% used` }
        : null
    case 'error':
      return { tone: 'error', text: event.message }
    case 'done':
      return { tone: 'stage', text: `${event.status}${event.note ? ` — ${event.note}` : ''}` }
    default:
      return null
  }
}
