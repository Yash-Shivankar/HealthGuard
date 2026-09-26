/**
 * Intake, with the itemised DPDP consent notice (AUDIT 2.3, 4.1).
 *
 * Two things this page does that the original did not:
 *
 * It states what is collected, why, for how long and how to withdraw, *before*
 * collection - the DPDP Rules 2025 require notice to be specific and itemised,
 * not buried in a policy page.
 *
 * It offers a way past. The symptom checker does not need anyone's identity, and
 * requiring details for a function that does not use them is exactly what the
 * notice obligations exist to discourage. So "skip" is a real option, not a dark
 * pattern in reverse.
 */

import {
  ArrowRight,
  ClipboardList,
  Clock3,
  Lock,
  ShieldCheck,
  Stethoscope,
  Undo2,
} from 'lucide-react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useNavigate } from 'react-router-dom';

import { Alert, Card, Field, PageHeader } from '../../components/common';
import { FlowSteps } from '../../components/layout/FlowSteps';
import { ApiRequestError, patientsApi } from '../../services/api';
import { isValidMobile } from '../../utils/validators/phone';

interface FormState {
  name: string;
  contact: string;
  age: string;
  height_cm: string;
  weight_kg: string;
  consent: boolean;
}

const EMPTY: FormState = {
  name: '',
  contact: '',
  age: '',
  height_cm: '',
  weight_kg: '',
  consent: false,
};

export function IntakePage() {
  const { t } = useTranslation();
  const navigate = useNavigate();

  const [form, setForm] = useState<FormState>(EMPTY);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [failure, setFailure] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  function set<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  /**
   * Client-side checks that save a round trip.
   *
   * The server validates independently and is the authority - AUDIT 2.7's
   * finding was that a JavaScript-only check is bypassed by anything that is
   * not a browser.
   */
  function validate(): boolean {
    const found: Record<string, string> = {};
    if (!form.name.trim()) found.name = t('common.required');
    if (!isValidMobile(form.contact)) found.contact = t('intake.errorContact');
    if (!form.age || Number(form.age) < 0 || Number(form.age) > 120) found.age = t('intake.errorAge');
    if (!form.height_cm) found.height_cm = t('common.required');
    if (!form.weight_kg) found.weight_kg = t('common.required');
    if (!form.consent) found.consent = t('intake.errorConsent');
    setErrors(found);
    return Object.keys(found).length === 0;
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setFailure(null);
    if (!validate()) return;

    setSaving(true);
    try {
      await patientsApi.intake({
        name: form.name.trim(),
        contact: form.contact,
        age: Number(form.age),
        height_cm: Number(form.height_cm),
        weight_kg: Number(form.weight_kg),
        consent: form.consent,
      });
      navigate('/symptoms');
    } catch (cause) {
      if (cause instanceof ApiRequestError) {
        // Surface the server's per-field messages rather than a generic
        // failure: it knows things the client does not, like a number in a
        // series the client accepted.
        const fields: Record<string, string> = {};
        for (const [key, messages] of Object.entries(cause.fields)) {
          fields[key] = messages.join(' ');
        }
        setErrors(fields);
        if (Object.keys(fields).length === 0) setFailure(cause.message);
      } else {
        setFailure(t('common.error'));
      }
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="hg-flow">
      <FlowSteps current="details" />

      <PageHeader
        title={t('intake.title')}
        eyebrow={
          <span className="hg-chip hg-chip--soft">
            <Lock size={14} aria-hidden="true" />
            {t('intake.optionalBadge')}
          </span>
        }
      />

      <div className="hg-split">
        {/* The notice comes first in the DOM, so a screen reader and a phone
            user both meet it before the fields - "notice before collection" is
            the DPDP requirement, and layout must not reorder it. On wide
            screens it sits beside the form instead of above it. */}
        <section className="hg-notice" aria-labelledby="consent-notice-heading">
          <h2 id="consent-notice-heading">
            <ShieldCheck size={18} aria-hidden="true" />
            {t('intake.noticeHeading')}
          </h2>
          <dl>
            <div>
              <dt>
                <ClipboardList size={16} aria-hidden="true" />
                {t('intake.whatWeCollect')}
              </dt>
              <dd>{t('intake.whatWeCollectBody')}</dd>
            </div>
            <div>
              <dt>
                <Stethoscope size={16} aria-hidden="true" />
                {t('intake.why')}
              </dt>
              <dd>{t('intake.whyBody')}</dd>
            </div>
            <div>
              <dt>
                <Clock3 size={16} aria-hidden="true" />
                {t('intake.retention')}
              </dt>
              <dd>{t('intake.retentionBody')}</dd>
            </div>
            <div>
              <dt>
                <Undo2 size={16} aria-hidden="true" />
                {t('intake.withdrawing')}
              </dt>
              <dd>{t('intake.withdrawingBody')}</dd>
            </div>
          </dl>
        </section>

        <Card className="hg-form-card">
          {failure ? <Alert>{failure}</Alert> : null}
          <form onSubmit={submit} noValidate>
            <div className="hg-form-grid">
              <Field
                id="name"
                label={t('intake.name')}
                value={form.name}
                error={errors.name}
                maxLength={100}
                autoComplete="name"
                required
                onChange={(e) => set('name', e.target.value)}
              />
              <Field
                id="contact"
                label={t('intake.contact')}
                value={form.contact}
                error={errors.contact}
                inputMode="tel"
                autoComplete="tel"
                maxLength={20}
                hint={t('intake.contactHint')}
                required
                onChange={(e) => set('contact', e.target.value)}
              />
            </div>
            <div className="hg-form-grid hg-form-grid--3">
              <Field
                id="age"
                label={t('intake.age')}
                type="number"
                min={0}
                max={120}
                value={form.age}
                error={errors.age}
                required
                onChange={(e) => set('age', e.target.value)}
              />
              <Field
                id="height_cm"
                label={t('intake.height')}
                type="number"
                min={30}
                max={275}
                step="any"
                suffix="cm"
                value={form.height_cm}
                error={errors.height_cm}
                required
                onChange={(e) => set('height_cm', e.target.value)}
              />
              <Field
                id="weight_kg"
                label={t('intake.weight')}
                type="number"
                min={1}
                max={650}
                step="any"
                suffix="kg"
                value={form.weight_kg}
                error={errors.weight_kg}
                required
                onChange={(e) => set('weight_kg', e.target.value)}
              />
            </div>

            {/* The consent box is a large, obvious target and unticked by
                default. A pre-ticked box is not consent under DPDP. */}
            <div className={`hg-field${errors.consent ? ' hg-field--error' : ''}`}>
              <label className="hg-consent" htmlFor="consent">
                <input
                  id="consent"
                  type="checkbox"
                  checked={form.consent}
                  aria-describedby={errors.consent ? 'consent-error' : undefined}
                  onChange={(e) => set('consent', e.target.checked)}
                />
                <span>{t('intake.consent')}</span>
              </label>
              {errors.consent ? (
                <small id="consent-error" className="hg-field__error" role="alert">
                  {errors.consent}
                </small>
              ) : null}
            </div>

            <div className="hg-actions">
              <button type="submit" className="hg-btn hg-btn--primary hg-btn--lg" disabled={saving}>
                {t('intake.submit')}
                <ArrowRight size={18} aria-hidden="true" />
              </button>
              <Link to="/symptoms" className="hg-btn hg-btn--ghost">
                {t('intake.skip')}
              </Link>
            </div>
          </form>
        </Card>
      </div>
    </div>
  );
}
