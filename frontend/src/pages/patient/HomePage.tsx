/**
 * Landing page.
 *
 * The "How it works, honestly" section is deliberate: the audit's whole
 * complaint about the original product was that it stated a disease as fact and
 * claimed accuracy it did not have. Saying plainly what the tool will and will
 * not do is the fix, and it is also the more credible sales page.
 */

import { useTranslation } from 'react-i18next';
import { Link } from 'react-router-dom';

import { Card } from '../../components/common';

export function HomePage() {
  const { t } = useTranslation();

  return (
    <div>
      <h1>{t('app.name')}</h1>
      <p className="hg-muted" style={{ fontSize: '1.05rem' }}>
        {t('app.tagline')}
      </p>

      <div className="hg-actions" style={{ marginTop: 0, marginBottom: 32 }}>
        <Link to="/intake" className="hg-btn hg-btn--primary">
          {t('nav.symptomCheck')}
        </Link>
      </div>

      <Card>
        <h2 style={{ fontSize: '1.05rem' }}>How it works, honestly</h2>
        <ul>
          <li>
            <strong>It suggests, it does not diagnose.</strong> You get a ranked list of
            possibilities with a match score, never a single verdict.
          </li>
          <li>
            <strong>It says when it does not know.</strong> If your symptoms do not match
            anything it recognises, it tells you so and points you to a doctor instead of
            guessing.
          </li>
          <li>
            <strong>It will not suggest medicines.</strong> Only a registered medical
            practitioner can prescribe.
          </li>
          <li>
            <strong>You control your data.</strong> We tell you what we collect before you
            enter it, you can skip it entirely, and you can withdraw at any time.
          </li>
        </ul>
      </Card>
    </div>
  );
}
