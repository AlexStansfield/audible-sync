import { useQuery } from "@tanstack/react-query";
import { api, unwrap } from "./api/client";

/**
 * A placeholder until the app shell: proves the build, the service serving it, the
 * base path and the typed client end to end.
 */
export function App() {
  const health = useQuery({
    queryKey: ["health"],
    queryFn: () => unwrap(api.GET("/api/health")),
  });

  return (
    <main style={{ padding: "28px 36px" }}>
      <h1 style={{ fontSize: 24, margin: 0 }}>Audible Sync</h1>
      <p style={{ color: "var(--muted)" }}>
        {health.isPending && "Connecting to the service…"}
        {health.isError && `The service did not answer: ${health.error.message}`}
        {health.data && `Service ${health.data.status}, version ${health.data.version}`}
      </p>
    </main>
  );
}
