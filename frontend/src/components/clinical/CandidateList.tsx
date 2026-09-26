/**
 * The ranked differential (AUDIT 6 Tier 0.3, 4.3).
 *
 * The match band leads and the percentage is secondary, deliberately. On this
 * dataset a textbook presentation returns a unanimous forest vote, and
 * rendering that as "100% confident" would put back exactly the false certainty
 * the audit set out to remove. The wording is "symptom match" because that is
 * what the number actually measures - pattern overlap, not the chance of having
 * the condition.
 */

import { useTranslation } from 'react-i18next';

import type { Candidate } from '../../types';

export function CandidateList({ candidates }: { candidates: Candidate[] }) {
  const { t } = useTranslation();

  return (
    <ol className="hg-candidates">
      {candidates.map((candidate, index) => (
        <li
          key={candidate.disease}
          className={`hg-candidate${index === 0 ? ' hg-candidate--top' : ''}`}
        >
          <div className="hg-candidate__header">
            <span className="hg-candidate__name">{candidate.disease}</span>
            <span className="hg-candidate__band">
              {candidate.match_band}
              <span className="hg-candidate__score">
                {t('result.symptomMatch', { percent: candidate.confidence_percent })}
              </span>
            </span>
          </div>
          {/* Decorative. The percentage above is the real value, so the
              information is never carried by the bar alone. */}
          <div className="hg-candidate__bar" aria-hidden="true">
            <span style={{ width: `${candidate.confidence_percent}%` }} />
          </div>
        </li>
      ))}
    </ol>
  );
}
