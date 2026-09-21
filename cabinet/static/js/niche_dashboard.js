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

  function card(r, kind) {
    const facts = [
      ['процедур', r.procedures_count],
      ['заявок обычно', num(r.median_bids)],
      ['снижение', r.drop_known ? pct(r.median_drop) : 'н/д'],
      ['цена', r.price_bucket_label || '—'],
    ];
    return '<a class="niche-card ' + kind + '" href="/cabinet/niches/' +
      encodeURIComponent(r.okpd2) +
      (r.region ? '?region=' + encodeURIComponent(r.region) : '') + '">' +
      '<div class="code">' + esc(r.okpd2) + ' · ' + esc(r.region_name || '') + '</div>' +
      '<div class="cat">' + esc(r.okpd2_name || 'категория без названия') + '</div>' +
      '<div class="facts">' + facts.map(function (f) {
        return '<span class="fact">' + esc(f[0]) + '<b>' + esc(f[1]) + '</b></span>';
      }).join('') + '</div></a>';
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
        ? good.map(function (r) { return card(r, 'good'); }).join('')
        : '<div class="loading">Пока нечего рекомендовать: ни одна ниша не ' +
          'набрала достаточно измеренных данных. Это не значит, что таких ниш ' +
          'нет — значит, история ещё загружается.</div>';

      const bad = d.crowded || [];
      document.getElementById('d-bad').innerHTML = bad.length
        ? bad.map(function (r) { return card(r, 'bad'); }).join('')
        : '<div class="loading">нет данных</div>';

      if (window.Chart) { drawCharts(d.charts || {}); }
    })
    .catch(function () {
      document.getElementById('d-warning').textContent = 'Не удалось загрузить сводку.';
    });

  /* Цвета берём из темы кабинета: жёстко прописанные ломаются при смене
     светлой темы на тёмную — подписи сливаются с фоном. */
  function themeColor(name, fallback) {
    const v = getComputedStyle(document.documentElement)
      .getPropertyValue(name).trim();
    return v || fallback;
  }

  function drawCharts(c) {
    const text = themeColor('--text', '#222');
    const muted = themeColor('--muted', '#888');
    const line = themeColor('--line', 'rgba(128,128,128,.25)');
    const GREEN = '#1f9d55', RED = '#c53030', BLUE = '#3182ce';

    Chart.defaults.color = muted;
    Chart.defaults.borderColor = line;
    Chart.defaults.font.size = 11;

    const base = {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
    };

    const bids = c.bids_distribution || [];
    if (bids.length) {
      new Chart(document.getElementById('c-bids'), {
        type: 'bar',
        data: {
          labels: bids.map(function (b) {
            return b.bids >= 10 ? '10+' : String(b.bids);
          }),
          datasets: [{
            data: bids.map(function (b) { return b.procedures; }),
            // Ноль и одна заявка — это и есть свободный рынок, поэтому
            // они выделены цветом, а не оставлены в общей массе.
            backgroundColor: bids.map(function (b) {
              return b.bids <= 1 ? GREEN : BLUE;
            }),
          }],
        },
        options: Object.assign({}, base, {
          scales: {
            x: { title: { display: true, text: 'заявок в закупке', color: muted } },
            y: { title: { display: true, text: 'закупок', color: muted } },
          },
        }),
      });
    }

    const drop = c.drop_by_bids || [];
    if (drop.length) {
      new Chart(document.getElementById('c-drop'), {
        type: 'line',
        data: {
          labels: drop.map(function (d) { return String(d.bids); }),
          datasets: [{
            data: drop.map(function (d) { return d.avg_drop; }),
            borderColor: RED, backgroundColor: 'rgba(197,48,48,.12)',
            fill: true, tension: .25, pointRadius: 4,
          }],
        },
        options: Object.assign({}, base, {
          plugins: {
            legend: { display: false },
            tooltip: { callbacks: { label: function (ctx) {
              const row = drop[ctx.dataIndex];
              return ctx.parsed.y + '% — по ' + row.procedures + ' закупкам';
            } } },
          },
          scales: {
            x: { title: { display: true, text: 'заявок в закупке', color: muted } },
            y: { title: { display: true, text: 'снижение, %', color: muted },
                 beginAtZero: true },
          },
        }),
      });
    }

    const monthly = c.monthly || [];
    if (monthly.length) {
      new Chart(document.getElementById('c-monthly'), {
        data: {
          labels: monthly.map(function (m) { return m.month; }),
          datasets: [
            { type: 'bar', label: 'закупок', yAxisID: 'y',
              data: monthly.map(function (m) { return m.procedures; }),
              backgroundColor: 'rgba(49,130,206,.45)' },
            { type: 'line', label: 'снижение, %', yAxisID: 'y1',
              data: monthly.map(function (m) { return m.avg_drop; }),
              borderColor: RED, tension: .25, pointRadius: 3 },
          ],
        },
        options: Object.assign({}, base, {
          plugins: { legend: { display: true, labels: { color: muted } } },
          scales: {
            y: { position: 'left', title: { display: true, text: 'закупок', color: muted } },
            y1: { position: 'right', grid: { drawOnChartArea: false },
                  title: { display: true, text: 'снижение, %', color: muted } },
          },
        }),
      });
    }

    const cats = c.top_categories || [];
    if (cats.length) {
      new Chart(document.getElementById('c-cats'), {
        type: 'bar',
        data: {
          labels: cats.map(function (t) {
            const name = t.okpd2_name || '';
            return t.okpd2 + (name ? ' · ' + name.slice(0, 34) : '');
          }),
          datasets: [{
            data: cats.map(function (t) { return t.procedures; }),
            // Цвет по тесноте: мало участников зелёный, много красный.
            backgroundColor: cats.map(function (t) {
              return t.avg_bids <= 2 ? GREEN : t.avg_bids <= 4 ? BLUE : RED;
            }),
          }],
        },
        options: Object.assign({}, base, {
          indexAxis: 'y',
          plugins: {
            legend: { display: false },
            tooltip: { callbacks: { label: function (ctx) {
              const row = cats[ctx.dataIndex];
              return row.procedures + ' закупок, в среднем ' + row.avg_bids + ' заявок';
            } } },
          },
          scales: { x: { title: { display: true, text: 'закупок', color: muted } } },
        }),
      });
    }
  }
})();
