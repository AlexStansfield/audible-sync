import { QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter } from "react-router";
import { makeQueryClient } from "./api/queryClient";
import { App } from "./app/App";
import { basenameFrom, routes } from "./app/routes";
import { ToastProvider } from "./components/Toasts";
import "./styles/fonts";
import "./styles/theme.css";
import "./styles/controls.css";

const queryClient = makeQueryClient();
const router = createBrowserRouter(routes, { basename: basenameFrom(document.baseURI) });

const root = document.getElementById("root");
if (!root) {
  throw new Error("index.html has no #root");
}

createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <App router={router} />
      </ToastProvider>
    </QueryClientProvider>
  </StrictMode>,
);
