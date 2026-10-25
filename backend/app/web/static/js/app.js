// Progressive enhancement only: every page works without this file except live typeahead.
(() => {
  // Theme toggle. The initial class is set by an inline script in <head> to avoid a flash.
  document.addEventListener('click', (e) => {
    const t = e.target.closest('[data-theme-toggle]');
    if (!t) return;
    const dark = document.documentElement.classList.toggle('dark');
    try { localStorage.setItem('theme', dark ? 'dark' : 'light'); } catch (_) {}
  });

  // "/" focuses the first search box.
  document.addEventListener('keydown', (e) => {
    if (e.key !== '/' || e.ctrlKey || e.metaKey || e.altKey) return;
    const a = document.activeElement;
    if (a && /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName)) return;
    const box = document.querySelector('[data-search] input[name=q]');
    if (box) { e.preventDefault(); box.focus(); }
  });

  // Typeahead keyboard navigation over the server-rendered result list.
  const rowsOf = (root) => [...root.querySelectorAll('.sr-row')];
  const select = (root, i) => {
    const rows = rowsOf(root);
    rows.forEach((r, n) => r.setAttribute('aria-selected', n === i ? 'true' : 'false'));
    if (rows[i]) rows[i].scrollIntoView({ block: 'nearest' });
  };
  const current = (root) => rowsOf(root).findIndex((r) => r.getAttribute('aria-selected') === 'true');

  document.addEventListener('keydown', (e) => {
    const input = e.target.closest?.('[data-search] input[name=q]');
    if (!input) return;
    const root = input.closest('[data-search]');
    const list = root.querySelector('.sr-list');
    const rows = rowsOf(root);
    const i = current(root);
    if (e.key === 'ArrowDown' && rows.length) { e.preventDefault(); select(root, Math.min(i + 1, rows.length - 1)); }
    else if (e.key === 'ArrowUp' && rows.length) { e.preventDefault(); select(root, Math.max(i - 1, 0)); }
    else if (e.key === 'Enter' && i >= 0) { e.preventDefault(); rows[i].click(); }
    else if (e.key === 'Escape') { list.replaceChildren(); }
  });

  // After results arrive, highlight the first row so Enter opens the best match.
  document.body.addEventListener('htmx:afterSwap', (e) => {
    const root = e.target.closest?.('[data-search]');
    if (root) select(root, 0);
  });

  // "Don't use" chips: remove on click; after adding one, clear the search and drop duplicates.
  document.addEventListener('click', (e) => {
    const b = e.target.closest('[data-skip-remove]');
    if (b) b.closest('.skip-chip').remove();
  });
  document.body.addEventListener('htmx:afterSwap', (e) => {
    if (e.target.id !== 'skip-chips') return;
    const seen = new Set();
    e.target.querySelectorAll('input[type=hidden]').forEach((i) => {
      const k = i.name + '=' + i.value;
      if (seen.has(k)) i.closest('.skip-chip').remove(); else seen.add(k);
    });
    const root = e.target.closest('.field').querySelector('[data-search]');
    root.querySelector('.sr-list').replaceChildren();
    root.querySelector('input[name=q]').value = '';
  });

  // Close the list when clicking elsewhere.
  document.addEventListener('click', (e) => {
    document.querySelectorAll('[data-search] .sr-list').forEach((l) => {
      if (!l.closest('[data-search]').contains(e.target)) l.replaceChildren();
    });
  });
})();
