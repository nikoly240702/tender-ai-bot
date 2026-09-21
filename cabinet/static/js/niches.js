/* Рейтинг ниш: загрузка, сортировка, выгрузка.
 *
 * Сортировка и фильтрация — на клиенте по уже полученному списку:
 * срезов сотни, не тысячи, и гонять запрос ради смены столбца незачем.
 * Данные приходят из материализованного представления, считать на лету
 * ничего не нужно.
 */
(function () {
  const rowsEl = document.getElementById('niche-rows');
  const noteEl = document.getElementById('data-note');
  let current = [];
  let sortKey = 'index';
  let sortDesc = true;

  const BUCKETS = {
    '0-500k': 'до 500 тыс',
    '500k-1m': '500 тыс – 1 млн',
    '1m-3m': '1 – 3 млн',
    '3m-5m': '3 – 5 млн',
  };

  /* Экранирование обязательно: код ОКПД2 и название корзины приходят
   * из документов ЕИС, то есть это данные, которые пишем не мы.
   * Числа и наши собственные строки безопасны, но отделять одно от
   * другого по памяти — способ однажды ошибиться. */
  function esc(value) {
    if (value === null || value === undefined) return '';
    return String(value).replace(/[&<>"']/g, function (ch) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;',
               '"': '&quot;', "'": '&#39;' }[ch];
    });
  }

  function pct(value) {
    return value === null || value === undefined ? '—'
      : (value * 100).toFixed(0) + '%';
  }

  function num(value, digits) {
    return value === null || value === undefined ? '—'
      : Number(value).toFixed(digits === undefined ? 1 : digits);
  }

  function indexClass(value) {
    if (value >= 75) return 'idx-high';
    if (value >= 50) return 'idx-mid';
    return 'idx-low';
  }

  function render() {
    if (!current.length) {
      rowsEl.innerHTML = '<tr><td colspan="10" class="loading">' +
        'Под условия ничего не попало. Попробуйте снизить минимум процедур ' +
        'или выбрать другой уровень ОКПД2.</td></tr>';
      return;
    }
    const sorted = current.slice().sort(function (a, b) {
      const x = a[sortKey], y = b[sortKey];
      if (x === null || x === undefined) return 1;
      if (y === null || y === undefined) return -1;
      if (x === y) return 0;
      return (x > y ? 1 : -1) * (sortDesc ? -1 : 1);
    });

    rowsEl.innerHTML = sorted.map(function (r) {
      // «Снижение 0%» и «снижение не измерено» выглядят одинаково, если
      // не пометить: первое значит «цену не роняют», второе — «сравнить
      // было не с чем». Пользователь по такой таблице принимает решение.
      const drop = r.drop_known ? pct(r.median_drop)
        : '<span class="unknown" title="не с чем сравнивать">нет данных</span>';
      // Больше половины закупок без единой заявки — это не свободная
      // ниша, а систематически срывающиеся процедуры: невыполнимые
      // требования, сроки или цена ниже рынка. Помечаем цветом.
      const zero = r.share_zero_bid === null || r.share_zero_bid === undefined
        ? '—'
        : '<span class="' + (r.share_zero_bid >= 0.5 ? 'warn' : '') + '">' +
          pct(r.share_zero_bid) + '</span>';
      const hhi = r.hhi_known ? num(r.winner_hhi, 2)
        : '<span class="unknown" title="победители ещё неизвестны">—</span>';
      return '<tr>' +
        '<td><a href="/cabinet/niches/' + encodeURIComponent(r.okpd2) +
          '?level=' + encodeURIComponent(r.okpd2_level) +
          (r.region ? '&region=' + encodeURIComponent(r.region) : '') +
          '">' + esc(r.okpd2) + '</a>' +
          (r.okpd2_name ? '<span class="okpd-name">' +
            esc(r.okpd2_name) + '</span>' : '') + '</td>' +
        '<td>' + esc(r.region_name || '—') + '</td>' +
        '<td>' + esc(BUCKETS[r.price_bucket] || r.price_bucket || '—') + '</td>' +
        '<td class="num">' + (r.procedures_count || 0) + '</td>' +
        '<td class="num">' + num(r.median_bids) + '</td>' +
        '<td class="num">' + zero + '</td>' +
        '<td class="num">' + pct(r.share_single_bid) + '</td>' +
        '<td class="num">' + drop + '</td>' +
        '<td class="num">' + hhi + '</td>' +
        '<td class="num ' + indexClass(r.index) + '">' + num(r.index) +
          '<span class="confidence">' + esc(r.confidence) + '</span></td>' +
        '</tr>';
    }).join('');
  }

  function showNote(data) {
    const a = data.availability || {};
    const parts = [];
    if (a.data_from && a.data_to) {
      parts.push('Данные за ' + a.data_from + ' — ' + a.data_to);
    }
    parts.push('срезов ' + data.total + ', показано ' + data.niches.length);
    if (data.sparse_count) {
      parts.push('ещё ' + data.sparse_count +
        ' срезов отложено: в них меньше ' + data.min_count +
        ' процедур, медиана по такому числу ничего не значит');
    }
    if (a.known_winners === 0 || a.known_winners === null) {
      parts.push('<b>победители пока неизвестны — концентрация не измерена, ' +
        'индексы завышены</b>');
    }
    noteEl.innerHTML = parts.join(' · ');
  }

  function load() {
    const params = new URLSearchParams({
      level: document.getElementById('f-level').value,
      min_count: document.getElementById('f-min').value,
    });
    const region = document.getElementById('f-region').value;
    const bucket = document.getElementById('f-bucket').value;
    if (region) params.set('region', region);
    if (bucket) params.set('bucket', bucket);

    rowsEl.innerHTML = '<tr><td colspan="10" class="loading">загрузка…</td></tr>';
    fetch('/cabinet/api/niches?' + params.toString())
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.error) {
          rowsEl.innerHTML = '<tr><td colspan="10" class="loading">' +
            esc(data.error) + '</td></tr>';
          return;
        }
        current = data.niches || [];
        fillSelects(data);
        showNote(data);
        render();
      })
      .catch(function () {
        rowsEl.innerHTML = '<tr><td colspan="10" class="loading">' +
          'Не удалось загрузить данные.</td></tr>';
      });
  }

  let selectsFilled = false;
  function fillSelects(data) {
    if (selectsFilled) return;
    selectsFilled = true;
    const regions = {};
    (data.niches || []).forEach(function (r) {
      if (r.region) regions[r.region] = r.region_name || r.region;
    });
    const regionEl = document.getElementById('f-region');
    Object.keys(regions).sort(function (a, b) {
      return regions[a].localeCompare(regions[b]);
    }).forEach(function (code) {
      const o = document.createElement('option');
      o.value = code; o.textContent = regions[code];  // textContent — не innerHTML
      regionEl.appendChild(o);
    });
    const bucketEl = document.getElementById('f-bucket');
    Object.keys(BUCKETS).forEach(function (key) {
      const o = document.createElement('option');
      o.value = key; o.textContent = BUCKETS[key];
      bucketEl.appendChild(o);
    });
  }

  function toCsv() {
    const head = ['ОКПД2', 'категория', 'регион', 'корзина', 'процедур',
      'медиана заявок', 'без заявок %', 'одна заявка %', 'снижение',
      'концентрация', 'индекс', 'достоверность'];
    const lines = [head.join(';')];
    current.forEach(function (r) {
      lines.push([r.okpd2, r.okpd2_name || '', r.region_name || '', r.price_bucket || '',
        r.procedures_count || 0, num(r.median_bids),
        r.share_zero_bid === null ? '' : (r.share_zero_bid * 100).toFixed(0),
        r.share_single_bid === null ? '' : (r.share_single_bid * 100).toFixed(0),
        r.drop_known ? (r.median_drop * 100).toFixed(1) : '',
        r.hhi_known ? num(r.winner_hhi, 2) : '',
        num(r.index), r.confidence].join(';'));
    });
    // BOM — иначе Excel открывает кириллицу кракозябрами.
    const blob = new Blob(['﻿' + lines.join('\n')],
      { type: 'text/csv;charset=utf-8;' });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = 'niches.csv';
    link.click();
    URL.revokeObjectURL(link.href);
  }

  document.querySelectorAll('.niche-table th[data-sort]').forEach(function (th) {
    th.addEventListener('click', function () {
      const key = th.getAttribute('data-sort');
      if (key === sortKey) { sortDesc = !sortDesc; }
      else { sortKey = key; sortDesc = true; }
      render();
    });
  });

  document.getElementById('f-apply').addEventListener('click', load);
  document.getElementById('f-csv').addEventListener('click', toCsv);
  load();
})();
