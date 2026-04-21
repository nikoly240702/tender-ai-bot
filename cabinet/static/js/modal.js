/* Cabinet v3 — modal open/close, ESC, click-backdrop. */

const Modal = {
  open(id) {
    const m = document.getElementById(id);
    if (!m) return;
    m.classList.add('open');
    document.body.style.overflow = 'hidden';
  },
  close(id) {
    const m = id ? document.getElementById(id) : document.querySelector('.modal-backdrop.open');
    if (!m) return;
    m.classList.remove('open');
    document.body.style.overflow = '';
  },
  init() {
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') Modal.close();
    });
    document.addEventListener('click', (e) => {
      if (e.target.classList.contains('modal-backdrop')) Modal.close();
      if (e.target.closest('[data-modal-close]')) {
        const backdrop = e.target.closest('.modal-backdrop');
        if (backdrop) Modal.close(backdrop.id);
      }
    });
  },
};

document.addEventListener('DOMContentLoaded', Modal.init);

window.Cabinet = window.Cabinet || {};
window.Cabinet.Modal = Modal;
