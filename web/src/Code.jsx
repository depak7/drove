import { useEffect, useRef, useState } from 'react'
import '@xterm/xterm/css/xterm.css'
import { api } from './api'
import { Blob, Changes } from './components'

/**
 * One panel for the code: what changed, and everything else.
 *
 * These were two tabs — a diff and a file browser — which meant answering "what does this file
 * actually say now" involved leaving the diff, opening the browser, and finding the same file
 * again in a tree. Same tree, same file pane, one toggle. The only thing that changes is which
 * files are listed.
 */
export function Files({ featureId, changes, picked, blob, onPick }) {
  const [mode, setMode] = useState('changes')
  const [dirs, setDirs] = useState({})
  const [open, setOpen] = useState({ '': true })
  const [error, setError] = useState('')

  const files = changes?.files ?? []
  // Nothing changed yet, so there is no "changes" view to default to.
  useEffect(() => { if (changes && !files.length) setMode('all') }, [changes])

  const load = (path) => {
    if (dirs[path]) return
    api.tree(featureId, path)
      .then((body) => setDirs((d) => ({ ...d, [path]: body.entries })))
      .catch((e) => setError(String(e.message || e)))
  }
  useEffect(() => { if (mode === 'all') load('') }, [mode, featureId])

  const toggle = (path) => {
    setOpen((o) => ({ ...o, [path]: !o[path] }))
    load(path)
  }

  const rows = (path, depth) => (dirs[path] ?? []).map((entry) => (
    <div key={entry.path}>
      <button
        className={`treerow ${picked === `:${entry.path}` || picked?.endsWith(`:${entry.path}`) ? 'on' : ''}`}
        style={{ paddingLeft: 8 + depth * 13 }}
        onClick={() => (entry.dir ? toggle(entry.path) : onPick({ repo: '', path: entry.path }))}
      >
        <span className="twisty">{entry.dir ? (open[entry.path] ? '▾' : '▸') : ''}</span>
        <span className={entry.dir ? 'tdir' : ''}>{entry.name}</span>
      </button>
      {entry.dir && open[entry.path] && rows(entry.path, depth + 1)}
    </div>
  ))

  if (changes?.note) return <div className="card"><p className="dim">{changes.note}</p></div>

  return (
    <div className="split">
      <div className="filelist">
        <div className="listhead">
          <div className="toggle">
            <button className={mode === 'changes' ? 'on' : ''} onClick={() => setMode('changes')}>
              changes{files.length ? ` ${files.length}` : ''}
            </button>
            <button className={mode === 'all' ? 'on' : ''} onClick={() => setMode('all')}>
              all files
            </button>
          </div>
        </div>
        {error && <p className="dim pad">{error}</p>}
        {mode === 'changes'
          ? (files.length
              ? <Changes files={files} selected={picked} onSelect={onPick} />
              : <p className="dim pad">Nothing has changed on this branch yet.</p>)
          : <div className="tree">{rows('', 0)}</div>}
      </div>
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
