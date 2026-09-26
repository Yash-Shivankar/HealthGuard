/**
 * Indian mobile validation, mirroring utils/validators/phone.py.
 *
 * Client-side validation here is a courtesy that saves a round trip. **The
 * server validates independently** - the audit's 2.7 finding was precisely that
 * a JavaScript-only check is bypassed by anything that is not a browser, so
 * this must never be the only check and the rules are kept deliberately
 * identical so the two cannot disagree about what is valid.
 */

const COUNTRY_CODE = '91';
const NATIONAL_LENGTH = 10;
const VALID_FIRST_DIGITS = new Set(['6', '7', '8', '9']);

/** Strip formatting and a country or trunk prefix. Returns null if unusable. */
export function normaliseMobile(raw: string): string | null {
  let digits = raw.replace(/\D/g, '');

  if (digits.length === NATIONAL_LENGTH + COUNTRY_CODE.length && digits.startsWith(COUNTRY_CODE)) {
    digits = digits.slice(COUNTRY_CODE.length);
  } else if (digits.length === NATIONAL_LENGTH + 1 && digits.startsWith('0')) {
    digits = digits.slice(1);
  }

  if (digits.length !== NATIONAL_LENGTH) return null;
  if (!VALID_FIRST_DIGITS.has(digits[0])) return null;
  return digits;
}

export function isValidMobile(raw: string): boolean {
  return normaliseMobile(raw) !== null;
}
