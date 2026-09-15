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

  function resolveUserName(userId) {
    if (userId == null) return 'Система';
    if (userId === currentUserId) return 'Я';
    const m = teamMembers.find(x => x.user_id === userId);
    return (m && m.display_name) || ('User ' + userId);
  }

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
      const cnt = col.querySelectorAll('.kb-card:not([style*="display: none"])').length;
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

  async function setResult(cardId, result, reason) {
    try {
      const r = await fetch('/cabinet/api/pipeline/cards/' + cardId + '/result', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ result, reason: reason || null }),
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
  const resultReason = document.getElementById('result-modal-reason');
  let pendingResultCardId = null;

  function openResultModal(cardId, tenderName) {
    pendingResultCardId = cardId;
    if (resultTender) resultTender.textContent = tenderName;
    if (resultReason) resultReason.value = '';
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
    if (pendingResultCardId) { setResult(pendingResultCardId, 'won', resultReason?.value); closeResultModal(); }
  });
  if (lostBtn) lostBtn.addEventListener('click', () => {
    if (pendingResultCardId) { setResult(pendingResultCardId, 'lost', resultReason?.value); closeResultModal(); }
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

  /* ================ BITRIX IMPORT ================ */

  function initBitrixImport() {
    const btn = document.getElementById('btn-bitrix-import');
    if (!btn) return;
    btn.addEventListener('click', async () => {
      if (!confirm('Перенести все сделки из Bitrix24 в pipeline?\n\n' +
                   'Уже импортированные карточки будут пропущены.')) return;
      const orig = btn.textContent;
      btn.disabled = true;
      btn.textContent = '⏳ Импорт…';
      try {
        const r = await fetch('/cabinet/api/pipeline/bitrix-import', {
          method: 'POST', credentials: 'same-origin',
        });
        const d = await r.json().catch(() => ({}));
        if (r.ok && d.ok) {
          Toast.show(`✓ Импортировано: ${d.imported}, пропущено: ${d.skipped}` +
                     (d.errors ? `, ошибок: ${d.errors}` : ''), 'positive');
          if (d.imported > 0) setTimeout(() => window.location.reload(), 1200);
        } else {
          Toast.show(d.error || 'Ошибка импорта', 'alert');
        }
      } catch (e) {
        Toast.show('Сетевая ошибка', 'alert');
      } finally {
        btn.disabled = false;
        btn.textContent = orig;
      }
    });
  }

  function initBitrixPull() {
    const btn = document.getElementById('btn-bitrix-pull');
    if (!btn) return;
    btn.addEventListener('click', async () => {
      const orig = btn.textContent;
      btn.disabled = true;
      btn.textContent = '⏳ Синхронизирую…';
      try {
        const r = await fetch('/cabinet/api/pipeline/bitrix-pull', {
          method: 'POST', credentials: 'same-origin',
        });
        const d = await r.json().catch(() => ({}));
        if (r.ok && d.ok) {
          if (d.updated > 0) {
            Toast.show(`✓ Обновлено карточек: ${d.updated} (проверено ${d.checked})`, 'positive');
            setTimeout(() => window.location.reload(), 1000);
          } else {
            Toast.show(`Изменений нет (проверено ${d.checked})`, 'positive');
          }
        } else {
          Toast.show(d.error || 'Ошибка синхронизации', 'alert');
        }
      } catch (e) {
        Toast.show('Сетевая ошибка', 'alert');
      } finally {
        btn.disabled = false;
        btn.textContent = orig;
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
    // try/catch обязателен: при обрыве соединения fetch БРОСАЕТ исключение
    // (а не возвращает !r.ok), renderModal не вызывается, и в модалке
    // остаются данные прошлой карточки со ссылкой href="#" из разметки —
    // клик по ней открывал новую вкладку с самим кабинетом вместо карточки
    // закупки. Ловим ошибку, явно сообщаем и закрываем модалку, чтобы не
    // показывать заведомо нерабочее состояние.
    try {
      const r = await fetch('/cabinet/api/pipeline/cards/' + cardId + '/full', {
        credentials: 'same-origin',
      });
      if (!r.ok) throw new Error('Сервер вернул ' + r.status);
      const data = await r.json();
      renderModal(data);
    } catch (e) {
      Toast.show('Не удалось загрузить карточку — проверьте соединение', 'alert');
      closeModal();
    }
  }

  // Ссылка на карточку закупки. НИКОГДА не возвращаем '#': раньше при
  // отсутствующем data.url href становился '#', и клик по ссылке
  // (target="_blank") открывал новую вкладку с самим кабинетом вместо
  // тендера. Номер тендера у карточки есть всегда, поэтому строим URL из
  // него — ровно как на странице «Результаты».
  function tenderUrl(c) {
    if (c.data && c.data.url) return c.data.url;
    const num = c.tender_number || '';
    if (num.startsWith('MOS-')) {
      return 'https://zakupki.mos.ru/auction/' + num.slice(4);
    }
    return 'https://zakupki.gov.ru/epz/order/notice/ea20/view/common-info.html?regNumber=' + num;
  }

  // Источник тендера — подпись у ссылки должна соответствовать тому, куда
  // она ведёт. Раньше всегда писали «zakupki.gov.ru», в том числе для
  // московских КС, которые открываются на Портале поставщиков.
  function tenderSourceLabel(c) {
    const num = c.tender_number || '';
    if (num.startsWith('MOS-')) return 'Портал поставщиков';
    return 'zakupki.gov.ru';
  }

  function renderModal(data) {
    const c = data.card;

    document.getElementById('cm-title').textContent = (c.data && c.data.name) || ('Тендер ' + c.tender_number);
    const linkEl = document.getElementById('cm-zakupki-link');
    linkEl.href = tenderUrl(c);
    linkEl.textContent = '\u2197 Ссылка на тендер \u00B7 ' + tenderSourceLabel(c);

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

    // Prices: показываем с разделителями тысяч, парсим терпимо к пробелам/запятым
    const fmtMoney = v => (v == null) ? '' : Math.round(v).toLocaleString('ru-RU');
    const parseMoney = v => {
      if (v == null || v === '') return null;
      const cleaned = String(v).replace(/[^\d.,-]/g, '').replace(/\s/g, '').replace(',', '.');
      const n = parseFloat(cleaned);
      return isNaN(n) ? null : n;
    };
    document.getElementById('cm-purchase').value = fmtMoney(c.purchase_price);
    document.getElementById('cm-sale').value = fmtMoney(c.sale_price);
    document.getElementById('cm-logistics').value = fmtMoney(c.logistics_cost);
    document.getElementById('cm-extra').value = fmtMoney(c.extra_costs);

    // НМЦК — справочно, не редактируется
    const nmckEl = document.getElementById('cm-nmck');
    const nmck = c.data && c.data.price_max;
    nmckEl.textContent = nmck ? fmtMoney(nmck) + ' \u20BD' : '—';

    ['cm-purchase', 'cm-sale', 'cm-logistics', 'cm-extra'].forEach(id => {
      const inp = document.getElementById(id);
      // Live re-format на blur для красоты
      inp.onblur = () => {
        const n = parseMoney(inp.value);
        inp.value = n == null ? '' : fmtMoney(n);
      };
      inp.onchange = async () => {
        await fetch('/cabinet/api/pipeline/cards/' + c.id + '/prices', {
          method: 'POST', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            purchase_price: parseMoney(document.getElementById('cm-purchase').value),
            sale_price: parseMoney(document.getElementById('cm-sale').value),
            logistics_cost: parseMoney(document.getElementById('cm-logistics').value),
            extra_costs: parseMoney(document.getElementById('cm-extra').value),
          }),
        });
        Toast.show('\u2713 Сохранено', 'positive');
        loadCardFull(c.id);
      };
    });

    // Разбивка расчёта + итоговая чистая прибыль
    const mEl = document.getElementById('cm-margin');
    const bEl = document.getElementById('cm-breakdown');
    const m = data.margin;
    if (m) {
      const money = (v) => Math.round(v || 0).toLocaleString('ru-RU') + ' \u20BD';
      const rows = [
        ['Наша цена', money(m.sale), ''],
        ['Закупочная', '\u2212 ' + money(m.purchase), 'minus'],
      ];
      if (m.logistics_cost) rows.push(['Логистика', '\u2212 ' + money(m.logistics_cost), 'minus']);
      if (m.extra_costs) rows.push(['Доп. расходы', '\u2212 ' + money(m.extra_costs), 'minus']);
      rows.push([`Налог ${m.tax_rate}%`, '\u2212 ' + money(m.tax), 'minus']);

      bEl.replaceChildren();
      rows.forEach(([label, value, cls]) => {
        const row = el('div', { cls: 'calc-row' });
        row.appendChild(el('span', { cls: 'calc-label', text: label }));
        row.appendChild(el('span', { cls: 'calc-value ' + (cls || ''), text: value }));
        bEl.appendChild(row);
      });
      // Снижение от НМЦК — сколько уже уступили от начальной цены
      if (m.discount_abs != null) {
        const row = el('div', { cls: 'calc-row calc-row-note' });
        row.appendChild(el('span', { cls: 'calc-label', text: 'Снижение от НМЦК' }));
        row.appendChild(el('span', {
          cls: 'calc-value',
          text: money(m.discount_abs) + ' (' + m.discount_pct.toFixed(1) + '%)',
        }));
        bEl.appendChild(row);
      }
      bEl.hidden = false;

      mEl.hidden = false;
      mEl.className = 'margin-box ' + m.color;
      mEl.textContent = `Чистая прибыль: ${Math.round(m.abs).toLocaleString('ru-RU')} \u20BD (${m.pct.toFixed(1)}%)`;
    } else {
      mEl.hidden = true;
      bEl.hidden = true;
    }

    // Quotes (предложения поставщиков)
    renderQuotes(c.id, data.quotes);
    setupQuoteForm(c.id);

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
    const requestBtn = document.getElementById('cm-btn-request');
    if (requestBtn) {
      requestBtn.hidden = c.stage !== 'RFQ';
      requestBtn.onclick = () => {
        if (window.Cabinet && window.Cabinet.SupplierRequest) {
          const tenderName = (c.data && c.data.name) || ('Тендер ' + c.tender_number);
          window.Cabinet.SupplierRequest.open(c.id, tenderName);
        } else {
          Toast.show('Supplier UI не загружен (обнови страницу)', 'alert');
        }
      };
    }

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
      row.appendChild(el('div', { cls: 'note-meta', text: `${resolveUserName(n.user_id)} · ${n.created_at || ''}` }));
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

  function renderQuotes(cardId, quotes) {
    const list = document.getElementById('cm-quotes-list');
    list.replaceChildren();
    if (!quotes || !quotes.length) {
      list.appendChild(el('div', { cls: 'empty', text: 'Позиций пока нет' }));
      return;
    }
    const fmt = v => v != null ? Math.round(v).toLocaleString('ru-RU') + ' ₽' : '—';
    let sum = 0;
    quotes.forEach(q => {
      if (q.line_total != null) sum += q.line_total;
      const row = el('div', { cls: 'note-item' });
      row.appendChild(el('div', {
        text: `${q.product_name || '—'} · ${q.supplier_name} — ${q.quantity} × ${fmt(q.unit_price)} = ${fmt(q.line_total)}`,
      }));
      if (q.notes) row.appendChild(el('div', { cls: 'note-meta', text: q.notes }));
      row.appendChild(el('div', { cls: 'note-meta', text: `${resolveUserName(q.created_by)} · ${q.created_at || ''}` }));
      const edit = el('button', { cls: 'btn btn-ghost btn-sm', text: 'Изменить' });
      edit.onclick = async () => {
        const priceStr = prompt('Цена за единицу, ₽:', String(Math.round(q.unit_price)));
        if (priceStr === null) return;
        const price = parseFloat(String(priceStr).replace(/[^\d.,-]/g, '').replace(',', '.'));
        if (isNaN(price)) { Toast.show('Некорректная цена', 'alert'); return; }
        const qtyStr = prompt('Количество:', String(q.quantity));
        if (qtyStr === null) return;
        const qty = parseFloat(String(qtyStr).replace(/[^\d.,-]/g, '').replace(',', '.'));
        if (isNaN(qty) || qty <= 0) { Toast.show('Некорректное количество', 'alert'); return; }
        const r = await fetch('/cabinet/api/pipeline/quotes/' + q.id, {
          method: 'PUT', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ unit_price: price, quantity: qty }),
        });
        if (r.ok) { Toast.show('✓ Обновлено', 'positive'); loadCardFull(cardId); }
        else Toast.show('Ошибка', 'alert');
      };
      const del = el('button', { cls: 'btn btn-ghost btn-sm', text: 'Удалить' });
      del.onclick = async () => {
        const r = await fetch('/cabinet/api/pipeline/quotes/' + q.id, {
          method: 'DELETE', credentials: 'same-origin',
        });
        if (r.ok) loadCardFull(cardId);
      };
      row.appendChild(edit);
      row.appendChild(del);
      list.appendChild(row);
    });
    if (quotes.length > 1) {
      list.appendChild(el('div', { cls: 'note-meta', text: `Итого по всем позициям: ${fmt(sum)}` }));
    }
  }

  async function setupQuoteForm(cardId) {
    const sel = document.getElementById('cm-quote-supplier');
    const newNameInp = document.getElementById('cm-quote-new-supplier-name');
    const nameInp = document.getElementById('cm-quote-product-name');
    const priceInp = document.getElementById('cm-quote-price');
    const qtyInp = document.getElementById('cm-quote-qty');
    const options = document.getElementById('cm-quote-product-options');
    const totalEl = document.getElementById('cm-quote-line-total');

    const parseMoney = v => {
      if (v == null || v === '') return null;
      const n = parseFloat(String(v).replace(/[^\d.,-]/g, '').replace(',', '.'));
      return isNaN(n) ? null : n;
    };

    let catalog = [];

    async function loadCatalogFor(supplierId) {
      options.replaceChildren();
      catalog = [];
      if (!supplierId || supplierId === '__new__') return;
      const r = await fetch('/cabinet/api/suppliers/' + supplierId + '/products', { credentials: 'same-origin' });
      const d = r.ok ? await r.json() : { products: [] };
      catalog = d.products || [];
      catalog.forEach(p => {
        const opt = document.createElement('option');
        opt.value = p.name;
        options.appendChild(opt);
      });
    }

    function updateTotal() {
      const price = parseMoney(priceInp.value);
      const qty = parseMoney(qtyInp.value) || 1;
      if (price == null) { totalEl.hidden = true; return; }
      totalEl.hidden = false;
      totalEl.className = 'margin-box';
      totalEl.textContent = `Итого: ${Math.round(price * qty).toLocaleString('ru-RU')} ₽`;
    }

    const r = await fetch('/cabinet/api/suppliers', { credentials: 'same-origin' });
    const d = r.ok ? await r.json() : { suppliers: [] };
    sel.replaceChildren();
    (d.suppliers || []).forEach(s => {
      const opt = document.createElement('option');
      opt.value = s.id;
      opt.textContent = s.name;
      sel.appendChild(opt);
    });
    const newOpt = document.createElement('option');
    newOpt.value = '__new__';
    newOpt.textContent = '+ Новый поставщик…';
    sel.appendChild(newOpt);

    newNameInp.hidden = sel.value !== '__new__';
    await loadCatalogFor(sel.value);
    sel.onchange = () => {
      newNameInp.hidden = sel.value !== '__new__';
      loadCatalogFor(sel.value);
    };

    nameInp.oninput = () => {
      const match = catalog.find(p => p.name.toLowerCase() === nameInp.value.trim().toLowerCase());
      if (match) {
        priceInp.value = Math.round(match.unit_price).toLocaleString('ru-RU');
        updateTotal();
      }
    };
    priceInp.oninput = updateTotal;
    qtyInp.oninput = updateTotal;
    updateTotal();

    document.getElementById('cm-quote-add').onclick = async () => {
      const notesInp = document.getElementById('cm-quote-notes');
      const productName = nameInp.value.trim();
      const price = parseMoney(priceInp.value);
      const qty = parseMoney(qtyInp.value) || 1;
      if (!productName) { Toast.show('Укажите наименование позиции', 'alert'); return; }
      if (price == null) { Toast.show('Укажите цену за единицу', 'alert'); return; }

      const body = {
        product_name: productName, unit_price: price, quantity: qty,
        notes: notesInp.value.trim(),
      };
      if (sel.value === '__new__') {
        const name = newNameInp.value.trim();
        if (!name) { Toast.show('Укажите название поставщика', 'alert'); return; }
        body.new_supplier_name = name;
      } else {
        body.supplier_id = parseInt(sel.value, 10);
      }

      const resp = await fetch('/cabinet/api/pipeline/cards/' + cardId + '/quotes', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const rd = await resp.json().catch(() => ({}));
      if (resp.ok && rd.ok) {
        nameInp.value = ''; priceInp.value = ''; qtyInp.value = ''; notesInp.value = ''; newNameInp.value = '';
        totalEl.hidden = true;
        Toast.show('✓ Позиция добавлена', 'positive');
        loadCardFull(cardId);
      } else {
        Toast.show(rd.error || 'Ошибка', 'alert');
      }
    };
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
      row.appendChild(el('div', { cls: 'history-meta', text: `${resolveUserName(h.user_id)} · ${h.created_at || ''}` }));
      wrap.appendChild(row);
    });
  }

  function formatHistoryAction(h) {
    const map = {
      'created': 'создал карточку',
      'stage_changed': `перевёл «${h.payload.from || ''}» → «${h.payload.to || ''}»`
        + (h.payload.reason === 'deadline_expired' ? ' (истёк срок подачи)' : ''),
      'quote_added': `внёс позицию «${h.payload.product_name || ''}» от ${h.payload.supplier_name || 'поставщика'}`
        + (h.payload.unit_price != null ? `: ${h.payload.quantity || 1} × ${Math.round(h.payload.unit_price).toLocaleString('ru-RU')} ₽` : ''),
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

  /* ================ FILTERS ================ */

  function initFilters() {
    const searchInput = document.getElementById('pipeline-search');
    const assigneeSelect = document.getElementById('pipeline-filter-assignee');
    if (!searchInput) return;

    function applyFilters() {
      const q = (searchInput.value || '').toLowerCase().trim();
      const assigneeId = assigneeSelect ? assigneeSelect.value : '';
      document.querySelectorAll('.kb-card').forEach(card => {
        const title = (card.querySelector('.kb-card-title')?.textContent || '').toLowerCase();
        const tender = (card.dataset.tender || '').toLowerCase();
        const matchText = !q || title.includes(q) || tender.includes(q);
        const matchAssignee = !assigneeId || card.dataset.assignee === assigneeId;
        card.style.display = (matchText && matchAssignee) ? '' : 'none';
      });
      updateCounts();
    }

    searchInput.addEventListener('input', applyFilters);
    if (assigneeSelect) assigneeSelect.addEventListener('change', applyFilters);
  }

  // Pipeline-mode: sidebar collapsed по умолчанию, но юзер может раскрыть.
  // Состояние сохраняется в localStorage между переходами на pipeline.
  const SIDEBAR_KEY = 'pipeline_sidebar_collapsed';
  function applySidebarState() {
    const collapsed = localStorage.getItem(SIDEBAR_KEY) !== 'false';
    document.body.classList.toggle('pipeline-mode', collapsed);
    const btn = document.getElementById('btn-toggle-sidebar');
    if (btn) btn.textContent = collapsed ? '☰ Меню' : '← Свернуть';
  }
  applySidebarState();

  const toggleBtn = document.getElementById('btn-toggle-sidebar');
  if (toggleBtn) {
    toggleBtn.addEventListener('click', () => {
      const wasCollapsed = document.body.classList.contains('pipeline-mode');
      localStorage.setItem(SIDEBAR_KEY, wasCollapsed ? 'false' : 'true');
      applySidebarState();
    });
  }

  initSortable();
  initManualCreate();
  initBitrixImport();
  initBitrixPull();
  initFilters();
})();
