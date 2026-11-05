/**
 * The frame around every signed-in page: the sidebar on a desktop; on a phone (720px
 * and narrower) a top bar whose menu button opens the sidebar as a drawer.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { Outlet, useLocation, useMatches, useNavigate } from "react-router";
import { api, onUnauthorized, unwrap } from "../api/client";
import { useRefreshAfterRun, useStatus } from "../api/queries";
import { useToast } from "../components/Toasts";
import { loginPath } from "../lib/next";
import styles from "./Layout.module.css";
import { SidebarContent, TopBar } from "./Sidebar";

export const PHONE_QUERY = "(max-width: 720px)";

export type RouteHandle = { title?: string };

export function Layout() {
  const navigate = useNavigate();
  const location = useLocation();
  const toast = useToast();
  const status = useStatus();
  const [menuOpen, setMenuOpen] = useState(false);
  const drawer = useRef<HTMLDialogElement>(null);
  const here = useRef("");
  here.current = location.pathname + location.search;

  useRefreshAfterRun(status.data?.scheduler.running);

  // Any 401 means the session has ended: to the sign-in, then back here
  useEffect(
    () =>
      onUnauthorized(() => {
        navigate(loginPath(here.current), { replace: true });
      }),
    [navigate],
  );

  const signOut = useCallback(async () => {
    try {
      await unwrap(api.POST("/api/auth/logout"));
    } catch (error) {
      toast.error("Could not sign out", error);
      return;
    }
    navigate("/login", { replace: true });
  }, [navigate, toast]);

  // The drawer: a modal <dialog>, so focus stays inside it and Escape closes it
  useEffect(() => {
    const dialog = drawer.current;
    if (!dialog) return;
    if (menuOpen && !dialog.open) dialog.showModal();
    if (!menuOpen && dialog.open) dialog.close();
    // The page behind does not scroll while it is open
    document.documentElement.classList.toggle("no-scroll", menuOpen);
    return () => document.documentElement.classList.remove("no-scroll");
  }, [menuOpen]);

  // Picking a page closes it, and so does widening the window past the phone layout
  // biome-ignore lint/correctness/useExhaustiveDependencies: runs on each navigation on purpose
  useEffect(() => {
    setMenuOpen(false);
  }, [location.pathname]);
  useEffect(() => {
    if (typeof matchMedia !== "function") return;
    const phone = matchMedia(PHONE_QUERY);
    const widened = () => {
      if (!phone.matches) setMenuOpen(false);
    };
    phone.addEventListener("change", widened);
    return () => phone.removeEventListener("change", widened);
  }, []);

  const matches = useMatches();
  const handle = [...matches]
    .reverse()
    .map((match) => match.handle as RouteHandle | undefined)
    .find((candidate) => candidate?.title);
  const pageTitle = handle?.title ?? "Audible Sync";

  useEffect(() => {
    document.title = pageTitle === "Audible Sync" ? pageTitle : `${pageTitle} · Audible Sync`;
  }, [pageTitle]);

  return (
    <div className={styles.shell}>
      <TopBar title={pageTitle} menuOpen={menuOpen} onMenu={() => setMenuOpen(true)} />
      <nav aria-label="Main" className={styles.sidebar}>
        <SidebarContent onSignOut={signOut} />
      </nav>
      {/* biome-ignore lint/a11y/useKeyWithClickEvents: the backdrop click; Escape (onCancel) is the keyboard equivalent */}
      <dialog
        ref={drawer}
        id="nav-drawer"
        aria-label="Menu"
        className={styles.drawer}
        onCancel={(event) => {
          event.preventDefault();
          setMenuOpen(false);
        }}
        onClick={(event) => {
          if (event.target === drawer.current) setMenuOpen(false);
        }}
      >
        {menuOpen && (
          <nav aria-label="Main" className={styles.drawerNav}>
            <SidebarContent onClose={() => setMenuOpen(false)} onSignOut={signOut} />
          </nav>
        )}
      </dialog>
      <main className={styles.main}>
        <Outlet />
      </main>
    </div>
  );
}
