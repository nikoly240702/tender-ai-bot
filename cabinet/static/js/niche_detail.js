/* Детализация ниши: срезы, заказчики, победители, последние процедуры. */
(function () {
  const N = window.NICHE || {};

  function esc(value) {
    if (value === null || value === undefined) return '';
    return String(value).replace(/[&<>"']/g, function (ch) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;',
               '"': '&quot;', "'": '&#39;' }[ch];
    });
  }

  function money(value) {
    if (value === null || value === undefined || value === '') return '—';
    return Number(value).toLocaleString('ru-RU',
      { maximumFractionDigits: 0 }) + ' ₽';
  }

  function pct(value) {
    return value === null || value === undefined ? '—'
      : (value * 100).toFixed(0) + '%';
  }

  function num(value, digits) {
    return value === null || value === undefined ? '—'
      : Number(value).toFixed(digits === undefined ? 1 : digits);
  }

  function fill(id, html) { document.getElementById(id).innerHTML = html; }

  function empty(cols, text) {
    return '<tr><td colspan="' + cols + '" class="loading">' + text + '</td></tr>';
  }

  function render(d) {
    const slices = d.slices || [];
    document.getElementById('d-summary').textContent =
      slices.length
        ? 'Срезов ' + slices.length + ' · регион: ' +
          (slices[0].region_name || 'все') +
          ' · процедур всего ' +
          slices.reduce(function (s, r) { return s + (r.procedures_count || 0); }, 0)
        : 'По этой категории данных пока нет.';

    fill('d-slices', slices.length ? slices.map(function (r) {
      // «Снижение 0%» и «не измерено» читаются противоположно — помечаем.
      const drop = r.drop_known ? pct(r.median_drop)
        : '<span class="unknown" title="сравнивать было не с чем">нет данных</span>';
      const p90 = r.drop_known && r.p90_drop !== null ? pct(r.p90_drop) : '—';
      return '<tr>' +
        '<td>' + esc(r.price_bucket_label || r.price_bucket) + '</td>' +
        '<td>' + esc(r.region_name || '—') + '</td>' +
        '<td class="num">' + (r.procedures_count || 0) + '</td>' +
        '<td class="num">' + num(r.median_bids) + '</td>' +
        '<td class="num">' + pct(r.share_single_bid) + '</td>' +
        '<td class="num">' + pct(r.share_zero_bid) + '</td>' +
        '<td class="num">' + drop + '</td>' +
        '<td class="num">' + p90 + '</td>' +
        '<td class="num">' + num(r.index) + '</td>' +
      '</tr>';
    }).join('') : empty(9, 'нет данных'));

    fill('d-customers', (d.customers || []).length
      ? d.customers.map(function (c) {
          return '<tr><td>' + esc((c.customer_name || c.customer_inn || '—')) +
            '</td><td class="num">' + c.purchases +
            '</td><td class="num">' + money(c.total_nmck) + '</td></tr>';
        }).join('')
      : empty(3, 'нет данных'));

    const winners = d.winners || [];
    fill('d-winners', winners.length
      ? winners.map(function (w) {
          return '<tr><td>' + esc((w.supplier_name || w.supplier_inn || '—')) +
            '</td><td class="num">' + w.wins +
            '</td><td class="num">' + money(w.total_price) + '</td></tr>';
        }).join('')
      : empty(3, 'победители пока неизвестны'));

    // Почему список победителей бывает пуст — вопрос, который возникает
    // первым. Отвечаем сразу, а не оставляем гадать.
    document.getElementById('d-winners-hint').textContent = winners.length
      ? 'Победитель известен только после заключения контракта: в протоколе участник обезличен номером заявки.'
      : 'Контракты по этим закупкам ещё не заключены либо не загружены — в протоколе участник обезличен номером заявки, поэтому победителя пока не видно.';

    fill('d-recent', (d.recent || []).length
      ? d.recent.map(function (r) {
          let drop = '—';
          if (r.quantity_undefined) {
            drop = '<span class="unknown" title="объём не определён: торгуются суммы цен за единицу">н/д</span>';
          } else if (r.nmck && r.winner_price && Number(r.winner_price) <= Number(r.nmck)) {
            drop = ((1 - Number(r.winner_price) / Number(r.nmck)) * 100).toFixed(1) + '%';
          }
          const url = 'https://zakupki.gov.ru/epz/order/notice/ea44/view/' +
            'supplier-results.html?regNumber=' + encodeURIComponent(r.purchase_number);
          return '<tr>' +
            '<td><a href="' + url + '" target="_blank" rel="noopener">' +
              esc(r.purchase_number) + '</a></td>' +
            // Обрезаем ДО экранирования: наоборот можно разрезать
            // сущность вроде &amp; пополам и получить мусор в выводе.
            '<td>' + esc((r.customer_name || '—').slice(0, 60)) + '</td>' +
            '<td class="num">' + money(r.nmck) + '</td>' +
            '<td class="num">' + (r.bids_submitted === null ||
              r.bids_submitted === undefined ? '—' : r.bids_submitted) + '</td>' +
            '<td class="num">' + money(r.winner_price) + '</td>' +
            '<td class="num">' + drop + '</td>' +
          '</tr>';
        }).join('')
      : empty(6, 'нет данных'));
  }

  const params = new URLSearchParams({ level: N.level || 4 });
  if (N.region) params.set('region', N.region);

  fetch('/cabinet/api/niches/' + encodeURIComponent(N.okpd2) + '?' + params)
    .then(function (r) { return r.json(); })
    .then(function (d) {
      if (d.error) {
        document.getElementById('d-summary').textContent = d.error;
        return;
      }
      render(d);
    })
    .catch(function () {
      document.getElementById('d-summary').textContent =
        'Не удалось загрузить данные ниши.';
    });
})();
