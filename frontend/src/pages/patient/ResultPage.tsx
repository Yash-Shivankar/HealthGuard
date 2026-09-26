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

import {
  BookOpen,
  CircleQuestionMark,
  Leaf,
  ListChecks,
  Printer,
  RotateCcw,
  ShieldCheck,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Link, Navigate, useLocation } from 'react-router-dom';

import { Card, PageHeader } from '../../components/common';
import { FlowSteps } from '../../components/layout/FlowSteps';
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
    <div className="hg-flow">
      <FlowSteps current="result" />

      {/* Composed from two separately pluralised fragments. i18next pluralises
          on a single `count`, so "3 symptoms over 1 day" needs two lookups -
          a single string got it wrong in every language that inflects. */}
      <PageHeader
        title={t('result.title')}
        lead={t('result.basedOn', {
          symptoms: t('result.basedOnSymptoms', { count: symptoms.length }),
          days: t('result.basedOnDays', { count: result.days }),
        })}
      />

      <UrgencyBanner urgency={result.urgency} />

      {result.abstained ? (
        <Card className="hg-state">
          <span className="hg-state__icon hg-state__icon--calm" aria-hidden="true">
            <CircleQuestionMark size={22} />
          </span>
          <h2>{t('result.abstainedHeading')}</h2>
          <p>{t('result.abstainedBody')}</p>
          <p className="hg-muted">{t('result.abstainedAction')}</p>
        </Card>
      ) : (
        <div className="hg-result-grid">
          <Card className="hg-result-main">
            <h2 className="hg-card__title">
              <ListChecks size={20} aria-hidden="true" />
              {t('result.possibilities')}
            </h2>
            <p className="hg-small hg-muted">{t('result.notDiagnosis')}</p>
            <CandidateList candidates={result.candidates} />
          </Card>

          <div className="hg-result-side">
            {result.description ? (
              <Card>
                <h2 className="hg-card__title">
                  <BookOpen size={20} aria-hidden="true" />
                  {t('result.about', { disease: result.candidates[0]?.disease ?? '' })}
                </h2>
                <p className="hg-flush">{result.description}</p>
              </Card>
            ) : null}

            {result.precautions.length > 0 ? (
              <Card>
                <h2 className="hg-card__title">
                  <ShieldCheck size={20} aria-hidden="true" />
                  {t('result.precautions')}
                </h2>
                <ul className="hg-ticks">
                  {result.precautions.map((precaution) => (
                    <li key={precaution}>{precaution}</li>
                  ))}
                </ul>
              </Card>
            ) : null}

            <Card>
              <h2 className="hg-card__title">
                <Leaf size={20} aria-hidden="true" />
                {t('result.remedies')}
              </h2>
              {/* Always an array from the server, including on a miss - the old
                  code assigned a bare string and the template iterated it
                  character by character (AUDIT 2.9). */}
              <ul className="hg-ticks">
                {result.remedies.map((remedy) => (
                  <li key={remedy}>{remedy}</li>
                ))}
              </ul>
              <p className="hg-small hg-muted hg-flush">{t('result.remediesNote')}</p>
            </Card>
          </div>
        </div>
      )}

      <Card>
        <h2 className="hg-card__title hg-card__title--sm">{t('result.whatYouTold')}</h2>
        <ul className="hg-chips">
          {symptoms.map((symptom) => (
            <li key={symptom}>{symptom}</li>
          ))}
        </ul>
      </Card>

      {/* The server sends this as part of the contract, so render it verbatim
          rather than relying on the banner alone. */}
      <p className="hg-fineprint">{result.disclaimer}</p>

      <div className="hg-actions hg-no-print">
        <Link to="/symptoms" className="hg-btn hg-btn--primary">
          <RotateCcw size={18} aria-hidden="true" />
          {t('result.checkAgain')}
        </Link>
        {/* A printout is what a patient can actually hand to a doctor. The
            print stylesheet drops the chrome and keeps the disclaimer. */}
        <button type="button" className="hg-btn hg-btn--secondary" onClick={() => window.print()}>
          <Printer size={18} aria-hidden="true" />
          {t('result.print')}
        </button>
        <Link to="/" className="hg-btn hg-btn--ghost">
          {t('common.backHome')}
        </Link>
      </div>
    </div>
  );
}
