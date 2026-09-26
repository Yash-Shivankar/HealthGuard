/**
 * The ranked differential (AUDIT 6 Tier 0.3, 4.3).
 *
 * The match band leads and the percentage is secondary, deliberately. On this
 * dataset a textbook presentation returns a unanimous forest vote, and
 * rendering that as "100% confident" would put back exactly the false certainty
 * the audit set out to remove. The wording is "symptom match" because that is
 * what the number actually measures - pattern overlap, not the chance of having
 * the condition.
 *
 * Redesign: each candidate is a row with its rank, a band pill and a slim
 * meter. The meter is teal for every rank - colouring the top row green and the
 * rest grey would read as "this one is right", which is the verdict this page
 * refuses to give.
 */

import { useTranslation } from 'react-i18next';

import type { Candidate } from '../../types';

const BAND_CLASS: Record<Candidate['match_band'], string> = {
  'Strong match': 'strong',
  'Possible match': 'possible',
  'Weak match': 'weak',
};

export function CandidateList({ candidates }: { candidates: Candidate[] }) {
  const { t } = useTranslation();

  return (
    <ol className="hg-candidates">
      {candidates.map((candidate, index) => (
        <li
          key={candidate.disease}
          className={`hg-candidate${index === 0 ? ' hg-candidate--top' : ''}`}
        >
          <span className="hg-candidate__rank" aria-hidden="true">
            {index + 1}
          </span>
          <div className="hg-candidate__body">
            <div className="hg-candidate__header">
              <span className="hg-candidate__name">{candidate.disease}</span>
              <span className={`hg-band hg-band--${BAND_CLASS[candidate.match_band] ?? 'weak'}`}>
                {candidate.match_band}
              </span>
            </div>
            {/* Decorative. The percentage beside it is the real value, so the
                information is never carried by the bar alone. */}
            <div className="hg-candidate__meter">
              <div className="hg-meter" aria-hidden="true">
                <span style={{ width: `${Math.max(4, candidate.confidence_percent)}%` }} />
              </div>
              <span className="hg-candidate__score">
                {t('result.symptomMatch', { percent: candidate.confidence_percent })}
              </span>
            </div>
          </div>
        </li>
      ))}
    </ol>
  );
}
