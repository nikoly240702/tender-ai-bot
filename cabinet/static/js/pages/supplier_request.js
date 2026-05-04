/* Cabinet — Supplier request modal: оценка по своему каталогу + clean ТЗ. */
(function () {
  const Toast = (window.Cabinet && window.Cabinet.Toast) || { show: console.log };

  const modal = document.getElementById('sr-modal');
  if (!modal) return;

  const closeBtn = document.getElementById('sr-modal-close');
  const subtitle = document.getElementById('sr-subtitle');
  const tabs = modal.querySelectorAll('.modal-tab');
  const panes = modal.querySelectorAll('.tab-pane');

  // Estimate elements
  const estLoading = document.getElementById('sr-est-loading');
  const estError = document.getElementById('sr-est-error');
  const estErrorMsg = document.getElementById('sr-est-error-msg');
  const estResults = document.getElementById('sr-est-results');
  const estSummary = document.getElementById('sr-est-summary');
  const estTable = document.getElementById('sr-est-table');
  const estUnmatched = document.getElementById('sr-est-unmatched');
  const estRetry = document.getElementById('sr-est-retry');

  // Clean elements
  const cleanLoading = document.getElementById('sr-clean-loading');
  const cleanError = document.getElementById('sr-clean-error');
  const cleanErrorMsg = document.getElementById('sr-clean-error-msg');
  const cleanResults = document.getElementById('sr-clean-results');
  const cleanMeta = document.getElementById('sr-clean-meta');
  const cleanText = document.getElementById('sr-clean-text');
  const cleanCopy = document.getElementById('sr-clean-copy');
  const cleanRerun = document.getElementById('sr-clean-rerun');
  const cleanRetry = document.getElementById('sr-clean-retry');

  let currentCardId = null;
  let estLoaded = false;
  let cleanLoaded = false;

  function open(cardId, tenderName) {
    currentCardId = cardId;
    estLoaded = false;
    cleanLoaded = false;
    subtitle.textContent = tenderName || ('Карточка #' + cardId);
    modal.hidden = false;
    activateTab('estimate');
    loadEstimate();
  }

  function close() {
    modal.hidden = true;
    currentCardId = null;
  }

  closeBtn.addEventListener('click', close);
  modal.addEventListener('click', (e) => { if (e.target === modal) close(); });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !modal.hidden) close();
  });

  function activateTab(tabName) {
    tabs.forEach(t => t.classList.toggle('active', t.dataset.srTab === tabName));
    panes.forEach(p => p.classList.toggle('active', p.dataset.srTab === tabName));
    if (tabName === 'estimate' && !estLoaded) loadEstimate();
    if (tabName === 'clean' && !cleanLoaded) loadClean();
  }
  tabs.forEach(t => t.addEventListener('click', () => activateTab(t.dataset.srTab)));

  // ============== ESTIMATE ==============

  function setEstState(state) {
    estLoading.hidden = state !== 'loading';
    estError.hidden = state !== 'error';
    estResults.hidden = state !== 'results';
  }

  async function loadEstimate() {
    setEstState('loading');
    try {
      const r = await fetch(
        '/cabinet/api/pipeline/cards/' + currentCardId + '/estimate-own',
        { method: 'POST', credentials: 'same-origin' },
      );
      const data = await r.json().catch(() => ({}));
      if (!r.ok || !data.ok) {
        showEstError(data.error || ('HTTP ' + r.status));
        return;
      }
      estLoaded = true;
      renderEstimate(data);
    } catch (e) {
      showEstError(e.message || 'Ошибка соединения');
    }
  }

  function showEstError(msg) {
    setEstState('error');
    estErrorMsg.textContent = msg;
  }
  estRetry.addEventListener('click', loadEstimate);

  function renderEstimate(data) {
    setEstState('results');
    estSummary.replaceChildren();
    estTable.replaceChildren();
    estUnmatched.replaceChildren();

    // Summary
    const totalDiv = document.createElement('div');
    totalDiv.className = 'sr-total';
    totalDiv.textContent = 'Оценка стоимости: ' + Math.round(data.total).toLocaleString('ru-RU') + ' ₽';
    estSummary.appendChild(totalDiv);

    const noteDiv = document.createElement('div');
    noteDiv.className = 'sr-total-note';
    noteDiv.textContent = `${data.matches.length} позиций сопоставлено, ${data.unmatched.length} без матча. Каталог: ${data.catalogue_size} товаров.`;
    estSummary.appendChild(noteDiv);

    // Source banner
    const srcDiv = document.createElement('div');
    srcDiv.className = 'sr-source-banner sr-source-' + (data.tz_source || 'unknown');
    srcDiv.textContent = formatSourceLabel(data.tz_source, data.tz_files_used, data.tz_note);
    estSummary.appendChild(srcDiv);

    // Table
    const thead = document.createElement('thead');
    const trh = document.createElement('tr');
    ['Позиция ТЗ', 'Кол-во', 'Наш товар', 'Цена', 'Сумма', 'Уверенность'].forEach(h => {
      const th = document.createElement('th');
      th.textContent = h;
      trh.appendChild(th);
    });
    thead.appendChild(trh);
    estTable.appendChild(thead);

    const tbody = document.createElement('tbody');
    data.matches.forEach(m => {
      const tr = document.createElement('tr');
      tr.className = m.item ? '' : 'sr-row-no-match';

      const tdTz = document.createElement('td');
      tdTz.className = 'sr-cell-tz';
      tdTz.textContent = m.tz_position || '—';
      tr.appendChild(tdTz);

      const tdQty = document.createElement('td');
      tdQty.className = 'sr-cell-num';
      tdQty.textContent = m.tz_quantity || '—';
      tr.appendChild(tdQty);

      const tdItem = document.createElement('td');
      if (m.item) {
        const name = document.createElement('div');
        name.className = 'sr-item-name';
        name.textContent = m.item.name;
        tdItem.appendChild(name);
        if (m.item.params) {
          const params = document.createElement('div');
          params.className = 'sr-item-params';
          params.textContent = m.item.params;
          tdItem.appendChild(params);
        }
        if (m.rationale) {
          const rat = document.createElement('div');
          rat.className = 'sr-item-rationale';
          rat.textContent = '⤷ ' + m.rationale;
          tdItem.appendChild(rat);
        }
      } else {
        tdItem.className = 'sr-cell-empty';
        tdItem.textContent = '— нет в каталоге —';
      }
      tr.appendChild(tdItem);

      const tdPrice = document.createElement('td');
      tdPrice.className = 'sr-cell-num';
      tdPrice.textContent = m.item && m.item.price ?
        Math.round(m.item.price).toLocaleString('ru-RU') + ' ₽ / ' + (m.item.price_unit || 'шт') :
        '—';
      tr.appendChild(tdPrice);

      const tdSum = document.createElement('td');
      tdSum.className = 'sr-cell-num sr-cell-sum';
      tdSum.textContent = m.line_total != null ?
        Math.round(m.line_total).toLocaleString('ru-RU') + ' ₽' :
        '—';
      tr.appendChild(tdSum);

      const tdConf = document.createElement('td');
      tdConf.className = 'sr-cell-num sr-conf-' + (m.confidence || 'low');
      tdConf.textContent = ({ high: '✓ высокая', medium: '~ средняя', low: '? низкая' })[m.confidence] || '—';
      tr.appendChild(tdConf);

      tbody.appendChild(tr);
    });
    estTable.appendChild(tbody);

    // Unmatched
    if (data.unmatched && data.unmatched.length) {
      const head = document.createElement('h3');
      head.className = 'sr-unmatched-head';
      head.textContent = 'Не найдено в нашем каталоге:';
      estUnmatched.appendChild(head);
      const ul = document.createElement('ul');
      ul.className = 'sr-unmatched-list';
      data.unmatched.forEach(u => {
        const li = document.createElement('li');
        li.textContent = u;
        ul.appendChild(li);
      });
      estUnmatched.appendChild(ul);

      const hint = document.createElement('div');
      hint.className = 'sr-unmatched-hint';
      hint.textContent = '↗ По этим позициям перейдите на вкладку «Запрос внешним поставщикам».';
      estUnmatched.appendChild(hint);
    }
  }

  // ============== CLEAN ==============

  function setCleanState(state) {
    cleanLoading.hidden = state !== 'loading';
    cleanError.hidden = state !== 'error';
    cleanResults.hidden = state !== 'results';
  }

  async function loadClean() {
    setCleanState('loading');
    try {
      const r = await fetch(
        '/cabinet/api/pipeline/cards/' + currentCardId + '/clean-request',
        { method: 'POST', credentials: 'same-origin' },
      );
      const data = await r.json().catch(() => ({}));
      if (!r.ok || !data.ok) {
        showCleanError(data.error || ('HTTP ' + r.status));
        return;
      }
      cleanLoaded = true;
      renderClean(data);
    } catch (e) {
      showCleanError(e.message || 'Ошибка соединения');
    }
  }

  function showCleanError(msg) {
    setCleanState('error');
    cleanErrorMsg.textContent = msg;
  }
  cleanRetry.addEventListener('click', loadClean);
  cleanRerun.addEventListener('click', loadClean);

  function renderClean(data) {
    setCleanState('results');
    cleanMeta.replaceChildren();
    const meta = document.createElement('div');
    meta.className = 'sr-clean-meta-row';
    meta.textContent = (data.tender_name || '') + ' · № ' + (data.tender_number || '');
    cleanMeta.appendChild(meta);

    const srcDiv = document.createElement('div');
    srcDiv.className = 'sr-source-banner sr-source-' + (data.tz_source || 'unknown');
    srcDiv.textContent = formatSourceLabel(data.tz_source, data.tz_files_used, data.tz_note);
    cleanMeta.appendChild(srcDiv);

    cleanText.value = data.letter_text || '';
  }

  function formatSourceLabel(source, filesUsed, note) {
    const filesCount = (filesUsed || []).length;
    if (source === 'card_files') {
      return `📎 Источник ТЗ: ${filesCount} файл(ов) из карточки`;
    }
    if (source === 'zakupki') {
      return `🌐 Источник ТЗ: документация скачана с zakupki.gov.ru (${filesCount} файлов)`;
    }
    if (source === 'cache') {
      return `💾 Источник ТЗ: кэш (последнее скачивание ≤7 дней)`;
    }
    if (source === 'fallback_summary') {
      return `⚠️ Полная документация недоступна — используем AI-summary как fallback. Загрузите ТЗ-файлы в карточку для точности.`;
    }
    if (source === 'name_only') {
      return `⚠️ Доступно только название тендера. Загрузите ТЗ-файлы в карточку (вкладка «Файлы»).`;
    }
    return note || `Источник ТЗ: ${source}`;
  }

  cleanCopy.addEventListener('click', async () => {
    const text = cleanText.value;
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      Toast.show('✓ Письмо скопировано', 'positive');
    } catch (e) {
      cleanText.select();
      Toast.show('Текст выделен — скопируйте Cmd/Ctrl+C', 'alert');
    }
  });

  // Public API
  window.Cabinet = window.Cabinet || {};
  window.Cabinet.SupplierRequest = { open };
})();
