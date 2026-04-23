/* Cabinet v3 — settings page.
   Загружает GET /cabinet/api/settings, применяет состояние к форме,
   сохраняет через POST /cabinet/api/settings.
*/
(function () {
  const { apiGet, apiPost, Toast } = window.Cabinet;

  function byId(id) { return document.getElementById(id); }

  async function load() {
    const s = await apiGet('/cabinet/api/settings');
    if (!s) return;

    byId('toggle-notifs').classList.toggle('on', !!s.notifications_enabled);
    byId('toggle-quiet').classList.toggle('on', !!s.quiet_hours_enabled);
    byId('quiet-start').value = s.quiet_hours_start != null ? s.quiet_hours_start : 22;
    byId('quiet-end').value = s.quiet_hours_end != null ? s.quiet_hours_end : 8;

    byId('tier-name').textContent = tierLabel(s.subscription_tier);
    const trialTxt = s.trial_expires_at ? 'триал до ' + s.trial_expires_at.slice(0, 10) : '';
    byId('tier-meta').textContent = (s.filters_limit != null ? s.filters_limit + ' фильтров · ' : '')
      + (s.notifications_limit != null ? s.notifications_limit + ' уведомлений/день' : '')
      + (trialTxt ? ' · ' + trialTxt : '');

    if (s.monitoring_paused_until) {
      byId('pause-status').textContent = 'Мониторинг на паузе до ' + s.monitoring_paused_until.slice(0, 16).replace('T', ' ');
    } else {
      byId('pause-status').textContent = 'Мониторинг активен';
    }

    // Bitrix24
    const bx = byId('bx-webhook');
    if (bx) bx.value = s.bitrix24_webhook || '';
    const bxT = byId('toggle-bitrix');
    if (bxT) bxT.classList.toggle('on', !!s.bitrix24_enabled);
  }

  function tierLabel(t) {
    return ({ trial: 'Trial', starter: 'Starter', pro: 'Pro', premium: 'Business', expired: 'Истёк' })[t] || t || '—';
  }

  async function save(patch, toastMsg) {
    const data = await apiPost('/cabinet/api/settings', patch);
    if (data && data.ok) {
      if (toastMsg) Toast.show(toastMsg, 'positive');
      load();
    }
  }

  byId('toggle-notifs').dataset.url = '';
  byId('toggle-quiet').dataset.url = '';

  byId('toggle-notifs').addEventListener('click', () => {
    const on = byId('toggle-notifs').classList.contains('on');
    save({ notifications_enabled: on }, on ? 'Уведомления включены' : 'Уведомления выключены');
  });
  byId('toggle-quiet').addEventListener('click', () => {
    const on = byId('toggle-quiet').classList.contains('on');
    save({ quiet_hours_enabled: on }, on ? 'Тихие часы включены' : 'Тихие часы выключены');
  });

  byId('save-quiet-hours').addEventListener('click', () => {
    save({
      quiet_hours_start: parseInt(byId('quiet-start').value, 10),
      quiet_hours_end: parseInt(byId('quiet-end').value, 10),
    }, 'Часы сохранены');
  });

  byId('pause-24h').addEventListener('click', () => save({ monitoring_pause: '24h' }, 'Мониторинг на 24 часа'));
  byId('pause-forever').addEventListener('click', () => save({ monitoring_pause: 'forever' }, 'Мониторинг остановлен'));
  byId('pause-resume').addEventListener('click', () => save({ monitoring_pause: 'resume' }, 'Мониторинг возобновлён'));

  // Bitrix24 handlers
  const bxSave = byId('bx-save');
  if (bxSave) {
    bxSave.addEventListener('click', async (e) => {
      const btn = e.currentTarget;
      if (btn.disabled) return;
      const orig = btn.textContent;
      btn.disabled = true;
      btn.textContent = '⏳ Проверяем и сохраняем…';
      const payload = {
        webhook_url: byId('bx-webhook').value.trim(),
        enabled: byId('toggle-bitrix').classList.contains('on'),
      };
      try {
        const data = await apiPost('/cabinet/api/settings/bitrix24', payload);
        if (data && data.ok) {
          Toast.show('✓ Настройки Битрикс24 сохранены', 'positive');
          byId('bx-status').textContent = 'Сохранено';
          byId('bx-status').style.color = 'var(--positive)';
        }
      } finally {
        btn.disabled = false;
        btn.textContent = orig;
      }
    });
  }

  const bxTest = byId('bx-test');
  if (bxTest) {
    bxTest.addEventListener('click', async (e) => {
      const btn = e.currentTarget;
      if (btn.disabled) return;
      const orig = btn.textContent;
      btn.disabled = true;
      btn.textContent = '⏳ Проверяем…';
      try {
        const data = await apiPost('/cabinet/api/settings/bitrix24/test', {});
        const status = byId('bx-status');
        if (data && data.ok) {
          Toast.show('✓ Соединение работает', 'positive');
          status.textContent = data.message || 'Соединение работает';
          status.style.color = 'var(--positive)';
        } else {
          status.textContent = '';
        }
      } finally {
        btn.disabled = false;
        btn.textContent = orig;
      }
    });
  }

  load();
})();
