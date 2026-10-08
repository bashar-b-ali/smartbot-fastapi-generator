import { formatBytes, initialsOf } from './formatters';

describe('formatters', () => {
  test('formatBytes handles empty, zero, and kilobyte values', () => {
    expect(formatBytes(null)).toBe('\u2014');
    expect(formatBytes(0)).toBe('0 B');
    expect(formatBytes(1536)).toBe('1.5 KB');
  });

  test('initialsOf prefers names and falls back to email', () => {
    expect(initialsOf({ first_name: 'Ada', last_name: 'Lovelace' })).toBe('AL');
    expect(initialsOf({ email: 'user@example.com' })).toBe('U');
    expect(initialsOf(null)).toBe('?');
  });
});
