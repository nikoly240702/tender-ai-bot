/* Cabinet — Holodilnik supplier search modal.
   Async + polling. Никакого innerHTML — только createElement / textContent. */
(function () {
  const Toast = (window.Cabinet && window.Cabinet.Toast) || { show: console.log };

  const modal = document.getElementById('hl-modal');
  if (!modal) return;

  const closeBtn = document.getElementById('hl-modal-close');
  const subtitle = document.getElementById('hl-subtitle');
  const loadingEl = document.getElementById('hl-loading');
  const loadingStep = document.getElementById('hl-loading-step');
  const loadingProgress = document.getElementById('hl-loading-progress');
  const errorEl = document.getElementById('hl-error');
  const errorMsg = document.getElementById('hl-error-msg');
  const retryBtn = document.getElementById('hl-retry-btn');
  const resultsEl = document.getElementById('hl-results');
  const footerEl = document.getElementById('hl-footer');
  const summaryEl = document.getElementById('hl-summary');
  const rerunBtn = document.getElementById('hl-rerun-btn');
  const copyBtn = document.getElementById('hl-copy-skus');

  let currentCardId = null;
  let currentPollInterval = null;

  function setState(state) {
    loadingEl.hidden = state !== 'loading';
    errorEl.hidden = state !== 'error';
    resultsEl.hidden = state !== 'results';
    footerEl.hidden = state !== 'results';
  }

  function openModal(cardId, tenderName) {
    currentCardId = cardId;
    subtitle.textContent = tenderName || ('Карточка #' + cardId);
    modal.hidden = false;
    setState('loading');
    loadingStep.textContent = 'Запускаю поиск…';
    loadingProgress.textContent = '';
    startSearch({ force: false });
  }

  function closeModal() {
    modal.hidden = true;
    if (currentPollInterval) {
      clearInterval(currentPollInterval);
      currentPollInterval = null;
    }
    currentCardId = null;
  }

  closeBtn.addEventListener('click', closeModal);
  modal.addEventListener('click', (e) => { if (e.target === modal) closeModal(); });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !modal.hidden) closeModal();
  });

  async function startSearch({ force }) {
    setState('loading');
    loadingStep.textContent = force ? 'Запускаю поиск заново…' : 'Запускаю поиск…';
    loadingProgress.textContent = '';
    try {
      const r = await fetch(
        '/cabinet/api/pipeline/cards/' + currentCardId + '/holodilnik-search',
        {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ force }),
        },
      );
      const data = await r.json().catch(() => ({}));
      if (r.status === 200 && data.cached) {
        renderResults(data.results);
        return;
      }
      if (r.status === 202 && data.task_id) {
        startPolling(data.task_id);
        return;
      }
      showError(data.error || ('HTTP ' + r.status));
    } catch (e) {
      showError(e.message || 'Ошибка соединения');
    }
  }

  function startPolling(taskId) {
    if (currentPollInterval) clearInterval(currentPollInterval);
    let attempts = 0;
    const maxAttempts = 150; // ~5 минут при 2с
    currentPollInterval = setInterval(async () => {
      attempts += 1;
      if (attempts > maxAttempts) {
        clearInterval(currentPollInterval);
        currentPollInterval = null;
        showError('Поиск занял больше 5 минут. Попробуйте позже.');
        return;
      }
      try {
        const r = await fetch(
          '/cabinet/api/pipeline/cards/' + currentCardId
          + '/holodilnik-status?task_id=' + encodeURIComponent(taskId),
          { credentials: 'same-origin' },
        );
        if (!r.ok) return;
        const data = await r.json();
        if (data.progress) loadingProgress.textContent = data.progress;
        if (data.current_step) loadingStep.textContent = data.current_step;

        if (data.status === 'done') {
          clearInterval(currentPollInterval);
          currentPollInterval = null;
          renderResults(data.results);
        } else if (data.status === 'error') {
          clearInterval(currentPollInterval);
          currentPollInterval = null;
          showError(data.error || 'Ошибка во время поиска');
        }
      } catch (e) {
        // ignore poll failures
      }
    }, 2000);
  }

  function showError(msg) {
    setState('error');
    errorMsg.textContent = msg || 'Неизвестная ошибка';
  }

  retryBtn.addEventListener('click', () => startSearch({ force: true }));
  rerunBtn.addEventListener('click', () => startSearch({ force: true }));

  copyBtn.addEventListener('click', async () => {
    const skus = collectSelectedSkus();
    if (!skus.length) {
      Toast.show('Не выбрано ни одной модели', 'alert');
      return;
    }
    try {
      await navigator.clipboard.writeText(skus.join('\n'));
      Toast.show('✓ Артикулы скопированы (' + skus.length + ')', 'positive');
    } catch (e) {
      window.prompt('Скопируйте артикулы:', skus.join('\n'));
    }
  });

  function collectSelectedSkus() {
    return Array.from(resultsEl.querySelectorAll('.hl-item.selected'))
      .map(el => el.dataset.sku)
      .filter(Boolean);
  }

  function renderResults(blob) {
    setState('results');
    resultsEl.replaceChildren();

    const positions = (blob && blob.positions) || [];
    if (!positions.length) {
      const empty = document.createElement('div');
      empty.className = 'hl-error';
      empty.textContent = 'Не нашли подходящих моделей. Попробуйте обновить ТЗ или нажмите 🔄 Заново.';
      resultsEl.appendChild(empty);
      footerEl.hidden = true;
      return;
    }

    positions.forEach((pos, idx) => {
      const section = document.createElement('div');
      section.className = 'hl-position';

      const head = document.createElement('div');
      head.className = 'hl-position-head';
      const name = document.createElement('span');
      name.className = 'hl-position-name';
      name.textContent = pos.tz_text || ('Позиция ' + (idx + 1));
      head.appendChild(name);
      const meta = document.createElement('span');
      meta.className = 'hl-position-meta';
      const cnt = (pos.results || []).length;
      meta.textContent = cnt + ' моделей' + (pos.ai_query ? ' · «' + pos.ai_query + '»' : '');
      head.appendChild(meta);
      section.appendChild(head);

      const grid = document.createElement('div');
      grid.className = 'hl-grid';
      (pos.results || []).forEach(item => {
        grid.appendChild(buildItemCard(item, idx));
      });
      section.appendChild(grid);

      if (!cnt) {
        const empty = document.createElement('div');
        empty.style.cssText = 'padding: 12px; font-size: 12px; color: var(--muted);';
        empty.textContent = 'Не нашли подходящих моделей по этой позиции.';
        section.appendChild(empty);
      }

      resultsEl.appendChild(section);
    });

    refreshSummary();
  }

  function buildItemCard(item, positionIdx) {
    const card = document.createElement('div');
    card.className = 'hl-item' + (item.selected ? ' selected' : '');
    card.dataset.sku = item.sku || '';
    card.dataset.positionIdx = String(positionIdx);

    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.className = 'hl-check';
    cb.checked = !!item.selected;
    cb.addEventListener('change', () => onToggleSelect(card, positionIdx, item.sku, cb.checked));
    card.appendChild(cb);

    const photo = document.createElement('a');
    photo.className = 'hl-photo';
    photo.href = item.url || '#';
    photo.target = '_blank';
    photo.rel = 'noopener';
    if (item.img && /^https?:\/\//.test(item.img)) {
      const img = document.createElement('img');
      img.src = item.img;
      img.alt = '';
      img.loading = 'lazy';
      photo.appendChild(img);
    } else {
      photo.textContent = '📦';
    }
    card.appendChild(photo);

    const nameEl = document.createElement('div');
    nameEl.className = 'hl-name';
    nameEl.textContent = item.name || '';
    card.appendChild(nameEl);

    const skuEl = document.createElement('div');
    skuEl.className = 'hl-sku';
    skuEl.textContent = '№ ' + (item.sku || '');
    card.appendChild(skuEl);

    const priceEl = document.createElement('div');
    priceEl.className = 'hl-price';
    priceEl.textContent = (item.price ? Number(item.price).toLocaleString('ru-RU') + ' ₽' : '—');
    card.appendChild(priceEl);

    if (item.in_stock === false) {
      const oos = document.createElement('div');
      oos.className = 'hl-out-of-stock';
      oos.textContent = 'Нет в наличии';
      card.appendChild(oos);
    }

    return card;
  }

  async function onToggleSelect(cardEl, positionIdx, sku, selected) {
    cardEl.classList.toggle('selected', selected);
    refreshSummary();
    try {
      await fetch(
        '/cabinet/api/pipeline/cards/' + currentCardId + '/holodilnik-toggle',
        {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ position_idx: positionIdx, sku, selected }),
        },
      );
    } catch (e) {
      // визуально уже отражено
    }
  }

  function refreshSummary() {
    const selected = resultsEl.querySelectorAll('.hl-item.selected');
    let totalPrice = 0;
    selected.forEach(el => {
      const priceEl = el.querySelector('.hl-price');
      const txt = priceEl ? priceEl.textContent.replace(/[^\d]/g, '') : '';
      if (txt) totalPrice += parseInt(txt, 10);
    });
    summaryEl.textContent = 'Выбрано: ' + selected.length + ' · ' + totalPrice.toLocaleString('ru-RU') + ' ₽';
  }

  // Public API
  window.Cabinet = window.Cabinet || {};
  window.Cabinet.Holodilnik = { open: openModal };
})();
