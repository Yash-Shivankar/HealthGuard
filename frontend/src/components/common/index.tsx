/** Small shared pieces. Kept in one file while each is a few lines. */

import type { InputHTMLAttributes, ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

export function Card({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <section className={`hg-card ${className}`}>{children}</section>;
}

export function Spinner({ label }: { label?: string }) {
  const { t } = useTranslation();
  // A live region rather than a spinning glyph: a screen reader needs to be
  // told the page is busy, and an animation says nothing to it.
  return (
    <p className="hg-muted" role="status" aria-live="polite">
      {label ?? t('common.loading')}
    </p>
  );
}

export function Alert({ children }: { children: ReactNode }) {
  return (
    <div className="hg-alert" role="alert">
      {children}
    </div>
  );
}

interface FieldProps extends InputHTMLAttributes<HTMLInputElement> {
  id: string;
  label: string;
  error?: string;
  hint?: string;
}

export function Field({ id, label, error, hint, ...input }: FieldProps) {
  const errorId = `${id}-error`;
  const hintId = `${id}-hint`;

  return (
    <div className={`hg-field${error ? ' hg-field--error' : ''}`}>
      <label htmlFor={id}>
        {label}
        {input.required ? <span aria-hidden="true"> *</span> : null}
      </label>
      <input
        id={id}
        // Errors are associated with the field, not delivered through alert()
        // as the original page did (AUDIT 3, accessibility).
        aria-invalid={error ? true : undefined}
        aria-describedby={[error ? errorId : null, hint ? hintId : null].filter(Boolean).join(' ') || undefined}
        {...input}
      />
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
    <Card>
      <h2 style={{ fontSize: '1.1rem' }}>{t('common.error')}</h2>
      <p className="hg-muted">{message}</p>
      {onRetry ? (
        <button type="button" className="hg-btn hg-btn--secondary" onClick={onRetry}>
          {t('common.retry')}
        </button>
      ) : null}
    </Card>
  );
}
