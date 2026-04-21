/* Cabinet v3 — fetch wrapper + toast notifications. */

const Toast = {
  show(msg, type, duration) {
    duration = duration || 3500;
    let container = document.querySelector('.toast-container');
    if (!container) {
      container = document.createElement('div');
      container.className = 'toast-container';
      document.body.appendChild(container);
    }
    const el = document.createElement('div');
    el.className = 'toast' + (type === 'alert' ? ' alert' : type === 'positive' ? ' positive' : '');
    el.textContent = msg;
    container.appendChild(el);
    setTimeout(() => el.remove(), duration);
  },
};

async function apiGet(url) {
  const resp = await fetch(url, { credentials: 'same-origin' });
  if (resp.status === 401) { location.href = '/cabinet/login'; return null; }
  if (!resp.ok) { Toast.show('Ошибка ' + resp.status, 'alert'); return null; }
  return resp.json();
}

async function apiPost(url, body) {
  const resp = await fetch(url, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
  if (resp.status === 401) { location.href = '/cabinet/login'; return null; }
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) { Toast.show(data.error || ('Ошибка ' + resp.status), 'alert'); return null; }
  return data;
}

window.Cabinet = window.Cabinet || {};
window.Cabinet.Toast = Toast;
window.Cabinet.apiGet = apiGet;
window.Cabinet.apiPost = apiPost;
