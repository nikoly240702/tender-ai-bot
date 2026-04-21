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

  load();
})();
