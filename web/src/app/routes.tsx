/**
 * The routes (DESIGN.md, "The screen files"). Sign in sits outside the layout; every
 * other page is inside it, behind the sign-in check. An unknown path is the 404 page,
 * inside the layout too.
 */
import type { RouteObject } from "react-router";
import { basePath } from "../api/client";
import { LoginPage } from "../auth/LoginPage";
import { RequireAuth } from "../auth/RequireAuth";
import { Layout, type RouteHandle } from "../layout/Layout";
import { AccountsPage } from "../pages/accounts/AccountsPage";
import { BookPage } from "../pages/book/BookPage";
import { ComingSoon } from "../pages/ComingSoon";
import { DashboardPage } from "../pages/dashboard/DashboardPage";
import { LibraryPage } from "../pages/library/LibraryPage";
import { NotFoundPage } from "../pages/NotFoundPage";
import { ProfilePage } from "../pages/ProfilePage";

const page = (title: string): RouteHandle => ({ title });

export const routes: RouteObject[] = [
  { path: "/login", element: <LoginPage /> },
  {
    element: (
      <RequireAuth>
        <Layout />
      </RequireAuth>
    ),
    children: [
      { index: true, element: <DashboardPage />, handle: page("Dashboard") },
      { path: "library", element: <LibraryPage />, handle: page("Library") },
      { path: "library/:id", element: <BookPage />, handle: page("Library") },
      { path: "activity", element: <ComingSoon title="Activity" />, handle: page("Activity") },
      { path: "accounts", element: <AccountsPage />, handle: page("Accounts") },
      { path: "settings", element: <ComingSoon title="Settings" />, handle: page("Settings") },
      { path: "profile", element: <ProfilePage />, handle: page("Your sign-in") },
      { path: "*", element: <NotFoundPage />, handle: page("Not found") },
    ],
  },
];

/**
 * The router's basename: the path the service serves the app under, from the
 * <base href> it writes into the page (`/audible-sync/` → `/audible-sync`).
 */
export function basenameFrom(baseURI: string): string {
  return basePath(baseURI) || "/";
}
