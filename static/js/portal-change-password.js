// Modal de troca de senha obrigatória (login com senha temporária).
// Em arquivo externo porque a CSP bloqueia <script> inline — inline o handler
// não registrava e o form caía em submit nativo (GET), vazando a senha na URL
// e deixando o modal aberto.
(function () {
    const form = document.getElementById('chgpwd-form');
    if (!form) return;
    const err = document.getElementById('chgpwd-error');
    const submit = form.querySelector('.chgpwd-submit');
    // Bloqueia interação com a página por baixo: scroll trava enquanto modal está aberto
    document.body.style.overflow = 'hidden';

    form.addEventListener('submit', async (ev) => {
        ev.preventDefault();
        err.textContent = '';
        const password = form.password.value;
        const password_confirm = form.password_confirm.value;
        if (password.length < 6) { err.textContent = 'A senha deve ter pelo menos 6 caracteres.'; return; }
        if (password !== password_confirm) { err.textContent = 'As senhas não conferem.'; return; }
        submit.disabled = true;
        try {
            const body = new FormData();
            body.append('password', password);
            body.append('password_confirm', password_confirm);
            const r = await fetch('/portal/change-password', { method: 'POST', body, credentials: 'same-origin' });
            const j = await r.json().catch(() => ({}));
            if (!r.ok) { err.textContent = j.error || 'Erro ao salvar.'; submit.disabled = false; return; }
            document.body.style.overflow = '';
            window.location.reload();
        } catch (e) {
            err.textContent = 'Erro de rede. Tente novamente.';
            submit.disabled = false;
        }
    });
})();
