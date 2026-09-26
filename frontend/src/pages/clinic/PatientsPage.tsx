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

import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Card, ErrorState, Spinner } from '../../components/common';
import { ApiRequestError, patientsApi } from '../../services/api';
import { useAsync, useDebounced } from '../../services/hooks';
import { formatDateTime } from '../../utils/formatters';

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
        <Card>
          <h1 style={{ fontSize: '1.1rem' }}>{t('clinic.patientsTitle')}</h1>
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
      <h1>{t('clinic.patientsTitle')}</h1>

      <div className="hg-toolbar">
        <label htmlFor="patient-search" className="hg-small hg-muted">
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

      {rows.length === 0 ? (
        <Card>
          <p className="hg-empty" style={{ margin: 0 }}>
            {t('clinic.noPatients')}
          </p>
        </Card>
      ) : (
        <div className="hg-table-wrap">
          <table className="hg-table">
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
                  <td>{patient.name}</td>
                  {/* Already masked by the server. */}
                  <td className="hg-mono">{patient.contact}</td>
                  <td>{patient.age}</td>
                  <td className="hg-mono">{patient.bmi ?? '–'}</td>
                  <td className="hg-small hg-muted">
                    {formatDateTime(patient.created_at, i18n.language)}
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
