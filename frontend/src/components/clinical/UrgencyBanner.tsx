/**
 * Urgency advice.
 *
 * Every level carries an icon and a text label, never colour alone. Red appears
 * here only for clinical danger, which is the one thing it is reserved for
 * (AUDIT 8.3).
 */

import type { Urgency } from '../../types';

const ICONS: Record<Urgency['level'], string> = {
  urgent: '⚠', // warning triangle
  consult: '⏱', // stopwatch
  routine: '✓', // check
  unknown: '–', // en dash
};

export function UrgencyBanner({ urgency }: { urgency: Urgency }) {
  return (
    <section
      className={`hg-urgency hg-urgency--${urgency.level}`}
      role="note"
      aria-label={urgency.label}
    >
      <span className="hg-urgency__icon" aria-hidden="true">
        {ICONS[urgency.level]}
      </span>
      <div>
        <h3 style={{ margin: '0 0 4px', fontSize: '1.05rem' }}>{urgency.label}</h3>
        <p style={{ margin: 0 }}>{urgency.advice}</p>
      </div>
    </section>
  );
}
