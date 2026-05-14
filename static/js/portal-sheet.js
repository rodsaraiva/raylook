/* Portal do Cliente — Raylook — Bottom Sheet PIX + filtros + polling */
(function () {
    'use strict';

    // ── Referências do sheet ──────────────────────────────────────────────────

    const overlay    = document.getElementById('pix-sheet-overlay');
    const sheet      = document.getElementById('pix-sheet');
    const sheetName  = document.getElementById('pix-sheet-name');
    const sheetAmt   = document.getElementById('pix-sheet-amount');
    const qrBox      = document.getElementById('pix-qr-img');
    const codeText   = document.getElementById('pix-code-text');
    const copyBtn    = document.getElementById('pix-copy-btn');
    const closeBtn   = document.getElementById('pix-sheet-close');

    let _pixPayload = '';

    // ── Abrir / fechar sheet ──────────────────────────────────────────────────

    function openSheet(name, amount) {
        sheetName.textContent  = name;
        sheetAmt.textContent   = amount;
        qrBox.innerHTML        = '<i class="fas fa-spinner fa-spin" style="color:#6b6560;font-size:22px"></i>';
        codeText.textContent   = 'Gerando...';
        copyBtn.disabled       = true;
        _pixPayload            = '';

        overlay.classList.add('open');
        sheet.classList.add('open');
        document.body.style.overflow = 'hidden';
    }

    function closeSheet() {
        overlay.classList.remove('open');
        sheet.classList.remove('open');
        document.body.style.overflow = '';
    }

    // ── Preencher sheet com dados da API ──────────────────────────────────────

    function fillSheet(data) {
        qrBox.innerHTML = '';
        if (data.qr_code_base64) {
            var img = document.createElement('img');
            img.src = 'data:image/png;base64,' + data.qr_code_base64;
            img.alt = 'QR Code PIX';
            qrBox.appendChild(img);
        }
        _pixPayload = data.pix_payload || '';
        codeText.textContent = _pixPayload || '—';
        copyBtn.disabled = !_pixPayload;
    }

    function showSheetError(msg) {
        qrBox.innerHTML = '<i class="fas fa-exclamation-triangle" style="color:#f87171;font-size:20px"></i>';
        codeText.textContent = msg;
        copyBtn.disabled = true;
    }

    async function fetchAndOpen(url, name, amount) {
        openSheet(name, amount);
        try {
            const resp = await fetch(url, { method: 'POST', credentials: 'same-origin' });
            if (resp.status === 401) { window.location.href = '/portal'; return; }
            if (resp.status === 412) {
                // CPF obrigatório no Asaas — fluxo de contingência
                closeSheet();
                openCpfModal(function () { fetchAndOpen(url, name, amount); });
                return;
            }
            const data = await resp.json();
            if (data.error) { showSheetError(data.error); return; }
            fillSheet(data);
        } catch (e) {
            showSheetError('Erro ao gerar PIX. Tente novamente.');
        }
    }

    // ── Modal de CPF/CNPJ (contingência) ──────────────────────────────────────

    function maskCpfCnpj(raw) {
        const d = String(raw || '').replace(/\D/g, '').slice(0, 14);
        if (!d) return '';
        if (d.length <= 11) {
            if (d.length <= 3) return d;
            if (d.length <= 6) return d.slice(0, 3) + '.' + d.slice(3);
            if (d.length <= 9) return d.slice(0, 3) + '.' + d.slice(3, 6) + '.' + d.slice(6);
            return d.slice(0, 3) + '.' + d.slice(3, 6) + '.' + d.slice(6, 9) + '-' + d.slice(9);
        }
        if (d.length <= 12) return d.slice(0, 2) + '.' + d.slice(2, 5) + '.' + d.slice(5, 8) + '/' + d.slice(8);
        return d.slice(0, 2) + '.' + d.slice(2, 5) + '.' + d.slice(5, 8) + '/' + d.slice(8, 12) + '-' + d.slice(12);
    }

    let _cpfRetry = null;

    function openCpfModal(onSuccess) {
        _cpfRetry = onSuccess || null;
        const ov = document.getElementById('cpf-modal-overlay');
        const md = document.getElementById('cpf-modal');
        const inp = document.getElementById('cpf-modal-input');
        const err = document.getElementById('cpf-modal-error');
        if (!ov || !md || !inp) return;
        inp.value = '';
        err.textContent = '';
        ov.classList.add('open');
        md.classList.add('open');
        document.body.style.overflow = 'hidden';
        setTimeout(function () { inp.focus(); }, 50);
    }

    function closeCpfModal() {
        const ov = document.getElementById('cpf-modal-overlay');
        const md = document.getElementById('cpf-modal');
        if (ov) ov.classList.remove('open');
        if (md) md.classList.remove('open');
        document.body.style.overflow = '';
        _cpfRetry = null;
    }

    async function submitCpf() {
        const inp = document.getElementById('cpf-modal-input');
        const err = document.getElementById('cpf-modal-error');
        const btn = document.getElementById('cpf-modal-submit');
        const digits = String(inp.value || '').replace(/\D/g, '');
        if (digits.length !== 11 && digits.length !== 14) {
            err.textContent = 'CPF (11 dígitos) ou CNPJ (14 dígitos) inválido.';
            return;
        }
        btn.disabled = true;
        err.textContent = '';
        try {
            const resp = await fetch('/portal/api/cpf', {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ cpf_cnpj: digits }),
            });
            if (resp.status === 401) { window.location.href = '/portal'; return; }
            const data = await resp.json();
            if (!resp.ok || data.error) {
                err.textContent = data.error || 'Erro ao salvar CPF.';
                btn.disabled = false;
                return;
            }
            const retry = _cpfRetry;
            closeCpfModal();
            if (typeof retry === 'function') retry();
        } catch (e) {
            err.textContent = 'Erro de conexão. Tente novamente.';
            btn.disabled = false;
        }
    }

    document.addEventListener('input', function (e) {
        if (e.target && e.target.id === 'cpf-modal-input') {
            e.target.value = maskCpfCnpj(e.target.value);
        }
    });
    document.addEventListener('click', function (e) {
        if (e.target && e.target.id === 'cpf-modal-submit') submitCpf();
        if (e.target && e.target.id === 'cpf-modal-close') closeCpfModal();
        if (e.target && e.target.id === 'cpf-modal-overlay') closeCpfModal();
    });
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') {
            const md = document.getElementById('cpf-modal');
            if (md && md.classList.contains('open')) submitCpf();
        }
    });

    // ── Botões Pagar (por card) ───────────────────────────────────────────────

    document.querySelectorAll('.btn-pay').forEach(function (btn) {
        btn.addEventListener('click', function () {
            var id    = btn.dataset.pagamento;
            var nome  = btn.dataset.nome  || 'Pedido';
            var valor = btn.dataset.valor || '—';
            if (id) fetchAndOpen('/portal/api/pay/' + id, nome, valor);
        });
    });

    // ── Botão Pagar Todos ─────────────────────────────────────────────────────

    var btnPayAll = document.getElementById('btnPayAll');
    if (btnPayAll) {
        btnPayAll.addEventListener('click', function () {
            var valor = btnPayAll.dataset.valor || '—';
            fetchAndOpen('/portal/api/pay-all', 'Todos os pedidos pendentes', valor);
        });
    }

    // ── Copiar código PIX ─────────────────────────────────────────────────────

    if (copyBtn) copyBtn.addEventListener('click', function () {
        if (!_pixPayload) return;

        function onCopied() {
            copyBtn.textContent = '✓ Copiado!';
            copyBtn.classList.add('copied');
            setTimeout(function () {
                copyBtn.innerHTML = '📋 Copiar código';
                copyBtn.classList.remove('copied');
            }, 2000);
        }

        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(_pixPayload).then(onCopied).catch(fallback);
        } else {
            fallback();
        }

        function fallback() {
            var ta = document.createElement('textarea');
            ta.value = _pixPayload;
            ta.style.cssText = 'position:fixed;opacity:0;pointer-events:none';
            document.body.appendChild(ta);
            ta.select();
            try { document.execCommand('copy'); } catch (e) {}
            document.body.removeChild(ta);
            onCopied();
        }
    });

    // ── Fechar sheet ──────────────────────────────────────────────────────────

    if (closeBtn) closeBtn.addEventListener('click', closeSheet);
    if (overlay) overlay.addEventListener('click', closeSheet);

    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') closeSheet();
    });

    // ── Filtros ───────────────────────────────────────────────────────────────

    document.querySelectorAll('.filter-chip').forEach(function (chip) {
        chip.addEventListener('click', function () {
            document.querySelectorAll('.filter-chip').forEach(function (c) {
                c.classList.remove('active');
            });
            chip.classList.add('active');
            var filter = chip.dataset.filter;
            document.querySelectorAll('.order-card').forEach(function (card) {
                card.style.display =
                    (filter === 'all' || card.dataset.status === filter) ? '' : 'none';
            });
        });
    });

    // ── Polling de status (30s) ───────────────────────────────────────────────

    (function poll() {
        var INTERVAL = 30000;

        async function check() {
            try {
                var resp = await fetch('/portal/api/status', { credentials: 'same-origin' });
                if (resp.status === 401 || !resp.ok) return;
                var data = await resp.json();

                var changed = false;
                document.querySelectorAll('.order-card').forEach(function (card) {
                    var vendaId = card.dataset.vendaId;
                    if (!vendaId || !data.orders) return;
                    var newStatus = data.orders[vendaId];
                    if (newStatus && newStatus !== card.dataset.status) changed = true;
                });
                if (changed) { window.location.reload(); return; }

                if (data.kpis) {
                    var pending = document.querySelector('.kpi-card.pending .kpi-value');
                    var paid    = document.querySelector('.kpi-card.paid .kpi-value');
                    if (pending) {
                        var vp = 'R$ ' + data.kpis.total_pending.toFixed(2).replace('.', ',');
                        if (pending.textContent.trim() !== vp) pending.textContent = vp;
                    }
                    if (paid) {
                        var vd = 'R$ ' + data.kpis.total_paid.toFixed(2).replace('.', ',');
                        if (paid.textContent.trim() !== vd) paid.textContent = vd;
                    }
                }
            } catch (e) { /* silencioso */ }
            setTimeout(check, INTERVAL);
        }

        setTimeout(check, INTERVAL);
    })();

})();
