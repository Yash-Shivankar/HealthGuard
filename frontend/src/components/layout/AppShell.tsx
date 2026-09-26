/**
 * The frame every page renders inside.
 *
 * The disclaimer lives here rather than on each page, so a page added later
 * cannot ship without it (AUDIT 2.3). The skip link and the `<main>` landmark
 * are here for the same reason - the original templates had neither, which
 * AUDIT 3 lists under accessibility, and WCAG 2.1 AA is a line item in Indian
 * government and enterprise procurement.
 */

import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, NavLink } from 'react-router-dom';

import { useSession } from '../../services/store/SessionContext';
import { ClinicSwitcher } from './ClinicSwitcher';
import { Disclaimer } from '../clinical/Disclaimer';
import { LanguagePicker } from './LanguagePicker';

export function AppShell({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const { session, signOut } = useSession();

  return (
    <div className="hg-shell">
      <a className="hg-skip-link" href="#main-content">
        Skip to main content
      </a>

      <header className="hg-header">
        <div className="hg-header__inner">
          <Link to="/" className="hg-brand">
            {t('app.name')}
          </Link>

          <nav className="hg-nav" aria-label="Main navigation">
            <NavLink to="/symptoms">{t('nav.symptomCheck')}</NavLink>

            {/* Clinic links appear only for a signed-in staff account. Hiding
                them is presentation; the server refuses the endpoints
                regardless. */}
            {session ? (
              <>
                <NavLink to="/clinic/patients">{t('nav.patients')}</NavLink>
                <NavLink to="/clinic/queue">{t('nav.reviewQueue')}</NavLink>
              </>
            ) : (
              <NavLink to="/clinic/sign-in">{t('nav.signIn')}</NavLink>
            )}

            <ClinicSwitcher />
            <LanguagePicker />

            {session ? (
              <button
                type="button"
                className="hg-btn hg-btn--neutral"
                style={{ padding: '7px 16px' }}
                onClick={() => void signOut()}
              >
                {t('nav.signOut')}
              </button>
            ) : null}
          </nav>
        </div>
      </header>

      <Disclaimer />

      <main id="main-content" className="hg-main">
        <div className="hg-container">{children}</div>
      </main>

      <footer className="hg-footer">
        <div className="hg-container">
          {t('app.name')} &mdash; {t('app.tagline')}
        </div>
      </footer>
    </div>
  );
}
