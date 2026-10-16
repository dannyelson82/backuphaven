// The user guide (docs/guide) will be shown in the app, loaded like this:
// every page needs front matter, and links between pages must work.
const files = import.meta.glob<string>('../../docs/guide/*.md', {
  query: '?raw',
  import: 'default',
  eager: true,
});
const pages = Object.fromEntries(
  Object.entries(files)
    .map(([path, text]) => [path.split('/').pop()!, text] as const)
    .filter(([name]) => name !== 'README.md'),
);
const names = Object.keys(pages);

function frontMatter(text: string): Record<string, string> {
  const match = /^---\n([\s\S]*?)\n---\n/.exec(text);
  if (!match) return {};
  return Object.fromEntries(
    match[1].split('\n').map((line) => {
      const [key, ...rest] = line.split(':');
      return [key.trim(), rest.join(':').trim()];
    }),
  );
}

it('has pages', () => {
  expect(names.length).toBeGreaterThan(0);
});

it.each(names)('%s has a title, order and summary', (name) => {
  const meta = frontMatter(pages[name]);
  expect(meta.title).toBeTruthy();
  expect(Number(meta.order)).toBeGreaterThan(0);
  expect(meta.summary).toBeTruthy();
});

it('orders pages uniquely', () => {
  const orders = names.map((name) => frontMatter(pages[name]).order);
  expect(new Set(orders).size).toBe(names.length);
});

it.each(names)('%s links only to existing pages', (name) => {
  for (const [, target] of pages[name].matchAll(/\]\(([^)#\s]+\.md)(#[^)]*)?\)/g)) {
    expect(names, `${name} links to ${target}`).toContain(target);
  }
});
