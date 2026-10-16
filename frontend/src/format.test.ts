import { fileName, formatBytes, formatDuration, timeAgo } from './format';

it('formats sizes and durations', () => {
  expect(formatBytes(null)).toBe('–');
  expect(formatBytes(512)).toBe('512 B');
  expect(formatBytes(4_200_000_000)).toBe('4.2 GB');
  expect(formatDuration(75)).toBe('1:15');
  expect(formatDuration(3725)).toBe('1:02:05');
});

it('says how long ago', () => {
  const now = new Date('2026-10-17T12:00:00Z');
  expect(timeAgo('2026-10-17T11:59:30Z', now)).toBe('just now');
  expect(timeAgo('2026-10-17T11:55:00Z', now)).toBe('5 min ago');
  expect(timeAgo('2026-10-17T09:00:00Z', now)).toBe('3 h ago');
  expect(timeAgo('2026-10-16T09:00:00Z', now)).toBe('yesterday');
  expect(timeAgo('2026-10-14T12:00:00Z', now)).toBe('3 days ago');
});

it('takes the file name from a path', () => {
  expect(fileName('Library/Plug-in Support/Databases/com.plexapp.plugins.library.db')).toBe(
    'com.plexapp.plugins.library.db',
  );
});
