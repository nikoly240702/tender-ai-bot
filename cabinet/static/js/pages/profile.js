(function () {
  const { apiGet, apiPost, Toast } = window.Cabinet;
  const FIELDS = [
    'company_name','company_name_short','legal_form','inn','kpp','ogrn',
    'legal_address','actual_address','postal_address',
    'director_name','director_position','director_basis',
    'phone','email','website',
    'bank_name','bank_bik','bank_account','bank_corr_account',
    'smp_status','licenses_text','experience_description',
  ];
  const byId = id => document.getElementById(id);

  async function load() {
    const data = await apiGet('/cabinet/api/profile');
    if (!data) return;
    const p = data.profile || {};
    FIELDS.forEach(f => {
      const el = byId('pf-' + f);
      if (el) el.value = p[f] == null ? '' : p[f];
    });
  }

  async function save(btn) {
    if (btn.disabled) return;
    const orig = btn.textContent;
    btn.disabled = true;
    btn.textContent = '⏳ Сохраняем…';
    const payload = {};
    FIELDS.forEach(f => { const el = byId('pf-' + f); if (el) payload[f] = el.value.trim(); });
    try {
      const data = await apiPost('/cabinet/api/profile', payload);
      if (data && data.ok) {
        Toast.show('✓ Профиль сохранён', 'positive');
        const s = byId('save-status');
        if (s) { s.textContent = 'Сохранено ' + new Date().toLocaleTimeString('ru-RU', {hour:'2-digit',minute:'2-digit'}); s.classList.add('ok'); }
      }
    } catch (e) { Toast.show('Ошибка соединения', 'alert'); }
    finally { btn.disabled = false; btn.textContent = orig; }
  }

  byId('btn-save-profile').addEventListener('click', e => save(e.currentTarget));
  load();
})();
