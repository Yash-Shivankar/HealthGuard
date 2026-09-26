/**
 * Describe the problem in your own words, in your own language.
 *
 * The Tier 1 surface (AUDIT 6): a patient types or speaks in Hindi, Marathi or
 * English, and the system proposes canonical symptoms. It is an *alternative* to
 * the 131-checkbox list, not a replacement - the checkbox page still works, and
 * this page's confirm step hands off to it, because the fallback has to be one
 * click away when the extraction misreads someone.
 *
 * Three things this page is careful about.
 *
 * **It never shows a condition.** Extraction normalises language; the model
 * classifies, after the symptoms are confirmed. Nothing on this screen names a
 * disease, and there is no API call here that could return one.
 *
 * **It shows the words each symptom came from.** A patient confirming "itching"
 * needs to see that it was read from "khujli", or the confirmation is a guess
 * about a machine's guess.
 *
 * **It says when the transcript is fabricated.** With no speech vendor
 * contracted (AUDIT 11.5), voice returns canned text. That draft is marked
 * simulated and the server refuses to confirm it, so this page says so plainly
 * rather than presenting a dead button.
 */

import { ArrowRight, CalendarDays, Languages, Sparkles } from 'lucide-react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useNavigate } from 'react-router-dom';

import { Alert, Card, PageHeader, Spinner } from '../../components/common';
import { ReviewBadge } from '../../components/clinical/ReviewBadge';
import { aiApi } from '../../services/api';
import type { IntakeDraft } from '../../types';

