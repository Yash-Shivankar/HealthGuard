/** Light / dark switch. See utils/theme for why the choice is optional. */

import { Moon, Sun } from 'lucide-react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { applyTheme, currentTheme, type Theme } from '../../utils/theme';

export function ThemeToggle() {
  const { t } = useTranslation();
  const [theme, setTheme] = useState<Theme>(currentTheme);
  const next: Theme = theme === 'dark' ? 'light' : 'dark';
  const label = t('nav.theme', { mode: t(next === 'dark' ? 'nav.themeDark' : 'nav.themeLight') });

  return (
    <button
      type="button"
      className="hg-icon-btn"
      aria-label={label}
      title={label}
      onClick={() => {
        applyTheme(next);
        setTheme(next);
      }}
    >
      {theme === 'dark' ? <Sun size={18} aria-hidden="true" /> : <Moon size={18} aria-hidden="true" />}
    </button>
  );
}
