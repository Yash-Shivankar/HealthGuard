/**
 * The HealthGuard mark: a shield (guard) carrying a pulse line (health).
 *
 * Drawn as inline SVG so it inherits no network request, scales to any size,
 * and stays sharp on a clinic's cheap reception monitor. The same geometry is
 * in public/favicon.svg — change both together.
 */

import { useId } from 'react';

export function LogoMark({ size = 32 }: { size?: number }) {
  // Unique per instance: two logos on one page (header + footer) must not
  // share a gradient id, or one of them renders with the other's fill.
  const gradient = useId();

  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true" focusable="false">
      <defs>
        <linearGradient id={gradient} x1="6" y1="3" x2="26" y2="29" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#2fb9a4" />
          <stop offset="1" stopColor="#0b6b5f" />
        </linearGradient>
      </defs>
      <path
        d="M16 2.5 27 6.6V15c0 7-4.8 12.3-11 14.5C9.8 27.3 5 22 5 15V6.6L16 2.5Z"
        fill={`url(#${gradient})`}
      />
      <path
        d="M8.8 16.4h3.6l2-4.6 3.1 9 2-4.4h3.7"
        fill="none"
        stroke="#fff"
        strokeWidth="2.3"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function Logo({ name }: { name: string }) {
  // Splits "HealthGuard" into two tones. A translated or rebranded name that
  // does not end in "Guard" simply renders in one tone.
  const split = name.endsWith('Guard') ? name.length - 'Guard'.length : name.length;

  return (
    <span className="hg-logo">
      <LogoMark />
      <span className="hg-logo__word">
        {name.slice(0, split)}
        <span className="hg-logo__accent">{name.slice(split)}</span>
      </span>
    </span>
  );
}
