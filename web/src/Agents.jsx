import { useState } from 'react'

/**
 * Who did what, and how to go and look.
 *
 * Every row is a real conversation. Because the session id is minted before the process starts,
 * it is recorded even for a run that died on its first turn — so the failures are inspectable too,
 * which is exactly when you want to look.
 */
export function Agents({ agents }) {
  const [copied, setCopied] = useState('')

  if (agents.length === 0) {
    return <Blank title="No agents have run yet" body="Start a feature and the sessions appear here." />
  }

  return (
    <div className="agents">
      {agents.map((a) => (
        <div className={`agent ${a.running ? 'on' : ''}`} key={`${a.session_id}-${a.stage}-${a.attempt}`}>
          <div className="who">
            <span className={`beacon ${a.running ? 'live' : ''}`} />
            <div>
              <b>{a.harness}</b>
              <span className="role">{a.role}{a.attempt > 1 ? ` · round ${a.attempt}` : ''}</span>
            </div>
          </div>

          <div className="what">
            <div className="task">{a.title}</div>
            <div className="sid mono" title={a.session_id}>{a.session_id}</div>
          </div>

          <div className="cost">
            <span>{(a.tokens_in + a.tokens_out).toLocaleString()} tok</span>
            <span className="usd">{a.cost_usd != null ? `$${a.cost_usd.toFixed(3)}` : '—'}</span>
          </div>

          <div className="acts">
            {a.resume ? (
              <button
                className="tiny"
                onClick={() => { navigator.clipboard?.writeText(a.resume); setCopied(a.session_id) }}
                title={a.resume}
              >
                {copied === a.session_id ? 'copied' : 'Open session'}
              </button>
            ) : (
              <span className="dim">no resume</span>
            )}
          </div>
        </div>
      ))}
      <p className="foot-note">
        “Open session” copies the command that reattaches you to that agent’s real conversation in
        its worktree. Drove drives the CLIs headlessly, so there is no live terminal to attach to —
        but the transcript is real and you can carry on talking to it.
      </p>
    </div>
  )
}

export function Runs({ runs, onOpen }) {
  if (runs.length === 0) {
    return <Blank title="No runs yet" body="Every approved plan becomes a run, recorded here with what it cost." />
  }
  return (
    <div className="runs">
      <div className="rrow head">
        <span>Feature</span><span>Run</span><span>Status</span><span>Took</span><span>Tokens</span><span>Cost</span>
      </div>
      {runs.map((r) => (
        <button className="rrow" key={r.id} onClick={() => onOpen(r.feature_id)}>
          <span className="ttl">{r.title}</span>
          <span className="dim">#{r.iteration}</span>
          <span className={`st ${r.status}`}>{r.status.replace(/_/g, ' ')}</span>
          <span className="dim">{r.duration_s != null ? fmt(r.duration_s) : '—'}</span>
          <span className="dim">{((r.tokens_in + r.tokens_out) / 1000).toFixed(1)}k</span>
          <span className="dim">{r.cost_usd ? `$${r.cost_usd.toFixed(2)}` : '—'}</span>
        </button>
      ))}
    </div>
  )
}

const fmt = (s) => (s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`)

export function Blank({ title, body }) {
  return (
    <div className="blank">
      <h3>{title}</h3>
      <p>{body}</p>
    </div>
  )
}
