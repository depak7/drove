import { useEffect, useRef, useState } from 'react'
import { STAGES, STAGE_COLOR, label, progress } from './stages'

export function Pill({ status }) {
  // The status now names the stage the run is in; overriding it with "executing" whenever a job
  // was attached made every running feature claim to be implementing, mid-plan and mid-review.
  const [tone, text] = label(status)
  return (
    <span className={`pill ${tone}`}>
      <i />
      {text}
    </span>
  )
}

/**
 * The signature element: five segments, one per pipeline stage.
 * Done is green, the current stage pulses in its own hue, a failure is red.
 * You can read a feature's state from three metres away.
 */
export function Rail({ status, large = false }) {
  const { at, state } = progress(status)
  return (
    <div className={`rail ${large ? 'lg' : ''}`}>
      {STAGES.map((stage, i) => {
        let cls = ''
        if (i < at) cls = 'done'
        else if (i === at) cls = state === 'failed' ? 'fail' : state === 'running' ? 'now' : ''
        return (
          <span
            key={stage}
            className={`seg ${cls}`}
            style={{
              '--seg': STAGE_COLOR[stage],
              ...(i === at && state === 'gate' ? { background: 'var(--accent)' } : null),
              ...(i === at && state === 'stopped' ? { background: 'var(--line-strong)' } : null),
            }}
            title={stage}
          />
        )
      })}
    </div>
  )
}

export function StageLegend({ status }) {
  const { at, state } = progress(status)
  return (
    <div className="stagerow">
      {STAGES.map((stage, i) => (
        <span key={stage} className={`s ${i < at ? 'done' : i === at && state !== 'stopped' ? 'now' : ''}`}>
          {stage}
        </span>
      ))}
    </div>
  )
}

/**
 * Model prose arrives as one dense block. Break it so it can be read.
 *
 * Grouped by length rather than by sentence count: planners write long sentences, and three of
 * them at two hundred characters each is the wall this exists to prevent, while three short ones
 * are a normal paragraph. A blank line in the source is always honoured; a single sentence is
 * never split, however long it is.
 */
const BLOCK_CHARS = 260

export function Prose({ text, limit = BLOCK_CHARS }) {
  const paragraphs = String(text ?? '').trim().split(/\n\s*\n/).filter(Boolean)

  const blocks = paragraphs.flatMap((para) => {
    const clean = para.replace(/\s+/g, ' ').trim()
    const sentences = clean.match(/[^.!?]+[.!?]+(\s|$)|[^.!?]+$/g)
    if (!sentences || sentences.length < 2) return [clean]

    const out = []
    let current = ''
    for (const sentence of sentences) {
      // Start a new block once this one has had its say, but never mid-sentence.
      if (current && (current + sentence).length > limit) {
        out.push(current.trim())
        current = sentence
      } else {
        current += sentence
      }
    }
    if (current.trim()) out.push(current.trim())
    return out
  })

  return <div className="prose">{blocks.map((b, i) => <p key={i}>{b}</p>)}</div>
}

/** Planners prefix their strongest risks with a label; pull it out so it can be scanned. */
function Risk({ text }) {
  const match = String(text).match(/^([A-Z][A-Z \-]{3,40}?)\s*[:—-]\s*(.+)$/s)
  return (
    <div className="risk">
      <span />
      <span>
        {match && <b className="risk-tag">{match[1].trim()}</b>}
        {match ? match[2].trim() : text}
      </span>
    </div>
  )
}

