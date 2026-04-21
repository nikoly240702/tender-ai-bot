(function () {
  const { apiGet } = window.Cabinet;
  const byId = id => document.getElementById(id);
  function el(tag, opts) {
    const e = document.createElement(tag);
    if (opts && opts.cls) e.className = opts.cls;
    if (opts && opts.text !== undefined) e.textContent = opts.text;
    if (opts && opts.data) Object.keys(opts.data).forEach(k => e.dataset[k] = opts.data[k]);
    return e;
  }

  const MONTHS_RU = ['Январь','Февраль','Март','Апрель','Май','Июнь','Июль','Август','Сентябрь','Октябрь','Ноябрь','Декабрь'];
  let current = new Date();
  let currentData = {};

  function key(y, m, d) {
    return y + '-' + String(m + 1).padStart(2, '0') + '-' + String(d).padStart(2, '0');
  }

  async function render() {
    const y = current.getFullYear();
    const m = current.getMonth();
    byId('month-label').textContent = MONTHS_RU[m] + ' ' + y;

    const monthStr = y + '-' + String(m + 1).padStart(2, '0');
    const data = await apiGet('/cabinet/api/calendar?month=' + monthStr);
    currentData = (data && data.days) || {};

    const grid = byId('cal-grid');
    grid.textContent = '';

    const firstDay = new Date(y, m, 1);
    const dowMonFirst = (firstDay.getDay() + 6) % 7;
    const daysInMonth = new Date(y, m + 1, 0).getDate();
    const today = new Date();

    const prevDays = new Date(y, m, 0).getDate();
    for (let i = 0; i < dowMonFirst; i++) {
      const cell = el('div', { cls: 'cal-cell out' });
      cell.appendChild(el('div', { cls: 'day-num', text: String(prevDays - dowMonFirst + 1 + i) }));
      grid.appendChild(cell);
    }

    for (let d = 1; d <= daysInMonth; d++) {
      const k = key(y, m, d);
      const cell = el('div', { cls: 'cal-cell', data: { key: k } });
      const isToday = today.getFullYear() === y && today.getMonth() === m && today.getDate() === d;
      if (isToday) cell.classList.add('today');
      cell.appendChild(el('div', { cls: 'day-num', text: String(d) }));
      const list = currentData[k] || [];
      if (list.length) cell.appendChild(el('span', { cls: 'count', text: String(list.length) }));
      cell.addEventListener('click', () => showDay(k));
      grid.appendChild(cell);
    }

    const filled = dowMonFirst + daysInMonth;
    const tail = (7 - (filled % 7)) % 7;
    for (let i = 1; i <= tail; i++) {
      const cell = el('div', { cls: 'cal-cell out' });
      cell.appendChild(el('div', { cls: 'day-num', text: String(i) }));
      grid.appendChild(cell);
    }

    byId('day-details').style.display = 'none';
  }

  function showDay(k) {
    document.querySelectorAll('.cal-cell').forEach(c => c.classList.remove('active'));
    const cell = document.querySelector('.cal-cell[data-key="' + k + '"]');
    if (cell) cell.classList.add('active');

    const details = byId('day-details');
    const body = byId('day-details-body');
    body.textContent = '';
    byId('day-details-title').textContent = k;
    const tenders = currentData[k] || [];
    if (!tenders.length) {
      body.appendChild(el('div', { cls: 'empty', text: 'Тендеров на этот день нет' }));
    } else {
      tenders.forEach(t => {
        const row = el('div', { cls: 'day-tender' });
        row.appendChild(el('div', { cls: 'name', text: t.name || 'Без названия' }));
        const metaParts = [t.customer_name, t.price ? (Math.round(t.price).toLocaleString('ru-RU') + ' ₽') : null].filter(Boolean);
        row.appendChild(el('div', { cls: 'meta', text: metaParts.join(' · ') }));
        body.appendChild(row);
      });
    }
    details.style.display = 'block';
  }

  byId('prev-month').addEventListener('click', () => {
    current = new Date(current.getFullYear(), current.getMonth() - 1, 1);
    render();
  });
  byId('next-month').addEventListener('click', () => {
    current = new Date(current.getFullYear(), current.getMonth() + 1, 1);
    render();
  });

  render();
})();
