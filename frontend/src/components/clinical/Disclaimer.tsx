/**
 * The medical disclaimer (AUDIT 2.3).
 *
 * A search of every template in the original project for "disclaimer",
 * "consent", "not a substitute" or "consult a doctor" returned zero matches.
 *
 * It renders in the shell so it cannot be forgotten on a new page, and it is a
 * `role="note"` rather than a dismissible alert: a disclaimer the user can
 * close is a disclaimer that was not shown.
 */

import { useTranslation } from 'react-i18next';

export function Disclaimer() {
  const { t } = useTranslation();

  return (
    <section className="hg-disclaimer" role="note" aria-label={t('disclaimer.heading')}>
      <div className="hg-disclaimer__inner">
        <span className="hg-disclaimer__icon" aria-hidden="true">
          i
        </span>
        <p style={{ margin: 0 }}>
          <strong>{t('disclaimer.heading')}</strong> {t('disclaimer.body')}{' '}
          <strong>{t('disclaimer.emergency')}</strong>
        </p>
      </div>
    </section>
  );
}
