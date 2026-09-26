/**
 * The symptom picker.
 *
 * A searchable checkbox list rather than a combobox library. 131 symptoms need
 * a filter, but checkboxes announce their state to a screen reader where a
 * custom combobox does not, and it is one fewer dependency on the page - the
 * original loaded jQuery and Select2 from two CDNs for this (AUDIT 3).
 *
 * The vocabulary is fetched from the server, which reads it from the trained
 * artefact. So the form cannot offer a symptom the model does not know, which
 * is what turned a crafted POST into a KeyError and a 500 before (AUDIT 2.7).
 *
 * **The symptom labels are English even when the interface is not.** They come
 * from the model's feature names, and the source dataset is English. Translating
 * 131 clinical terms is not a string-file exercise - a mistranslated symptom
 * changes what someone reports, so it needs the clinician sign-off AUDIT 11.6
 * asks for. Inventing the translations here would be the more dangerous kind of
 * completeness, so the gap is visible instead.
 */

import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';

import { Alert, Card, ErrorState, Spinner } from '../../components/common';
import { useAsync } from '../../services/hooks';
import { triageApi } from '../../services/api';

export function SymptomsPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();

  const { data: symptoms, loading, error, reload } = useAsync(
    (signal) => triageApi.symptoms(signal),
    [],
  );

  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [filter, setFilter] = useState('');
  const [days, setDays] = useState('');
  const [problem, setProblem] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);

  const visible = useMemo(() => {
    if (!symptoms) return [];
    const term = filter.trim().toLowerCase();
    if (!term) return symptoms;
    return symptoms.filter((symptom) => symptom.label.toLowerCase().includes(term));
  }, [symptoms, filter]);

  function toggle(value: string) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(value)) next.delete(value);
      else next.add(value);
      return next;
    });
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setProblem(null);

    if (selected.size === 0) {
      setProblem(t('symptoms.errorNoneSelected'));
      return;
    }
    if (!days || Number(days) < 1) {
      setProblem(t('symptoms.errorDays'));
      return;
    }

    setChecking(true);
    try {
      const result = await triageApi.check([...selected], Number(days));
      // Passed through navigation state rather than refetched: the result is
      // the outcome of one submission, and a reload that silently re-ran the
      // model could show a different answer for the same visit.
      navigate('/result', { state: { result } });
    } catch {
      setProblem(t('common.error'));
    } finally {
      setChecking(false);
    }
  }

  if (loading) return <Spinner />;
  if (error || !symptoms) {
    return <ErrorState message={error?.message ?? t('common.error')} onRetry={reload} />;
  }

  return (
    <div>
      <h1>{t('symptoms.title')}</h1>
      <p className="hg-muted">{t('symptoms.subtitle')}</p>

      {problem ? <Alert>{problem}</Alert> : null}

      <Card>
        <form onSubmit={submit} noValidate>
          <fieldset style={{ border: 0, padding: 0, margin: '0 0 16px' }}>
            <legend style={{ fontWeight: 600, marginBottom: 8 }}>
              {t('symptoms.title')}
            </legend>

            <label htmlFor="symptom-filter" className="hg-small hg-muted">
              {t('symptoms.filter')}
            </label>
            <input
              id="symptom-filter"
              type="search"
              value={filter}
              placeholder={t('symptoms.filter')}
              autoComplete="off"
              onChange={(event) => setFilter(event.target.value)}
              style={{
                width: '100%',
                padding: '10px 12px',
                marginBottom: 8,
                border: '1px solid var(--hg-border)',
                borderRadius: 'var(--hg-radius)',
                font: 'inherit',
                background: 'var(--hg-surface)',
                color: 'var(--hg-text)',
              }}
            />

            <p className="hg-small hg-muted" aria-live="polite">
              {t('symptoms.selected', { count: selected.size })}
            </p>

            {visible.length === 0 ? (
              <p className="hg-muted">{t('symptoms.noMatch')}</p>
            ) : (
              <div className="hg-symptom-grid">
                {visible.map((symptom) => (
                  <div className="hg-symptom" key={symptom.value}>
                    <input
                      id={`symptom-${symptom.value}`}
                      type="checkbox"
                      checked={selected.has(symptom.value)}
                      onChange={() => toggle(symptom.value)}
                    />
                    <label htmlFor={`symptom-${symptom.value}`}>{symptom.label}</label>
                  </div>
                ))}
              </div>
            )}
          </fieldset>

          <div className="hg-field">
            <label htmlFor="days">{t('symptoms.days')}</label>
            <input
              id="days"
              type="number"
              min={1}
              max={365}
              value={days}
              required
              onChange={(event) => setDays(event.target.value)}
            />
          </div>

          <button type="submit" className="hg-btn hg-btn--primary" disabled={checking}>
            {checking ? t('symptoms.checking') : t('symptoms.submit')}
          </button>
        </form>
      </Card>
    </div>
  );
}
