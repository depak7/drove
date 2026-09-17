/**
 * The mark: three tracks converging into one.
 *
 * A drove is a herd moved as a group, and the product runs several models — one plans, one
 * implements, another reviews — whose work has to arrive as a single change. Three lines entering
 * from the left, meeting at a point on the right, says that without a picture of a robot.
 *
 * Built on a 24-grid so it stays crisp at 16px in a title bar and a Dock icon alike.
 */
export function Mark({ size = 22, className = '' }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" className={className} aria-hidden="true">
      {/* three tracks, converging */}
      <path d="M2.5 6.5h8.5" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" opacity=".38" />
      <path d="M2.5 12h12"   stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" opacity=".68" />
      <path d="M2.5 17.5h8.5" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" opacity=".38" />
      {/* the head they arrive at */}
      <path d="M15.5 7.2 20.8 12l-5.3 4.8" stroke="currentColor" strokeWidth="2.4"
            strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

export function Wordmark({ size = 22 }) {
  return (
    <span className="wordmark">
      <Mark size={size} />
      <b>drove</b>
    </span>
  )
}
