import { Link, NavLink } from "react-router";
import { useBookCount, useHealth, useMe, useStatus } from "../api/queries";
import { useWishlistCount } from "../catalog/queries";
import { Icon, type IconName } from "../components/Icon";
import styles from "./Layout.module.css";
import { accountsNeedLogin, serviceState } from "./serviceState";

const LINKS: { to: string; label: string; icon: IconName; end?: boolean }[] = [
  { to: "/", label: "Dashboard", icon: "dashboard", end: true },
  { to: "/library", label: "Library", icon: "library" },
  { to: "/search", label: "Search", icon: "search" },
  { to: "/wishlist", label: "Wishlist", icon: "heart" },
  { to: "/activity", label: "Activity", icon: "activity" },
  { to: "/accounts", label: "Accounts", icon: "accounts" },
  { to: "/settings", label: "Settings", icon: "settings" },
];

/**
 * The sidebar's content, the same on a desktop and in the phone's drawer: the logo
 * and version, the pages, and at the foot the service state, your username and
 * Sign out. `onClose` adds the drawer's close button.
 */
export function SidebarContent({ onClose, onSignOut }: { onClose?: () => void; onSignOut: () => void }) {
  const health = useHealth();
  const status = useStatus();
  const me = useMe();
  const count = useBookCount();
  const wishlist = useWishlistCount();
  const state = serviceState(status.data, status.isError && !status.data);
  const needLogin = accountsNeedLogin(status.data);
  const initial = me.data?.username.charAt(0).toUpperCase() ?? "";

  return (
    <>
      <div className={styles.brand}>
        <span className={styles.logo}>
          <Icon name="logo" size={20} strokeWidth={2.2} />
        </span>
        <span className={styles.brandText}>
          <span className={styles.brandName}>Audible Sync</span>
          {health.data && <span className={styles.version}>v{health.data.version}</span>}
        </span>
        {onClose && (
          <button type="button" className={`icon-btn ${styles.close}`} aria-label="Close menu" onClick={onClose}>
            <Icon name="close" size={20} strokeWidth={2.2} />
          </button>
        )}
      </div>
      <ul className={styles.links}>
        {LINKS.map((link) => {
          const books = link.to === "/library" ? count.data : link.to === "/wishlist" ? wishlist.data : undefined;
          const alert = link.to === "/accounts" && needLogin;
          // One accessible name rather than hidden fragments, which read with stray spaces
          const name = [
            link.label,
            books !== undefined && `${books.toLocaleString()} books`,
            alert && "an account needs you to sign in again",
          ]
            .filter(Boolean)
            .join(", ");
          return (
            <li key={link.to}>
              <NavLink to={link.to} end={link.end} className={styles.link} aria-label={name}>
                <Icon name={link.icon} />
                {link.label}
                {books !== undefined && <span className={styles.count}>{books.toLocaleString()}</span>}
                {alert && <span className={styles.alertDot} title="An account needs you to sign in again" />}
              </NavLink>
            </li>
          );
        })}
      </ul>
      <div className={styles.foot}>
        <div className={styles.state}>
          <span className={styles.stateDot} style={{ background: state.colour }} />
          {state.text}
        </div>
        <div className={styles.user}>
          <NavLink
            to="/profile"
            className={styles.userLink}
            aria-label={me.data?.generated_password ? `${me.data.username}, using the generated password` : undefined}
          >
            <span className={styles.avatar} aria-hidden="true">
              {initial}
              {me.data?.generated_password && <span className={styles.avatarDot} />}
            </span>
            <span className={styles.username}>{me.data?.username}</span>
          </NavLink>
          <button type="button" className={styles.signOut} onClick={onSignOut}>
            Sign out
          </button>
        </div>
      </div>
    </>
  );
}

/** The phone's top bar: the menu button, the logo and page name, and your avatar. */
export function TopBar({ title, menuOpen, onMenu }: { title: string; menuOpen: boolean; onMenu: () => void }) {
  const me = useMe();
  return (
    <header className={styles.topbar}>
      <button
        type="button"
        className="icon-btn"
        aria-label="Open menu"
        aria-controls="nav-drawer"
        aria-expanded={menuOpen}
        onClick={onMenu}
      >
        <Icon name="menu" size={22} strokeWidth={2.2} />
      </button>
      <span className={styles.logoSmall} aria-hidden="true">
        <Icon name="logo" size={16} strokeWidth={2.4} />
      </span>
      <span className={styles.topTitle}>{title}</span>
      <Link to="/profile" className={styles.topAvatar} aria-label="Your sign-in">
        {me.data?.username.charAt(0).toUpperCase()}
      </Link>
    </header>
  );
}
