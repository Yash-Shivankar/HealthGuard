/**
 * The result (AUDIT 2.3, 4.3, 6 Tier 0.3).
 *
 * The original printed `Disease: {{ result.disease }}` as bare fact. That is
 * both a product failure and a regulatory one: a definite diagnostic output
 * pushes the product toward being regulated medical device software, whereas
 * ranked possibilities with urgency guidance and an explicit "not a diagnosis"
 * keep it in the wellness and triage lane.
 *
 * So this page names possibilities, never a diagnosis; leads with urgency; and
 * has a real abstention state for when the model declines to answer. It shows no
 * accuracy figure anywhere, because the dataset cannot support one - see
 * ml/artifacts/MODEL_CARD.md.
 */

import { useTranslation } from 'react-i18next';
import { Link, Navigate, useLocation } from 'react-router-dom';

import { Card } from '../../components/common';
import { CandidateList } from '../../components/clinical/CandidateList';
import { UrgencyBanner } from '../../components/clinical/UrgencyBanner';
import { humanise } from '../../utils/formatters';
import type { TriageResult } from '../../types';

export function ResultPage() {
  const { t } = useTranslation();
  const location = useLocation();
  const result = (location.state as { result?: TriageResult } | null)?.result;

  // Landing here directly - a refresh, or a shared link - has nothing to show.
  // Redirecting is the honest answer; re-running the model would invent a
  // result the person never asked for. The old app returned None here and
  // 500'd (AUDIT 2.6).
  if (!result) return <Navigate to="/symptoms" replace />;

  const symptoms = result.symptoms.map(humanise);

  return (
    <div>
      <h1>{t('result.title')}</h1>
      {/* Composed from two separately pluralised fragments. i18next pluralises
          on a single `count`, so "3 symptoms over 1 day" needs two lookups -
          a single string got it wrong in every language that inflects. */}
      <p className="hg-muted">
        {t('result.basedOn', {
          symptoms: t('result.basedOnSymptoms', { count: symptoms.length }),
          days: t('result.basedOnDays', { count: result.days }),
        })}
      </p>

      <UrgencyBanner urgency={result.urgency} />

      {result.abstained ? (
        <Card>
          <h2 style={{ fontSize: '1.1rem' }}>{t('result.abstainedHeading')}</h2>
          <p>{t('result.abstainedBody')}</p>
          <p style={{ marginBottom: 0 }}>{t('result.abstainedAction')}</p>
        </Card>
      ) : (
        <>
          <Card>
            <h2 style={{ fontSize: '1.1rem' }}>{t('result.possibilities')}</h2>
            <p className="hg-small hg-muted">{t('result.notDiagnosis')}</p>
            <CandidateList candidates={result.candidates} />
          </Card>

          {result.description ? (
            <Card>
              <h2 style={{ fontSize: '1.1rem' }}>
                {t('result.about', { disease: result.candidates[0]?.disease ?? '' })}
              </h2>
              <p style={{ marginBottom: 0 }}>{result.description}</p>
            </Card>
          ) : null}

          {result.precautions.length > 0 ? (
            <Card>
              <h2 style={{ fontSize: '1.1rem' }}>{t('result.precautions')}</h2>
              <ul style={{ marginBottom: 0 }}>
                {result.precautions.map((precaution) => (
                  <li key={precaution}>{precaution}</li>
                ))}
              </ul>
            </Card>
          ) : null}

          <Card>
            <h2 style={{ fontSize: '1.1rem' }}>{t('result.remedies')}</h2>
            {/* Always an array from the server, including on a miss - the old
                code assigned a bare string and the template iterated it
                character by character (AUDIT 2.9). */}
            <ul>
              {result.remedies.map((remedy) => (
                <li key={remedy}>{remedy}</li>
              ))}
            </ul>
            <p className="hg-small hg-muted" style={{ marginBottom: 0 }}>
              {t('result.remediesNote')}
            </p>
          </Card>
        </>
      )}

      <Card>
        <h2 style={{ fontSize: '0.95rem' }}>{t('result.whatYouTold')}</h2>
        <ul className="hg-chips">
          {symptoms.map((symptom) => (
            <li key={symptom}>{symptom}</li>
          ))}
        </ul>
      </Card>

      {/* The server sends this as part of the contract, so render it verbatim
          rather than relying on the banner alone. */}
      <p className="hg-small hg-muted">{result.disclaimer}</p>

      <div className="hg-actions">
        <Link to="/symptoms" className="hg-btn hg-btn--primary">
          {t('result.checkAgain')}
        </Link>
        <Link to="/" className="hg-btn hg-btn--secondary">
          {t('common.backHome')}
        </Link>
      </div>
    </div>
  );
}