export function DescribePage() {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();

  const [text, setText] = useState('');
  const [draft, setDraft] = useState<IntakeDraft | null>(null);
  const [accepted, setAccepted] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setProblem(null);

    if (!text.trim()) {
      setProblem(t('describe.errorEmpty'));
      return;
    }

    setBusy(true);
    try {
      // The interface language is the best available guess at what they typed,
      // and it is a guess: someone using the English UI may well type Hinglish.
      // The server stores it as a label, never as a parsing instruction - the
      // matcher tries all three languages regardless.
      const result = await aiApi.describe(text, i18n.language);
      setDraft(result);
      setAccepted(new Set(result.proposed_symptoms.map((entry) => entry.symptom)));
    } catch {
      setProblem(t('describe.errorFailed'));
    } finally {
      setBusy(false);
    }
  }

  function toggle(symptom: string) {
    setAccepted((current) => {
      const next = new Set(current);
      if (next.has(symptom)) next.delete(symptom);
      else next.add(symptom);
      return next;
    });
  }

  /**
   * Hand the accepted symptoms to the ordinary symptom-check flow.
   *
   * Deliberately not the clinician confirm endpoint: this is the patient, and
   * the sign-off that endpoint performs is a clinical act. What happens here is
   * that the patient's own words have saved them some clicking.
   */
  function continueToCheck() {
    navigate('/symptoms', {
      state: {
        preselected: [...accepted],
        days: draft?.suggested_days ?? undefined,
      },
    });
  }

  return (
    <div className="hg-flow hg-narrow-wide">
      <PageHeader title={t('describe.title')} lead={t('describe.subtitle')} />

      <Card className="hg-compose">
        <form onSubmit={submit} noValidate>
          <label htmlFor="description" className="hg-compose__label">
            {t('describe.label')}
          </label>
          <textarea
            id="description"
            className="hg-textarea"
            rows={5}
            value={text}
            onChange={(event) => setText(event.target.value)}
            placeholder={t('describe.placeholder')}
            aria-describedby="description-hint"
            // The three languages the interface supports. A browser with a
            // Devanagari keyboard needs no help; this is for the rest.
            lang={i18n.language}
          />
          <p id="description-hint" className="hg-field__hint">
            <Languages size={14} aria-hidden="true" /> {t('describe.languagesHint')}
          </p>

          {/* Examples in all three languages, whatever the interface language:
              seeing Devanagari accepted is what convinces a Hindi speaker they
              can type the way they talk. */}
          <div className="hg-examples">
            <span className="hg-small hg-muted">{t('describe.tryExample')}</span>
            {EXAMPLES.map((example) => (
              <button
                key={example.text}
                type="button"
                className="hg-example"
                lang={example.lang}
                onClick={() => setText(example.text)}
              >
                {example.text}
              </button>
            ))}
          </div>

          <div className="hg-compose__actions">
            <button type="submit" className="hg-btn hg-btn--primary hg-btn--lg" disabled={busy}>
              <Sparkles size={18} aria-hidden="true" />
              {busy ? t('describe.reading') : t('describe.submit')}
            </button>
            <span className="hg-muted hg-small">{t('describe.or')}</span>
            <Link to="/symptoms" className="hg-btn hg-btn--ghost">
              {t('describe.pickManually')}
            </Link>
          </div>
        </form>
      </Card>

      {problem && <Alert>{problem}</Alert>}
      {busy && <Spinner label={t('describe.reading')} />}

      {draft && (
        <Card className="hg-reading">
          <div className="hg-reading__head">
            <h2 className="hg-card__title">{t('describe.weRead')}</h2>
            {/* AUDIT 8.3: AI-generated and unreviewed content is visually
                distinct wherever it appears - including here, where the reader
                is the patient rather than a clinician. */}
            <ReviewBadge state="unreviewed" />
          </div>

          {/* Distinct from "you have no symptoms", and the wording must not let
              the first be read as the second. */}
          {draft.understood_nothing ? (
            <Alert>{t('describe.understoodNothing')}</Alert>
          ) : (
            <>
              <p className="hg-small hg-muted">{t('describe.checkThese')}</p>
              <ul className="hg-checklist">
                {draft.proposed_symptoms.map((entry) => (
                  <li key={entry.symptom}>
                    <label className="hg-check-row">
                      <input
                        type="checkbox"
                        checked={accepted.has(entry.symptom)}
                        onChange={() => toggle(entry.symptom)}
                      />
                      <span className="hg-check-row__text">
                        <strong>{entry.label}</strong>
                        {/* The evidence, not the number. */}
                        <small>{t('describe.matchedOn', { phrase: entry.evidence })}</small>
                      </span>
                    </label>
                  </li>
                ))}
              </ul>
            </>
          )}

          {/* Includes the "fever - which one?" questions, which are the most
              useful thing on this screen when they appear. */}
          {draft.unmapped_phrases.length > 0 && (
            <div className="hg-unmapped">
              <h3>{t('describe.notUnderstood')}</h3>
              <p className="hg-small hg-muted">{t('describe.notUnderstoodHint')}</p>
              <ul className="hg-chips">
                {draft.unmapped_phrases.map((phrase) => (
                  <li key={phrase}>{phrase}</li>
                ))}
              </ul>
            </div>
          )}

          {draft.suggested_days !== null && (
            <p className="hg-inline-note">
              <CalendarDays size={16} aria-hidden="true" />
              {t('describe.durationRead', { count: draft.suggested_days })}
            </p>
          )}

          <div className="hg-actions">
            <button
              type="button"
              className="hg-btn hg-btn--primary"
              onClick={continueToCheck}
              disabled={accepted.size === 0}
            >
              {t('describe.continue')}
              <ArrowRight size={18} aria-hidden="true" />
            </button>
            <button type="button" className="hg-btn hg-btn--ghost" onClick={() => navigate('/symptoms')}>
              {t('describe.pickManually')}
            </button>
          </div>

          <p className="hg-ai-note">
            <Sparkles size={16} aria-hidden="true" />
            <span>
              {t('describe.aiNotice')}
              {draft.simulated && ` ${t('describe.simulatedNotice')}`}
            </span>
          </p>
        </Card>
      )}
    </div>
  );
}

const EXAMPLES = [
  { lang: 'en', text: 'Fever and headache since yesterday' },
  { lang: 'hi', text: 'चार दिन से खुजली और दाने हैं' },
  { lang: 'mr', text: 'दोन दिवसांपासून खोकला आणि ताप आहे' },
] as const;
