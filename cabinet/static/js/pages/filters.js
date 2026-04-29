/* Cabinet v3 — filters page.
   Переносит логику старой filters.html:
   - теги ключевых слов/исключений (Enter/запятая добавляют)
   - pills для типа и закона
   - список регионов с поиском
   - AI-намерение в Расширенных настройках
   - saveFilter с дизейблом кнопки (fix из 9c50320)
*/
(function () {
  const { apiGet, apiPost, Toast } = window.Cabinet;

  let editingId = null;
  let allRegions = [];
  let keywords = [];
  let excludes = [];

  function byId(id) { return document.getElementById(id); }
  function el(tag, opts) {
    const e = document.createElement(tag);
    if (!opts) return e;
    if (opts.cls) e.className = opts.cls;
    if (opts.text !== undefined) e.textContent = opts.text;
    if (opts.attrs) Object.keys(opts.attrs).forEach(k => e.setAttribute(k, opts.attrs[k]));
    if (opts.data) Object.keys(opts.data).forEach(k => e.dataset[k] = opts.data[k]);
    return e;
  }

  function fmtPrice(p) {
    if (!p) return '';
    const n = parseFloat(p);
    if (n >= 1e6) return (n / 1e6).toFixed(1).replace(/\.0$/, '') + ' млн';
    if (n >= 1e3) return Math.round(n / 1e3) + ' тыс';
    return String(Math.round(n));
  }

  function initTagInput(wrapId, inputId, arr, hiddenId) {
    const input = byId(inputId);
    input.addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ',') {
        e.preventDefault();
        addTag(wrapId, arr, hiddenId, input.value.trim().replace(/,$/, ''));
        input.value = '';
      } else if (e.key === 'Backspace' && !input.value && arr.length > 0) {
        arr.pop();
        renderTags(wrapId, inputId, arr, hiddenId);
      }
    });
    input.addEventListener('blur', () => {
      if (input.value.trim()) {
        addTag(wrapId, arr, hiddenId, input.value.trim());
        input.value = '';
      }
    });
  }

  function addTag(wrapId, arr, hiddenId, val) {
    if (!val || arr.includes(val)) return;
    arr.push(val);
    const inputId = wrapId === 'kw-wrap' ? 'kw-input' : 'ex-input';
    renderTags(wrapId, inputId, arr, hiddenId);
  }

  function renderTags(wrapId, inputId, arr, hiddenId) {
    const wrap = byId(wrapId);
    const input = byId(inputId);
    wrap.querySelectorAll('.tag-item').forEach(t => t.remove());
    arr.forEach((tag, idx) => {
      const item = el('span', { cls: 'tag-item' });
      item.appendChild(document.createTextNode(tag + ' '));
      const rm = el('span', { cls: 'rm', text: '×' });
      rm.addEventListener('click', () => {
        arr.splice(idx, 1);
        renderTags(wrapId, inputId, arr, hiddenId);
      });
      item.appendChild(rm);
      wrap.insertBefore(item, input);
    });
    byId(hiddenId).value = JSON.stringify(arr);
  }

  function initPills(groupId, hiddenId) {
    document.querySelectorAll('#' + groupId + ' .chip').forEach(p => {
      p.addEventListener('click', () => {
        document.querySelectorAll('#' + groupId + ' .chip').forEach(x => x.classList.remove('active'));
        p.classList.add('active');
        byId(hiddenId).value = p.dataset.val;
      });
    });
  }

  async function loadRegions() {
    const data = await apiGet('/cabinet/api/regions');
    if (!data) return;
    allRegions = data.districts || [];
    renderRegions('');
  }

  function renderRegions(filter) {
    const list = byId('region-list');
    list.textContent = '';
    let rendered = 0;
    allRegions.forEach(d => {
      const regions = d.regions.filter(r => !filter || r.toLowerCase().includes(filter.toLowerCase()));
      if (!regions.length) return;
      list.appendChild(el('div', { cls: 'region-district', text: d.district + ' (' + d.code + ')' }));
      regions.forEach(r => {
        const row = el('label', { cls: 'region-item' });
        const cb = el('input', { attrs: { type: 'checkbox', value: r } });
        cb.addEventListener('change', updateRegionsHidden);
        row.appendChild(cb);
        row.appendChild(document.createTextNode(' ' + r));
        list.appendChild(row);
        rendered++;
      });
    });
    if (rendered === 0) {
      list.appendChild(el('div', { cls: 'empty', text: 'Ничего не найдено' }));
    }
  }

  function updateRegionsHidden() {
    const selected = [];
    document.querySelectorAll('#region-list input[type=checkbox]:checked').forEach(cb => selected.push(cb.value));
    byId('f-regions').value = JSON.stringify(selected);
  }

  function setSelectedRegions(regions) {
    byId('f-regions').value = JSON.stringify(regions);
    document.querySelectorAll('#region-list input[type=checkbox]').forEach(cb => {
      cb.checked = regions.includes(cb.value);
    });
  }

  function openForm(filter) {
    editingId = filter ? filter.id : null;
    keywords = filter ? (filter.keywords || []).slice() : [];
    excludes = filter ? (filter.exclude_keywords || []).slice() : [];

    byId('form-title').textContent = filter ? 'Редактировать фильтр' : 'Создать фильтр';
    byId('f-name').value = filter ? (filter.name || '') : '';
    byId('f-price-min').value = filter ? (filter.price_min || '') : '';
    byId('f-price-max').value = filter ? (filter.price_max || '') : '';
    byId('f-intent').value = filter ? (filter.ai_intent || '') : '';

    renderTags('kw-wrap', 'kw-input', keywords, 'f-keywords');
    renderTags('ex-wrap', 'ex-input', excludes, 'f-exclude');

    const law = filter ? (filter.law_type || '') : '';
    document.querySelectorAll('#law-pills .chip').forEach(p => p.classList.toggle('active', p.dataset.val === law));
    byId('f-law').value = law;

    const types = filter ? (filter.tender_types || []) : [];
    const typeVal = types.length === 1 ? types[0] : '';
    document.querySelectorAll('#types-pills .chip').forEach(p => p.classList.toggle('active', p.dataset.val === typeVal));
    byId('f-types').value = typeVal;

    setSelectedRegions(filter ? (filter.regions || []) : []);

    byId('form-panel').classList.add('open');
    byId('f-name').focus();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  function closeForm() {
    byId('form-panel').classList.remove('open');
    editingId = null;
  }

  async function saveFilter(btn) {
    const name = byId('f-name').value.trim();
    if (!name) { Toast.show('Введите название фильтра', 'alert'); return; }
    if (btn.disabled) return;

    const origText = btn.textContent;
    btn.disabled = true;
    btn.textContent = '⏳ Сохраняем…';

    const payload = {
      name,
      keywords: JSON.parse(byId('f-keywords').value || '[]'),
      exclude_keywords: JSON.parse(byId('f-exclude').value || '[]'),
      law_type: byId('f-law').value || null,
      tender_types: byId('f-types').value ? [byId('f-types').value] : [],
      price_min: byId('f-price-min').value ? parseFloat(byId('f-price-min').value) : null,
      price_max: byId('f-price-max').value ? parseFloat(byId('f-price-max').value) : null,
      regions: JSON.parse(byId('f-regions').value || '[]'),
      ai_intent: byId('f-intent').value.trim() || null,
    };

    try {
      let data;
      if (editingId) {
        const resp = await fetch('/cabinet/api/filters/' + editingId, {
          method: 'PUT',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        data = await resp.json();
      } else {
        data = await apiPost('/cabinet/api/filters/create', payload);
      }

      if (data && data.ok) {
        Toast.show(editingId ? '✓ Фильтр обновлён' : '✓ Фильтр создан', 'positive');
        closeForm();
        loadFilters();
      } else if (data) {
        Toast.show(data.error || 'Ошибка сохранения', 'alert');
      }
    } catch (e) {
      Toast.show('Ошибка соединения. Попробуйте снова', 'alert');
    } finally {
      btn.disabled = false;
      btn.textContent = origText;
    }
  }

  async function toggleFilter(id) {
    const data = await apiPost('/cabinet/api/filters/' + id + '/toggle', {});
    if (data && data.ok) {
      Toast.show(data.is_active ? 'Фильтр активирован' : 'Фильтр приостановлен');
      loadFilters();
    }
  }

  async function deleteFilter(id) {
    if (!confirm('Удалить фильтр?')) return;
    const resp = await fetch('/cabinet/api/filters/' + id, { method: 'DELETE', credentials: 'same-origin' });
    const data = await resp.json().catch(() => ({}));
    if (data.ok) { Toast.show('Фильтр удалён'); loadFilters(); }
    else Toast.show(data.error || 'Ошибка удаления', 'alert');
  }

  function duplicateFilter(filter) {
    const dup = Object.assign({}, filter, { name: filter.name + ' (копия)', id: null });
    openForm(dup);
  }

  async function loadNotifyTargets(filterId, container, summaryEl) {
    const data = await apiGet('/cabinet/api/filters/' + filterId + '/notify-targets');
    if (!data) {
      container.textContent = 'Не удалось загрузить';
      return;
    }
    container.dataset.personalChatId = String(data.personal.chat_id);
    container.textContent = '';

    const personal = el('label', { cls: 'notify-row' });
    const personalCb = el('input', { attrs: { type: 'checkbox' } });
    personalCb.checked = !!data.personal.enabled;
    personalCb.addEventListener('change', () => saveNotifyTargets(filterId, container, summaryEl));
    personal.appendChild(personalCb);
    personal.appendChild(document.createTextNode(' Мне в личку'));
    personal.dataset.chatId = String(data.personal.chat_id);
    personal.dataset.kind = 'personal';
    container.appendChild(personal);

    if (!data.groups.length) {
      const hint = el('div', { cls: 'notify-hint' });
      hint.textContent = 'Чтобы получать в группу — добавьте бота в свой Telegram-чат и сделайте админом';
      container.appendChild(hint);
    } else {
      data.groups.forEach(g => {
        const row = el('label', { cls: 'notify-row' });
        const cb = el('input', { attrs: { type: 'checkbox' } });
        cb.checked = !!g.enabled;
        cb.addEventListener('change', () => saveNotifyTargets(filterId, container, summaryEl));
        row.appendChild(cb);
        row.appendChild(document.createTextNode(' ' + g.name));
        row.dataset.chatId = String(g.chat_id);
        row.dataset.kind = 'group';
        container.appendChild(row);
      });
    }

    if (summaryEl) refreshNotifySummary(container, summaryEl);
  }

  function refreshNotifySummary(container, summaryEl) {
    let personal = false;
    let groupCount = 0;
    container.querySelectorAll('.notify-row').forEach(row => {
      const cb = row.querySelector('input[type=checkbox]');
      if (!cb || !cb.checked) return;
      if (row.dataset.kind === 'personal') personal = true;
      else groupCount += 1;
    });
    let txt;
    if (!personal && groupCount === 0) txt = 'Мне в личку';
    else if (personal && groupCount === 0) txt = 'Мне в личку';
    else if (!personal && groupCount === 1) txt = 'В группу';
    else if (!personal) txt = groupCount + ' групп';
    else if (groupCount === 1) txt = 'Личка + 1 группа';
    else txt = 'Личка + ' + groupCount + ' групп';
    summaryEl.textContent = txt;
  }

  const notifySaveTimers = {};

  async function saveNotifyTargets(filterId, container, summaryEl) {
    if (summaryEl) refreshNotifySummary(container, summaryEl);
    if (notifySaveTimers[filterId]) clearTimeout(notifySaveTimers[filterId]);
    notifySaveTimers[filterId] = setTimeout(async () => {
      const chatIds = [];
      container.querySelectorAll('.notify-row').forEach(row => {
        const cb = row.querySelector('input[type=checkbox]');
        if (cb && cb.checked) chatIds.push(parseInt(row.dataset.chatId, 10));
      });
      const resp = await fetch('/cabinet/api/filters/' + filterId + '/notify-targets', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ chat_ids: chatIds }),
      });
      const data = await resp.json().catch(() => ({}));
      if (data.ok) {
        Toast.show('✓ Сохранено', 'positive');
        const filter = allFilters.find(f => f.id === filterId);
        if (filter) {
          const personalId = parseInt(container.dataset.personalChatId || '0', 10);
          const isDefault = !chatIds.length || (chatIds.length === 1 && chatIds[0] === personalId);
          filter.notify_chat_ids = isDefault ? null : chatIds;
        }
      } else {
        Toast.show(data.error || 'Ошибка сохранения', 'alert');
      }
    }, 350);
  }

  let currentTab = 'all';

  function filterList(list, tab) {
    if (tab === 'active') return list.filter(f => f.is_active);
    if (tab === 'paused') return list.filter(f => !f.is_active);
    return list;
  }

  function renderList(filters) {
    const container = byId('filters-list');
    container.textContent = '';
    const visible = filterList(filters, currentTab);
    if (visible.length === 0) {
      const empty = el('div', { cls: 'empty' });
      empty.appendChild(el('div', { cls: 'title', text: 'Фильтров пока нет' }));
      empty.appendChild(el('div', { text: 'Нажмите «+ Новый фильтр» чтобы создать первый' }));
      container.appendChild(empty);
      return;
    }

    visible.forEach(f => {
      const card = el('div', { cls: 'filter-card' + (f.is_active ? '' : ' paused') });

      const header = el('header');
      header.appendChild(el('div', { cls: 'name', text: f.name || ('Фильтр #' + f.id) }));

      const actions = el('div', { cls: 'actions' });
      const btnEdit = el('button', { cls: 'btn btn-secondary btn-sm', text: 'Изменить' });
      btnEdit.addEventListener('click', () => openForm(f));
      const btnDup = el('button', { cls: 'btn btn-ghost btn-sm', text: 'Дублировать' });
      btnDup.addEventListener('click', () => duplicateFilter(f));
      const btnToggle = el('button', { cls: 'btn btn-ghost btn-sm', text: f.is_active ? 'Пауза' : 'Включить' });
      btnToggle.addEventListener('click', () => toggleFilter(f.id));
      const btnDelete = el('button', { cls: 'btn btn-ghost btn-sm', text: 'Удалить' });
      btnDelete.addEventListener('click', () => deleteFilter(f.id));
      actions.appendChild(btnEdit);
      actions.appendChild(btnDup);
      actions.appendChild(btnToggle);
      actions.appendChild(btnDelete);
      header.appendChild(actions);
      card.appendChild(header);

      const kws = el('div', { cls: 'kws' });
      (f.keywords || []).forEach(k => kws.appendChild(el('span', { cls: 'kw', text: k })));
      (f.exclude_keywords || []).forEach(k => kws.appendChild(el('span', { cls: 'kw excl', text: '−' + k })));
      if (kws.children.length > 0) card.appendChild(kws);

      const meta = el('div', { cls: 'meta' });
      meta.appendChild(el('span', { cls: 'match-count', text: String(f.match_count || 0) + ' совпадений' }));
      if (f.price_min || f.price_max) {
        meta.appendChild(el('span', { text: 'Цена: ' + (fmtPrice(f.price_min) || '0') + '—' + (fmtPrice(f.price_max) || '∞') + ' ₽' }));
      }
      if (f.regions && f.regions.length) {
        meta.appendChild(el('span', { text: 'Регионы: ' + f.regions.slice(0, 2).join(', ') + (f.regions.length > 2 ? ' +' + (f.regions.length - 2) : '') }));
      }
      if (f.law_type) meta.appendChild(el('span', { text: f.law_type === '44' ? '44-ФЗ' : f.law_type === '223' ? '223-ФЗ' : f.law_type }));
      meta.appendChild(el('span', { text: f.is_active ? 'Активен' : 'На паузе' }));
      card.appendChild(meta);

      const notify = el('div', { cls: 'notify-routing' });
      const notifyHeader = el('button', { cls: 'notify-toggle' });
      notifyHeader.appendChild(el('span', { cls: 'notify-icon', text: '📨' }));
      notifyHeader.appendChild(el('span', { cls: 'notify-label', text: 'Куда уведомлять:' }));
      const initialTargets = f.notify_chat_ids || [];
      const initialSummary = !initialTargets.length
        ? 'Мне в личку'
        : initialTargets.length + (initialTargets.length === 1 ? ' получатель' : ' получателей');
      const summary = el('span', { cls: 'notify-summary', text: initialSummary });
      notifyHeader.appendChild(summary);
      notifyHeader.appendChild(el('span', { cls: 'notify-chevron', text: '▸' }));
      const notifyBody = el('div', { cls: 'notify-body' });
      let loaded = false;
      notifyHeader.addEventListener('click', async () => {
        const open = notify.classList.toggle('open');
        notifyHeader.querySelector('.notify-chevron').textContent = open ? '▾' : '▸';
        if (open && !loaded) {
          notifyBody.textContent = 'загрузка…';
          await loadNotifyTargets(f.id, notifyBody, summary);
          loaded = true;
        }
      });
      notify.appendChild(notifyHeader);
      notify.appendChild(notifyBody);
      card.appendChild(notify);

      container.appendChild(card);
    });
  }

  let allFilters = [];

  async function loadFilters() {
    const data = await apiGet('/cabinet/api/filters?active_only=false');
    if (!data) return;
    allFilters = data.filters || [];
    renderList(allFilters);
    const cntAll = allFilters.length;
    const cntActive = allFilters.filter(f => f.is_active).length;
    const cntPaused = cntAll - cntActive;
    byId('tab-all-count').textContent = cntAll;
    byId('tab-active-count').textContent = cntActive;
    byId('tab-paused-count').textContent = cntPaused;
  }

  document.querySelectorAll('.tabs button').forEach(b => {
    b.addEventListener('click', () => {
      document.querySelectorAll('.tabs button').forEach(x => x.classList.remove('active'));
      b.classList.add('active');
      currentTab = b.dataset.tab;
      renderList(allFilters);
    });
  });

  byId('advanced-btn').addEventListener('click', () => {
    const c = byId('advanced-content');
    const btn = byId('advanced-btn');
    if (c.classList.toggle('open')) btn.textContent = '▾ Расширенные настройки';
    else btn.textContent = '▸ Расширенные настройки';
  });

  byId('region-search').addEventListener('input', (e) => {
    renderRegions(e.target.value);
    const selected = JSON.parse(byId('f-regions').value || '[]');
    document.querySelectorAll('#region-list input[type=checkbox]').forEach(cb => {
      if (selected.includes(cb.value)) cb.checked = true;
    });
  });

  byId('btn-new-filter').addEventListener('click', () => openForm(null));
  byId('btn-save-filter').addEventListener('click', (e) => saveFilter(e.currentTarget));
  byId('btn-cancel-filter').addEventListener('click', closeForm);

  initTagInput('kw-wrap', 'kw-input', keywords, 'f-keywords');
  initTagInput('ex-wrap', 'ex-input', excludes, 'f-exclude');
  initPills('types-pills', 'f-types');
  initPills('law-pills', 'f-law');
  loadRegions();
  loadFilters();
})();
