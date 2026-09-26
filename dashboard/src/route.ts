import { useCallback, useEffect, useState } from "react";

export type Route = "overview" | "settings";

/** Two views, chosen by the URL hash so a reload or a shared link lands on the same page. */
export function parseRoute(hash: string): Route {
  return hash === "#/settings" ? "settings" : "overview";
}

export function useRoute(): [Route, (r: Route) => void] {
  const [route, setRoute] = useState<Route>(() => parseRoute(window.location.hash));
  useEffect(() => {
    const onChange = () => setRoute(parseRoute(window.location.hash));
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  const go = useCallback((r: Route) => {
    window.location.hash = `#/${r}`;
  }, []);
  return [route, go];
}
