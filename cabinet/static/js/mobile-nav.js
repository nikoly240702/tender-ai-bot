/* Мобильная навигация: сайдбар как выезжающая панель.
 *
 * До этого на ширине <=959px сайдбар просто скрывался (display:none) без
 * какой-либо замены — с телефона по кабинету было вообще невозможно
 * перемещаться между разделами.
 *
 * Намеренно НЕ трогает body.pipeline-mode: это отдельный desktop-механизм
 * сворачивания сайдбара в узкую полосу (pipeline.js), у него своя логика и
 * своё состояние в localStorage.
 */
(function () {
  const openBtn = document.getElementById('mobile-nav-open');
  const backdrop = document.getElementById('mobile-nav-backdrop');
  if (!openBtn || !backdrop) return;

  function setOpen(open) {
    document.body.classList.toggle('mobile-nav-open', open);
    backdrop.hidden = !open;
    openBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
    // Блокируем прокрутку страницы под открытой панелью
    document.body.style.overflow = open ? 'hidden' : '';
  }

  openBtn.addEventListener('click', () => {
    setOpen(!document.body.classList.contains('mobile-nav-open'));
  });

  backdrop.addEventListener('click', () => setOpen(false));

  // Переход по ссылке меню — панель закрывается сама
  document.querySelectorAll('.sidebar nav a').forEach(a => {
    a.addEventListener('click', () => setOpen(false));
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') setOpen(false);
  });

  // Возврат на десктопную ширину при открытой панели не должен оставлять
  // заблокированную прокрутку.
  window.addEventListener('resize', () => {
    if (window.innerWidth > 959 && document.body.classList.contains('mobile-nav-open')) {
      setOpen(false);
    }
  });
})();
