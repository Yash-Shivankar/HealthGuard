/**
 * Switch the active clinic.
 *
 * Exists because a locum doctor works at more than one clinic - the reason
 * Membership is a join rather than a field on the user. Hidden when the caller
 * belongs to one clinic, because a picker with a single option is noise.
 */

import { Building } from 'lucide-react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useSession } from '../../services/store/SessionContext';

export function ClinicSwitcher() {
  const { t } = useTranslation();
  const { session, switchClinic } = useSession();
  const [busy, setBusy] = useState(false);

  if (!session || session.available_clinics.length < 2) return null;

  return (
    <div className="hg-pill-select">
      <Building size={16} aria-hidden="true" className="hg-pill-select__icon" />
      <label htmlFor="clinic-switcher" className="hg-visually-hidden">
        {t('clinic.activeClinic')}
      </label>
      <select
        id="clinic-switcher"
        value={session.clinic?.id ?? ''}
        disabled={busy}
        onChange={async (event) => {
          const chosen = session.available_clinics.find(
            (clinic) => clinic.id === Number(event.target.value),
          );
          if (!chosen) return;
          setBusy(true);
          try {
            await switchClinic(chosen);
          } finally {
            setBusy(false);
          }
        }}
      >
        {session.available_clinics.map((clinic) => (
          <option key={clinic.id} value={clinic.id}>
            {clinic.name}
          </option>
        ))}
      </select>
    </div>
  );
}