export function Plan({ plan }) {
  if (!plan) return <div className="card"><p className="mono" style={{ color: 'var(--text-3)' }}>no plan yet</p></div>
  return (
    <>
      <div className="card">
        <h3 className="eyebrow">Approach</h3>
        <Prose text={plan.summary} />
      </div>

      {plan.steps?.length > 0 && (
        <div className="card">
          <h3 className="eyebrow">Steps</h3>
          <div className="steps">
            {plan.steps.map((step, i) => (
              <div className="step" key={i}>
                <span className="n">{i + 1}</span>
                <div>
                  <div>{step.title}</div>
                  {step.files?.length > 0 && <div className="files mono">{step.files.join('  ·  ')}</div>}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {plan.acceptance_criteria?.length > 0 && (
        <div className="card">
          <h3 className="eyebrow">Done when</h3>
          <ul>{plan.acceptance_criteria.map((c, i) => <li key={i}>{c}</li>)}</ul>
        </div>
      )}

      {plan.risks?.length > 0 && (
        <div className="card">
          <h3 className="eyebrow">Risks &amp; assumptions</h3>
          {plan.risks.map((r, i) => <Risk text={r} key={i} />)}
        </div>
      )}
    </>
  )
}

/**
 * The gate: the one decision the product asks of you.
 *
 * Pinned to the bottom of the pane rather than placed after the plan. A plan runs to several
 * screens — approach, steps, criteria, risks — and burying the only action under all of it means
 * the thing the product exists to ask never stays in view.
 */
export function Gate({ busy, onApprove, onDecline, onRevise }) {
  const [feedback, setFeedback] = useState('')
  const [open, setOpen] = useState(false)

  const send = () => {
    const text = feedback.trim()
    if (!text) return
    setFeedback('')
    setOpen(false)
    onRevise(text)
  }

  return (
    <div className="gatebar">
      {open && (
        <textarea
          autoFocus
          rows={2}
          placeholder="What should change? — “use a token bucket, skip the middleware”"
          value={feedback}
          disabled={busy}
          onChange={(e) => setFeedback(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) send()
            if (e.key === 'Escape') setOpen(false)
          }}
        />
      )}
      <div className="row">
        <span className="eyebrow">Your call</span>
        <span className="grow" />
        <button className="ghost danger" disabled={busy} onClick={onDecline}>Discard</button>
        {open ? (
          <button disabled={busy || !feedback.trim()} onClick={send}>Send feedback</button>
        ) : (
          <button disabled={busy} onClick={() => setOpen(true)}>Revise…</button>
        )}
        <button className="primary" disabled={busy} onClick={onApprove}>Approve &amp; run</button>
      </div>
    </div>
  )
}

export function Log({ lines, replay, connected }) {
  const ref = useRef(null)
  const pinned = useRef(true)

  // Follow the tail, but stop fighting the user the moment they scroll up to read something.
  const onScroll = () => {
    const el = ref.current
    if (!el) return
    pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40
  }
  useEffect(() => {
    if (pinned.current && ref.current) ref.current.scrollTop = ref.current.scrollHeight
  }, [lines])

  return (
    <div className="card">
      <h3 className="eyebrow">
        {lines.length > 0 ? (connected ? 'Live' : 'Reconnecting…') : 'Recorded'}
      </h3>
      <div className="log" ref={ref} onScroll={onScroll}>
        {lines.map((line, i) => <div className={line.tone} key={i}>{line.text}</div>)}

        {/* Nothing is streaming, so replay what the run recorded. The live view only exists while
            a tab is open, which left a finished or failed run with an empty panel. */}
        {lines.length === 0 && replay?.stages?.map((s) => (
          <div key={s.stage}>
            <div className="stage">{s.stage} · {s.harness}</div>
            {s.lines.map((line, i) => <div className={line.tone} key={i}>{line.text}</div>)}
          </div>
        ))}

        {lines.length === 0 && !replay?.stages?.length && (
          <div className="dim">nothing recorded for this run</div>
        )}
      </div>
    </div>
  )
}

/** One line of a unified diff, classified for colour. */
const META = ['+++', '---', 'diff ', 'index ', 'new file', 'deleted file', 'old mode', 'new mode']

function diffClass(line) {
  if (META.some((prefix) => line.startsWith(prefix))) return 'meta'
  if (line.startsWith('@@')) return 'hunk'
  if (line.startsWith('+')) return 'add'
  if (line.startsWith('-')) return 'del'
  return ''
}

/** A unified diff with line numbers down both sides, the way a review tool shows it. */
export function Hunks({ text }) {
  if (!text?.trim()) return <p className="dim">No textual changes.</p>
  let before = 0
  let after = 0
  return (
    <pre className="block diff numbered">
      {text.split('\n').map((line, i) => {
        const cls = diffClass(line)
        if (cls === 'hunk') {
          const m = line.match(/@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/)
          if (m) { before = Number(m[1]); after = Number(m[2]) }
          return <div className="hunk" key={i}><i /><i />{line}</div>
        }
        if (cls === 'meta') return <div className="meta" key={i}><i /><i />{line}</div>
        const l = cls === 'add' ? null : before++
        const r = cls === 'del' ? null : after++
        return (
          <div className={cls} key={i}>
            <i>{l ?? ''}</i><i>{r ?? ''}</i>{line || ' '}
          </div>
        )
      })}
    </pre>
  )
}

export function Blob({ blob }) {
  // A file the feature changed is more useful as a diff than as text; everything else is text.
  const [asDiff, setAsDiff] = useState(true)
  const changed = Boolean(blob.diff?.trim())
  useEffect(() => { setAsDiff(true) }, [blob.path])

  return (
    <>
      <div className="fileview-head">
        <b className="mono">{blob.path}</b>
        <span className="grow" />
        {changed && (
          <div className="toggle">
            <button className={asDiff ? 'on' : ''} onClick={() => setAsDiff(true)}>changes</button>
            <button className={!asDiff ? 'on' : ''} onClick={() => setAsDiff(false)}>file</button>
          </div>
        )}
      </div>
      {blob.note && <p className="dim pad">{blob.note}</p>}
      {changed && asDiff ? (
        <Hunks text={blob.diff} />
      ) : (
        <pre className="block code">
          {(blob.text || '').split('\n').map((line, i) => (
            <div key={i}><i>{i + 1}</i>{line || ' '}</div>
          ))}
        </pre>
      )}
    </>
  )
}

/**
 * Group a flat list of changed files into the directory tree they came from.
 *
 * Thirty changed files as a flat list is thirty near-identical paths sharing a prefix you have to
 * read past every time. Nested, you see the shape of the change — "it's all in the harness layer"
 * — before you read a single filename.
 *
 * Chains with one child collapse into one row, so `web/src/components/provenance` is a single
 * line rather than four rows of scaffolding holding one file.
 */
export function treeify(files) {
  const root = { dirs: new Map(), files: [] }
  for (const file of files) {
    const parts = file.path.split('/')
    const name = parts.pop()
    let node = root
    for (const part of parts) {
      if (!node.dirs.has(part)) node.dirs.set(part, { dirs: new Map(), files: [] })
      node = node.dirs.get(part)
    }
    node.files.push({ ...file, name })
  }

  const flatten = (node, label) => {
    // A directory holding exactly one directory and nothing else is scaffolding, not structure.
    while (node.dirs.size === 1 && node.files.length === 0) {
      const [only, child] = [...node.dirs.entries()][0]
      label = label ? `${label}/${only}` : only
      node = child
    }
    return {
      label,
      dirs: [...node.dirs.entries()]
        .map(([name, child]) => flatten(child, name))
        .sort((a, b) => a.label.localeCompare(b.label)),
      files: node.files.sort((a, b) => a.name.localeCompare(b.name)),
    }
  }
  return flatten(root, '')
}

/** A + M next to a filename, the way `git status --short` would put it. */
const MARK = { added: 'A', modified: 'M', deleted: 'D' }

function FileRow({ file, selected, onSelect, depth }) {
  const key = `${file.repo}:${file.path}`
  return (
    <button
      className={`fileitem ${selected === key ? 'on' : ''}`}
      style={{ paddingLeft: 8 + depth * 14 }}
      onClick={() => onSelect(file)}
      title={file.path}
    >
      <span className={`dot ${file.status}`} />
      <span className="fname">{file.name ?? file.path}</span>
      {file.binary ? (
        <span className="counts dim">bin</span>
      ) : (
        <span className="counts">
          {file.added > 0 && <b className="add">+{file.added}</b>}
          {file.removed > 0 && <b className="del"> −{file.removed}</b>}
          <i className={`mark ${file.status}`}>{MARK[file.status] ?? 'M'}</i>
        </span>
      )}
    </button>
  )
}

function Branch({ node, selected, onSelect, depth = 0 }) {
  return (
    <>
      {node.label && (
        <div className="treedir" style={{ paddingLeft: 8 + depth * 14 }}>
          {node.label.split('/').join(' / ')}
        </div>
      )}
      {node.dirs.map((dir) => (
        <Branch
          key={dir.label}
          node={dir}
          selected={selected}
          onSelect={onSelect}
          depth={node.label ? depth + 1 : depth}
        />
      ))}
      {node.files.map((file) => (
        <FileRow
          key={`${file.repo}:${file.path}`}
          file={file}
          selected={selected}
          onSelect={onSelect}
          depth={node.label ? depth + 1 : depth}
        />
      ))}
    </>
  )
}

/** The changed files, nested. */
export function Changes({ files, selected, onSelect }) {
  const byRepo = new Map()
  for (const file of files) {
    if (!byRepo.has(file.repo)) byRepo.set(file.repo, [])
    byRepo.get(file.repo).push(file)
  }
  const many = byRepo.size > 1
  return (
    <>
      {[...byRepo.entries()].map(([repo, list]) => (
        <div key={repo}>
          {many && <div className="treerepo">{repo}</div>}
          <Branch node={treeify(list)} selected={selected} onSelect={onSelect} />
        </div>
      ))}
    </>
  )
}

/**
 * What the app looked like when it was opened.
 *
 * Tests prove the code is correct; they cannot tell you the page rendered blank because one
 * component threw. This is the shallowest check that it actually runs — and the screenshot is
 * usually the fastest way to see that it did not.
 */
export function BrowserChecks({ featureId, data }) {
  if (!data?.checks?.length) {
    return <div className="card"><p style={{ color: 'var(--text-3)' }}>
      No browser checks recorded. Add a <code>[browser]</code> section to
      <code> .drove.toml</code> to have Drove open the app after each run.
    </p></div>
  }

  return (
    <>
      {data.checks.map((check) => {
        const problems = [
          ...check.console_errors.map((e) => ['console', e]),
          ...check.failed_requests.map((r) => ['request', r]),
          ...(check.error ? [['load', check.error]] : []),
        ]
        return (
          <div className={`card shot ${problems.length ? 'bad' : ''}`} key={check.path}>
            <div className="shot-head">
              <b className="mono">{check.path}</b>
              <span className="dim">{check.title}</span>
              <span className="grow" />
              <span className={problems.length ? 'flag' : 'okmark'}>
                {problems.length ? `${problems.length} problem${problems.length === 1 ? '' : 's'}` : 'clean'}
              </span>
            </div>

            {problems.length > 0 && (
              <ul className="problems">
                {problems.map(([kind, text], i) => (
                  <li key={i}><em>{kind}</em> {text}</li>
                ))}
              </ul>
            )}

            {check.screenshot && (
              <img
                className="screenshot"
                alt={`${check.path} rendered`}
                loading="lazy"
                src={`/api/features/${featureId}/screens/${check.screenshot}`}
              />
            )}
          </div>
        )
      })}
    </>
  )
}


/**
 * The review: who judged the change, and what they said.
 *
 * The independence line is the product's actual claim, so it is stated first and stated honestly —
 * including when it does not hold, because one harness reviewing its own work is a materially
 * weaker result and hiding that would make the whole pack untrustworthy.
 */
export function Review({ data }) {
  const rounds = data?.reviews ?? []
  if (!rounds.length) {
    return <div className="card"><p style={{ color: 'var(--text-3)' }}>
      Not reviewed yet. Review runs after the change is committed.
    </p></div>
  }

  const who = (stage) => {
    const s = data.stages?.[stage]
    if (!s) return null
    const detail = [s.lab, s.model].filter(Boolean).join(' · ')
    return { harness: s.harness, detail }
  }
  const builder = who('execute')
  const reviewer = who('review')

  return (
    <>
      <div className={`card indep ${data.independent ? '' : 'weak'}`}>
        {data.independent ? (
          <>
            <div className="vs">
              <span className="side">
                <b>{builder?.harness}</b>
                <em>{builder?.detail}</em>
                <span className="role">wrote it</span>
              </span>
              <span className="arrow">judged by</span>
              <span className="side">
                <b>{reviewer?.harness}</b>
                <em>{reviewer?.detail}</em>
                <span className="role">reviewed it</span>
              </span>
            </div>
            <p className="fine">
              A different tool from a different lab, given only the approved intent and the diff —
              never the implementer's reasoning — in a fresh session each round.
            </p>
          </>
        ) : (
          <p className="fine warnish">
            <b>{builder?.harness}</b> both wrote and reviewed this change. A model that has just
            argued for an approach tends to accept it, so this verdict is weaker than an
            independent one. Set a different harness for Review in Stages.
          </p>
        )}
      </div>

      {rounds.map((round, i) => (
        <div className={`card round ${round.verdict}`} key={i}>
          <div className="round-head">
            <span className="n">Round {i + 1}</span>
            <span className={`verdict ${round.verdict}`}>
              {round.verdict === 'pass' ? 'passed' : 'changes requested'}
            </span>
          </div>
          {round.summary && <p className="sum">{round.summary}</p>}
          {round.blocking?.length > 0 && (
            <ul className="blocking">
              {round.blocking.map((issue, j) => (
                <li key={j}>
                  <code>{issue.file}{issue.line ? `:${issue.line}` : ''}</code>
                  <span className={`sev ${issue.severity}`}>{issue.severity}</span>
                  <span>{issue.why}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      ))}

      {rounds.length > 1 && rounds[rounds.length - 1].verdict === 'pass' && (
        <p className="fine" style={{ color: 'var(--text-3)' }}>
          The issues raised in round {rounds.length - 1} were addressed, and a fresh reviewer —
          with no memory of having raised them — passed the result.
        </p>
      )}
    </>
  )
}


/**
 * The evidence pack, rendered from its structured form.
 *
 * It used to be the markdown file dumped into a <pre>, which is a monospace wall nobody reads —
 * the least readable presentation of the most important document. The .md still exists for
 * sending to someone outside the app; in here the same data is laid out.
 */
/**
 * Where the branch actually lives.
 *
 * A branch name is not an address. Until a run publishes it, the only way to see the work is to
 * know this machine's worktree paths — so the link is the thing this panel exists to give you.
 */
export function Source({ data, busy, onPush }) {
  const repos = data?.repos ?? []
  const publish = onPush && (
    <button className="primary" disabled={busy} onClick={onPush}>
      {repos.length ? 'Push again' : 'Publish branch'}
    </button>
  )
  if (!repos.length) {
    return (
      <div className="card">
        <p style={{ color: 'var(--text-3)' }}>
          Not published. A branch is pushed once review and your checks pass; set{' '}
          <code>push = false</code> under <code>[deliver]</code> in <code>.drove.toml</code> to keep
          a repo's branches local.
        </p>
        {publish}
      </div>
    )
  }
  return (
    <>
      {repos.length > 1 && (
        <div className="card note">
          These branches share a name and have to be merged together. Opening one without the
          others breaks the build.
        </div>
      )}
      {repos.map((repo) => (
        <div key={repo.repo} className={`card source ${repo.pushed ? '' : 'bad'}`}>
          <div className="shot-head">
            <b>{repo.repo}</b>
            <span className="mono dim">{repo.branch}</span>
            <span className="grow" />
            <span className={repo.pushed ? 'okmark' : 'flag'}>
              {repo.pushed ? 'pushed' : 'not pushed'}
            </span>
          </div>
          {repo.url ? (
            <a className="srclink" href={repo.url} target="_blank" rel="noreferrer">
              Open on the web ↗
            </a>
          ) : (
            repo.pushed && <p className="dim mono">{repo.remote}</p>
          )}
          {!repo.pushed && <p className="dim">{repo.skipped || repo.error}</p>}
        </div>
      ))}
      {publish && <div className="row">{publish}</div>}
    </>
  )
}

export function Evidence({ pack, markdown, onCopy }) {
  if (!pack || !Object.keys(pack).length) {
    return <div className="card"><p style={{ color: 'var(--text-3)' }}>
      No evidence pack yet — one is written when a run finishes.
    </p></div>
  }

  const verify = pack.verify ?? []
  const failed = verify.filter((c) => c.exit_code !== 0)

  return (
    <>
      <div className="card">
        <div className="ev-head">
          <h3 className="eyebrow">Evidence</h3>
          <span className="grow" />
          {markdown && (
            <button className="ghost tiny" onClick={() => onCopy?.(markdown)}>
              Copy as markdown
            </button>
          )}
        </div>

        <div className="factrow">
          <Fact k="Branch" v={pack.branch} mono />
          <Fact k="Into" v={pack.base} mono />
          <Fact k="Commit" v={(pack.head_sha ?? '—').slice(0, 12)} mono />
          <Fact
            k="Reviewed by"
            v={pack.independent_review ? (pack.stages?.review?.harness ?? 'yes') : 'same harness'}
            warn={!pack.independent_review}
          />
          <Fact
            k="Checks"
            v={verify.length ? (failed.length ? `${failed.length} failed` : 'all passed') : 'none set'}
            warn={Boolean(failed.length) || !verify.length}
          />
          <Fact
            k="Cost"
            v={pack.cost_usd != null ? `$${pack.cost_usd.toFixed(4)}` : '—'}
          />
        </div>
      </div>

      {pack.diff_stat && (
        <div className="card">
          <h3 className="eyebrow">Files changed</h3>
          <pre className="statblock">{pack.diff_stat.trim()}</pre>
        </div>
      )}

      {verify.length > 0 && (
        <div className="card">
          <h3 className="eyebrow">Checks</h3>
          {verify.map((check, i) => (
            <details className="check-item" key={i} open={check.exit_code !== 0}>
              <summary>
                <span className={check.exit_code === 0 ? 'ok' : 'bad'}>
                  {check.exit_code === 0 ? 'passed' : `failed (${check.exit_code})`}
                </span>
                <b>{check.repo ? `${check.repo}/${check.name}` : check.name}</b>
                <code>{check.command}</code>
                <span className="dur">{check.duration_s}s</span>
              </summary>
              <pre className="statblock">{(check.output || '(no output)').trim()}</pre>
            </details>
          ))}
        </div>
      )}

      {pack.files_changed?.length > 0 && !pack.diff_stat && (
        <div className="card">
          <h3 className="eyebrow">Files changed</h3>
          <ul>{pack.files_changed.map((f) => <li key={f}><code>{f}</code></li>)}</ul>
        </div>
      )}

      {Object.keys(pack.sessions ?? {}).length > 0 && (
        <div className="card">
          <h3 className="eyebrow">Reopen any of this</h3>
          <p style={{ fontSize: 12.5, color: 'var(--text-3)', margin: '0 0 10px' }}>
            Each is a real conversation you can continue from its worktree.
          </p>
          {Object.entries(pack.sessions).map(([stage, id]) => (
            <div className="sessrow" key={stage}>
              <b>{stage}</b>
              <code>{id}</code>
            </div>
          ))}
        </div>
      )}
    </>
  )
}

function Fact({ k, v, mono, warn }) {
  // An empty string renders as nothing, which reads as a broken panel rather than a missing value.
  const shown = v === undefined || v === null || v === '' ? '—' : v
  return (
    <div className="fact">
      <span className="k">{k}</span>
      <span className={`v ${mono ? 'mono' : ''} ${warn ? 'warn' : ''}`}>{shown}</span>
    </div>
  )
}
