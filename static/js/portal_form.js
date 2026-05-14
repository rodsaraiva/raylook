/* Portal — helpers para login/setup/reset (mascara telefone + toggle senha) */

// ---------------------------------------------------------------------------
// Telefone BR (mesmas funções do dashboard)
// ---------------------------------------------------------------------------
function sanitizeBrPhoneDigits(raw) {
    let d = String(raw || '').replace(/\D/g, '');
    d = d.replace(/^0+/, '');
    if (d.length >= 10 && d.length <= 11 && !d.startsWith('55')) {
        d = '55' + d;
    }
    return d;
}

function formatBrPhoneDisplay(raw) {
    const d = String(raw || '').replace(/\D/g, '');
    if (!d) return '';
    if (d.length <= 2) return '+' + d;
    if (d.length <= 4) return `+${d.slice(0, 2)} (${d.slice(2)}`;
    if (d.length <= 5) return `+${d.slice(0, 2)} (${d.slice(2, 4)}) ${d.slice(4)}`;
    if (d.length <= 9) {
        const ddi = d.slice(0, 2);
        const ddd = d.slice(2, 4);
        const rest = d.slice(4);
        if (rest.length === 0) return `+${ddi} (${ddd})`;
        if (rest.length <= 4) return `+${ddi} (${ddd}) ${rest}`;
        return `+${ddi} (${ddd}) ${rest.slice(0, 1)} ${rest.slice(1, 5)}-${rest.slice(5)}`;
    }
    const ddi = d.slice(0, 2);
    const ddd = d.slice(2, 4);
    const rest = d.slice(4);
    if (rest.length === 9) {
        return `+${ddi} (${ddd}) ${rest.slice(0, 1)} ${rest.slice(1, 5)}-${rest.slice(5)}`;
    }
    if (rest.length === 8) {
        return `+${ddi} (${ddd}) ${rest.slice(0, 4)}-${rest.slice(4)}`;
    }
    return `+${ddi} (${ddd}) ${rest}`;
}

// Aplicar máscara em todos os inputs type="tel" com classe .phone-br
document.querySelectorAll('input.phone-br').forEach(input => {
    // Formato inicial se já tem valor
    if (input.value) {
        input.value = formatBrPhoneDisplay(input.value);
    }
    input.addEventListener('input', (e) => {
        const digits = sanitizeBrPhoneDigits(e.target.value);
        e.target.value = formatBrPhoneDisplay(digits);
    });
    input.addEventListener('blur', (e) => {
        // Normalizar antes de enviar — o backend já sanitiza
    });
});

// Antes do submit, trocar o valor do campo pelos dígitos puros (o backend normaliza)
document.querySelectorAll('form').forEach(form => {
    form.addEventListener('submit', () => {
        form.querySelectorAll('input.phone-br').forEach(input => {
            input.value = sanitizeBrPhoneDigits(input.value);
        });
    });
});

// ---------------------------------------------------------------------------
// CPF / CNPJ — máscara dinâmica baseada no número de dígitos
// CPF (até 11): 000.000.000-00
// CNPJ (12-14): 00.000.000/0000-00
// ---------------------------------------------------------------------------
function formatCpfCnpj(raw) {
    const d = String(raw || '').replace(/\D/g, '').slice(0, 14);
    if (!d) return '';
    if (d.length <= 11) {
        if (d.length <= 3) return d;
        if (d.length <= 6) return `${d.slice(0,3)}.${d.slice(3)}`;
        if (d.length <= 9) return `${d.slice(0,3)}.${d.slice(3,6)}.${d.slice(6)}`;
        return `${d.slice(0,3)}.${d.slice(3,6)}.${d.slice(6,9)}-${d.slice(9)}`;
    }
    if (d.length <= 12) return `${d.slice(0,2)}.${d.slice(2,5)}.${d.slice(5,8)}/${d.slice(8)}`;
    return `${d.slice(0,2)}.${d.slice(2,5)}.${d.slice(5,8)}/${d.slice(8,12)}-${d.slice(12)}`;
}

document.querySelectorAll('input.cpf-cnpj').forEach(input => {
    if (input.value) input.value = formatCpfCnpj(input.value);
    input.addEventListener('input', (e) => {
        e.target.value = formatCpfCnpj(e.target.value);
    });
});

// Antes do submit, manda só os dígitos (backend normaliza, mas evita problema)
document.querySelectorAll('form').forEach(form => {
    form.addEventListener('submit', () => {
        form.querySelectorAll('input.cpf-cnpj').forEach(input => {
            input.value = String(input.value || '').replace(/\D/g, '');
        });
    });
});

// ---------------------------------------------------------------------------
// Validação de email (visual ao blur + ao submit)
// ---------------------------------------------------------------------------
const EMAIL_RE = /^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)*\.[a-zA-Z]{2,}$/;

function isValidEmail(raw) {
    const email = String(raw || '').trim().toLowerCase();
    if (!email || email.length < 6 || email.length > 254) return false;
    if (email.includes('..') || email.startsWith('.') || email.endsWith('.')) return false;
    if (email.includes('@.') || email.includes('.@')) return false;
    return EMAIL_RE.test(email);
}

function showEmailError(input, message) {
    let errMsg = input.parentElement.parentElement.querySelector('.email-error-msg');
    if (!errMsg) {
        errMsg = document.createElement('div');
        errMsg.className = 'email-error-msg';
        errMsg.style.cssText = 'color: var(--danger, #8B4444); font-size: 0.75rem; margin-top: 0.3rem;';
        input.parentElement.parentElement.appendChild(errMsg);
    }
    errMsg.textContent = message;
}

function clearEmailError(input) {
    const errMsg = input.parentElement.parentElement.querySelector('.email-error-msg');
    if (errMsg) errMsg.remove();
    input.style.borderColor = '';
}

document.querySelectorAll('input[type="email"]').forEach(input => {
    input.addEventListener('blur', (e) => {
        const val = e.target.value.trim();
        if (val && !isValidEmail(val)) {
            e.target.style.borderColor = 'var(--danger, #8B4444)';
            showEmailError(e.target, 'Email inválido. Exemplo: nome@dominio.com');
        } else {
            clearEmailError(e.target);
        }
    });
    input.addEventListener('input', () => clearEmailError(input));
});

// Interceptar submit para bloquear email inválido no frontend
document.querySelectorAll('form').forEach(form => {
    form.addEventListener('submit', (e) => {
        const emailInput = form.querySelector('input[type="email"]');
        if (emailInput && emailInput.value.trim() && !isValidEmail(emailInput.value)) {
            e.preventDefault();
            emailInput.style.borderColor = 'var(--danger, #8B4444)';
            showEmailError(emailInput, 'Email inválido. Exemplo: nome@dominio.com');
            emailInput.focus();
        }
    });
});

// ---------------------------------------------------------------------------
// Toggle de senha (olhinho)
// ---------------------------------------------------------------------------
document.querySelectorAll('.pwd-toggle').forEach(btn => {
    btn.addEventListener('click', () => {
        const targetId = btn.dataset.target;
        const input = document.getElementById(targetId);
        if (!input) return;
        const icon = btn.querySelector('i');
        if (input.type === 'password') {
            input.type = 'text';
            if (icon) icon.className = 'fas fa-eye-slash';
        } else {
            input.type = 'password';
            if (icon) icon.className = 'fas fa-eye';
        }
    });
});
