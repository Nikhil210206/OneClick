import type { ReactNode } from "react";

/**
 * An Ultra-class Galaxy handset with its stylus, drawn in CSS.
 *
 * Flat black frame, near-square corners, a thin even bezel and a centred punch-hole, with the pen
 * leaning on the right edge. Sized entirely in container-query units: the section sets the
 * phone's width, and the frame, pen and every pixel of the One UI screen inside scale with it, so
 * the device keeps true proportion at any viewport without measuring anything in JavaScript.
 * Drawn from scratch rather than from a product render, so the screen stays live and no Samsung
 * asset ships in the repo.
 */
export function Galaxy({
  children,
  className = "",
  pen = false,
}: {
  children: ReactNode;
  className?: string;
  pen?: boolean;
}) {
  return (
    <div className={`gx ${className}`}>
      <div className="gx-body">
        <span className="gx-btn gx-vol" />
        <span className="gx-btn gx-pwr" />
        <div className="gx-screen">
          <StatusBar />
          {children}
          <span className="gx-punch" />
        </div>
        <span className="gx-glare" />
      </div>
      {pen && (
        <span className="gx-pen" aria-hidden>
          <i className="gx-pen-tip" />
        </span>
      )}
    </div>
  );
}

/**
 * The lock screen the hero unlocks from: a soft dusk gradient with a ribbon folding through a
 * disc. An abstract piece of our own, in the spirit of a Galaxy wallpaper rather than a copy of one.
 */
export function Wallpaper({ className = "" }: { className?: string }) {
  return (
    <svg className={`gx-wall ${className}`} viewBox="0 0 340 740" preserveAspectRatio="xMidYMid slice" aria-hidden>
      <defs>
        <linearGradient id="gxSky" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#8a8a99" />
          <stop offset="0.45" stopColor="#c9a896" />
          <stop offset="0.72" stopColor="#6e6f86" />
          <stop offset="1" stopColor="#5a6386" />
        </linearGradient>
        <radialGradient id="gxGlow" cx="0.42" cy="0.46" r="0.5">
          <stop offset="0" stopColor="#f0c7ad" stopOpacity="0.95" />
          <stop offset="1" stopColor="#f0c7ad" stopOpacity="0" />
        </radialGradient>
        <linearGradient id="gxRibbon" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#2c2d38" />
          <stop offset="0.5" stopColor="#57505a" />
          <stop offset="1" stopColor="#23242e" />
        </linearGradient>
      </defs>
      <rect width="340" height="740" fill="url(#gxSky)" />
      <circle cx="170" cy="380" r="182" fill="#3d3e4b" opacity="0.55" />
      <circle cx="150" cy="360" r="150" fill="url(#gxGlow)" />
      <path
        d="M205 150c-60 20-78 88-40 150s60 130 20 200-110 70-150 60c50 30 150 20 190-60s-5-150-40-210-10-120 40-150c-10 0-15 5-20 10z"
        fill="url(#gxRibbon)"
        opacity="0.9"
      />
      <path
        d="M230 160c-40 30-40 90-5 150s50 140 5 210c30-40 40-120 5-190s-35-130-5-170z"
        fill="#1e1f28"
        opacity="0.55"
      />
    </svg>
  );
}

function StatusBar() {
  return (
    <div className="gx-status">
      <span>12:45</span>
      <span className="gx-icons" aria-hidden>
        <svg viewBox="0 0 16 12">
          <path d="M1 11h2V8H1zM5 11h2V6H5zM9 11h2V3.5H9zM13 11h2V1h-2z" fill="currentColor" />
        </svg>
        <svg viewBox="0 0 16 12">
          <path
            d="M8 11.2 1 4.4a10 10 0 0 1 14 0z"
            fill="currentColor"
          />
        </svg>
        <svg viewBox="0 0 24 12">
          <rect x="0.5" y="0.5" width="20" height="11" rx="3" fill="none" stroke="currentColor" opacity=".45" />
          <rect x="2" y="2" width="15" height="8" rx="1.8" fill="currentColor" />
          <rect x="21.5" y="4" width="2" height="4" rx="1" fill="currentColor" opacity=".45" />
        </svg>
      </span>
    </div>
  );
}

/** The OneClick app mark: a pressed button and its ripple. */
export function Mark({ className = "" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 32 32" aria-hidden>
      <rect width="32" height="32" rx="10" fill="#1d5cff" />
      <circle cx="16" cy="16" r="9" fill="none" stroke="#fff" strokeOpacity=".45" strokeWidth="2" />
      <circle cx="16" cy="16" r="4.2" fill="#d4ff3a" />
    </svg>
  );
}
