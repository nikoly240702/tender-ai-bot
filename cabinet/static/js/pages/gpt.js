(function () {
  const { Toast } = window.Cabinet;
  const byId = id => document.getElementById(id);

  const messages = byId('chat-messages');
  const input = byId('chat-input');
  const sendBtn = byId('chat-send');
  const typing = byId('chat-typing');
  const quota = byId('chat-quota');

  let sending = false;

  function scrollToBottom() {
    messages.scrollTop = messages.scrollHeight;
  }

  // Allowlist-based HTML insertion (XSS-safe).
  const ALLOWED_TAGS = new Set(['P','BR','STRONG','B','EM','I','U','CODE','PRE','UL','OL','LI','H1','H2','H3','H4','A','BLOCKQUOTE','SPAN','DIV']);
  const ALLOWED_HREF = /^(https?:|mailto:|tel:|\/)/i;

  function sanitizeAndAppend(parent, html) {
    const doc = new DOMParser().parseFromString(html, 'text/html');
    function walk(source, dest) {
      source.childNodes.forEach(node => {
        if (node.nodeType === Node.TEXT_NODE) {
          dest.appendChild(document.createTextNode(node.nodeValue));
        } else if (node.nodeType === Node.ELEMENT_NODE) {
          if (!ALLOWED_TAGS.has(node.tagName)) {
            walk(node, dest);
            return;
          }
          const clone = document.createElement(node.tagName.toLowerCase());
          if (node.tagName === 'A') {
            const href = node.getAttribute('href') || '';
            if (ALLOWED_HREF.test(href)) {
              clone.setAttribute('href', href);
              clone.setAttribute('target', '_blank');
              clone.setAttribute('rel', 'noopener noreferrer');
            }
          }
          walk(node, clone);
          dest.appendChild(clone);
        }
      });
    }
    walk(doc.body, parent);
  }

  function addMessage(role, content) {
    const greeting = messages.querySelector('.greeting');
    if (greeting) greeting.remove();
    const div = document.createElement('div');
    div.className = 'msg ' + role;
    if (role === 'user') {
      div.textContent = content;
    } else {
      sanitizeAndAppend(div, content);
    }
    messages.appendChild(div);
    scrollToBottom();
  }

  function updateQuota(q) {
    if (!q) return;
    quota.textContent = 'Запросов: ' + (q.used || 0) + '/' + (q.limit || 0) + ' (' + (q.tier || 'trial') + ')';
  }

  input.addEventListener('input', () => {
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 140) + 'px';
  });
  input.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  });

  async function send() {
    if (sending) return;
    const text = input.value.trim();
    if (!text) return;
    sending = true;
    sendBtn.disabled = true;
    input.value = '';
    input.style.height = 'auto';
    addMessage('user', text);
    typing.style.display = 'block';
    scrollToBottom();

    try {
      const resp = await fetch('/cabinet/api/gpt/chat', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text }),
      });
      if (resp.status === 401) { location.href = '/cabinet/login'; return; }
      const data = await resp.json().catch(() => ({}));
      typing.style.display = 'none';
      if (data.error) Toast.show(data.error, 'alert');
      else {
        addMessage('assistant', data.response || 'Нет ответа');
        if (data.quota) updateQuota(data.quota);
      }
    } catch (e) {
      typing.style.display = 'none';
      Toast.show('Ошибка соединения', 'alert');
    } finally {
      sending = false;
      sendBtn.disabled = false;
      input.focus();
    }
  }

  sendBtn.addEventListener('click', send);
  input.focus();
})();
