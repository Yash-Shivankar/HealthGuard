import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';

import App from './App';
import { SessionProvider } from './services/store/SessionContext';
import './utils/i18n';
// Self-hosted fonts (no third-party font CDN sees a patient's IP - DPDP).
// Only the subsets a page actually uses are downloaded, via unicode-range.
import '@fontsource-variable/inter';
import '@fontsource-variable/plus-jakarta-sans';
import '@fontsource-variable/noto-sans-devanagari';
import { restoreTheme } from './utils/theme';
import './styles/tokens.css';
import './styles/app.css';

restoreTheme();

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <SessionProvider>
        <App />
      </SessionProvider>
    </BrowserRouter>
  </StrictMode>,
);
