/**
 * Language switcher (AUDIT 7.3: multilingual from day one).
 *
 * A native <select> styled as a pill, not a custom dropdown: it keeps the
 * platform's own picker on phones (large, familiar, accessible) and needs no
 * library. The label is visually hidden rather than removed, so a screen
 * reader still announces "Language".
 */

import { Languages } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { SUPPORTED_LANGUAGES, setLanguage, type LanguageCode } from '../../utils/i18n';

export function LanguagePicker() {
  const { t, i18n } = useTranslation();

  return (
    <div className="hg-pill-select">
      <Languages size={16} aria-hidden="true" className="hg-pill-select__icon" />
      <label htmlFor="language-picker" className="hg-visually-hidden">
        {t('nav.language')}
      </label>
      <select
        id="language-picker"
        value={i18n.language}
        onChange={(event) => setLanguage(event.target.value as LanguageCode)}
      >
        {SUPPORTED_LANGUAGES.map((language) => (
          <option key={language.code} value={language.code}>
            {language.label}
          </option>
        ))}
      </select>
    </div>
  );
}
