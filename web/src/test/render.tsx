import { QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { createMemoryRouter } from "react-router";
import { makeQueryClient } from "../api/queryClient";
import { App } from "../app/App";
import { routes } from "../app/routes";
import { ToastProvider } from "../components/Toasts";

/** The whole app at `path`, as main.tsx builds it, on an in-memory router. */
export function renderApp(path: string) {
  const client = makeQueryClient();
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  const view = render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <App router={router} />
      </ToastProvider>
    </QueryClientProvider>,
  );
  return { ...view, router, client };
}
