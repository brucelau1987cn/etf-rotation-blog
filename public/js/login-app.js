/**
 * Login / force password-change client for /login/.
 */
    const loginForm = document.querySelector('#login-form');
    const changeForm = document.querySelector('#change-form');
    const status = document.querySelector('#status');
    const setStatus = (message = '') => { status.textContent = message; };
    loginForm.addEventListener('submit', async (event) => {
      event.preventDefault(); setStatus('登录中…');
      const body = Object.fromEntries(new FormData(loginForm));
      const response = await fetch('/api/auth/login', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) { setStatus(data.error || '登录失败'); return; }
      if (data.user.must_change_password) {
        loginForm.classList.add('hidden'); changeForm.classList.remove('hidden');
        changeForm.elements.old_password.value = body.password; setStatus('请先修改初始密码'); return;
      }
      location.href = '/lab/';
    });
    changeForm.addEventListener('submit', async (event) => {
      event.preventDefault(); setStatus('保存中…');
      const body = Object.fromEntries(new FormData(changeForm));
      const response = await fetch('/api/auth/change-password', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) { setStatus(data.error || '修改失败'); return; }
      location.href = '/lab/';
    });
