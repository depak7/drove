/**
 * The Electron bridge, and a browser-safe shim for it.
 *
 * The same renderer serves the desktop app and a plain localhost tab. Native features are offered
 * when the bridge exists and quietly fall back when it does not, so nothing in the UI has to know
 * which shell it is running in.
 */
const bridge = typeof window !== 'undefined' ? window.vorfluxDesktop : undefined

export const isDesktop = Boolean(bridge)

/** Native folder picker. Returns a path, or null when cancelled or unavailable. */
export async function chooseRepository() {
  if (!bridge?.chooseRepository) return null
  const result = await bridge.chooseRepository()
  // The handler returns either a path or a dialog result object depending on cancellation.
  if (!result || result.canceled) return null
  return typeof result === 'string' ? result : (result.filePaths?.[0] ?? null)
}

export function notify(title, body) {
  bridge?.notify?.(title, body)
}

/** Drives the notch pulse: what the Mac shows while you are looking at something else. */
export function setPulse(state) {
  bridge?.updatePulse?.(state)
}
