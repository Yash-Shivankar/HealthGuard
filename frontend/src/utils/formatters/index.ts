/** Display helpers. */

/** A date a clinician can scan, in their own locale. */
export function formatDateTime(iso: string, locale: string): string {
  return new Date(iso).toLocaleString(locale, {
    dateStyle: 'medium',
    timeStyle: 'short',
  });
}

/** How long ago, in whole units. Used for the review queue's ageing column. */
export function formatAge(iso: string, locale: string): string {
  const elapsedMs = Date.now() - new Date(iso).getTime();
  const formatter = new Intl.RelativeTimeFormat(locale, { numeric: 'auto' });

  const units: [Intl.RelativeTimeFormatUnit, number][] = [
    ['day', 86_400_000],
    ['hour', 3_600_000],
    ['minute', 60_000],
  ];

  for (const [unit, ms] of units) {
    const value = Math.floor(elapsedMs / ms);
    if (value >= 1) return formatter.format(-value, unit);
  }
  return formatter.format(0, 'minute');
}

/**
 * Raw feature name to something a patient reads.
 * `foul_smell_of urine` -> `Foul smell of urine`. Mirrors humanise() in
 * apps/triage/services/assessment_service.py.
 */
export function humanise(symptom: string): string {
  const cleaned = symptom.replace(/_/g, ' ').trim();
  return cleaned.charAt(0).toUpperCase() + cleaned.slice(1);
}
