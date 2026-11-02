/**
 * The mockups' inline SVG icons (24-unit viewBox, stroked in currentColor). Always
 * decorative: the button or link around one carries the label.
 */
import type { ReactNode } from "react";

const PATHS = {
  logo: (
    <>
      <path d="M4 15v-3a8 8 0 0 1 16 0v3" />
      <rect x="3" y="14" width="4" height="7" rx="1.5" />
      <rect x="17" y="14" width="4" height="7" rx="1.5" />
    </>
  ),
  dashboard: (
    <>
      <rect x="3" y="3" width="7" height="9" rx="1" />
      <rect x="14" y="3" width="7" height="5" rx="1" />
      <rect x="14" y="12" width="7" height="9" rx="1" />
      <rect x="3" y="16" width="7" height="5" rx="1" />
    </>
  ),
  library: (
    <>
      <path d="M4 19.5V5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2.5z" />
      <path d="M8 7h7" />
    </>
  ),
  activity: <path d="M3 12h4l3-8 4 16 3-8h4" />,
  accounts: (
    <>
      <circle cx="12" cy="8" r="4" />
      <path d="M4 21c0-4 4-6 8-6s8 2 8 6" />
    </>
  ),
  settings: (
    <>
      <path d="M4 6h10M4 12h4M12 12h8M4 18h12" />
      <circle cx="17" cy="6" r="2.5" />
      <circle cx="10" cy="12" r="2" />
      <circle cx="19" cy="18" r="2" />
    </>
  ),
  menu: <path d="M4 7h16M4 12h16M4 17h16" />,
  close: <path d="M6 6l12 12M18 6 6 18" />,
  eye: (
    <>
      <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z" />
      <circle cx="12" cy="12" r="3" />
    </>
  ),
  eyeOff: (
    <>
      <path d="M3 3l18 18" />
      <path d="M10.6 5.1A10.4 10.4 0 0 1 12 5c6.5 0 10 7 10 7a17.6 17.6 0 0 1-3.1 4" />
      <path d="M6.6 6.6C3.8 8.4 2 12 2 12s3.5 7 10 7a9.7 9.7 0 0 0 5.4-1.6" />
      <path d="M9.9 9.9a3 3 0 0 0 4.2 4.2" />
    </>
  ),
  warning: (
    <>
      <path d="M12 3 2 21h20L12 3z" />
      <path d="M12 10v4M12 17.5v.01" />
    </>
  ),
  retry: (
    <>
      <path d="M21 12a9 9 0 1 1-3-6.7L21 8" />
      <path d="M21 3v5h-5" />
    </>
  ),
  check: <path d="m5 12 5 5 9-10" />,
  alert: <path d="M12 7v6M12 17v.01" />,
  lock: (
    <>
      <rect x="4" y="10" width="16" height="11" rx="2" />
      <path d="M8 10V7a4 4 0 0 1 8 0v3" />
    </>
  ),
  offline: (
    <>
      <path d="M2 8.8a15 15 0 0 1 4.2-2.6" />
      <path d="M10 5.1a15 15 0 0 1 12 3.7" />
      <path d="M5 12.9a10 10 0 0 1 5.2-2.7" />
      <path d="M16.8 11.3a10 10 0 0 1 2.2 1.6" />
      <path d="M8.5 16.4a5 5 0 0 1 7 0" />
      <path d="M12 20h.01" />
      <path d="m3 3 18 18" />
    </>
  ),
  spinner: <path d="M12 3a9 9 0 1 0 9 9" />,
  book: (
    <>
      <path d="M4 19.5V5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2.5z" />
      <path d="M8 7h7M8 11h5" />
    </>
  ),
} satisfies Record<string, ReactNode>;

export type IconName = keyof typeof PATHS;

export function Icon({
  name,
  size = 18,
  strokeWidth = 2,
  className,
}: {
  name: IconName;
  size?: number;
  strokeWidth?: number;
  className?: string;
}) {
  return (
    <svg
      aria-hidden="true"
      focusable="false"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
    >
      {PATHS[name]}
    </svg>
  );
}
