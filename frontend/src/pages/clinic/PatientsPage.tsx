/**
 * The clinic's patient list.
 *
 * Scoped by the server to the caller's clinic - this page has no clinic
 * parameter to pass, deliberately, so there is nothing here to tamper with.
 *
 * The contact column shows what the server sent, which is masked. The full
 * number is never sent to a browser at all, so there is no "reveal" control to
 * build and no way for this page to leak one (AUDIT 8.8).
 */

import { Building, Search, Users } from 'lucide-react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Card, ErrorState, PageHeader, Spinner } from '../../components/common';
import { ApiRequestError, patientsApi } from '../../services/api';
import { useAsync, useDebounced } from '../../services/hooks';
import { formatDateTime, initials } from '../../utils/formatters';

export function PatientsPage() {
  const { t, i18n } = useTranslation();
  const [search, setSearch] = useState('');
  // Debounced so typing does not fire a request per keystroke. The server
  // throttles too, and an unthrottled search over patient names is the
  // extraction path AUDIT 8.8 warns about.
  const debounced = useDebounced(search, 300);

  const { data, loading, error, reload } = useAsync(
    (signal) => patientsApi.list({ search: debounced, signal }),
    [debounced],
  );

  if (loading && !data) return <Spinner />;

  if (error) {
    if (error instanceof ApiRequestError && error.status === 403) {
      return (
        <Card className="hg-state">
          <span className="hg-state__icon hg-state__icon--calm" aria-hidden="true">
            <Building size={22} />
          </span>
          <h1>{t('clinic.patientsTitle')}</h1>
          <p className="hg-muted hg-flush">{error.message || t('clinic.noClinic')}</p>
        </Card>
      );
    }
    return <ErrorState message={error.message} onRetry={reload} />;
  }

  const rows = data?.results ?? [];

  return (
    <div className="hg-dash">
      <div className="hg-dash__head">
        <PageHeader
          title={t('clinic.patientsTitle')}
          lead={t('clinic.patientsCount', { count: data?.count ?? rows.length })}
        />
      </div>

      <div className="hg-toolbar">
        <div className="hg-search">
          <Search size={18} aria-hidden="true" className="hg-search__icon" />
          <label htmlFor="patient-search" className="hg-visually-hidden">
            {t('clinic.searchPatients')}
          </label>
          <input
            id="patient-search"
            type="search"
            value={search}
            placeholder={t('clinic.searchPatients')}
            onChange={(event) => setSearch(event.target.value)}
          />
        </div>
      </div>

      {rows.length === 0 ? (
        <Card className="hg-state">
          <span className="hg-state__icon hg-state__icon--calm" aria-hidden="true">
            <Users size={22} />
          </span>
          <p className="hg-muted hg-flush">{t('clinic.noPatients')}</p>
        </Card>
      ) : (
        <div className="hg-table-card">
          <div className="hg-table-wrap">
            <table className="hg-table hg-table--stack">
              <thead>
                <tr>
                  <th scope="col">{t('intake.name')}</th>
                  <th scope="col">{t('intake.contact')}</th>
                  <th scope="col">{t('intake.age')}</th>
                  <th scope="col">BMI</th>
                  <th scope="col">{t('clinic.columnWhen')}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((patient) => (
                  <tr key={patient.id}>
                    <td data-label={t('intake.name')}>
                      <span className="hg-person">
                        <span className="hg-avatar" aria-hidden="true">
                          {initials(patient.name)}
                        </span>
                        {patient.name}
                      </span>
                    </td>
                    {/* Already masked by the server. */}
                    <td className="hg-mono" data-label={t('intake.contact')}>
                      {patient.contact}
                    </td>
                    <td data-label={t('intake.age')}>{patient.age}</td>
                    <td className="hg-mono" data-label="BMI">
                      {patient.bmi ?? '–'}
                    </td>
                    <td className="hg-small hg-muted" data-label={t('clinic.columnWhen')}>
                      {formatDateTime(patient.created_at, i18n.language)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
