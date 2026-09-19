import { useEffect, useRef, useState } from 'react'
import '@xterm/xterm/css/xterm.css'
import { api } from './api'
import { Blob } from './components'

/**
 * Reading the code the agents are working on, without leaving the app.
 *
 * The diff answers "what changed". This answers the question that follows it — what the file
 * looks like now, and what else is around it — which otherwise means finding the worktree path
 * and opening an editor on a checkout you did not make.
 */
export function Code({ featureId }) {
  const [dirs, setDirs] = useState({})          // path -> entries
  const [open, setOpen] = useState({ '': true })
  const [picked, setPicked] = useState(null)
  const [blob, setBlob] = useState(null)
  const [error, setError] = useState('')

  const load = (path) => {
    if (dirs[path]) return
    api.tree(featureId, path)
      .then((body) => setDirs((d) => ({ ...d, [path]: body.entries })))
      .catch((e) => setError(String(e.message || e)))
  }

  useEffect(() => { load('') }, [featureId])

  const toggle = (path) => {
    setOpen((o) => ({ ...o, [path]: !o[path] }))
    load(path)
  }

  const show = (path) => {
    setPicked(path)
    setBlob(null)
    api.blob(featureId, path).then(setBlob).catch((e) => setError(String(e.message || e)))
  }

  const rows = (path, depth) => (dirs[path] ?? []).map((entry) => (
    <div key={entry.path}>
      <button
        className={`treerow ${picked === entry.path ? 'on' : ''}`}
        style={{ paddingLeft: 8 + depth * 13 }}
        onClick={() => (entry.dir ? toggle(entry.path) : show(entry.path))}
      >
        <span className="twisty">{entry.dir ? (open[entry.path] ? '▾' : '▸') : ''}</span>
        <span className={entry.dir ? 'tdir' : ''}>{entry.name}</span>
      </button>
      {entry.dir && open[entry.path] && rows(entry.path, depth + 1)}
    </div>
  ))

  if (error) return <div className="card"><p className="dim">{error}</p></div>

  return (
    <div className="split">
      <div className="filelist tree">{rows('', 0)}</div>
      <div className="fileview">
        {blob ? <Blob blob={blob} /> : <p className="dim pad">Pick a file to read it.</p>}
      </div>
    </div>
  )
}

/**
 * A real shell in the feature's worktree.
 *
 * Agents work in a checkout you did not make, in a directory you would otherwise have to go and
 * find. Sometimes the fastest thing is to run the tests yourself, read `git log`, or fix one line
 * by hand — and having to leave the app to do it is what makes people stop trusting it.
 *
 * The pty lives in the daemon rather than in Electron, so this works the same in a browser and in
 * the packaged app instead of existing in only one of them.
 */
export function Terminal({ featureId }) {
  const host = useRef(null)
  const [state, setState] = useState('connecting')

  useEffect(() => {
    let term, fit, socket, observer, disposed = false

    ;(async () => {
      const [{ Terminal: Xterm }, { FitAddon }] = await Promise.all([
        import('@xterm/xterm'),
        import('@xterm/addon-fit'),
      ])
      if (disposed || !host.current) return

      term = new Xterm({
        fontSize: 12,
        fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
        cursorBlink: true,
        theme: {
          background: '#120C0C', foreground: '#E9DEDA', cursor: '#C0442F',
          selectionBackground: '#C0442F44',
        },
      })
      fit = new FitAddon()
      term.loadAddon(fit)
      term.open(host.current)
      fit.fit()

      const url = new URL(`/api/features/${featureId}/terminal`, window.location.href)
      url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
      socket = new WebSocket(url)
      socket.binaryType = 'arraybuffer'

      const size = () => {
        try {
          fit.fit()
          if (socket.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify({ resize: true, rows: term.rows, cols: term.cols }))
          }
        } catch { /* the pane is hidden or gone; the next resize will catch up */ }
      }

      socket.onopen = () => { setState('open'); size(); term.focus() }
      socket.onmessage = (event) => term.write(new Uint8Array(event.data))
      socket.onclose = () => setState('closed')
      socket.onerror = () => setState('closed')
      term.onData((data) => {
        if (socket.readyState === WebSocket.OPEN) socket.send(new TextEncoder().encode(data))
      })

      observer = new ResizeObserver(size)
      observer.observe(host.current)
    })()

    return () => {
      disposed = true
      observer?.disconnect()
      socket?.close()
      term?.dispose()
    }
  }, [featureId])

  return (
    <div className="termwrap">
      {state === 'closed' && (
        <div className="termnote">The shell has exited. Reopen this tab to start another.</div>
      )}
      <div className="term" ref={host} />
    </div>
  )
}
