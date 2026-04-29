/* Cabinet v3 — Team page */
(function () {
  const { Toast } = window.Cabinet;
  const headerEl = document.querySelector('.page-header');
  const isOwner = headerEl && headerEl.dataset.isOwner === '1';

  // Create invite
  const btnCreate = document.getElementById('btn-create-invite');
  if (btnCreate) {
    btnCreate.addEventListener('click', async () => {
      const r = await fetch('/cabinet/api/team/invites', {
        method: 'POST', credentials: 'same-origin',
      });
      const d = await r.json().catch(() => ({}));
      if (r.ok && d.ok && d.invite && d.invite.url) {
        try {
          await navigator.clipboard.writeText(d.invite.url);
          Toast.show('✓ Ссылка создана и скопирована', 'positive');
        } catch (e) {
          prompt('Скопируйте инвайт-ссылку:', d.invite.url);
        }
        setTimeout(() => window.location.reload(), 600);
      } else {
        Toast.show(d.error || 'Ошибка', 'alert');
      }
    });
  }

  // Copy invite
  document.querySelectorAll('.copy-invite-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      const token = btn.dataset.token;
      const url = `${window.location.protocol}//${window.location.host}/cabinet/invite/${token}`;
      try {
        await navigator.clipboard.writeText(url);
        Toast.show('✓ Ссылка скопирована', 'positive');
      } catch (e) {
        prompt('Скопируйте инвайт-ссылку:', url);
      }
    });
  });

  // Revoke invite
  document.querySelectorAll('.revoke-invite-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      if (!confirm('Отозвать инвайт-ссылку?')) return;
      const id = btn.dataset.inviteId;
      const r = await fetch('/cabinet/api/team/invites/' + id, {
        method: 'DELETE', credentials: 'same-origin',
      });
      if (r.ok) {
        Toast.show('Отозвано', 'positive');
        setTimeout(() => window.location.reload(), 400);
      }
    });
  });

  // Remove member
  document.querySelectorAll('.remove-member-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      if (!confirm('Удалить сотрудника из команды? Его карточки перейдут к owner-у.')) return;
      const userId = btn.dataset.userId;
      const r = await fetch('/cabinet/api/team/members/' + userId, {
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

  // Leave team
  const btnLeave = document.getElementById('btn-leave-team');
  if (btnLeave) {
    btnLeave.addEventListener('click', async () => {
      if (!confirm('Покинуть команду? Все ваши карточки перейдут к owner-у.')) return;
      const r = await fetch('/cabinet/api/team/leave', {
        method: 'POST', credentials: 'same-origin',
      });
      const d = await r.json().catch(() => ({}));
      if (r.ok && d.ok) {
        Toast.show('Вы покинули команду', 'positive');
        setTimeout(() => window.location.href = '/cabinet/', 600);
      } else {
        Toast.show(d.error || 'Ошибка', 'alert');
      }
    });
  }
})();
