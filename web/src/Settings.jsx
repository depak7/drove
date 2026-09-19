import { useEffect, useState } from 'react'

const STAGES = [
  ['plan',    'Plan',    'Reads your code and writes the plan you approve.'],
  ['execute', 'Execute', 'Writes the code on an isolated branch.'],
  ['review',  'Review',  'Judges the diff. Must not be the harness that wrote it.'],
  // No arbiter here until there is one. It was offered, saved, and did nothing — a setting that
  // lies about what the product does is worse than a feature that is visibly missing.
]

export function SettingsSheet({ workspace, harnesses: warmed, onClose, onSave }) {
  const [harnesses, setHarnesses] = useState(warmed ?? [])
  const [harness, setHarness] = useState(workspace.harness)
  const [models, setModels] = useState(workspace.models ?? {})
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (warmed?.length) { setHarnesses(warmed); return }
    api_harnesses().then(setHarnesses).catch(() => setHarnesses([]))
  }, [warmed])

  const usable = harnesses.filter((h) => h.installed)
  const modelsFor = (name) => harnesses.find((h) => h.name === name)?.models ?? []
  const noteFor = (name, id) => modelsFor(name).find((m) => m.id === id)?.note ?? ''

  const missing = harnesses.filter((h) => !h.installed)
  const independent = harness.review !== harness.execute

  const loading = harnesses.length === 0

  const save = async () => {
    setSaving(true)
    await onSave(harness, models)
    setSaving(false)
    onClose()
  }

  return (
    <>
      <div className="sheet-scrim" onClick={onClose} />
      <div className="sheet settings">
        <div className="srow-head">
          <span className="eyebrow">Stages · {workspace.name}</span>
          <button className="ghost tiny" onClick={onClose}>esc</button>
        </div>

        {loading && STAGES.map(([key, label, why]) => (
          <div className="stagecfg" key={`skel-${key}`}>
            <div className="lbl"><b>{label}</b><span>{why}</span></div>
            <div className="picks"><span className="skel" /><span className="skel" /></div>
          </div>
        ))}

        {!loading && STAGES.map(([key, label, why]) => (
          <div className="stagecfg" key={key}>
            <div className="lbl">
              <b>{label}</b>
              <span>{why}</span>
            </div>
            <div className="picks">
              <select
                value={harness[key] ?? ''}
                onChange={(e) => {
                  const next = e.target.value
                  setHarness({ ...harness, [key]: next })
                  // A model id belongs to one harness; carrying it across would fail at spawn.
                  setModels({ ...models, [key]: '' })
                }}
              >
                {usable.map((h) => <option key={h.name} value={h.name}>{h.name}</option>)}
              </select>

              <ModelPicker
                value={models[key] ?? ''}
                options={modelsFor(harness[key])}
                onChange={(v) => setModels({ ...models, [key]: v })}
              />
              {noteFor(harness[key], models[key]) && (
                <span className="mnote">{noteFor(harness[key], models[key])}</span>
              )}
            </div>
          </div>
        ))}

        {missing.length > 0 && (
          <div className="note">
            Not installed: {missing.map((h) => h.name).join(', ')} — these cannot be chosen until
            the CLI is on this machine.
          </div>
        )}

        {!independent && (
          <div className="note bad">
            {harness.execute} would review its own work. A model that just argued for a shortcut
            tends to accept it — that is not an independent review.
          </div>
        )}

        <div className="srow-foot">
          <span className="hint">
            {loading ? 'Reading what each CLI offers…' : 'Leave a model unset to use whatever that CLI defaults to.'}
          </span>
          <button className="primary" onClick={save} disabled={saving || loading}>
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
    </>
  )
}

/**
 * A select when the CLI can enumerate its models, a text field when it cannot.
 *
 * Only opencode has a list command. Offering a hardcoded dropdown for the others would go stale
 * the week a provider retires an id, and the run would then fail at spawn rather than here.
 */
function ModelPicker({ value, options, onChange }) {
  const [free, setFree] = useState(false)

  if (options.length === 0 || free) {
    return (
      <div className="freemodel">
        <input
          placeholder="default"
          value={value}
          onChange={(e) => onChange(e.target.value)}
        />
        {options.length > 0 && (
          <button className="ghost tiny" onClick={() => setFree(false)}>list</button>
        )}
      </div>
    )
  }

  return (
    <div className="freemodel">
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">CLI default</option>
        {options.map((m) => (
          <option key={m.id} value={m.id} title={m.note}>
            {m.label === m.id ? m.id : `${m.label} · ${m.id}`}
          </option>
        ))}
      </select>
      <button className="ghost tiny" onClick={() => setFree(true)} title="Type a model id">type</button>
    </div>
  )
}

// Imported lazily to keep this file free of a circular import with api.js consumers.
async function api_harnesses() {
  const res = await fetch('/api/harnesses')
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}
