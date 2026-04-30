/* Cabinet v3 — Pipeline / Kanban board.
   Drag через Sortable.js + optimistic UI. Модалка карточки с 5 табами.
   Никакого innerHTML — только createElement / textContent / replaceChildren. */
(function () {
  const { Toast } = window.Cabinet;

  const STAGE_LABELS = {
    'FOUND': 'Найденные',
    'IN_WORK': 'Взято в работу',
    'RFQ': 'Запрос предложений',
    'QUOTED': 'Получено КП',
    'SUBMITTED': 'Участвуем',
    'RESULT': 'Результат',
    'REJECTED': 'Не берём в работу',
  };
  // Стадии для dropdown в модалке (без RESULT — оно через win/lost кнопки)
  const SELECTABLE_STAGES = ['FOUND', 'IN_WORK', 'RFQ', 'QUOTED', 'SUBMITTED', 'REJECTED'];

  const headerEl = document.querySelector('.page-header');
  const teamMembers = headerEl ? JSON.parse(headerEl.dataset.members || '[]') : [];
  const isOwner = headerEl ? headerEl.dataset.isOwner === '1' : false;
  const currentUserId = headerEl ? parseInt(headerEl.dataset.currentUserId || '0', 10) : 0;

  const modal = document.getElementById('card-modal');
  const modalClose = document.getElementById('card-modal-close');
  let openCardId = null;

  function el(tag, opts) {
    const e = document.createElement(tag);
    if (!opts) return e;
    if (opts.cls) e.className = opts.cls;
    if (opts.text !== undefined) e.textContent = opts.text;
    if (opts.attrs) Object.keys(opts.attrs).forEach(k => e.setAttribute(k, opts.attrs[k]));
    return e;
  }

  function fmtPrice(v) {
    if (v === null || v === undefined) return '';
    return Math.round(v).toLocaleString('ru-RU') + ' ₽';
  }

  function updateCounts() {
    document.querySelectorAll('.kb-col').forEach(col => {
      const cnt = col.querySelectorAll('.kb-card').length;
      const badge = col.querySelector('.kb-count');
      if (badge) badge.textContent = cnt;
    });
  }

  /* ================ DRAG ================ */

  async function moveCard(cardId, newStage, fromCol) {
    try {
      const r = await fetch('/cabinet/api/pipeline/cards/' + cardId + '/stage', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ stage: newStage }),
      });
      const d = await r.json();
      if (!r.ok || !d.ok) throw new Error(d.error || 'Не удалось переместить');
      Toast.show('✓ Перемещено', 'positive');
      updateCounts();
    } catch (e) {
      Toast.show(e.message || 'Ошибка', 'alert');
      const card = document.querySelector('[data-card-id="' + cardId + '"]');
      if (card && fromCol) fromCol.appendChild(card);
      updateCounts();
    }
  }

  async function setResult(cardId, result) {
    try {
      const r = await fetch('/cabinet/api/pipeline/cards/' + cardId + '/result', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ result }),
      });
      const d = await r.json();
      if (!r.ok || !d.ok) throw new Error(d.error || 'Ошибка');
      Toast.show(result === 'won' ? '✓ Выиграно' : '✓ Проиграно', 'positive');
      window.location.reload();
    } catch (e) {
      Toast.show(e.message || 'Ошибка', 'alert');
    }
  }

  function initSortable() {
    if (typeof Sortable === 'undefined') return;
    document.querySelectorAll('.kb-col-body').forEach(body => {
      Sortable.create(body, {
        group: 'pipeline',
        animation: 150,
        ghostClass: 'sortable-ghost',
        dragClass: 'sortable-drag',
        onAdd: (evt) => {
          const card = evt.item;
          const cardId = parseInt(card.dataset.cardId, 10);
          const targetStage = body.dataset.stage;
          const fromCol = evt.from;
          if (targetStage === 'RESULT') {
            // Возвращаем карточку и спрашиваем результат через модалку
            evt.from.appendChild(card);
            updateCounts();
            const tenderName = card.querySelector('.kb-card-title')?.textContent || ('Карточка #' + cardId);
            openResultModal(cardId, tenderName);
            return;
          }
          moveCard(cardId, targetStage, fromCol);
        },
      });
    });
  }

  /* ================ RESULT MODAL ================ */

  const resultModal = document.getElementById('result-modal');
  const resultClose = document.getElementById('result-modal-close');
  const resultTender = document.getElementById('result-modal-tender');
  let pendingResultCardId = null;

  function openResultModal(cardId, tenderName) {
    pendingResultCardId = cardId;
    if (resultTender) resultTender.textContent = tenderName;
    if (resultModal) resultModal.hidden = false;
  }
  function closeResultModal() {
    pendingResultCardId = null;
    if (resultModal) resultModal.hidden = true;
  }
  if (resultClose) resultClose.addEventListener('click', closeResultModal);
  if (resultModal) {
    resultModal.addEventListener('click', (e) => {
      if (e.target === resultModal) closeResultModal();
    });
  }
  const wonBtn = document.getElementById('result-btn-won');
  const lostBtn = document.getElementById('result-btn-lost');
  if (wonBtn) wonBtn.addEventListener('click', () => {
    if (pendingResultCardId) { setResult(pendingResultCardId, 'won'); closeResultModal(); }
  });
  if (lostBtn) lostBtn.addEventListener('click', () => {
    if (pendingResultCardId) { setResult(pendingResultCardId, 'lost'); closeResultModal(); }
  });

  /* ================ MANUAL CREATE ================ */

  function initManualCreate() {
    const btn = document.getElementById('btn-create-manual');
    if (!btn) return;
    btn.addEventListener('click', async () => {
      const num = prompt('Номер тендера на zakupki.gov.ru:');
      if (!num) return;
      const trimmed = num.trim();
      const r = await fetch('/cabinet/api/pipeline/cards', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tender_number: trimmed }),
      });
      const d = await r.json().catch(() => ({}));
      if (r.ok && d.ok) {
        Toast.show('✓ Карточка создана', 'positive');
        setTimeout(() => window.location.reload(), 700);
      } else if (r.status === 409) {
        Toast.show('Уже в Pipeline', 'alert');
      } else {
        Toast.show(d.error || 'Ошибка', 'alert');
      }
    });
  }

  /* ================ MODAL ================ */

  function openModal(cardId) {
    openCardId = cardId;
    if (modal) modal.hidden = false;
    document.querySelectorAll('.modal-tab').forEach(t =>
      t.classList.toggle('active', t.dataset.tab === 'details'));
    document.querySelectorAll('.tab-pane').forEach(p =>
      p.classList.toggle('active', p.dataset.tab === 'details'));
    loadCardFull(cardId);
  }

  function closeModal() {
    if (modal) modal.hidden = true;
    openCardId = null;
  }

  if (modalClose) modalClose.addEventListener('click', closeModal);
  if (modal) {
    modal.addEventListener('click', (e) => { if (e.target === modal) closeModal(); });
  }
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && modal && !modal.hidden) closeModal();
  });

  document.querySelectorAll('.modal-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      const target = tab.dataset.tab;
      document.querySelectorAll('.modal-tab').forEach(t => t.classList.toggle('active', t === tab));
      document.querySelectorAll('.tab-pane').forEach(p =>
        p.classList.toggle('active', p.dataset.tab === target));
    });
  });

  document.querySelectorAll('.kb-card').forEach(card => {
    card.addEventListener('click', (e) => {
      if (e.target.closest('button')) return;
      openModal(parseInt(card.dataset.cardId, 10));
    });
  });

  async function loadCardFull(cardId) {
    const r = await fetch('/cabinet/api/pipeline/cards/' + cardId + '/full', {
      credentials: 'same-origin',
    });
    if (!r.ok) { Toast.show('Не удалось загрузить', 'alert'); return; }
    const data = await r.json();
    renderModal(data);
  }

  function renderModal(data) {
    const c = data.card;

    document.getElementById('cm-title').textContent = (c.data && c.data.name) || ('Тендер ' + c.tender_number);
    document.getElementById('cm-zakupki-link').href = (c.data && c.data.url) || '#';

    // Stage select
    const stageSel = document.getElementById('cm-stage');
    stageSel.replaceChildren();
    SELECTABLE_STAGES.forEach(s => {
      const opt = document.createElement('option');
      opt.value = s;
      opt.textContent = STAGE_LABELS[s];
      if (c.stage === s) opt.selected = true;
      stageSel.appendChild(opt);
    });
    if (c.stage === 'RESULT') {
      const opt = document.createElement('option');
      opt.value = 'RESULT';
      opt.textContent = 'Результат: ' + (c.result === 'won' ? 'Победа' : 'Проигрыш');
      opt.selected = true;
      stageSel.appendChild(opt);
    }
    stageSel.onchange = async () => {
      const v = stageSel.value;
      if (v === 'RESULT') return;
      const r = await fetch('/cabinet/api/pipeline/cards/' + c.id + '/stage', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ stage: v }),
      });
      Toast.show(r.ok ? '✓ Стадия обновлена' : 'Ошибка', r.ok ? 'positive' : 'alert');
    };

    // Assignee
    const asSel = document.getElementById('cm-assignee');
    asSel.replaceChildren();
    const noopt = document.createElement('option');
    noopt.value = '';
    noopt.textContent = '— не назначен —';
    if (!c.assignee_user_id) noopt.selected = true;
    asSel.appendChild(noopt);
    teamMembers.forEach(m => {
      const opt = document.createElement('option');
      opt.value = m.user_id;
      opt.textContent = m.display_name || ('User ' + m.user_id);
      if (c.assignee_user_id === m.user_id) opt.selected = true;
      asSel.appendChild(opt);
    });
    asSel.onchange = async () => {
      const v = asSel.value;
      if (!v) return;
      const r = await fetch('/cabinet/api/pipeline/cards/' + c.id + '/assignee', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: parseInt(v, 10) }),
      });
      if (r.ok) Toast.show('✓ Ответственный обновлён', 'positive');
    };

    // Prices
    document.getElementById('cm-purchase').value = c.purchase_price || '';
    document.getElementById('cm-sale').value = c.sale_price || '';
    ['cm-purchase', 'cm-sale'].forEach(id => {
      document.getElementById(id).onchange = async () => {
        const purchase = parseFloat(document.getElementById('cm-purchase').value) || null;
        const sale = parseFloat(document.getElementById('cm-sale').value) || null;
        await fetch('/cabinet/api/pipeline/cards/' + c.id + '/prices', {
          method: 'POST', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ purchase_price: purchase, sale_price: sale }),
        });
        Toast.show('✓ Цены сохранены', 'positive');
        loadCardFull(c.id);
      };
    });

    // Margin
    const mEl = document.getElementById('cm-margin');
    if (data.margin) {
      mEl.hidden = false;
      mEl.className = 'margin-box ' + data.margin.color;
      mEl.textContent = `Маржа: ${Math.round(data.margin.abs).toLocaleString('ru-RU')} ₽ (${data.margin.pct.toFixed(1)}%)`;
    } else {
      mEl.hidden = true;
    }

    // Meta
    document.getElementById('cm-customer').textContent = (c.data && c.data.customer) || '—';
    document.getElementById('cm-region').textContent = (c.data && c.data.region) || '—';
    document.getElementById('cm-deadline').textContent = (c.data && c.data.deadline) || '—';

    // AI block
    document.getElementById('cm-ai-summary').textContent = c.ai_summary || 'Анализ ещё не запускался.';
    document.getElementById('cm-ai-recommendation').textContent = c.ai_recommendation
      ? ('Рекомендация: ' + c.ai_recommendation) : '';
    document.getElementById('cm-ai-run').onclick = () => runAi(c.id);

    // Action buttons
    const supplierBtn = document.getElementById('cm-btn-supplier');
    supplierBtn.hidden = c.stage !== 'RFQ';
    supplierBtn.onclick = () => Toast.show('Функция в разработке (holodilnik integration)', 'alert');

    const delBtn = document.getElementById('cm-btn-delete');
    delBtn.hidden = !isOwner;
    delBtn.onclick = async () => {
      if (!confirm('Удалить карточку безвозвратно?')) return;
      const r = await fetch('/cabinet/api/pipeline/cards/' + c.id, {
        method: 'DELETE', credentials: 'same-origin',
      });
      if (r.ok) {
        Toast.show('Удалено', 'positive');
        closeModal();
        window.location.reload();
      }
    };

    document.getElementById('cm-btn-won').onclick = () => setResult(c.id, 'won');
    document.getElementById('cm-btn-lost').onclick = () => setResult(c.id, 'lost');

    // Notes
    renderNotes(c.id, data.notes);
    document.getElementById('cm-note-add').onclick = async () => {
      const inp = document.getElementById('cm-note-input');
      const text = inp.value.trim();
      if (!text) return;
      const r = await fetch('/cabinet/api/pipeline/cards/' + c.id + '/notes', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }),
      });
      const d = await r.json();
      if (d.ok) {
        inp.value = '';
        Toast.show('✓ Заметка добавлена', 'positive');
        loadCardFull(c.id);
      } else {
        Toast.show(d.error || 'Ошибка', 'alert');
      }
    };

    // Files
    renderFiles(c.id, data.files);
    document.getElementById('cm-file-upload-btn').onclick = () =>
      document.getElementById('cm-file-input').click();
    document.getElementById('cm-file-input').onchange = async (ev) => {
      const file = ev.target.files[0];
      if (!file) return;
      if (file.size > 10 * 1024 * 1024) {
        Toast.show('Файл больше 10 MB', 'alert');
        return;
      }
      const fd = new FormData();
      fd.append('file', file);
      const r = await fetch('/cabinet/api/pipeline/cards/' + c.id + '/files', {
        method: 'POST', credentials: 'same-origin', body: fd,
      });
      const d = await r.json().catch(() => ({}));
      if (d.ok) {
        Toast.show('✓ Загружено', 'positive');
        loadCardFull(c.id);
      } else {
        Toast.show(d.error || 'Ошибка', 'alert');
      }
      ev.target.value = '';
    };

    // Checklist
    renderChecklist(c.id, data.checklist);
    document.getElementById('cm-checklist-add').onclick = async () => {
      const inp = document.getElementById('cm-checklist-input');
      const text = inp.value.trim();
      if (!text) return;
      const r = await fetch('/cabinet/api/pipeline/cards/' + c.id + '/checklist', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }),
      });
      const d = await r.json();
      if (d.ok) {
        inp.value = '';
        loadCardFull(c.id);
      } else {
        Toast.show(d.error || 'Ошибка', 'alert');
      }
    };

    // Relations
    renderRelations(c.id, data.relations);
    document.getElementById('cm-relation-add').onclick = async () => {
      const inp = document.getElementById('cm-relation-input');
      const num = inp.value.trim();
      if (!num) return;
      const r = await fetch('/cabinet/api/pipeline/cards/' + c.id + '/relations', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ related_tender_number: num }),
      });
      const d = await r.json();
      if (d.ok) {
        inp.value = '';
        Toast.show('✓ Связано', 'positive');
        loadCardFull(c.id);
      } else {
        Toast.show(d.error || 'Ошибка', 'alert');
      }
    };

    // History
    renderHistory(data.history);
  }

  function renderNotes(cardId, notes) {
    const list = document.getElementById('cm-notes-list');
    list.replaceChildren();
    if (!notes || !notes.length) {
      list.appendChild(el('div', { cls: 'empty', text: 'Заметок пока нет' }));
      return;
    }
    notes.forEach(n => {
      const row = el('div', { cls: 'note-item' });
      row.appendChild(el('div', { text: n.text }));
      row.appendChild(el('div', { cls: 'note-meta', text: `User ${n.user_id} · ${n.created_at || ''}` }));
      list.appendChild(row);
    });
  }

  function renderFiles(cardId, files) {
    const list = document.getElementById('cm-files-list');
    list.replaceChildren();
    if (!files || !files.length) {
      list.appendChild(el('div', { cls: 'empty', text: 'Файлов пока нет' }));
      return;
    }
    files.forEach(f => {
      const row = el('div', { cls: 'file-row' });
      const a = el('a', { text: f.filename });
      a.href = '/cabinet/api/pipeline/files/' + f.id + '/download';
      a.target = '_blank';
      row.appendChild(a);
      row.appendChild(el('span', { cls: 'file-meta', text: `${(f.size / 1024).toFixed(1)} KB` }));
      const del = el('button', { cls: 'btn btn-ghost btn-sm', text: '×' });
      del.onclick = async () => {
        if (!confirm('Удалить файл?')) return;
        const r = await fetch('/cabinet/api/pipeline/files/' + f.id, {
          method: 'DELETE', credentials: 'same-origin',
        });
        if (r.ok) {
          Toast.show('Удалено', 'positive');
          loadCardFull(cardId);
        }
      };
      row.appendChild(del);
      list.appendChild(row);
    });
  }

  function renderChecklist(cardId, items) {
    const list = document.getElementById('cm-checklist');
    list.replaceChildren();
    if (!items || !items.length) {
      list.appendChild(el('div', { cls: 'empty', text: 'Пунктов пока нет' }));
      return;
    }
    items.forEach(item => {
      const row = el('label', { cls: 'checklist-row' + (item.done ? ' done' : '') });
      const cb = el('input', { attrs: { type: 'checkbox' } });
      cb.checked = item.done;
      cb.onchange = async () => {
        await fetch('/cabinet/api/pipeline/checklist/' + item.id, {
          method: 'PATCH', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ done: cb.checked }),
        });
        loadCardFull(cardId);
      };
      row.appendChild(cb);
      row.appendChild(el('span', { cls: 'text', text: item.text }));
      const del = el('button', { cls: 'btn btn-ghost btn-sm', text: '×' });
      del.onclick = async () => {
        await fetch('/cabinet/api/pipeline/checklist/' + item.id, {
          method: 'DELETE', credentials: 'same-origin',
        });
        loadCardFull(cardId);
      };
      row.appendChild(del);
      list.appendChild(row);
    });
  }

  function renderRelations(cardId, relations) {
    const list = document.getElementById('cm-relations');
    list.replaceChildren();
    if (!relations || !relations.length) {
      list.appendChild(el('div', { cls: 'empty', text: 'Связей нет' }));
      return;
    }
    relations.forEach(rel => {
      const row = el('div', { cls: 'relation-row' });
      const a = el('a', { text: rel.related_name || rel.related_tender_number });
      a.href = '#';
      a.onclick = (e) => {
        e.preventDefault();
        openModal(rel.related_card_id);
      };
      row.appendChild(a);
      row.appendChild(el('span', { cls: 'file-meta', text: rel.related_stage }));
      const del = el('button', { cls: 'btn btn-ghost btn-sm', text: '×' });
      del.onclick = async () => {
        await fetch('/cabinet/api/pipeline/relations/' + rel.id, {
          method: 'DELETE', credentials: 'same-origin',
        });
        loadCardFull(cardId);
      };
      row.appendChild(del);
      list.appendChild(row);
    });
  }

  function renderHistory(history) {
    const wrap = document.getElementById('cm-history');
    wrap.replaceChildren();
    if (!history || !history.length) {
      wrap.appendChild(el('div', { cls: 'empty', text: 'История пуста' }));
      return;
    }
    history.forEach(h => {
      const row = el('div', { cls: 'history-item' });
      row.appendChild(el('div', { text: formatHistoryAction(h) }));
      row.appendChild(el('div', { cls: 'history-meta', text: `User ${h.user_id} · ${h.created_at || ''}` }));
      wrap.appendChild(row);
    });
  }

  function formatHistoryAction(h) {
    const map = {
      'created': 'создал карточку',
      'stage_changed': `перевёл «${h.payload.from || ''}» → «${h.payload.to || ''}»`,
      'assigned': `назначил ответственного (user ${h.payload.to || ''})`,
      'note_added': 'добавил заметку',
      'file_uploaded': `загрузил файл${h.payload.filename ? ' ' + h.payload.filename : ''}`,
      'file_deleted': 'удалил файл',
      'price_set': 'обновил цены',
      'won': 'отметил ПОБЕДУ',
      'lost': 'отметил ПРОИГРЫШ',
      'ai_enriched': 'запустил AI-анализ',
      'checklist_added': 'добавил пункт чек-листа',
      'checklist_done': 'отметил пункт выполненным',
      'imported_from_bitrix': 'импортирован из Bitrix24',
      'related_added': 'связал с другим тендером',
    };
    return map[h.action] || h.action;
  }

  /* ================ AI ================ */

  async function runAi(cardId) {
    const btn = document.getElementById('cm-ai-run');
    btn.disabled = true;
    btn.textContent = '⏳ Запускаю…';
    try {
      const r = await fetch('/cabinet/api/pipeline/cards/' + cardId + '/ai-enrich', {
        method: 'POST', credentials: 'same-origin',
      });
      if (r.status === 202) {
        Toast.show('AI-анализ запущен. Появится через 30-60 сек.', 'positive');
        // Poll каждые 3 секунды, max 30 раз (1.5 мин)
        let i = 0;
        const poll = setInterval(async () => {
          i++;
          if (i > 30) { clearInterval(poll); btn.disabled = false; btn.textContent = 'Запустить AI-анализ'; return; }
          const cr = await fetch('/cabinet/api/pipeline/cards/' + cardId, { credentials: 'same-origin' });
          if (!cr.ok) return;
          const cd = await cr.json();
          if (cd.card.ai_enriched_at) {
            clearInterval(poll);
            btn.disabled = false;
            btn.textContent = 'Запустить AI-анализ';
            Toast.show('✓ AI-анализ готов', 'positive');
            loadCardFull(cardId);
          }
        }, 3000);
      } else {
        const d = await r.json().catch(() => ({}));
        Toast.show(d.error || 'Не удалось запустить', 'alert');
        btn.disabled = false;
        btn.textContent = 'Запустить AI-анализ';
      }
    } catch (e) {
      Toast.show('Ошибка', 'alert');
      btn.disabled = false;
      btn.textContent = 'Запустить AI-анализ';
    }
  }

  // Включить fullscreen-режим (sidebar collapsed, no rightrail)
  document.body.classList.add('pipeline-mode');

  initSortable();
  initManualCreate();
})();
