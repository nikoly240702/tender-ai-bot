/* Cabinet v3 — search page. XSS-safe DOM rendering. */
(function () {
  const { apiGet, Toast, Modal } = window.Cabinet;

  function byId(id) { return document.getElementById(id); }
  function el(tag, opts) {
    const e = document.createElement(tag);
    if (!opts) return e;
    if (opts.cls) e.className = opts.cls;
    if (opts.text !== undefined) e.textContent = opts.text;
    if (opts.data) Object.keys(opts.data).forEach(k => e.dataset[k] = opts.data[k]);
    return e;
  }

  function fmtPrice(p) {
    if (!p) return { main: '—', unit: '' };
    const n = parseFloat(p);
    if (n >= 1e6) return { main: (n / 1e6).toFixed(1).replace(/\.0$/, ''), unit: 'млн ₽' };
    if (n >= 1e3) return { main: String(Math.round(n / 1e3)), unit: 'тыс ₽' };
    return { main: String(Math.round(n)), unit: '₽' };
  }

  let lastResults = [];

  function buildRow(t) {
    const row = el('div', { cls: 'tender-row', data: { number: t.number || '', url: t.url || '' } });
    const left = el('div');
    left.appendChild(el('div', { cls: 'title', text: t.name || 'Без названия' }));
    const meta = el('div', { cls: 'meta' });
    [t.customer_name, t.law_type ? t.law_type + '-ФЗ' : null, t.region]
      .filter(Boolean)
      .forEach(part => meta.appendChild(el('span', { text: part })));
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
    if (score > 0) right.appendChild(el('div', { cls: 'score' + (score < 40 ? ' alert' : ''), text: score + '% совпадение' }));
    row.appendChild(right);
    return row;
  }

  function openTender(number) {
    const t = lastResults.find(x => x.number === number);
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
    Modal.open('tender-modal');
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
          Toast.show('✓ Сделка #' + data.deal_id + ' создана в Битрикс24', 'positive');
        }
      } finally {
        bxBtn.disabled = false;
        bxBtn.textContent = orig;
      }
    });
  }

  function render(results) {
    const feed = byId('feed');
    feed.textContent = '';
    if (!results || results.length === 0) {
      const empty = el('div', { cls: 'empty' });
      empty.appendChild(el('div', { cls: 'title', text: 'Ничего не найдено' }));
      empty.appendChild(el('div', { text: 'Попробуйте изменить запрос или убрать фильтры' }));
      feed.appendChild(empty);
      return;
    }
    results.forEach(t => feed.appendChild(buildRow(t)));
  }

  async function runSearch() {
    const q = byId('q').value.trim();
    if (!q) { Toast.show('Введите поисковый запрос', 'alert'); return; }
    byId('feed').textContent = '';
    byId('feed').appendChild(el('div', { cls: 'loading', text: 'ищем…' }));

    const params = new URLSearchParams({ q });
    const region = byId('region').value.trim();
    const priceMin = byId('price-min').value.trim();
    const priceMax = byId('price-max').value.trim();
    const law = byId('law').value;
    if (region) params.set('region', region);
    if (priceMin) params.set('price_min', priceMin);
    if (priceMax) params.set('price_max', priceMax);
    if (law) params.set('law', law);

    const data = await apiGet('/cabinet/api/search?' + params.toString());
    if (!data) return;
    lastResults = data.tenders || [];
    byId('results-count').textContent = lastResults.length;
    render(lastResults);
  }

  byId('search-btn').addEventListener('click', runSearch);
  byId('q').addEventListener('keydown', (e) => { if (e.key === 'Enter') runSearch(); });
  byId('feed').addEventListener('click', (e) => {
    const row = e.target.closest('.tender-row');
    if (row) openTender(row.dataset.number);
  });
})();
