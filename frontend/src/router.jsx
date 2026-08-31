/**
 * A ~90-line client-side router, hand-written on purpose.
 *
 * react-router-dom is the obvious answer and is not available: this project's
 * dependencies are installed and pinned, and adding one is not a change I can
 * make and verify here. It also turns out not to be needed. The whole
 * requirement is four static paths, a back button that works, and deep links
 * that survive a hard refresh — which is `history.pushState`, a `popstate`
 * listener, and a click handler.
 *
 * The server side of deep linking already exists: api/__init__.py hands back
 * index.html for any unknown non-API path, so /nations reloads correctly in
 * production. In development Vite does the same.
 *
 * Navigation goes through real <a href> elements rather than buttons with
 * onClick. That is what makes middle-click, ctrl-click, "copy link address",
 * and screen-reader link lists work; the handler only intercepts the plain
 * left-click case and otherwise lets the browser do its normal thing.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

const RouterContext = createContext({ path: "/", navigate: () => {} });

/** Strip the query and hash, and collapse a trailing slash to "/". */
function normalise(pathname) {
  const clean = (pathname || "/").split("?")[0].split("#")[0];
  if (clean.length > 1 && clean.endsWith("/")) return clean.slice(0, -1);
  return clean || "/";
}

function currentPath() {
  if (typeof window === "undefined") return "/";
  return normalise(window.location.pathname);
}

export function RouterProvider({ children }) {
  const [path, setPath] = useState(currentPath);

  const navigate = useCallback((to, { replace = false } = {}) => {
    const next = normalise(to);
    if (next === normalise(window.location.pathname)) return;

    if (replace) window.history.replaceState({}, "", next);
    else window.history.pushState({}, "", next);

    setPath(next);
    // A new page should start at the top. Browsers restore scroll on a real
    // navigation but pushState leaves the viewport where it was, which lands
    // you halfway down a page you have not read yet.
    window.scrollTo({ top: 0, behavior: "instant" in window ? "instant" : "auto" });
  }, []);

  // Back / forward.
  useEffect(() => {
    const onPop = () => setPath(currentPath());
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  // One delegated click handler for the whole document, so <Link> stays a thin
  // wrapper and a hand-written <a href="/method"> in prose works too.
  useEffect(() => {
    const onClick = (event) => {
      // Let the browser handle anything that is not a plain left-click.
      if (event.defaultPrevented) return;
      if (event.button !== 0) return;
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;

      const anchor = event.target.closest?.("a");
      if (!anchor) return;

      // External, new-tab, download, or explicitly opted out.
      if (anchor.target && anchor.target !== "_self") return;
      if (anchor.hasAttribute("download")) return;
      if (anchor.dataset.native !== undefined) return;

      const href = anchor.getAttribute("href") || "";
      if (!href.startsWith("/") || href.startsWith("//")) return;

      event.preventDefault();
      navigate(href);
    };

    document.addEventListener("click", onClick);
    return () => document.removeEventListener("click", onClick);
  }, [navigate]);

  const value = useMemo(() => ({ path, navigate }), [path, navigate]);
  return <RouterContext.Provider value={value}>{children}</RouterContext.Provider>;
}

export function useRouter() {
  return useContext(RouterContext);
}

/**
 * An anchor that the delegated handler above will intercept.
 * `aria-current="page"` is set for the active route, which is both the
 * accessible signal and the hook the CSS uses — no separate "active" class to
 * keep in sync.
 */
export function Link({ to, children, className = "", ...rest }) {
  const { path } = useRouter();
  const active = path === normalise(to);
  return (
    <a href={to} className={className} aria-current={active ? "page" : undefined} {...rest}>
      {children}
    </a>
  );
}

export { normalise };
