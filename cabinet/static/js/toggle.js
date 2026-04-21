/* Cabinet v3 — toggle switch with optional API call. */

document.addEventListener('click', async (e) => {
  const t = e.target.closest('.toggle');
  if (!t) return;

  const wasOn = t.classList.contains('on');
  t.classList.toggle('on');

  const url = t.dataset.url;
  if (!url) return;

  const data = await window.Cabinet.apiPost(url, { active: !wasOn });
  if (!data) t.classList.toggle('on');
});
