(function () {
  const { apiGet } = window.Cabinet;
  const byId = id => document.getElementById(id);
  function el(tag, opts) {
    const e = document.createElement(tag);
    if (opts && opts.cls) e.className = opts.cls;
    if (opts && opts.text !== undefined) e.textContent = opts.text;
    return e;
  }

  async function load() {
    const s = await apiGet('/cabinet/api/stats');
    if (!s) return;
    byId('s-today').textContent = s.notifications_today != null ? s.notifications_today : 0;
    byId('s-total').textContent = s.total_notifications != null ? s.total_notifications : 0;
    byId('s-matches').textContent = s.total_matches != null ? s.total_matches : 0;
    byId('s-filters').textContent = s.active_filters != null ? s.active_filters : 0;
    byId('s-today-limit').textContent = 'из ' + (s.notifications_limit || '∞') + ' в день';

    const tf = byId('top-filters');
    tf.textContent = '';
    (s.top_filters || []).forEach(f => {
      const row = el('div', { cls: 'top-filter' });
      row.appendChild(el('span', { cls: 'name', text: f.name || '—' }));
      row.appendChild(el('span', { cls: 'count', text: String(f.count) }));
      tf.appendChild(row);
    });
    if (!(s.top_filters || []).length) {
      tf.appendChild(el('div', { cls: 'empty', text: 'Нет данных' }));
    }

    const rt = byId('recent-tenders');
    rt.textContent = '';
    (s.recent_tenders || []).forEach(t => {
      const row = el('div', { cls: 'recent-tender' });
      row.appendChild(el('div', { cls: 'name', text: t.name || 'Без названия' }));
      const meta = [t.customer_name, t.filter_name].filter(Boolean).join(' · ');
      row.appendChild(el('div', { cls: 'meta', text: meta || '—' }));
      rt.appendChild(row);
    });
    if (!(s.recent_tenders || []).length) {
      rt.appendChild(el('div', { cls: 'empty', text: 'Нет тендеров' }));
    }
  }

  load();
})();
