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

import { ArrowRight, Check, Minus, Plus, Search, Sparkles, X } from 'lucide-react';
import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useLocation, useNavigate } from 'react-router-dom';

import { Alert, Card, ErrorState, PageHeader, Spinner } from '../../components/common';
import { FlowSteps } from '../../components/layout/FlowSteps';
import { useAsync } from '../../services/hooks';
import { triageApi } from '../../services/api';

export function SymptomsPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();

  const { data: symptoms, loading, error, reload } = useAsync(
    (signal) => triageApi.symptoms(signal),
    [],
  );

  // Arrives from /describe when the patient described the problem in their own
  // words. Seeded rather than locked: the extraction can misread someone, and
  // the whole point of landing here is that every box stays editable.
  const handoff = (useLocation().state ?? {}) as {
    preselected?: string[];
    days?: number | null;
  };

  const [selected, setSelected] = useState<Set<string>>(
    () => new Set(handoff.preselected ?? []),
  );
  const [filter, setFilter] = useState('');
  const [days, setDays] = useState(handoff.days ? String(handoff.days) : '');
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

  // The selected list is shown alphabetically rather than in picking order:
  // stable while ticking, and easy to scan when checking it before submit.
  const labelFor = new Map(symptoms.map((symptom) => [symptom.value, symptom.label]));
  const chosen = [...selected].sort((a, b) =>
    (labelFor.get(a) ?? a).localeCompare(labelFor.get(b) ?? b),
  );
  const dayCount = Number(days) || 0;

  return (
    <div className="hg-flow">
      <FlowSteps current="symptoms" />
      <PageHeader title={t('symptoms.title')} lead={t('symptoms.subtitle')} />

      {problem ? <Alert>{problem}</Alert> : null}

      <form onSubmit={submit} noValidate className="hg-symptoms-layout">
        <Card className="hg-symptoms-picker">
          <fieldset className="hg-fieldset">
            <legend className="hg-visually-hidden">{t('symptoms.title')}</legend>

            <div className="hg-search">
              <Search size={18} aria-hidden="true" className="hg-search__icon" />
              <label htmlFor="symptom-filter" className="hg-visually-hidden">
                {t('symptoms.filter')}
              </label>
              <input
                id="symptom-filter"
                type="search"
                value={filter}
                placeholder={t('symptoms.filter')}
                autoComplete="off"
                onChange={(event) => setFilter(event.target.value)}
              />
            </div>

            {handoff.preselected?.length ? (
              <p className="hg-inline-note">
                <Sparkles size={16} aria-hidden="true" />
                {t('symptoms.fromDescription')}
              </p>
            ) : null}

            {visible.length === 0 ? (
              <p className="hg-empty">{t('symptoms.noMatch')}</p>
            ) : (
              // Real checkboxes, styled as chips. The input stays in the
              // accessibility tree and takes keyboard focus; only its box is
              // hidden. A div-with-onClick "chip" would announce nothing.
              <div className="hg-symptom-grid">
                {visible.map((symptom) => (
                  <label className="hg-symptom-chip" key={symptom.value}>
                    <input
                      type="checkbox"
                      checked={selected.has(symptom.value)}
                      onChange={() => toggle(symptom.value)}
                    />
                    <span>
                      <Check size={14} strokeWidth={3} aria-hidden="true" className="hg-symptom-chip__tick" />
                      {symptom.label}
                    </span>
                  </label>
                ))}
              </div>
            )}
          </fieldset>
        </Card>

        <aside className="hg-summary" aria-label={t('symptoms.yourSymptoms')}>
          <div className="hg-summary__head">
            <h2>{t('symptoms.yourSymptoms')}</h2>
            <span className="hg-count" aria-live="polite">
              {t('symptoms.selected', { count: selected.size })}
            </span>
          </div>

          {chosen.length === 0 ? (
            <p className="hg-summary__empty">{t('symptoms.emptySelection')}</p>
          ) : (
            <>
              <ul className="hg-selected">
                {chosen.map((value) => {
                  const label = labelFor.get(value) ?? value;
                  return (
                    <li key={value}>
                      {label}
                      <button
                        type="button"
                        aria-label={t('symptoms.remove', { symptom: label })}
                        onClick={() => toggle(value)}
                      >
                        <X size={14} aria-hidden="true" />
                      </button>
                    </li>
                  );
                })}
              </ul>
              <button
                type="button"
                className="hg-link-btn"
                onClick={() => setSelected(new Set())}
              >
                {t('symptoms.clearAll')}
              </button>
            </>
          )}

          <div className="hg-field hg-days">
            <label htmlFor="days">{t('symptoms.days')}</label>
            <div className="hg-stepper">
              <button
                type="button"
                aria-label={t('symptoms.fewerDays')}
                disabled={dayCount <= 1}
                onClick={() => setDays(String(Math.max(1, dayCount - 1)))}
              >
                <Minus size={16} aria-hidden="true" />
              </button>
              <input
                id="days"
                type="number"
                inputMode="numeric"
                min={1}
                max={365}
                value={days}
                required
                onChange={(event) => setDays(event.target.value)}
              />
              <button
                type="button"
                aria-label={t('symptoms.moreDays')}
                disabled={dayCount >= 365}
                onClick={() => setDays(String(Math.min(365, dayCount + 1)))}
              >
                <Plus size={16} aria-hidden="true" />
              </button>
            </div>
          </div>

          <button
            type="submit"
            className="hg-btn hg-btn--primary hg-btn--lg hg-btn--block"
            disabled={checking}
          >
            {checking ? t('symptoms.checking') : t('symptoms.submit')}
            {!checking ? <ArrowRight size={18} aria-hidden="true" /> : null}
          </button>
        </aside>
      </form>
    </div>
  );
}
