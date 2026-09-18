import { Pill, Rail } from './components'
import { needsYou, progress } from './stages'

/**
 * The board: columns are pipeline stages, and nothing is dragged.
 *
 * A card's column is derived from where its run actually got to, so the board is a readout of the
 * system rather than a thing you maintain. That is the whole difference between this and Trello.
 */
const COLUMNS = [
  { key: 'plan',    label: 'Plan',    hint: 'waiting on you' },
  { key: 'build',   label: 'Build',   hint: 'agents writing code' },
  { key: 'review',  label: 'Review',  hint: 'independent judgement' },
  { key: 'verify',  label: 'Verify',  hint: 'your own tests' },
  { key: 'done',    label: 'Done',    hint: 'branch ready' },
]

function columnFor(feature) {
  const status = feature.status
  if (['delivered', 'landed'].includes(status)) return 'done'
  if (['verify_failed'].includes(status)) return 'verify'
  if (['reviewing', 'needs_human'].includes(status)) return 'review'
  if (['verifying', 'verify_failed'].includes(status)) return 'verify'
  if (['reviewing', 'needs_human'].includes(status)) return 'review'
  if (['approved', 'executing', 'fixing', 'no_changes', 'failed', 'cancelled'].includes(status)) return 'build'
  return 'plan'
}

export function Board({ features, onOpen }) {
  return (
    <div className="board">
      {COLUMNS.map((col) => {
        const cards = features.filter((f) => columnFor(f) === col.key)
        return (
          <div className="bcol" key={col.key}>
            <div className="bcol-head">
              <b>{col.label}</b>
              <span className="n">{cards.length || ''}</span>
              <span className="hint">{col.hint}</span>
            </div>
            <div className="bcol-body">
              {cards.length === 0 && <div className="bempty" />}
              {cards.map((f) => <BoardCard key={f.id} feature={f} onOpen={onOpen} />)}
            </div>
          </div>
        )
      })}
    </div>
  )
}

function BoardCard({ feature, onOpen }) {
  const status = feature.status
  const { at } = progress(status)
  return (
    <button
      className={`bcard ${feature.status === 'awaiting_approval' ? 'gate' : ''} ${
        needsYou(feature) && feature.status !== 'awaiting_approval' ? 'blocked' : ''
      }`}
      onClick={() => onOpen(feature.id)}
    >
      <div className="t">{feature.title}</div>
      <Rail status={status} />
      <div className="meta">
        <Pill status={feature.status} busy={feature.busy} />
        <span className="step">{Math.min(at + 1, 5)}/5</span>
      </div>
      <div className="br mono">{feature.branch}</div>
    </button>
  )
}
