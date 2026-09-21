/* Аудит фильтров: что они ловят и насколько тесные это ниши. */
(function () {
  function esc(v) {
    if (v === null || v === undefined) return '';
    return String(v).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;',
               '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function pct(v) { return v === null || v === undefined ? '—' : (v * 100).toFixed(0) + '%'; }
  function num(v) { return v === null || v === undefined ? '—' : Number(v).toFixed(1); }
  function money(v) {
    return v === null || v === undefined ? '—'
      : Number(v).toLocaleString('ru-RU', { maximumFractionDigits: 0 }) + ' ₽';
  }
  const VERDICT_CLASS = {
    'людно': 'idx-low', 'цену роняют': 'idx-mid',
    'свободно': 'idx-high', 'умеренно': '', 'нет истории': '', 'результаты неизвестны': '',
  };

  fetch('/cabinet/api/niches-audit')
    .then(function (r) { return r.json(); })
    .then(function (d) {
      if (d.error) { document.getElementById('a-note').textContent = d.error; return; }

      const note = ['За ' + d.days + ' дней уведомлений ' + d.notifications +
        ', из них с историей ' + d.covered];
      if (d.notifications && d.covered < d.notifications) {
        // Иначе вывод «фильтр ловит мало» будет прочитан как свойство
        // фильтра, хотя это свойство загруженных данных.
        note.push('<b>остальные регионы ещё не загружены — по ним выводов нет</b>');
      }
      document.getElementById('a-note').innerHTML = note.join(' · ');

      const filters = d.filters || [];
      if (!filters.length) {
        document.getElementById('a-filters').innerHTML =
          '<p class="hint">Пока не по чему судить: ни одно совпадение ' +
          'не попало в загруженную историю.</p>';
        return;
      }
      document.getElementById('a-filters').innerHTML = filters.map(function (f) {
        const rows = f.categories.map(function (c) {
          return '<tr>' +
            '<td><a href="/cabinet/niches/' + encodeURIComponent(c.okpd2) +
              (c.region ? '?region=' + encodeURIComponent(c.region) : '') + '">' +
              esc(c.okpd2) + '</a></td>' +
            '<td>' + esc(c.region_name || '—') + '</td>' +
            '<td>' + esc(c.price_bucket_label || '—') + '</td>' +
            '<td class="num">' + c.hits + '</td>' +
            '<td class="num">' + money(c.avg_nmck) + '</td>' +
            '<td class="num">' + num(c.median_bids) + '</td>' +
            '<td class="num">' + pct(c.share_single_bid) + '</td>' +
            '<td class="num">' + (c.drop_known ? pct(c.median_drop) :
              '<span class="unknown">нет данных</span>') + '</td>' +
            '<td class="' + (VERDICT_CLASS[c.verdict] || '') + '">' +
              esc(c.verdict) + '</td>' +
          '</tr>';
        }).join('');
        return '<h2>' + esc(f.filter_name || ('фильтр ' + f.filter_id)) +
          ' <span class="hint">— совпадений ' + f.hits + '</span></h2>' +
          '<div class="niche-table-wrap"><table class="niche-table"><thead><tr>' +
          '<th>ОКПД2</th><th>Регион</th><th>Корзина</th><th class="num">Ловит</th>' +
          '<th class="num">Средняя НМЦК</th><th class="num">Заявок</th>' +
          '<th class="num">Одна заявка</th><th class="num">Снижение</th>' +
          '<th>Вывод</th></tr></thead><tbody>' + rows + '</tbody></table></div>';
      }).join('');
    })
    .catch(function () {
      document.getElementById('a-note').textContent = 'Не удалось загрузить аудит.';
    });
})();
