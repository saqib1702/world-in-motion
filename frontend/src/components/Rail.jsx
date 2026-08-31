import { Link } from "../router";

/**
 * The top rail, on every page.
 *
 * The mark's ring is a CSS 3D animation rather than a fourth WebGL context —
 * something is always turning, even on the pages that have no canvas, and it
 * costs one compositor-thread transform.
 *
 * The two spellings of the mark are a width trade, not decoration: at 320px the
 * full name plus three nav labels overflow, and truncating the nav is worse than
 * shortening the wordmark.
 */
export default function Rail() {
  return (
    <header className="rail">
      <Link to="/" className="rail-mark">
        <i className="rail-ring" aria-hidden="true" />
        <span className="mark-full">World in Motion</span>
        <span className="mark-short" aria-hidden="true">
          WiM
        </span>
      </Link>

      <nav className="rail-links" aria-label="Main">
        <Link to="/simulation">Live board</Link>
        <Link to="/nations">Nations</Link>
        <Link to="/method">Method</Link>
      </nav>
    </header>
  );
}
