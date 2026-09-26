/**
 * The "AI draft, unreviewed" state (AUDIT 8.3).
 *
 * The audit is explicit that this is a compliance control rather than
 * decoration: TPG 2020 requires clinician sign-off before an AI output enters a
 * record, and a visually distinct, consistently applied unreviewed state is how
 * that gets enforced in practice.
 */

import { useTranslation } from 'react-i18next';

import type { Assessment } from '../../types';

export function ReviewBadge({ state }: { state: Assessment['review_state'] }) {
  const { t } = useTranslation();

  if (state === 'unreviewed') {
    return (
      <span className="hg-badge hg-badge--ai">
        <span aria-hidden="true">{'✦'}</span>
        {t('clinic.aiDraft')}
      </span>
    );
  }

  return <span className="hg-badge hg-badge--reviewed">{t('clinic.reviewed')}</span>;
}

export function UrgencyBadge({
  level,
  label,
}: {
  level: Assessment['urgency_level'];
  label: string;
}) {
  const variant = level === 'unknown' ? 'neutral' : level;
  return <span className={`hg-badge hg-badge--${variant}`}>{label}</span>;
}
