/**
 * Urgency advice.
 *
 * Every level carries an icon and a text label, never colour alone. Red appears
 * here only for clinical danger, which is the one thing it is reserved for
 * (AUDIT 8.3).
 *
 * Redesign: this is now the hero of the result page - the first, largest thing
 * a patient sees - because "how soon should I act" is the question they came
 * with, and the ranked conditions are supporting detail.
 */

import { CircleCheck, CircleQuestionMark, Clock3, Siren, type LucideIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import type { Urgency } from '../../types';

const ICONS: Record<Urgency['level'], LucideIcon> = {
  urgent: Siren,
  consult: Clock3,
  routine: CircleCheck,
  unknown: CircleQuestionMark,
};

export function UrgencyBanner({ urgency }: { urgency: Urgency }) {
  const { t } = useTranslation();
  const Icon = ICONS[urgency.level];

  return (
    <section
      className={`hg-urgency hg-urgency--${urgency.level}`}
      role="note"
      aria-label={urgency.label}
    >
      <span className="hg-urgency__icon" aria-hidden="true">
        <Icon size={28} strokeWidth={2.2} />
      </span>
      <div>
        <p className="hg-urgency__eyebrow">{t('result.nextStep')}</p>
        <h2 className="hg-urgency__title">{urgency.label}</h2>
        <p className="hg-urgency__advice">{urgency.advice}</p>
        {urgency.level === 'urgent' ? (
          <a className="hg-btn hg-btn--danger hg-btn--sm" href="tel:112">
            {t('disclaimer.call')}
          </a>
        ) : null}
      </div>
    </section>
  );
}
