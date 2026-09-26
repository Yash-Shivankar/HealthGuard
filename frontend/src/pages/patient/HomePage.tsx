/**
 * Landing page.
 *
 * The "How it works, honestly" section is deliberate: the audit's whole
 * complaint about the original product was that it stated a disease as fact and
 * claimed accuracy it did not have. Saying plainly what the tool will and will
 * not do is the fix, and it is also the more credible sales page.
 *
 * Redesign (Sep 2026), in the order a first-time visitor reads it:
 *   1. Hero - one promise, and the two ways in side by side. "Describe in my
 *      words" leads, because typing "khujli aur daane" is easier for most
 *      patients than scanning 131 English symptom names.
 *   2. An example result, labelled as an example, so people see what they will
 *      get (urgency first, possibilities second) before they commit to anything.
 *   3. Trust chips, three steps, the honest limits.
 *   4. A clinic band - this page is also the sales page for the B2B product.
 *
 * All copy is in the locale files. The previous version hard-coded the honesty
 * section in English, so Hindi and Marathi visitors met English mid-page.
 */

import {
  ArrowRight,
  BadgeCheck,
  ClipboardCheck,
  Clock3,
  HandHeart,
  Languages,
  ListChecks,
  Lock,
  MessageSquareText,
  Pill,
  ShieldCheck,
  Stethoscope,
  type LucideIcon,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router-dom';

export function HomePage() {
  const { t } = useTranslation();

  const trust: [LucideIcon, string][] = [
    [Stethoscope, t('home.trustNotDiagnosis')],
    [Pill, t('home.trustNoMedicines')],
    [Lock, t('home.trustPrivacy')],
    [Languages, t('home.trustLanguages')],
  ];

  const steps: [LucideIcon, string, string][] = [
    [MessageSquareText, t('home.step1Title'), t('home.step1Body')],
    [ListChecks, t('home.step2Title'), t('home.step2Body')],
    [HandHeart, t('home.step3Title'), t('home.step3Body')],
  ];

  const honest = [1, 2, 3, 4].map((n) => [
    t(`home.honest${n}Title`),
    t(`home.honest${n}Body`),
  ]);

  return (
    <div className="hg-home">
      {/* ---- Hero ---------------------------------------------------------- */}
      <section className="hg-hero">
        <div className="hg-hero__copy">
          <p className="hg-eyebrow hg-eyebrow--pill">
            <HeartPulseDot />
            {t('home.eyebrow')}
          </p>
          <h1 className="hg-hero__title">
            {t('home.title')} <span className="hg-gradient-text">{t('home.titleAccent')}</span>
          </h1>
          <p className="hg-lead">{t('home.lead')}</p>

          <div className="hg-start">
            <Link to="/describe" className="hg-start__card hg-start__card--primary">
              <span className="hg-start__icon" aria-hidden="true">
                <MessageSquareText size={22} />
              </span>
              <span className="hg-start__text">
                <strong>{t('home.startDescribe')}</strong>
                <small>{t('home.startDescribeHint')}</small>
              </span>
              <ArrowRight size={20} aria-hidden="true" className="hg-start__arrow" />
            </Link>
            <Link to="/intake" className="hg-start__card">
              <span className="hg-start__icon" aria-hidden="true">
                <ListChecks size={22} />
              </span>
              <span className="hg-start__text">
                <strong>{t('home.startPick')}</strong>
                <small>{t('home.startPickHint')}</small>
              </span>
              <ArrowRight size={20} aria-hidden="true" className="hg-start__arrow" />
            </Link>
          </div>
        </div>

        {/* A picture of the product, not stock photography of smiling doctors.
            aria-hidden: it is an illustration of the result page, and a screen
            reader user reaches the real thing in two steps. */}
        <div className="hg-hero__visual" aria-hidden="true">
          <div className="hg-blob hg-blob--teal" />
          <div className="hg-blob hg-blob--sunrise" />
          <div className="hg-preview">
            <span className="hg-preview__tag">{t('home.previewLabel')}</span>
            <div className="hg-preview__urgency">
              <span className="hg-preview__urgency-icon">
                <Clock3 size={20} />
              </span>
              <div>
                <strong>{t('home.previewUrgency')}</strong>
                <small>{t('home.previewUrgencyHint')}</small>
              </div>
            </div>
            <ul className="hg-preview__list">
              {[
                [t('home.previewCondition1'), t('home.previewStrong'), 86],
                [t('home.previewCondition2'), t('home.previewPossible'), 58],
                [t('home.previewCondition3'), t('home.previewWeak'), 24],
              ].map(([name, band, value], index) => (
                <li key={String(name)}>
                  <span className="hg-preview__rank">{index + 1}</span>
                  <span className="hg-preview__name">{name}</span>
                  <span className="hg-preview__band">{band}</span>
                  <span className="hg-meter">
                    <span style={{ width: `${value}%` }} />
                  </span>
                </li>
              ))}
            </ul>
          </div>
          <div className="hg-float-chip hg-float-chip--top">
            <ShieldCheck size={16} />
            {t('home.trustNoMedicines')}
          </div>
          <div className="hg-float-chip hg-float-chip--bottom">
            <Languages size={16} />
            English · हिंदी · मराठी
          </div>
        </div>
      </section>

      {/* ---- Trust strip ---------------------------------------------------- */}
      <ul className="hg-trust" aria-label={t('home.honestTitle')}>
        {trust.map(([Icon, label]) => (
          <li key={label}>
            <Icon size={18} aria-hidden="true" />
            {label}
          </li>
        ))}
      </ul>

      {/* ---- How it works --------------------------------------------------- */}
      <section className="hg-section" aria-labelledby="how-heading">
        <div className="hg-section__head">
          <h2 id="how-heading">{t('home.howTitle')}</h2>
          <p className="hg-lead">{t('home.howLead')}</p>
        </div>
        <ol className="hg-how">
          {steps.map(([Icon, title, body], index) => (
            <li key={title} className="hg-how__step">
              <span className="hg-how__num" aria-hidden="true">
                {index + 1}
              </span>
              <span className="hg-how__icon" aria-hidden="true">
                <Icon size={24} />
              </span>
              <h3>{title}</h3>
              <p>{body}</p>
            </li>
          ))}
        </ol>
      </section>

      {/* ---- Honest limits -------------------------------------------------- */}
      <section className="hg-section hg-honest" aria-labelledby="honest-heading">
        <div className="hg-section__head">
          <h2 id="honest-heading">{t('home.honestTitle')}</h2>
          <p className="hg-lead">{t('home.honestLead')}</p>
        </div>
        <ul className="hg-honest__grid">
          {honest.map(([title, body]) => (
            <li key={title}>
              <BadgeCheck size={22} aria-hidden="true" className="hg-honest__icon" />
              <div>
                <h3>{title}</h3>
                <p>{body}</p>
              </div>
            </li>
          ))}
        </ul>
      </section>

      {/* ---- For clinics ---------------------------------------------------- */}
      <section className="hg-clinic-band" aria-labelledby="clinic-heading">
        <div>
          <p className="hg-eyebrow hg-eyebrow--light">{t('home.clinicEyebrow')}</p>
          <h2 id="clinic-heading">{t('home.clinicTitle')}</h2>
          <p>{t('home.clinicBody')}</p>
          <Link to="/clinic/sign-in" className="hg-btn hg-btn--light">
            {t('home.clinicCta')}
            <ArrowRight size={18} aria-hidden="true" />
          </Link>
        </div>
        <ul className="hg-clinic-band__points">
          {[t('home.clinicPoint1'), t('home.clinicPoint2'), t('home.clinicPoint3')].map((point) => (
            <li key={point}>
              <ClipboardCheck size={20} aria-hidden="true" />
              {point}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

/** A small pulsing dot: the "live, caring" signal in the eyebrow. Motion stops
 *  under prefers-reduced-motion. */
function HeartPulseDot() {
  return <span className="hg-pulse-dot" aria-hidden="true" />;
}
