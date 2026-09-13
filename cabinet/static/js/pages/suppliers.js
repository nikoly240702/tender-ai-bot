/* Cabinet v3 — Suppliers page */
(function () {
  const { Toast } = window.Cabinet;

  const fmtMoney = v => (v == null) ? '' : Math.round(v).toLocaleString('ru-RU');
  const parseMoney = v => {
    if (v == null || v === '') return null;
    const cleaned = String(v).replace(/[^\d.,-]/g, '').replace(/\s/g, '').replace(',', '.');
    const n = parseFloat(cleaned);
    return isNaN(n) ? null : n;
  };

  const btnCreate = document.getElementById('btn-create-supplier');
  if (btnCreate) {
    btnCreate.addEventListener('click', async () => {
      const name = document.getElementById('new-supplier-name').value.trim();
      const website = document.getElementById('new-supplier-website').value.trim();
      const contact = document.getElementById('new-supplier-contact').value.trim();
      const contactPerson = document.getElementById('new-supplier-contact-person').value.trim();
      if (!name) {
        Toast.show('Укажите название', 'alert');
        return;
      }
      const r = await fetch('/cabinet/api/suppliers', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, website, contact, contact_person: contactPerson }),
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
      const website = prompt('Сайт:', btn.dataset.website || '');
      if (website === null) return;
      const contact = prompt('Контакт (телефон/email):', btn.dataset.contact || '');
      if (contact === null) return;
      const contactPerson = prompt('Имя контакта:', btn.dataset.contactPerson || '');
      if (contactPerson === null) return;
      const r = await fetch('/cabinet/api/suppliers/' + id, {
        method: 'PUT', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: name.trim(), website: website.trim(),
          contact: contact.trim(), contact_person: contactPerson.trim(),
        }),
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

  /* ===== Catalog (позиции поставщика) ===== */

  async function loadCatalog(supplierId) {
    const panel = document.querySelector('.supplier-catalog[data-supplier-id="' + supplierId + '"]');
    const list = panel.querySelector('.supplier-catalog-list');
    list.textContent = 'Загрузка…';
    const r = await fetch('/cabinet/api/suppliers/' + supplierId + '/products', { credentials: 'same-origin' });
    const d = r.ok ? await r.json() : { products: [] };
    list.replaceChildren();
    const products = d.products || [];
    if (!products.length) {
      const empty = document.createElement('div');
      empty.className = 'empty';
      empty.textContent = 'Каталог пуст';
      list.appendChild(empty);
      return;
    }
    products.forEach(p => {
      const row = document.createElement('div');
      row.className = 'catalog-item-row';
      const name = document.createElement('span');
      name.className = 'catalog-item-name';
      name.textContent = p.name;
      const price = document.createElement('span');
      price.className = 'catalog-item-price';
      price.textContent = fmtMoney(p.unit_price) + ' ₽/ед.';
      const del = document.createElement('button');
      del.className = 'btn btn-ghost btn-sm';
      del.textContent = '×';
      del.onclick = async () => {
        const rr = await fetch('/cabinet/api/supplier-products/' + p.id, {
          method: 'DELETE', credentials: 'same-origin',
        });
        if (rr.ok) loadCatalog(supplierId);
      };
      row.appendChild(name);
      row.appendChild(price);
      row.appendChild(del);
      list.appendChild(row);
    });
  }

  document.querySelectorAll('.toggle-catalog-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const id = btn.dataset.supplierId;
      const panel = document.querySelector('.supplier-catalog[data-supplier-id="' + id + '"]');
      const wasHidden = panel.hidden;
      panel.hidden = !wasHidden;
      if (wasHidden) loadCatalog(id);
    });
  });

  document.querySelectorAll('.add-catalog-item-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      const supplierId = btn.dataset.supplierId;
      const panel = document.querySelector('.supplier-catalog[data-supplier-id="' + supplierId + '"]');
      const nameInp = panel.querySelector('.catalog-new-name');
      const priceInp = panel.querySelector('.catalog-new-price');
      const name = nameInp.value.trim();
      const price = parseMoney(priceInp.value);
      if (!name) { Toast.show('Укажите название позиции', 'alert'); return; }
      if (price == null) { Toast.show('Укажите цену', 'alert'); return; }
      const r = await fetch('/cabinet/api/suppliers/' + supplierId + '/products', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, unit_price: price }),
      });
      const d = await r.json().catch(() => ({}));
      if (r.ok && d.ok) {
        nameInp.value = ''; priceInp.value = '';
        Toast.show('✓ Позиция добавлена', 'positive');
        loadCatalog(supplierId);
      } else {
        Toast.show(d.error || 'Ошибка', 'alert');
      }
    });
  });
})();
