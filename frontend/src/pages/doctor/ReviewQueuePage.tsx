/**
 * The clinician review queue.
 *
 * This is the screen the whole "AI draft, unreviewed" state exists for. TPG 2020
 * requires a registered practitioner to sign off an AI output before it enters a
 * record, and AUDIT 8.3 asks for that state to be visually distinct wherever it
 * appears - so the badge is not decoration, it is the control.
 *
 * Oldest first, because what has been waiting longest is what matters. That is
 * the audit's ageing indicator (8.5), as a list.
 */

import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Alert, Card, ErrorState, Spinner } from '../../components/common';
import { ReviewBadge, UrgencyBadge } from '../../components/clinical/ReviewBadge';
import { ApiRequestError, triageApi } from '../../services/api';
import { useAsync } from '../../services/hooks';
import { formatAge } from '../../utils/formatters';
import { useSession } from '../../services/store/SessionContext';

export function ReviewQueuePage() {
  const { t, i18n } = useTranslation();
  const { can } = useSession();

  const [unreviewedOnly, setUnreviewedOnly] = useState(true);
  const [signing, setSigning] = useState<number | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  const { data, loading, error, reload } = useAsync(
    (signal) => triageApi.queue({ unreviewedOnly, signal }),
    [unreviewedOnly],
  );

  // Hiding the button is presentation only; the server refuses the PATCH from
  // an account without the permission regardless.
  const maySignOff = can('triage.change_triageassessment');

  async function signOff(id: number) {
    setProblem(null);
    setSigning(id);
    try {
      await triageApi.markReviewed(id);
      reload();
    } catch (cause) {
      setProblem(cause instanceof ApiRequestError ? cause.message : t('common.error'));
    } finally {
      setSigning(null);
    }
  }

  if (loading) return <Spinner />;

  if (error) {
    // 403 here means the account has no clinic, which the server says in words.
    // Showing an empty queue instead would read as "no work to do".
    if (error instanceof ApiRequestError && error.status === 403) {
      return (
        <Card>
          <h1 style={{ fontSize: '1.1rem' }}>{t('clinic.queueTitle')}</h1>
          <p className="hg-muted" style={{ marginBottom: 0 }}>
            {error.message || t('clinic.noClinic')}
          </p>
        </Card>
      );
    }
    return <ErrorState message={error.message} onRetry={reload} />;
  }

  const rows = data?.results ?? [];

  return (
    <div>
      <h1>{t('clinic.queueTitle')}</h1>
      <p className="hg-muted">{t('clinic.queueSubtitle')}</p>

      {problem ? <Alert>{problem}</Alert> : null}

      <div className="hg-toolbar">
        <div className="hg-checkbox">
          <input
            id="unreviewed-only"
            type="checkbox"
            checked={unreviewedOnly}
            onChange={(event) => setUnreviewedOnly(event.target.checked)}
          />
          <label htmlFor="unreviewed-only">{t('clinic.unreviewedOnly')}</label>
        </div>
      </div>

      {rows.length === 0 ? (
        <Card>
          <p className="hg-empty" style={{ margin: 0 }}>
            {t('clinic.noQueue')}
          </p>
        </Card>
      ) : (
        <div className="hg-table-wrap">
          <table className="hg-table">
            <thead>
              <tr>
                <th scope="col">{t('clinic.columnPatient')}</th>
                <th scope="col">{t('clinic.columnSuggestion')}</th>
                <th scope="col">{t('clinic.columnUrgency')}</th>
                <th scope="col">{t('clinic.columnWhen')}</th>
                <th scope="col">{t('clinic.columnReview')}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((assessment) => (
                <tr key={assessment.id}>
                  <td>
                    {assessment.patient ? assessment.patient_name : t('clinic.anonymous')}
                  </td>
                  <td>
                    {assessment.abstained || !assessment.top_disease ? (
                      <span className="hg-muted">{t('clinic.abstained')}</span>
                    ) : (
                      <>
                        {assessment.top_disease}
                        {assessment.top_confidence !== null ? (
                          <span className="hg-small hg-muted hg-mono">
                            {' '}
                            {t('result.symptomMatch', {
                              percent: Math.round(assessment.top_confidence * 100),
                            })}
                          </span>
                        ) : null}
                      </>
                    )}
                  </td>
                  <td>
                    <UrgencyBadge
                      level={assessment.urgency_level}
                      label={assessment.urgency_label}
                    />
                  </td>
                  <td className="hg-small hg-muted">
                    {formatAge(assessment.created_at, i18n.language)}
                  </td>
                  <td>
                    {assessment.needs_review ? (
                      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                        <ReviewBadge state={assessment.review_state} />
                        {maySignOff ? (
                          <button
                            type="button"
                            className="hg-btn hg-btn--secondary"
                            style={{ padding: '6px 14px', fontSize: '0.85rem' }}
                            disabled={signing === assessment.id}
                            onClick={() => void signOff(assessment.id)}
                          >
                            {signing === assessment.id
                              ? t('clinic.reviewing')
                              : t('clinic.markReviewed')}
                          </button>
                        ) : null}
                      </div>
                    ) : (
                      <ReviewBadge state={assessment.review_state} />
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
