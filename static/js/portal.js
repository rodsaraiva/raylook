/* Portal do Cliente — Raylook */

// ---------------------------------------------------------------------------
// Filtros
// ---------------------------------------------------------------------------
document.querySelectorAll('.filter-chip').forEach(chip => {
    chip.addEventListener('click', () => {
        document.querySelectorAll('.filter-chip').forEach(c => c.classList.remove('active'));
        chip.classList.add('active');

        const filter = chip.dataset.filter;
        document.querySelectorAll('.order-card').forEach(card => {
            if (filter === 'all' || card.dataset.status === filter) {
                card.style.display = '';
            } else {
                card.style.display = 'none';
            }
        });
    });
});

// ---------------------------------------------------------------------------
// Botão Pagar (event listener, sem inline onclick)
// ---------------------------------------------------------------------------
document.querySelectorAll('.btn-pay').forEach(btn => {
    btn.addEventListener('click', () => {
        const pagamentoId = btn.dataset.pagamento;
        if (pagamentoId) openPix(pagamentoId, btn);
    });
});

// ---------------------------------------------------------------------------
// Botão Comprovante (event listener, sem inline onclick)
// ---------------------------------------------------------------------------
document.querySelectorAll('.btn-receipt').forEach(btn => {
    btn.addEventListener('click', () => {
        const link = btn.dataset.link;
        if (link) window.open(link, '_blank');
    });
});

// ---------------------------------------------------------------------------
// Botão Copiar PIX (event listener, sem inline onclick)
// ---------------------------------------------------------------------------
document.querySelectorAll('.btn-copy').forEach(btn => {
    btn.addEventListener('click', () => copyPix(btn));
});

// ---------------------------------------------------------------------------
// Pagar Todos
// ---------------------------------------------------------------------------
const btnPayAll = document.getElementById('btnPayAll');
if (btnPayAll) {
    btnPayAll.addEventListener('click', async () => {
        const modal = document.getElementById('pix-all');
        if (!modal) return;

        // Toggle
        if (modal.style.display !== 'none') {
            modal.style.display = 'none';
            return;
        }

        modal.style.display = 'block';
        const loading = modal.querySelector('.pix-loading');
        const content = modal.querySelector('.pix-content');
        loading.style.display = 'block';
        content.style.display = 'none';

        btnPayAll.disabled = true;
        const originalHTML = btnPayAll.innerHTML;
        btnPayAll.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Gerando PIX...';

        try {
            const resp = await fetch('/portal/api/pay-all', {
                method: 'POST',
                credentials: 'same-origin',
            });

            if (resp.status === 401) {
                window.location.href = '/portal';
                return;
            }

            const data = await resp.json();

            if (data.error) {
                loading.innerHTML = '<i class="fas fa-exclamation-triangle"></i> ' + data.error;
                btnPayAll.innerHTML = originalHTML;
                btnPayAll.disabled = false;
                return;
            }

            // QR Code
            const qrDiv = content.querySelector('.pix-qr');
            if (data.qr_code_base64) {
                qrDiv.innerHTML = '<img src="data:image/png;base64,' + data.qr_code_base64 + '" alt="QR Code PIX">';
            }

            // Código PIX
            const codeInput = content.querySelector('.pix-code');
            codeInput.value = data.pix_payload || '';

            // Link Asaas
            const link = content.querySelector('.pix-link');
            if (data.payment_link) {
                link.href = data.payment_link;
                link.style.display = 'inline-flex';
            }

            loading.style.display = 'none';
            content.style.display = 'flex';

        } catch (err) {
            loading.innerHTML = '<i class="fas fa-exclamation-triangle"></i> Erro ao gerar PIX. Tente novamente.';
        }

        btnPayAll.innerHTML = originalHTML;
        btnPayAll.disabled = false;
    });
}

