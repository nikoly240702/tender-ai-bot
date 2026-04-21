(function () {
  const { apiGet } = window.Cabinet;
  const byId = id => document.getElementById(id);
  function el(tag, opts) {
    const e = document.createElement(tag);
    if (opts && opts.cls) e.className = opts.cls;
    if (opts && opts.text !== undefined) e.textContent = opts.text;
    if (opts && opts.attrs) Object.keys(opts.attrs).forEach(k => e.setAttribute(k, opts.attrs[k]));
    return e;
  }

  const TYPE_LABELS = {
    application: 'Заявка',
    commercial_offer: 'КП',
    contract: 'Договор',
    other: 'Документ',
  };

  async function load() {
    const data = await apiGet('/cabinet/api/documents');
    if (!data) return;
    const list = byId('doc-list');
    list.textContent = '';
    const docs = data.documents || [];
    if (docs.length === 0) {
      const empty = el('div', { cls: 'empty' });
      empty.appendChild(el('div', { cls: 'title', text: 'Документов пока нет' }));
      empty.appendChild(el('div', { text: 'Документы создаются из карточки тендера или через чат с Tender-GPT' }));
      list.appendChild(empty);
      byId('doc-count').textContent = '0';
      return;
    }
    byId('doc-count').textContent = docs.length;
    docs.forEach(d => {
      const row = el('div', { cls: 'doc-row' });
      const left = el('div');
      left.appendChild(el('div', { cls: 'name', text: TYPE_LABELS[d.document_type] || (d.document_type || 'Документ') }));
      const metaParts = [];
      if (d.tender_number) metaParts.push('Тендер ' + d.tender_number);
      if (d.created_at) metaParts.push(new Date(d.created_at).toLocaleDateString('ru-RU'));
      left.appendChild(el('div', { cls: 'meta', text: metaParts.join(' · ') }));
      row.appendChild(left);
      row.appendChild(el('span', { cls: 'type-badge', text: (d.document_type || 'doc').slice(0, 12) }));
      const a = el('a', { cls: 'btn btn-secondary', text: 'Скачать', attrs: { href: '/cabinet/api/documents/' + d.id + '/download', target: '_blank', rel: 'noopener' } });
      row.appendChild(a);
      list.appendChild(row);
    });
  }

  load();
})();
