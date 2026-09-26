/**
 * The medical disclaimer (AUDIT 2.3).
 *
 * A search of every template in the original project for "disclaimer",
 * "consent", "not a substitute" or "consult a doctor" returned zero matches.
 *
 * It renders in the shell so it cannot be forgotten on a new page, and it is a
 * `role="note"` rather than a dismissible alert: a disclaimer the user can
 * close is a disclaimer that was not shown.
 *
 * Redesign: calmer and slimmer, but the full text is still always visible.
 * The emergency line is now a real `tel:112` link - on a phone, the one action
 * that matters in an emergency is a single tap away instead of a sentence away.
 */

import { Info, Phone } from 'lucide-react';
import { useTranslation } from 'react-i18next';

export function Disclaimer() {
  const { t } = useTranslation();

  return (
    // <aside> gives it a landmark (so it is reachable by landmark navigation
    // and axe's "region" rule passes); the inner role="note" keeps the
    // original semantics of a standing notice.
    <aside className="hg-disclaimer" aria-label={t('disclaimer.heading')}>
      <div className="hg-disclaimer__inner" role="note">
        <Info size={18} aria-hidden="true" className="hg-disclaimer__icon" />
        <p className="hg-disclaimer__text">
          <strong>{t('disclaimer.heading')}</strong> {t('disclaimer.body')}{' '}
          <span className="hg-disclaimer__emergency">{t('disclaimer.emergency')}</span>
        </p>
        <a className="hg-disclaimer__call" href="tel:112">
          <Phone size={15} aria-hidden="true" />
          {t('disclaimer.call')}
        </a>
      </div>
    </aside>
  );
}
