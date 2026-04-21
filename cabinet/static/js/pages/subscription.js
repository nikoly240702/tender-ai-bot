(function () {
  const { apiGet, apiPost, Toast } = window.Cabinet;
  const byId = id => document.getElementById(id);
  const TIER_NAMES = { trial: 'Trial', starter: 'Starter', pro: 'Pro', premium: 'Business', expired: 'Истёк' };

  async function load() {
    const s = await apiGet('/cabinet/api/subscription');
    if (!s) return;

    byId('curr-name').textContent = TIER_NAMES[s.tier] || s.tier || '—';
    const parts = [];
    if (s.filters_limit != null) parts.push(s.filters_limit + ' фильтров');
    if (s.notifications_limit != null) parts.push(s.notifications_limit + ' уведомлений/день');
    if (s.expires_at) parts.push('до ' + s.expires_at.slice(0, 10) + ' (' + s.days_left + ' дн.)');
    byId('curr-meta').textContent = parts.join(' · ');

    const isFirst = !!s.is_first_payment;
    document.querySelectorAll('.pricing-card').forEach(card => {
      const tier = card.dataset.tier;
      card.classList.toggle('current', tier === s.tier);
      const priceEl = card.querySelector('.price-main');
      const oldEl = card.querySelector('.price-old');
      if (isFirst && priceEl && priceEl.dataset.first) {
        priceEl.textContent = priceEl.dataset.first + ' ₽';
        if (oldEl) oldEl.textContent = priceEl.dataset.regular + ' ₽';
      } else if (priceEl) {
        priceEl.textContent = priceEl.dataset.regular + ' ₽';
        if (oldEl) oldEl.textContent = '';
      }
    });
  }

  async function payTier(tier, btn) {
    if (btn.disabled) return;
    const orig = btn.textContent;
    btn.disabled = true;
    btn.textContent = '⏳ Создаём счёт…';
    try {
      const data = await apiPost('/cabinet/api/subscription/pay', { tier, months: 1 });
      if (data && data.url) {
        window.location.href = data.url;
      }
    } catch (e) { Toast.show('Ошибка создания платежа', 'alert'); }
    finally { btn.disabled = false; btn.textContent = orig; }
  }

  document.querySelectorAll('[data-pay]').forEach(btn => {
    btn.addEventListener('click', e => payTier(e.currentTarget.dataset.pay, e.currentTarget));
  });

  load();
})();
