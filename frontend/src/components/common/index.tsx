/** Small shared pieces. Kept in one file while each is a few lines. */

import { TriangleAlert, RefreshCw } from 'lucide-react';
import type { InputHTMLAttributes, ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

export { Logo, LogoMark } from './Logo';

export function Card({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <section className={`hg-card ${className}`.trim()}>{children}</section>;
}

/** A page's title block: optional eyebrow, the h1, and a lead paragraph. */
export function PageHeader({
  eyebrow,
  title,
  lead,
  children,
}: {
  eyebrow?: ReactNode;
  title: ReactNode;
  lead?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <header className="hg-page-header">
      {eyebrow ? <p className="hg-eyebrow">{eyebrow}</p> : null}
      <h1>{title}</h1>
      {lead ? <p className="hg-lead">{lead}</p> : null}
      {children}
    </header>
  );
}

export function Spinner({ label }: { label?: string }) {
  const { t } = useTranslation();
  // A live region rather than a spinning glyph alone: a screen reader needs to
  // be told the page is busy, and an animation says nothing to it. The ring is
  // decorative and stops under prefers-reduced-motion.
  return (
    <p className="hg-spinner" role="status" aria-live="polite">
      <span className="hg-spinner__ring" aria-hidden="true" />
      {label ?? t('common.loading')}
    </p>
  );
}

export function Alert({ children }: { children: ReactNode }) {
  return (
    <div className="hg-alert" role="alert">
      <TriangleAlert size={18} aria-hidden="true" className="hg-alert__icon" />
      <div>{children}</div>
    </div>
  );
}

interface FieldProps extends InputHTMLAttributes<HTMLInputElement> {
  id: string;
  label: string;
  error?: string;
  hint?: string;
  /** Unit shown inside the input's right edge, e.g. "cm". Decorative: the
   *  label already carries the unit for assistive technology. */
  suffix?: string;
}

export function Field({ id, label, error, hint, suffix, ...input }: FieldProps) {
  const errorId = `${id}-error`;
  const hintId = `${id}-hint`;

  return (
    <div className={`hg-field${error ? ' hg-field--error' : ''}`}>
      <label htmlFor={id}>
        {label}
        {input.required ? (
          <span className="hg-required" aria-hidden="true">
            {' '}
            *
          </span>
        ) : null}
      </label>
      <div className={suffix ? 'hg-input-wrap has-suffix' : 'hg-input-wrap'}>
        <input
          id={id}
          // Errors are associated with the field, not delivered through alert()
          // as the original page did (AUDIT 3, accessibility).
          aria-invalid={error ? true : undefined}
          aria-describedby={
            [error ? errorId : null, hint ? hintId : null].filter(Boolean).join(' ') || undefined
          }
          {...input}
        />
        {suffix ? (
          <span className="hg-input-suffix" aria-hidden="true">
            {suffix}
          </span>
        ) : null}
      </div>
      {hint ? (
        <small id={hintId} className="hg-field__hint">
          {hint}
        </small>
      ) : null}
      {error ? (
        <small id={errorId} className="hg-field__error" role="alert">
          {error}
        </small>
      ) : null}
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  const { t } = useTranslation();
  return (
    <Card className="hg-state">
      <span className="hg-state__icon" aria-hidden="true">
        <TriangleAlert size={22} />
      </span>
      <h2>{t('common.error')}</h2>
      <p className="hg-muted">{message}</p>
      {onRetry ? (
        <button type="button" className="hg-btn hg-btn--secondary" onClick={onRetry}>
          <RefreshCw size={16} aria-hidden="true" />
          {t('common.retry')}
        </button>
      ) : null}
    </Card>
  );
}
