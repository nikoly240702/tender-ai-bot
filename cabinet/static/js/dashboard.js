/* Cabinet v3 — dashboard (лента тендеров, фильтры, модалка).
   Весь пользовательский текст идёт через textContent — XSS-safe.
*/
(function () {
  const eyebrow = document.getElementById('today-date');
  if (eyebrow) {
    const d = new Date();
    const fmt = d.toLocaleDateString('ru-RU', { weekday: 'long', day: 'numeric', month: 'long' });
    eyebrow.textContent = fmt.toUpperCase();
  }

  function fmtPrice(p) {
    if (!p) return { main: '—', unit: '' };
    const n = parseFloat(p);
    if (n >= 1e6) return { main: (n / 1e6).toFixed(1).replace(/\.0$/, ''), unit: 'млн ₽' };
    if (n >= 1e3) return { main: String(Math.round(n / 1e3)), unit: 'тыс ₽' };
    return { main: String(Math.round(n)), unit: '₽' };
  }

  function byId(id) { return document.getElementById(id); }
  function el(tag, opts) {
    const e = document.createElement(tag);
    if (!opts) return e;
    if (opts.cls) e.className = opts.cls;
    if (opts.text !== undefined) e.textContent = opts.text;
    if (opts.data) Object.keys(opts.data).forEach(k => { e.dataset[k] = opts.data[k]; });
    return e;
  }

  let allTenders = [];
  let currentFilter = 'all';
  let serverCounts = { today: null, total: null };

  function applyFilter(tenders, filter) {
    if (filter === 'today') {
      const now = Date.now();
      return tenders.filter(t => now - new Date(t.sent_at || 0).getTime() < 24 * 3600 * 1000);
    }
    if (filter === 'hot') {
      return tenders.filter(t => typeof t.days_left === 'number' && t.days_left <= 3);
    }
    if (filter === 'inwork') {
      return tenders.filter(t => !!t.in_work);
    }
    return tenders;
  }

  function buildRow(t) {
    const row = el('div', { cls: 'tender-row', data: { number: t.number || '', url: t.url || '' } });

    const left = el('div');
    left.appendChild(el('div', { cls: 'title', text: t.name || 'Без названия' }));

    const meta = el('div', { cls: 'meta' });
    const metaParts = [t.customer_name, t.law_type ? t.law_type + '-ФЗ' : null, t.region].filter(Boolean);
    metaParts.forEach(part => meta.appendChild(el('span', { text: part })));
    left.appendChild(meta);
    row.appendChild(left);

    const right = el('div', { cls: 'right' });
    const p = fmtPrice(t.price);
    const price = el('div', { cls: 'price', text: p.main });
    if (p.unit) {
      price.appendChild(document.createTextNode(' '));
      price.appendChild(el('span', { cls: 'unit', text: p.unit }));
    }
    right.appendChild(price);

    const score = Number(t.score || 0);
    if (score > 0) {
      const scoreCls = 'score' + (score < 40 ? ' alert' : '');
      right.appendChild(el('div', { cls: scoreCls, text: score + '% совпадение' }));
    }
    row.appendChild(right);

    return row;
  }

  function renderFeed(tenders) {
    const feed = byId('feed');
    feed.textContent = '';
    if (!tenders || tenders.length === 0) {
      const empty = el('div', { cls: 'empty' });
      empty.appendChild(el('div', { cls: 'title', text: 'Пока ничего не найдено' }));
      empty.appendChild(el('div', { text: 'Настройте фильтры, чтобы получать уведомления' }));
      feed.appendChild(empty);
      return;
    }
    tenders.forEach(t => feed.appendChild(buildRow(t)));
  }

  function updateCounts(tenders) {
    const todayLocal = applyFilter(tenders, 'today').length;
    const totalLocal = tenders.length;
    const today = serverCounts.today != null ? serverCounts.today : todayLocal;
    const total = serverCounts.total != null ? serverCounts.total : totalLocal;

    byId('cnt-all').textContent = total;
    byId('cnt-today').textContent = today;
    byId('cnt-hot').textContent = applyFilter(tenders, 'hot').length;
    byId('cnt-inwork').textContent = applyFilter(tenders, 'inwork').length;
    byId('today-count').textContent = today;
    byId('today-total').textContent = today;
    byId('today-hot').textContent = applyFilter(tenders, 'hot').length;
    byId('today-inwork').textContent = applyFilter(tenders, 'inwork').length;
  }

  function openTender(number) {
    const t = allTenders.find(x => x.number === number);
    if (!t) return;
    const metaParts = [t.law_type ? t.law_type + '-ФЗ' : null, t.region].filter(Boolean).join(' · ');
    byId('tm-meta').textContent = metaParts || '—';
    byId('tm-title').textContent = t.name || 'Без названия';
    const p = fmtPrice(t.price);
    byId('tm-price').textContent = p.main + (p.unit ? ' ' + p.unit : '');
    byId('tm-main').textContent = t.customer_name || '—';
    byId('tm-btn-open').href = t.url || '#';
    const bxBtn = byId('tm-btn-bitrix');
    if (bxBtn) bxBtn.dataset.tenderNumber = t.number || '';
    const plBtn = byId('tm-btn-pipeline');
    if (plBtn) plBtn.dataset.tenderNumber = t.number || '';
    window.Cabinet.Modal.open('tender-modal');
  }

  const plBtn = byId('tm-btn-pipeline');
  if (plBtn) {
    plBtn.addEventListener('click', async () => {
      if (plBtn.disabled) return;
      const num = plBtn.dataset.tenderNumber;
      if (!num) return;
      const orig = plBtn.textContent;
      plBtn.disabled = true;
      plBtn.textContent = '⏳ Добавляем…';
      try {
        const r = await fetch('/cabinet/api/pipeline/from-feed/' + encodeURIComponent(num), {
          method: 'POST', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({}),
        });
        const d = await r.json().catch(() => ({}));
        if (r.ok && d.ok) {
          window.Cabinet.Toast.show('✓ Добавлено в Pipeline', 'positive');
        } else if (r.status === 409) {
          window.Cabinet.Toast.show('Уже в Pipeline (' + (d.stage || '') + ')', 'alert');
        } else {
          window.Cabinet.Toast.show(d.error || 'Ошибка', 'alert');
        }
      } finally {
        plBtn.disabled = false;
        plBtn.textContent = orig;
      }
    });
  }

  const bxBtn = byId('tm-btn-bitrix');
  if (bxBtn) {
    bxBtn.addEventListener('click', async () => {
      if (bxBtn.disabled) return;
      const num = bxBtn.dataset.tenderNumber;
      if (!num) return;
      const orig = bxBtn.textContent;
      bxBtn.disabled = true;
      bxBtn.textContent = '⏳ Создаём…';
      try {
        const data = await window.Cabinet.apiPost('/cabinet/api/tenders/' + encodeURIComponent(num) + '/bitrix24', {});
        if (data && data.ok) {
          window.Cabinet.Toast.show('✓ Сделка #' + data.deal_id + ' создана в Битрикс24', 'positive');
        }
      } finally {
        bxBtn.disabled = false;
        bxBtn.textContent = orig;
      }
    });
  }

  byId('chips').addEventListener('click', (e) => {
    const chip = e.target.closest('.chip');
    if (!chip) return;
    document.querySelectorAll('#chips .chip').forEach(c => c.classList.remove('active'));
    chip.classList.add('active');
    currentFilter = chip.dataset.filter;
    renderFeed(applyFilter(allTenders, currentFilter));
  });

  byId('feed').addEventListener('click', (e) => {
    const row = e.target.closest('.tender-row');
    if (row) openTender(row.dataset.number);
  });

  (async function load() {
    const data = await window.Cabinet.apiGet('/cabinet/api/tenders?limit=500');
    if (!data) return;
    allTenders = data.tenders || [];
    serverCounts = {
      today: typeof data.today_count === 'number' ? data.today_count : null,
      total: typeof data.total_count === 'number' ? data.total_count : null,
    };
    updateCounts(allTenders);
    renderFeed(applyFilter(allTenders, currentFilter));
  })();
})();
