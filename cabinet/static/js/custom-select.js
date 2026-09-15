/* Cabinet v3 — общий кастомный dropdown поверх нативного <select>.
   Системный список на macOS/iOS нельзя стилизовать через CSS вообще —
   только закрытый контрол. Настоящий <select> остаётся источником истины
   (value/onchange/change-событие/options — без изменений для вызывающего
   кода), а поверх рисуется своя кликабельная панель с опциональным
   поиском. Стили — components.css (.cs-*), общие для всех страниц. */
(function () {
  function el(tag, opts) {
    const e = document.createElement(tag);
    if (!opts) return e;
    if (opts.cls) e.className = opts.cls;
    if (opts.text !== undefined) e.textContent = opts.text;
    if (opts.attrs) Object.keys(opts.attrs).forEach(k => e.setAttribute(k, opts.attrs[k]));
    return e;
  }

  const SEARCH_THRESHOLD = 7; // от скольки опций показываем строку поиска

  function buildCustomSelect(selectEl, options) {
    if (!selectEl) return;
    if (selectEl._csRefresh) { selectEl._csRefresh(); return; }
    const opts = options || {};

    const wrap = el('div', { cls: 'cs-select' + (opts.wrapClass ? ' ' + opts.wrapClass : '') });
    selectEl.parentNode.insertBefore(wrap, selectEl);
    selectEl.hidden = true;
    wrap.appendChild(selectEl);

    const trigger = el('button', { cls: 'cs-trigger', attrs: { type: 'button' } });
    const label = el('span', { cls: 'cs-trigger-label' });
    trigger.appendChild(label);
    trigger.appendChild(el('span', { cls: 'cs-trigger-arrow' }));
    wrap.appendChild(trigger);

    // Панель рисуется в document.body, а не внутри wrap: если контрол лежит
    // в скроллящемся контейнере (например, модалка карточки), абсолютно
    // позиционированная панель обрезалась бы этим скроллом у нижнего края.
    const panel = el('div', { cls: 'cs-panel' });
    const searchInp = el('input', {
      cls: 'cs-search',
      attrs: { type: 'text', placeholder: 'Поиск…', autocomplete: 'off' },
    });
    const list = el('div', { cls: 'cs-list' });
    panel.appendChild(searchInp);
    panel.appendChild(list);
    document.body.appendChild(panel);

    function position() {
      const r = trigger.getBoundingClientRect();
      panel.style.left = r.left + 'px';
      panel.style.minWidth = r.width + 'px';
      panel.style.maxWidth = Math.min(420, window.innerWidth - r.left - 16) + 'px';
      const spaceBelow = window.innerHeight - r.bottom;
      if (spaceBelow < 200 && r.top > spaceBelow) {
        panel.style.bottom = (window.innerHeight - r.top + 6) + 'px';
        panel.style.top = 'auto';
      } else {
        panel.style.top = (r.bottom + 6) + 'px';
        panel.style.bottom = 'auto';
      }
    }

    function filterList(query) {
      const q = query.trim().toLowerCase();
      let anyVisible = false;
      Array.from(list.children).forEach(item => {
        if (item.classList.contains('cs-empty')) return;
        const match = !q || item.dataset.text.indexOf(q) !== -1;
        item.hidden = !match;
        if (match) anyVisible = true;
      });
      const emptyEl = list.querySelector('.cs-empty');
      if (!anyVisible && q) {
        if (!emptyEl) list.appendChild(el('div', { cls: 'cs-empty', text: 'Ничего не найдено' }));
      } else if (emptyEl) {
        emptyEl.remove();
      }
    }

    const close = () => {
      wrap.classList.remove('open');
      panel.classList.remove('open');
      searchInp.value = '';
      filterList('');
    };
    const open = () => {
      if (selectEl.disabled) return;
      document.querySelectorAll('.cs-panel.open').forEach(p => p.classList.remove('open'));
      document.querySelectorAll('.cs-select.open').forEach(w => w.classList.remove('open'));
      position();
      wrap.classList.add('open');
      panel.classList.add('open');
      if (searchInp.hidden) trigger.focus(); else searchInp.focus();
    };

    trigger.onclick = (e) => {
      e.stopPropagation();
      if (selectEl.disabled) return;
      panel.classList.contains('open') ? close() : open();
    };
    document.addEventListener('click', (e) => {
      if (!wrap.contains(e.target) && !panel.contains(e.target)) close();
    });
    window.addEventListener('resize', () => { if (panel.classList.contains('open')) position(); });
    // capture:true — скролл вложенного контейнера (например .modal-body)
    // не всплывает как обычное событие, но перехватывается на фазе
    // погружения; проще закрыть панель, чем пересчитывать позицию на
    // каждый кадр скролла.
    document.addEventListener('scroll', () => { if (panel.classList.contains('open')) close(); }, true);
    wrap.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && panel.classList.contains('open')) {
        e.stopPropagation();
        close();
        trigger.focus();
      }
    });
    searchInp.addEventListener('input', () => filterList(searchInp.value));
    searchInp.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') { e.stopPropagation(); close(); trigger.focus(); }
    });

    function refresh() {
      const current = selectEl.options[selectEl.selectedIndex];
      label.textContent = current ? current.textContent : '';
      searchInp.hidden = opts.search === false
        || (opts.search !== true && selectEl.options.length <= SEARCH_THRESHOLD);

      list.replaceChildren();
      Array.from(selectEl.options).forEach(opt => {
        const item = el('div', {
          cls: 'cs-option'
            + (opt.value === '__new__' ? ' cs-option-new' : '')
            + (opt.selected ? ' selected' : ''),
          text: opt.textContent,
        });
        item.dataset.text = opt.textContent.toLowerCase();
        item.onclick = () => {
          selectEl.value = opt.value;
          selectEl.dispatchEvent(new Event('change'));
          refresh();
          close();
        };
        list.appendChild(item);
      });
    }

    selectEl._csRefresh = refresh;
    refresh();
  }

  window.Cabinet = window.Cabinet || {};
  window.Cabinet.buildCustomSelect = buildCustomSelect;
})();
