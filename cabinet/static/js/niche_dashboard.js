/* Сводка «куда идти»: короткий ответ вместо таблицы на 400 строк. */
(function () {
  function esc(v) {
    if (v === null || v === undefined) return '';
    return String(v).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;',
               '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  const pct = function (v) {
    return v === null || v === undefined ? '—' : (v * 100).toFixed(0) + '%';
  };
  const num = function (v) {
    return v === null || v === undefined ? '—' : Number(v).toFixed(1);
  };

  function nameCell(r) {
    return '<td><a href="/cabinet/niches/' + encodeURIComponent(r.okpd2) +
      (r.region ? '?region=' + encodeURIComponent(r.region) : '') + '">' +
      esc(r.okpd2) + '</a>' +
      (r.okpd2_name ? '<span class="okpd-name">' + esc(r.okpd2_name) + '</span>' : '') +
      '</td>';
  }

  fetch('/cabinet/api/niches-dashboard')
    .then(function (r) { return r.json(); })
    .then(function (d) {
      if (d.error) {
        document.getElementById('d-warning').textContent = d.error;
        return;
      }
      const t = d.totals || {};
      document.getElementById('d-tiles').innerHTML =
        [['Процедур в базе', (t.procedures || 0).toLocaleString('ru-RU'),
          (t.data_from || '') + ' — ' + (t.data_to || '')],
         ['Результатов', (t.protocols || 0).toLocaleString('ru-RU'), 'протоколов'],
         ['Регионов', t.regions || 0, 'загружено'],
         ['Ниш в рейтинге', d.slices_ranked || 0, 'от ' + d.min_procedures + ' процедур']
        ].map(function (x) {
          return '<div class="stat-tile"><div class="label">' + esc(x[0]) +
            '</div><div class="value">' + esc(x[1]) +
            '</div><div class="sub">' + esc(x[2]) + '</div></div>';
        }).join('');

      // Без этой оговорки верхние строки читаются как готовый ответ,
      // хотя у них не измерена концентрация и индекс завышен.
      if (!d.with_winners) {
        document.getElementById('d-warning').innerHTML =
          '<b>Победители пока неизвестны ни по одной нише</b> — контракт заключается ' +
          'позже протокола. Значит концентрация не измерена, и ниша, занятая одним ' +
          'поставщиком, выглядит так же, как свободная. Данные накапливаются.';
      } else {
        document.getElementById('d-warning').textContent =
          'Концентрация измерена у ' + d.with_winners + ' ниш из ' + d.slices_ranked + '.';
      }

      const good = d.recommended || [];
      document.getElementById('d-good').innerHTML = good.length
        ? good.map(function (r) {
            return '<tr>' + nameCell(r) +
              '<td>' + esc(r.region_name || '—') + '</td>' +
              '<td>' + esc(r.price_bucket_label || '—') + '</td>' +
              '<td class="num">' + r.procedures_count + '</td>' +
              '<td class="num">' + num(r.median_bids) + '</td>' +
              '<td class="num">' + pct(r.median_drop) + '</td>' +
              '<td class="num idx-high">' + num(r.index) + '</td></tr>';
          }).join('')
        : '<tr><td colspan="7" class="loading">Пока нечего рекомендовать: ни одна ' +
          'ниша не набрала достаточно измеренных данных. Это не значит, что таких ' +
          'ниш нет — значит, история ещё загружается.</td></tr>';

      const bad = d.crowded || [];
      document.getElementById('d-bad').innerHTML = bad.length
        ? bad.map(function (r) {
            return '<tr>' + nameCell(r) +
              '<td>' + esc(r.region_name || '—') + '</td>' +
              '<td>' + esc(r.price_bucket_label || '—') + '</td>' +
              '<td class="num">' + r.procedures_count + '</td>' +
              '<td class="num idx-low">' + num(r.median_bids) + '</td>' +
              '<td class="num">' + (r.drop_known ? pct(r.median_drop) :
                '<span class="unknown">нет данных</span>') + '</td></tr>';
          }).join('')
        : '<tr><td colspan="6" class="loading">нет данных</td></tr>';
    })
    .catch(function () {
      document.getElementById('d-warning').textContent = 'Не удалось загрузить сводку.';
    });
})();
