/**
 * Where the patient is in the check: details → symptoms → result.
 *
 * Anxious people cope better with a task when they can see its end. It is an
 * ordered list with `aria-current="step"`, so a screen reader hears "step 2 of
 * 3, current" rather than three coloured dots. "Details" is marked optional,
 * because it is - the consent notice says so, and the flow should not
 * contradict it.
 */

import { Check } from 'lucide-react';
import { useTranslation } from 'react-i18next';

type Step = 'details' | 'symptoms' | 'result';
const ORDER: Step[] = ['details', 'symptoms', 'result'];

export function FlowSteps({ current }: { current: Step }) {
  const { t } = useTranslation();
  const currentIndex = ORDER.indexOf(current);

  return (
    <ol className="hg-steps" aria-label={t('flow.label')}>
      {ORDER.map((step, index) => {
        const state = index < currentIndex ? 'done' : index === currentIndex ? 'current' : 'todo';
        return (
          <li
            key={step}
            className={`hg-steps__item is-${state}`}
            aria-current={state === 'current' ? 'step' : undefined}
          >
            <span className="hg-steps__dot" aria-hidden="true">
              {state === 'done' ? <Check size={14} strokeWidth={3} /> : index + 1}
            </span>
            <span className="hg-steps__label">
              {t(`flow.${step}`)}
              {step === 'details' ? <small> · {t('flow.optional')}</small> : null}
            </span>
          </li>
        );
      })}
    </ol>
  );
}
