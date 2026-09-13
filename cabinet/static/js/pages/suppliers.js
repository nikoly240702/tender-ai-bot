/* Cabinet v3 — Suppliers page */
(function () {
  const { Toast } = window.Cabinet;

  const btnCreate = document.getElementById('btn-create-supplier');
  if (btnCreate) {
    btnCreate.addEventListener('click', async () => {
      const name = document.getElementById('new-supplier-name').value.trim();
      const contact = document.getElementById('new-supplier-contact').value.trim();
      if (!name) {
        Toast.show('Укажите название', 'alert');
        return;
      }
      const r = await fetch('/cabinet/api/suppliers', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, contact }),
      });
      const d = await r.json().catch(() => ({}));
      if (r.ok && d.ok) {
        Toast.show('✓ Поставщик добавлен', 'positive');
        setTimeout(() => window.location.reload(), 400);
      } else {
        Toast.show(d.error || 'Ошибка', 'alert');
      }
    });
  }

  document.querySelectorAll('.edit-supplier-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      const id = btn.dataset.supplierId;
      const name = prompt('Название поставщика:', btn.dataset.name || '');
      if (name === null) return;
      const contact = prompt('Контакты:', btn.dataset.contact || '');
      if (contact === null) return;
      const r = await fetch('/cabinet/api/suppliers/' + id, {
        method: 'PUT', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name.trim(), contact: contact.trim() }),
      });
      const d = await r.json().catch(() => ({}));
      if (r.ok && d.ok) {
        Toast.show('✓ Обновлено', 'positive');
        setTimeout(() => window.location.reload(), 400);
      } else {
        Toast.show(d.error || 'Ошибка', 'alert');
      }
    });
  });

  document.querySelectorAll('.delete-supplier-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      if (!confirm('Удалить поставщика?')) return;
      const id = btn.dataset.supplierId;
      const r = await fetch('/cabinet/api/suppliers/' + id, {
        method: 'DELETE', credentials: 'same-origin',
      });
      const d = await r.json().catch(() => ({}));
      if (r.ok && d.ok) {
        Toast.show('Удалено', 'positive');
        setTimeout(() => window.location.reload(), 400);
      } else {
        Toast.show(d.error || 'Ошибка', 'alert');
      }
    });
  });
})();
