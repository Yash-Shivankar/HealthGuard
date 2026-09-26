/**
 * The frame every page renders inside.
 *
 * The disclaimer lives here rather than on each page, so a page added later
 * cannot ship without it (AUDIT 2.3). The skip link and the `<main>` landmark
 * are here for the same reason - the original templates had neither, which
 * AUDIT 3 lists under accessibility, and WCAG 2.1 AA is a line item in Indian
 * government and enterprise procurement.
 *
 * Redesign (Sep 2026): a sticky translucent header, pill navigation, a mobile
 * menu, and a footer that carries India's emergency numbers. The header is the
 * one element on every screen, so it is where the brand has to feel calm and
 * modern first.
 */

import { LogOut, Menu, Phone, ShieldCheck, X } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, NavLink, useLocation } from 'react-router-dom';

import { useSession } from '../../services/store/SessionContext';
import { Logo, LogoMark } from '../common/Logo';
import { Disclaimer } from '../clinical/Disclaimer';
import { ClinicSwitcher } from './ClinicSwitcher';
import { LanguagePicker } from './LanguagePicker';
import { ThemeToggle } from './ThemeToggle';

/** The wordmark is the brand and stays in Latin script in every language, the
 *  way Practo or Apollo do; the translated name is still the accessible name. */
const BRAND = 'HealthGuard';

export function AppShell({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const { session, signOut } = useSession();
  const location = useLocation();
  // The mobile menu remembers which page it was opened on, so navigating
  // closes it by construction - no effect, no stale-open menu over the page you
  // just chose (the classic mobile-nav bug).
  const [menuOpenOn, setMenuOpenOn] = useState<string | null>(null);
  const menuOpen = menuOpenOn === location.pathname;

  const navClass = ({ isActive }: { isActive: boolean }) =>
    `hg-nav__link${isActive ? ' is-active' : ''}`;

  return (
    <div className="hg-shell">
      <a className="hg-skip-link" href="#main-content">
        {t('app.skipToContent')}
      </a>

      <header className="hg-header">
        <div className="hg-header__inner">
          <Link to="/" className="hg-brand" aria-label={t('app.name')}>
            <Logo name={BRAND} />
          </Link>

          <button
            type="button"
            className="hg-icon-btn hg-menu-toggle"
            aria-expanded={menuOpen}
            aria-controls="primary-nav"
            aria-label={t('nav.menu')}
            onClick={() => setMenuOpenOn(menuOpen ? null : location.pathname)}
          >
            {menuOpen ? <X size={20} aria-hidden="true" /> : <Menu size={20} aria-hidden="true" />}
          </button>

          <div id="primary-nav" className={`hg-header__menu${menuOpen ? ' is-open' : ''}`}>
            <nav className="hg-nav" aria-label="Main navigation">
              <NavLink to="/describe" className={navClass}>
                {t('nav.describe')}
              </NavLink>
              <NavLink to="/symptoms" className={navClass}>
                {t('nav.symptomCheck')}
              </NavLink>

              {/* Clinic links appear only for a signed-in staff account. Hiding
                  them is presentation; the server refuses the endpoints
                  regardless. */}
              {session ? (
                <>
                  <NavLink to="/clinic/queue" className={navClass}>
                    {t('nav.reviewQueue')}
                  </NavLink>
                  <NavLink to="/clinic/patients" className={navClass}>
                    {t('nav.patients')}
                  </NavLink>
                </>
              ) : null}
            </nav>

            <div className="hg-header__tools">
              <ClinicSwitcher />
              <LanguagePicker />
              <ThemeToggle />

              {session ? (
                <button
                  type="button"
                  className="hg-btn hg-btn--ghost hg-btn--sm"
                  onClick={() => void signOut()}
                >
                  <LogOut size={16} aria-hidden="true" />
                  {t('nav.signOut')}
                </button>
              ) : (
                <NavLink to="/clinic/sign-in" className="hg-btn hg-btn--soft hg-btn--sm">
                  <ShieldCheck size={16} aria-hidden="true" />
                  {t('nav.signIn')}
                </NavLink>
              )}
            </div>
          </div>
        </div>
      </header>

      <Disclaimer />

      <main id="main-content" className="hg-main">
        <div className="hg-container">{children}</div>
      </main>

      <footer className="hg-footer">
        <div className="hg-container hg-footer__grid">
          <div className="hg-footer__brand">
            <span className="hg-footer__logo">
              <LogoMark size={28} />
              <strong>{BRAND}</strong>
            </span>
            <p>{t('app.tagline')}</p>
            <a className="hg-footer__emergency" href="tel:112">
              <Phone size={16} aria-hidden="true" />
              {t('footer.emergency')}
            </a>
          </div>

          <div>
            <h2 className="hg-footer__heading">{t('footer.patients')}</h2>
            <ul className="hg-footer__links">
              <li>
                <Link to="/describe">{t('nav.describe')}</Link>
              </li>
              <li>
                <Link to="/symptoms">{t('nav.symptomCheck')}</Link>
              </li>
            </ul>
          </div>

          <div>
            <h2 className="hg-footer__heading">{t('footer.clinics')}</h2>
            <ul className="hg-footer__links">
              <li>
                <Link to="/clinic/sign-in">{t('nav.signIn')}</Link>
              </li>
            </ul>
          </div>
        </div>
        <div className="hg-container hg-footer__base">
          <span>{t('footer.madeIn')}</span>
          <span>© {new Date().getFullYear()} {BRAND}</span>
        </div>
      </footer>
    </div>
  );
}
