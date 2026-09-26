/** Language switcher (AUDIT 7.3: multilingual from day one). */

import { useTranslation } from 'react-i18next';

import { SUPPORTED_LANGUAGES, setLanguage, type LanguageCode } from '../../utils/i18n';

export function LanguagePicker() {
  const { t, i18n } = useTranslation();

  return (
    <div className="hg-field" style={{ margin: 0 }}>
      <label htmlFor="language-picker" className="hg-small hg-muted">
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
