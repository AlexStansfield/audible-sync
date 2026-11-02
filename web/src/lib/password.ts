/** The service's own limits (`webauth.validate_password`). */
export const MIN_PASSWORD_LENGTH = 12;
export const MAX_PASSWORD_LENGTH = 1024;

export type Strength = 0 | 1 | 2 | 3 | 4;

/**
 * A rough strength for the meter, 0 to 4: nothing under the minimum length, then
 * points for length and for mixing kinds of character. A hint, not a gate - the
 * service only enforces the length.
 */
export function passwordStrength(password: string): Strength {
  if (password.length < MIN_PASSWORD_LENGTH) {
    return password ? 1 : 0;
  }
  const kinds = [/[a-z]/, /[A-Z]/, /\d/, /[^A-Za-z0-9]/].filter((kind) => kind.test(password)).length;
  let score = 2;
  if (password.length >= 16) score += 1;
  if (password.length >= 24 || kinds >= 3) score += 1;
  // A single repeated character, or nothing but digits, is weak whatever its length
  if (/^(.)\1*$/.test(password) || /^\d+$/.test(password)) score = 1;
  return Math.min(score, 4) as Strength;
}

export const STRENGTH_LABEL: Record<Strength, string> = {
  0: "",
  1: "Too weak",
  2: "Fair",
  3: "Good",
  4: "Strong",
};

export const STRENGTH_COLOUR: Record<Strength, string> = {
  0: "var(--line)",
  1: "var(--bad)",
  2: "var(--accent)",
  3: "var(--ok)",
  4: "var(--ok)",
};
