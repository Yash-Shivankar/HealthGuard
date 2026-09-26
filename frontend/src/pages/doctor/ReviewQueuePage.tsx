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

import { Building, Check, ClipboardCheck, Sparkles } from 'lucide-react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Alert, Card, ErrorState, PageHeader, Spinner } from '../../components/common';
import { ReviewBadge, UrgencyBadge } from '../../components/clinical/ReviewBadge';
import { ApiRequestError, triageApi } from '../../services/api';
import { useAsync } from '../../services/hooks';
import { formatAge, initials } from '../../utils/formatters';
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
        <Card className="hg-state">
          <span className="hg-state__icon hg-state__icon--calm" aria-hidden="true">
            <Building size={22} />
          </span>
          <h1>{t('clinic.queueTitle')}</h1>
          <p className="hg-muted hg-flush">{error.message || t('clinic.noClinic')}</p>
        </Card>
      );
    }
    return <ErrorState message={error.message} onRetry={reload} />;
  }

  const rows = data?.results ?? [];

  // With the filter on, the server's total is exactly the number waiting, and
  // it covers every page. With it off, count what is on screen.
  const waiting = unreviewedOnly
    ? (data?.count ?? rows.length)
    : rows.filter((row) => row.needs_review).length;

  return (
    <div className="hg-dash">
      <div className="hg-dash__head">
        <PageHeader title={t('clinic.queueTitle')} lead={t('clinic.queueSubtitle')} />
        <span className="hg-stat">
          <Sparkles size={16} aria-hidden="true" />
          {t('clinic.queueWaiting', { count: waiting })}
        </span>
      </div>

      {problem ? <Alert>{problem}</Alert> : null}

      <div className="hg-toolbar">
        {/* A switch, visually; a checkbox, semantically. role="switch" makes a
            screen reader say "on/off", which is what this control means. */}
        <label className="hg-switch" htmlFor="unreviewed-only">
          <input
            id="unreviewed-only"
            type="checkbox"
            role="switch"
            checked={unreviewedOnly}
            onChange={(event) => setUnreviewedOnly(event.target.checked)}
          />
          <span className="hg-switch__track" aria-hidden="true" />
          {t('clinic.unreviewedOnly')}
        </label>
      </div>

      {rows.length === 0 ? (
        <Card className="hg-state">
          <span className="hg-state__icon" aria-hidden="true">
            <ClipboardCheck size={22} />
          </span>
          <p className="hg-muted hg-flush">{t('clinic.noQueue')}</p>
        </Card>
      ) : (
        <div className="hg-table-card">
          <div className="hg-table-wrap">
            <table className="hg-table hg-table--stack">
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
                {rows.map((assessment) => {
                  const name = assessment.patient
                    ? assessment.patient_name
                    : t('clinic.anonymous');
                  return (
                    <tr
                      key={assessment.id}
                      className={assessment.urgency_level === 'urgent' ? 'is-urgent' : undefined}
                    >
                      <td data-label={t('clinic.columnPatient')}>
                        <span className="hg-person">
                          <span className="hg-avatar" aria-hidden="true">
                            {assessment.patient ? initials(name) : '?'}
                          </span>
                          {name}
                        </span>
                      </td>
                      <td data-label={t('clinic.columnSuggestion')}>
                        {assessment.abstained || !assessment.top_disease ? (
                          <span className="hg-muted">{t('clinic.abstained')}</span>
                        ) : (
                          <span className="hg-suggestion">
                            <strong>{assessment.top_disease}</strong>
                            {assessment.top_confidence !== null ? (
                              <small className="hg-muted">
                                {t('result.symptomMatch', {
                                  percent: Math.round(assessment.top_confidence * 100),
                                })}
                              </small>
                            ) : null}
                          </span>
                        )}
                      </td>
                      <td data-label={t('clinic.columnUrgency')}>
                        <UrgencyBadge
                          level={assessment.urgency_level}
                          label={assessment.urgency_label}
                        />
                      </td>
                      <td className="hg-small hg-muted hg-nowrap" data-label={t('clinic.columnWhen')}>
                        {formatAge(assessment.created_at, i18n.language)}
                      </td>
                      <td data-label={t('clinic.columnReview')}>
                        <div className="hg-review-cell">
                          <ReviewBadge state={assessment.review_state} />
                          {assessment.needs_review && maySignOff ? (
                            <button
                              type="button"
                              className="hg-btn hg-btn--secondary hg-btn--xs"
                              disabled={signing === assessment.id}
                              onClick={() => void signOff(assessment.id)}
                            >
                              <Check size={14} aria-hidden="true" />
                              {signing === assessment.id
                                ? t('clinic.reviewing')
                                : t('clinic.markReviewed')}
                            </button>
                          ) : null}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
