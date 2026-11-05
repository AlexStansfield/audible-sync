import type { createBrowserRouter } from "react-router";
import { RouterProvider } from "react-router";
import { UnreachablePage } from "./UnreachablePage";
import { useReachability } from "./useReachability";

type Router = ReturnType<typeof createBrowserRouter>;

/**
 * The router, or the Unreachable page while the service does not answer. The router
 * lives outside this component, so the address bar keeps your place and the app comes
 * back to it.
 */
export function App({ router }: { router: Router }) {
  const reach = useReachability();
  if (reach.down) {
    return <UnreachablePage secondsLeft={reach.secondsLeft} checking={reach.checking} onRetry={reach.retryNow} />;
  }
  return <RouterProvider router={router} />;
}
