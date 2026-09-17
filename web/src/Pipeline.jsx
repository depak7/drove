/**
 * The pipeline, shown as a configured machine.
 *
 * The home screen used to be a prompt above empty space, which told a new user nothing about what
 * the product does or that any of it could be changed. This says both at once: five stages, who
 * runs each, on what model — and clicking it is how you change that.
 */
export function PipelineStrip({ workspace, onConfigure }) {
  const h = workspace.harness
  const m = workspace.models ?? {}
  const verify = workspace.repos.flatMap((r) => r.verify)

  const stages = [
    { key: 'plan',    label: 'Plan',    by: h.plan,    model: m.plan,    does: 'Reads your code, writes a plan' },
    { key: 'execute', label: 'Build',   by: h.execute, model: m.execute, does: 'Writes it on an isolated branch' },
    { key: 'review',  label: 'Review',  by: h.review,  model: m.review,  does: 'A different model judges the diff' },
    { key: 'verify',  label: 'Verify',  by: verify.length ? `${verify.length} command${verify.length === 1 ? '' : 's'}` : 'not set',
      does: verify.length ? verify.join(', ') : 'Add [verify] to .drove.toml' },
    { key: 'deliver', label: 'Deliver', by: 'branch', does: 'Plus an evidence pack. Never pushes' },
  ]

  return (
    <section className="pipe">
      <div className="pipe-head">
        <span className="eyebrow">How {workspace.name} runs</span>
        <button className="ghost tiny" onClick={onConfigure}>Configure stages →</button>
      </div>

      <div className="pipe-row">
        {stages.map((s, i) => (
          <div className={`pstage s-${s.key}`} key={s.key}>
            <span className="tick" />
            <b>{s.label}</b>
            <span className={`by ${s.by === 'not set' ? 'unset' : ''}`}>{s.by}</span>
            {s.model && <span className="model">{s.model}</span>}
            <span className="does">{s.does}</span>
            {i === 0 && <span className="gate-note">⏸ you approve</span>}
          </div>
        ))}
      </div>

      {h.review === h.execute && (
        <p className="pipe-warn">
          {h.execute} is set to review its own work. A model that just argued for a shortcut tends
          to accept it — pick a different harness for Review.
        </p>
      )}
    </section>
  )
}