// ---------------------------------------------------------------------------
// Auto-atualização de status (polling a cada 30s)
// ---------------------------------------------------------------------------
(function pollStatus() {
    const POLL_INTERVAL = 30000; // 30 segundos

    async function checkStatus() {
        try {
            const resp = await fetch('/portal/api/status', { credentials: 'same-origin' });
            if (resp.status === 401) return; // sessão expirou, para de pollar
            if (!resp.ok) return;

            const data = await resp.json();
            let changed = false;

            // Atualizar status dos cards
            document.querySelectorAll('.order-card').forEach(card => {
                const vendaId = card.dataset.vendaId;
                if (!vendaId || !data.orders) return;
                const newStatus = data.orders[vendaId];
                if (newStatus && newStatus !== card.dataset.status) {
                    changed = true;
                }
            });

            // Se algum status mudou, recarregar a página para refletir
            if (changed) {
                window.location.reload();
                return;
            }

            // Atualizar KPIs sem reload (valores de texto)
            if (data.kpis) {
                const pendingValue = document.querySelector('.kpi-card.pending .kpi-value');
                const paidValue = document.querySelector('.kpi-card.paid .kpi-value');
                if (pendingValue) {
                    const newPending = 'R$ ' + data.kpis.total_pending.toFixed(2).replace('.', ',');
                    if (pendingValue.textContent.trim() !== newPending) {
                        pendingValue.textContent = newPending;
                    }
                }
                if (paidValue) {
                    const newPaid = 'R$ ' + data.kpis.total_paid.toFixed(2).replace('.', ',');
                    if (paidValue.textContent.trim() !== newPaid) {
                        paidValue.textContent = newPaid;
                    }
                }
            }
        } catch (e) {
            // silencioso — polling não deve quebrar a experiência
        }
        setTimeout(checkStatus, POLL_INTERVAL);
    }

    setTimeout(checkStatus, POLL_INTERVAL);
})();

// ---------------------------------------------------------------------------
// PIX Modal
// ---------------------------------------------------------------------------
async function openPix(pagamentoId, btn) {
    const modal = document.getElementById('pix-' + pagamentoId);
    if (!modal) return;

    // Toggle — se já está visível, esconde
    if (modal.style.display !== 'none') {
        modal.style.display = 'none';
        return;
    }

    modal.style.display = 'block';
    const loading = modal.querySelector('.pix-loading');
    const content = modal.querySelector('.pix-content');
    loading.style.display = 'block';
    content.style.display = 'none';

    // Desabilitar botão enquanto carrega
    btn.disabled = true;
    const originalHTML = btn.innerHTML;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Gerando...';

    try {
        const resp = await fetch('/portal/api/pay/' + pagamentoId, {
            method: 'POST',
            credentials: 'same-origin',
        });

        if (resp.status === 401) {
            window.location.href = '/portal';
            return;
        }

        const data = await resp.json();

        if (data.error) {
            loading.innerHTML = '<i class="fas fa-exclamation-triangle"></i> ' + data.error;
            btn.innerHTML = originalHTML;
            btn.disabled = false;
            return;
        }

        // Preencher QR code
        const qrDiv = content.querySelector('.pix-qr');
        if (data.qr_code_base64) {
            qrDiv.innerHTML = '<img src="data:image/png;base64,' + data.qr_code_base64 + '" alt="QR Code PIX">';
        } else {
            qrDiv.innerHTML = '';
        }

        // Preencher código PIX
        const codeInput = content.querySelector('.pix-code');
        codeInput.value = data.pix_payload || '';

        // Link para Asaas
        const link = content.querySelector('.pix-link');
        if (data.payment_link) {
            link.href = data.payment_link;
            link.style.display = 'inline-flex';
        }

        loading.style.display = 'none';
        content.style.display = 'flex';

    } catch (err) {
        loading.innerHTML = '<i class="fas fa-exclamation-triangle"></i> Erro ao gerar PIX. Tente novamente.';
    }

    btn.innerHTML = originalHTML;
    btn.disabled = false;
}

// ---------------------------------------------------------------------------
// Copiar PIX
// ---------------------------------------------------------------------------
function copyPix(btn) {
    const input = btn.parentElement.querySelector('.pix-code');
    if (!input || !input.value) return;

    navigator.clipboard.writeText(input.value).then(() => {
        btn.classList.add('copied');
        const icon = btn.querySelector('i');
        icon.className = 'fas fa-check';

        setTimeout(() => {
            btn.classList.remove('copied');
            icon.className = 'fas fa-copy';
        }, 2000);
    }).catch(() => {
        // Fallback para navegadores antigos
        input.select();
        document.execCommand('copy');
        btn.classList.add('copied');
        setTimeout(() => btn.classList.remove('copied'), 2000);
    });
}
