import { useEffect, useState } from 'react'
import './pulse.css'

/**
 * The notch pulse: a small always-on-top strip under the MacBook notch.
 *
 * It exists so you do not have to keep the app in front of you. It says only what would make you
 * switch back — something is running, or something is waiting on you — and hides otherwise.
 */
export default function Pulse() {
  const [state, setState] = useState({ running: 0, waiting: 0 })

  useEffect(() => {
    window.vorfluxDesktop?.onPulse?.((next) => setState(next ?? { running: 0, waiting: 0 }))
  }, [])

  const { running = 0, waiting = 0 } = state
  const tone = waiting > 0 ? 'wait' : running > 0 ? 'run' : 'idle'

  return (
    <div className={`pulse ${tone}`}>
      <span className="dot" />
      <span className="label">
        {waiting > 0
          ? `${waiting} plan${waiting === 1 ? '' : 's'} need you`
          : running > 0
            ? `${running} run${running === 1 ? '' : 's'} in flight`
            : 'idle'}
      </span>
      {waiting > 0 && running > 0 && <span className="sub">· {running} running</span>}
    </div>
  )
}
