document.addEventListener('DOMContentLoaded', () => {
    const loader = document.getElementById('loader');
    const bodyDataset = document.body ? document.body.dataset : {};
    const dashboardConfig = {
        testMode: bodyDataset.testMode === 'true',
        testGroupChatId: bodyDataset.testGroupChatId || '',
        officialGroupChatId: bodyDataset.officialGroupChatId || '',
        autoRefreshIntervalMs: Number(bodyDataset.autoRefreshIntervalMs || 20000) || 20000,
    };

    let chartInstance = null;
    let globalCustomersMap = {}; // Global dictionary for late binding
    let lastMetricsData = null; // Store last fetched metrics for filtering
    let currentClosedSearch = ''; // Search term for closed packages
    let metricsRefreshInFlight = false;
    let autoRefreshTimer = null;
    let realtimeSource = null;
    let realtimeReconnectTimer = null;
    let lastRealtimeSignature = '';

    function getCustomerName(phone, fallbackName) {
        if (!phone) return fallbackName || 'Desconhecido';
        const cleanPhone = phone.toString().replace(/\D/g, '');
        return globalCustomersMap[cleanPhone] || fallbackName || 'Desconhecido';
    }

    function getGroupBadgeMeta(item) {
        if (!item) return null;
        const chatId = String(item.chat_id || item.chatId || '').trim();
        const fallbackKind = chatId && chatId === dashboardConfig.testGroupChatId
            ? 'test'
            : (chatId && chatId === dashboardConfig.officialGroupChatId ? 'official' : '');
        const label = item.group_label || (fallbackKind === 'test'
            ? 'Grupo de teste'
            : (fallbackKind === 'official' ? 'Grupo oficial' : ''));
        if (!label) return null;
        const kind = item.group_kind || (item.is_test_group ? 'test' : (fallbackKind || 'official'));
        return {
            kind,
            label
        };
    }

    function createGroupBadge(item, extraClass = '') {
        const meta = getGroupBadgeMeta(item);
        if (!meta) return null;
        const badge = document.createElement('span');
        badge.className = `group-pill ${meta.kind === 'test' ? 'test' : 'default'} ${extraClass}`.trim();
        badge.textContent = meta.label;
        return badge;
    }

    function buildGroupBadgeHtml(item, extraClass = '') {
        const meta = getGroupBadgeMeta(item);
        if (!meta) return '';
        const cssClass = `group-pill ${meta.kind === 'test' ? 'test' : 'default'} ${extraClass}`.trim();
        return `<span class="${cssClass}">${meta.label}</span>`;
    }

    function isSectionVisible(sectionId) {
        const section = document.getElementById(sectionId);
        return !!section && section.style.display !== 'none';
    }

    function stopAutoRefreshPolling() {
        if (autoRefreshTimer) {
            clearInterval(autoRefreshTimer);
            autoRefreshTimer = null;
        }
    }

    function startAutoRefreshPolling() {
        if (autoRefreshTimer) return;
        autoRefreshTimer = setInterval(performAutoRefresh, dashboardConfig.autoRefreshIntervalMs);
    }

    function clearRealtimeReconnectTimer() {
        if (realtimeReconnectTimer) {
            clearTimeout(realtimeReconnectTimer);
            realtimeReconnectTimer = null;
        }
    }

    function closeRealtimeStream() {
        if (realtimeSource) {
            realtimeSource.close();
            realtimeSource = null;
        }
    }

    function scheduleRealtimeReconnect() {
        clearRealtimeReconnectTimer();
        if (document.hidden) return;
        realtimeReconnectTimer = setTimeout(() => {
            realtimeReconnectTimer = null;
            connectRealtimeStream();
        }, 5000);
    }

    function handleRealtimePayload(payload) {
        const changed = payload && Array.isArray(payload.changed) ? payload.changed : [];
        const signature = JSON.stringify((payload && payload.state) || {});
        if (signature && signature === lastRealtimeSignature && changed.length === 0) return;
        if (signature) lastRealtimeSignature = signature;

        if (changed.includes('dashboard')) {
            performAutoRefresh();
            return;
        }

        if (changed.includes('finance') && isSectionVisible('section-finance')) {
            loadFinanceData(currentFinancePage, { silent: true });
        }
        if (changed.includes('customers') && isSectionVisible('section-customers')) {
            loadCustomersData({ silent: true });
        }
    }

    function connectRealtimeStream() {
        if (!window.EventSource || realtimeSource) {
            if (!window.EventSource) startAutoRefreshPolling();
            return;
        }

        clearRealtimeReconnectTimer();
        const source = new EventSource('/api/stream/dashboard');
        realtimeSource = source;

        source.onopen = () => {
            // NÃO parar o polling — mantém como backup caso SSE caia
        };

        source.addEventListener('ready', () => {
            // SSE pronto — dados vão chegar por aqui também
        });

        source.addEventListener('update', (event) => {
            try {
                const payload = JSON.parse(event.data || '{}');
                handleRealtimePayload(payload);
            } catch (error) {
                console.error('Falha ao processar atualização em tempo real:', error);
            }
        });

        source.addEventListener('ping', () => {
            // keepalive — conexão está viva
        });

        source.onerror = () => {
            if (realtimeSource !== source) return;
            closeRealtimeStream();
            startAutoRefreshPolling();
            scheduleRealtimeReconnect();
        };
    }

    // Navigation Logic
    const navLinks = document.querySelectorAll('.nav-item');
    const sections = {
        'dashboard': document.getElementById('section-dashboard'),
        'customers': document.getElementById('section-customers'),
        'finance': document.getElementById('section-finance'),
        'inventory': document.getElementById('section-inventory'),
        'settings': document.getElementById('section-settings')
    };

    navLinks.forEach(link => {
        link.addEventListener('click', (e) => {
            const target = link.getAttribute('data-target');
            // Sem data-target: é link real (ex: /logout) — deixa navegar normalmente
            if (!target) return;
            e.preventDefault();

            // Update active state in sidebar
            navLinks.forEach(l => l.classList.remove('active'));
            link.classList.add('active');

            // Show target section, hide others
            Object.keys(sections).forEach(key => {
                const section = sections[key];
                if (section) {
                    if (key === target) {
                        section.style.display = 'block';
                        // Specific initialization
                        if (key === 'dashboard') performAutoRefresh();
                        if (key === 'finance') loadFinanceData();
                        if (key === 'customers') loadCustomersData();
                    } else {
                        section.style.display = 'none';
                    }
                }
            });
        });
    });

    // Finance Logic
    let currentFinanceFilter = 'all';
    let currentFinanceSearch = '';
    let currentFinancePage = 1;
    const financePageSize = 50;
    let financeHasPrevPage = false;
    let financeHasNextPage = false;
    let financeTotalItems = 0;
    let financeSearchTimer = null;
    let allCharges = [];
    let queueByCharge = {};
    let financeCharts = {};
    let queueRefreshTimer = null;
    let currentEditingPackageId = null;
    let currentEditingMode = null;
    let editAvailableVotes = [];
    let editSelectedVotes = [];
    let draggedVoteId = null;

    const queueStatusTranslations = {
        'queued': 'Na fila',
        'sending': 'Enviando',
        'retry': 'Tentando novamente',
        'error': 'Erro',
        'sent': 'Enviado'
    };

    async function loadFinanceData(page = currentFinancePage, options = {}) {
        const tbody = document.getElementById('finance-table-body');
        if (!tbody) return;

        const silent = options.silent === true;
        // Preservar scroll position durante refresh silencioso
        const scrollY = silent ? window.scrollY : 0;

        if (!silent) {
            tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; padding: 2rem;">Carregando...</td></tr>';
        }
        currentFinancePage = Math.max(1, page || 1);

        try {
            const params = new URLSearchParams({
                page: String(currentFinancePage),
                page_size: String(financePageSize)
            });
            if (currentFinanceFilter && currentFinanceFilter !== 'all') {
                params.set('status', currentFinanceFilter);
            }
            if (currentFinanceSearch) {
                params.set('search', currentFinanceSearch);
            }

            const [chargesRes, statsRes, queueRes] = await Promise.all([
                fetch(`/api/finance/charges?${params.toString()}`),
                fetch('/api/finance/stats'),
                fetch('/api/finance/queue')
            ]);

            const chargesPayload = await chargesRes.json().catch(() => null);
            const stats = await statsRes.json().catch(() => null);
            const queue = await queueRes.json().catch(() => null);

            if (!chargesRes.ok) throw new Error('Erro ao carregar cobrancas');

            if (Array.isArray(chargesPayload)) {
                allCharges = chargesPayload;
                financeHasPrevPage = false;
                financeHasNextPage = false;
                financeTotalItems = allCharges.length;
            } else {
                allCharges = Array.isArray(chargesPayload?.items) ? chargesPayload.items : [];
                currentFinancePage = Number(chargesPayload?.page || currentFinancePage || 1);
                financeHasPrevPage = Boolean(chargesPayload?.has_prev);
                financeHasNextPage = Boolean(chargesPayload?.has_next);
                financeTotalItems = Number(chargesPayload?.total || 0);
            }
            queueByCharge = queue && queue.charge_jobs ? queue.charge_jobs : {};
            updateFinanceSummary(stats);
            renderFinanceTable(tbody, allCharges, queueByCharge);
            renderFinancePagination();

            if (stats) renderFinanceCharts(stats);

            // Restaurar scroll após render silencioso
            if (silent && scrollY > 0) {
                window.scrollTo(0, scrollY);
            }
        } catch (error) {
            console.error('Error loading finance data:', error);
            if (!silent) {
                tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; padding: 2rem;">Erro ao carregar dados financeiros.</td></tr>';
            }
        }
    }

    function renderFinanceCharts(stats) {
        const revenueCtx = document.getElementById('finance-revenue-chart')?.getContext('2d');
        if (!revenueCtx) return;

        const labels = Object.keys(stats.timeline);
        const createdData = labels.map(date => stats.timeline[date].created);
        const paidData = labels.map(date => stats.timeline[date].paid);

        // Se já existe, apenas atualizar os dados (sem piscar)
        if (financeCharts.revenue) {
            financeCharts.revenue.data.labels = labels;
            financeCharts.revenue.data.datasets[0].data = createdData;
            financeCharts.revenue.data.datasets[1].data = paidData;
            financeCharts.revenue.update('none'); // 'none' = sem animação
            return;
        }

        // Primeira renderização: criar do zero
        financeCharts.revenue = new Chart(revenueCtx, {
            type: 'line',
            data: {
                labels: labels,
                datasets: [
                    {
                        label: 'Cobranças (R$)',
                        data: createdData,
                        borderColor: '#8B4444',
                        backgroundColor: 'rgba(139, 68, 68, 0.1)',
                        fill: true,
                        tension: 0.3
                    },
                    {
                        label: 'Pago (R$)',
                        data: paidData,
                        borderColor: '#3E5A44',
                        backgroundColor: 'rgba(62, 90, 68, 0.1)',
                        fill: true,
                        tension: 0.3
                    }
                ]
            },
            options: {
                responsive: true,
                plugins: {
                    legend: { display: true, position: 'top' }
                },
                interaction: {
                    mode: 'index',
                    intersect: false,
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        grid: { color: 'rgba(0,0,0,0.05)' }
                    },
                    x: {
                        grid: { display: false }
                    }
                }
            }
        });
    }

    function updateFinanceSummary(stats) {
        // F-041: "Porcentagem Pagos" agora é calculada por VALOR (R$), não
        // por contagem de cobranças. Mais significativo pro fluxo do negócio:
        // "de cada R$ 100 cobrados, quantos R$ entraram no caixa".
        // Exclui cobranças canceladas do cálculo.
        //   ratio = total_paid / (total_pending + total_paid) * 100
        const safeStats = stats || {};
        const pendingTotal = Number(safeStats.total_pending ?? 0);
        const pendingCount = Number(safeStats.pending_count ?? 0);
        const paidTotal = Number(safeStats.total_paid ?? 0);
        const paidTodayTotal = Number(safeStats.paid_today_total ?? 0);
        const paidTodayCount = Number(safeStats.paid_today_count ?? 0);

        const activeValue = pendingTotal + paidTotal;
        const conversionRate = activeValue > 0 ? (paidTotal / activeValue) * 100 : 0;

        const formatter = new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' });

        document.getElementById('finance-pending-total').textContent = formatter.format(pendingTotal);
        document.getElementById('finance-pending-count').textContent = `${pendingCount} cobranças`;

        document.getElementById('finance-paid-today').textContent = formatter.format(paidTodayTotal);
        document.getElementById('finance-paid-count').textContent = `${paidTodayCount} cobranças`;

        document.getElementById('finance-conversion-rate').textContent = `${conversionRate.toFixed(1)}%`;
        const conversionAmountEl = document.getElementById('finance-conversion-amount');
        if (conversionAmountEl) {
            conversionAmountEl.textContent = `${formatter.format(paidTotal)} / ${formatter.format(activeValue)}`;
        }
    }

    function renderFinancePagination() {
        const summary = document.getElementById('finance-pagination-summary');
        const prevBtn = document.getElementById('finance-page-prev');
        const nextBtn = document.getElementById('finance-page-next');
        const jumpSelect = document.getElementById('finance-page-jump');
        const totalPages = Math.max(1, Math.ceil((financeTotalItems || 0) / financePageSize));
        if (summary) {
            const filterLabel = currentFinanceFilter && currentFinanceFilter !== 'all' ? `, filtro: ${currentFinanceFilter}` : '';
            const searchLabel = currentFinanceSearch ? `, busca: \"${currentFinanceSearch}\"` : '';
            summary.textContent = `Pagina ${currentFinancePage} de ${totalPages} (${financeTotalItems || allCharges.length} itens)${filterLabel}${searchLabel}`;
        }
        if (jumpSelect) {
            // Popular opções se mudou total de paginas ou se vazio
            const current = String(currentFinancePage);
            if (jumpSelect.dataset.total !== String(totalPages)) {
                jumpSelect.innerHTML = '';
                for (let p = 1; p <= totalPages; p++) {
                    const opt = document.createElement('option');
                    opt.value = String(p);
                    opt.textContent = `Pág. ${p}`;
                    jumpSelect.appendChild(opt);
                }
                jumpSelect.dataset.total = String(totalPages);
            }
            jumpSelect.value = current;
            jumpSelect.disabled = totalPages <= 1;
        }
        if (prevBtn) prevBtn.disabled = !financeHasPrevPage;
        if (nextBtn) nextBtn.disabled = !financeHasNextPage;
    }

    function renderFinanceTable(tbody, charges, queueMap = {}) {
        tbody.innerHTML = '';

        if (!charges || charges.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; padding: 2rem;">Nenhuma cobranca registrada.</td></tr>';
            return;
        }

        charges.forEach(charge => {
            const tr = document.createElement('tr');
            if (charge.is_test_group) tr.classList.add('finance-row-test-group');
            const formatter = new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' });
            const total = formatter.format(charge.total_amount || 0);

            let titleAttr = '';
            if (charge.subtotal && charge.commission_amount) {
                const sub = formatter.format(charge.subtotal);
                const comm = formatter.format(charge.commission_amount);
                titleAttr = `title="Base: ${sub} + Assessoria (R$5/peça): ${comm}"`;
            }

            // Data de Pagamento: só preenche quando status='paid'.
            // Usa updated_at (instante em que o sistema marcou como pago,
            // com hora real). paid_at do Asaas vem só com DATE, então
            // cairia sempre em 00:00 — não serve pra exibir hora.
            const payDateRaw = charge.status === 'paid'
                ? (charge.updated_at || charge.paid_at)
                : null;
            const date = payDateRaw
                ? new Date(payDateRaw).toLocaleString('pt-BR', {
                    day: '2-digit', month: '2-digit', year: 'numeric',
                    hour: '2-digit', minute: '2-digit',
                })
                : '—';

            let statusHtml = '';
            if (charge.status === 'paid') {
                statusHtml = '<span class="status-badge paid clickable-status" data-chargeid="' + charge.id + '" data-current="paid" title="Clique para alterar status" style="cursor:pointer;">Pago</span>';
            } else if (charge.status === 'enviando') {
                statusHtml = '<span class="status-badge enviando">Enviando</span>';
            } else if (charge.status === 'erro no envio') {
                statusHtml = '<span class="status-badge error">Erro no envio</span>';
            } else if (charge.status === 'cancelled' || charge.status === 'cancelado') {
                // F-036: cobrança cancelada — não conta no débito nem no total pago
                statusHtml = '<span class="status-badge cancelled clickable-status" data-chargeid="' + charge.id + '" data-current="cancelled" title="Clique para reverter para pendente" style="cursor:pointer; background:rgba(107,114,128,0.15); color:#6b7280;">Cancelado</span>';
            } else {
                statusHtml = '<span class="status-badge pending clickable-status" data-chargeid="' + charge.id + '" data-current="pending" title="Clique para alterar status" style="cursor:pointer;">Pendente</span>';
            }
            const queueJob = queueMap?.[charge.id];
            if (queueJob && ['queued', 'sending', 'retry'].includes(queueJob.status || '')) {
                const queueLabel = queueStatusTranslations[queueJob.status] || 'em processamento';
                statusHtml += `<div class="sub-status">${queueLabel.toLowerCase()}</div>`;
            }
            const groupBadgeHtml = '';  // Removido: "Grupo oficial/teste" poluía sem agregar

            tr.innerHTML = `
                <td>
                    <div style="font-weight: 600;">${getCustomerName(charge.customer_phone, charge.customer_name)}</div>
                    <div style="font-size: 0.8rem; color: var(--text-secondary);">${charge.customer_phone || ''}</div>
                </td>
                <td>
                    <div style="font-size: 0.9rem;">${charge.poll_title || 'Enquete'}</div>
                    ${groupBadgeHtml ? `<div class="finance-group-row">${groupBadgeHtml}</div>` : ''}
                </td>
                <td style="font-family: 'Outfit', sans-serif; font-weight: 600;" ${titleAttr}>${total}</td>
                <td>${statusHtml}</td>
                <td style="font-size: 0.85rem; color: var(--text-secondary);">${date}</td>
                <td style="text-align: right;">
                  <div style="display: inline-flex; gap: 0.4rem; align-items: center; vertical-align: middle;">
                    <button class="btn-package-action btn-cancel-charge"
                            title="Cancelar cobranca (mantém histórico)"
                            data-chargeid="${charge.id}"
                            style="background: rgba(107, 114, 128, 0.12); color: #6b7280; width: 30px; height: 30px;">
                        <i class="fas fa-ban"></i>
                    </button>
                    <button class="btn-package-action btn-delete-charge"
                            title="Excluir cobranca definitivamente"
                            data-chargeid="${charge.id}"
                            style="background: rgba(239, 68, 68, 0.1); color: var(--danger); width: 30px; height: 30px;">
                        <i class="fas fa-trash-alt"></i>
                    </button>
                  </div>
                </td>
            `;
            tbody.appendChild(tr);
        });

        tbody.querySelectorAll('.btn-delete-charge').forEach(btn => {
            btn.addEventListener('click', async (e) => {
                e.stopPropagation();
                const chargeId = btn.getAttribute('data-chargeid');
                if (!chargeId) return;

                if (confirm('Deseja realmente excluir esta cobranca? Esta acao nao pode ser desfeita.')) {
                    try {
                        const response = await fetch(`/api/finance/charges/${chargeId}`, { method: 'DELETE' });
                        const payload = await response.json().catch(() => ({}));
                        if (response.ok) {
                            loadFinanceData(currentFinancePage);
                        } else {
                            alert('Erro ao excluir: ' + (payload.detail || 'Falha desconhecida'));
                        }
                    } catch (error) {
                        console.error('Error deleting charge:', error);
                        alert('Falha na conexao com o servidor.');
                    }
                }
            });
        });

        // Status toggle click handler (paid ↔ pending; cancelled → pending)
        tbody.querySelectorAll('.clickable-status').forEach(badge => {
            badge.addEventListener('click', async (e) => {
                e.stopPropagation();
                const chargeId = badge.getAttribute('data-chargeid');
                const current = badge.getAttribute('data-current');
                if (!chargeId) return;

                let newStatus;
                if (current === 'paid') newStatus = 'pending';
                else if (current === 'cancelled') newStatus = 'pending';
                else newStatus = 'paid';

                const label = newStatus === 'paid' ? 'Pago' : 'Pendente';
                // F-039: ao tentar reverter paid → pending, exige confirmação
                // extra explicando que o Asaas pode ter marcado como pago
                // automaticamente. Pra evitar reverter pagamentos reais por
                // engano.
                if (current === 'paid' && newStatus === 'pending') {
                    const ok = confirm(
                        'ATENÇÃO: esta cobrança está marcada como Pago. ' +
                        'O pagamento pode ter sido confirmado automaticamente pelo Asaas. ' +
                        '\n\nTem certeza que quer reverter para Pendente? ' +
                        'Isso só deve ser feito se foi marcada como paga por engano.'
                    );
                    if (!ok) return;
                } else if (!confirm('Alterar status para ' + label + '?')) {
                    return;
                }

                try {
                    const response = await fetch('/api/finance/charges/' + chargeId + '/status', {
                        method: 'PATCH',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ status: newStatus })
                    });
                    if (response.ok) {
                        loadFinanceData(currentFinancePage);
                    } else {
                        const payload = await response.json().catch(() => ({}));
                        alert('Erro: ' + (payload.detail || 'Falha ao atualizar status'));
                    }
                } catch (error) {
                    console.error('Error toggling status:', error);
                    alert('Falha na conexao com o servidor.');
                }
            });
        });

        // F-036: botão cancelar cobrança (mantém a linha, só muda status)
        tbody.querySelectorAll('.btn-cancel-charge').forEach(btn => {
            btn.addEventListener('click', async (e) => {
                e.stopPropagation();
                const chargeId = btn.getAttribute('data-chargeid');
                if (!chargeId) return;
                if (!confirm('Cancelar esta cobranca? O valor sai do débito do cliente mas o histórico é mantido.')) return;
                try {
                    const response = await fetch('/api/finance/charges/' + chargeId + '/status', {
                        method: 'PATCH',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ status: 'cancelled' })
                    });
                    if (response.ok) {
                        loadFinanceData(currentFinancePage);
                    } else {
                        const payload = await response.json().catch(() => ({}));
                        alert('Erro: ' + (payload.detail || 'Falha ao cancelar cobranca'));
                    }
                } catch (error) {
                    console.error('Error cancelling charge:', error);
                    alert('Falha na conexao com o servidor.');
                }
            });
        });
    }

    const searchInput = document.getElementById('finance-search');
    if (searchInput) {
        searchInput.addEventListener('input', (e) => {
            currentFinanceSearch = e.target.value;
            currentFinancePage = 1;
            if (financeSearchTimer) clearTimeout(financeSearchTimer);
            financeSearchTimer = setTimeout(() => loadFinanceData(1), 250);
        });
    }

    document.getElementById('finance-page-prev')?.addEventListener('click', () => {
        if (!financeHasPrevPage) return;
        loadFinanceData(currentFinancePage - 1);
    });

    document.getElementById('finance-page-next')?.addEventListener('click', () => {
        if (!financeHasNextPage) return;
        loadFinanceData(currentFinancePage + 1);
    });

    document.getElementById('finance-page-jump')?.addEventListener('change', (e) => {
        const target = parseInt(e.target.value, 10);
        if (Number.isFinite(target) && target >= 1 && target !== currentFinancePage) {
            loadFinanceData(target);
        }
    });

    // ----- Extrato de Pagamentos -----
    let lastExtractData = null;
    let extractKind = 'paid'; // 'paid' ou 'pending'

    function fmtBR(value) {
        return new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(Number(value || 0));
    }

    async function loadExtractData() {
        const dateFrom = document.getElementById('extractDateFrom')?.value || '';
        const dateTo = document.getElementById('extractDateTo')?.value || '';
        const params = new URLSearchParams();
        if (dateFrom) params.set('date_from', dateFrom);
        if (dateTo) params.set('date_to', dateTo);
        params.set('kind', extractKind);
        const response = await fetch('/api/finance/extract?' + params.toString());
        if (!response.ok) throw new Error('Erro ao carregar extrato');
        return response.json();
    }

    function renderExtractData(data) {
        lastExtractData = data;
        const tbody = document.getElementById('extract-table-body');
        const countEl = document.getElementById('extractCount');
        const totalEl = document.getElementById('extractTotal');
        const countLabel = document.getElementById('extractCountLabel');
        const dateColLabel = document.getElementById('extractDateColLabel');
        const desc = document.getElementById('extractModalDesc');
        if (!tbody) return;

        const isPaid = extractKind === 'paid';
        if (countEl) countEl.textContent = data.count || 0;
        if (totalEl) {
            totalEl.textContent = fmtBR(data.total || 0);
            totalEl.style.color = isPaid ? 'var(--success)' : 'var(--warning)';
        }
        if (countLabel) countLabel.textContent = isPaid ? 'pagamentos' : 'cobranças em débito';
        if (dateColLabel) dateColLabel.textContent = isPaid ? 'Data do Pagamento' : 'Data da Cobrança';
        if (desc) desc.textContent = isPaid
            ? 'Pagamentos confirmados no período selecionado.'
            : 'Cobranças em débito (criadas no período selecionado e ainda não pagas).';

        const items = Array.isArray(data.items) ? data.items : [];
        if (!items.length) {
            const msg = isPaid ? 'Nenhum pagamento no período.' : 'Nenhuma cobrança em débito no período.';
            tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; padding: 1rem; color: var(--text-secondary);">${msg}</td></tr>`;
            return;
        }
        tbody.innerHTML = '';
        items.forEach(it => {
            const rawDate = isPaid ? it.paid_at : it.created_at;
            const dt = rawDate ? new Date(rawDate).toLocaleString('pt-BR') : '-';
            const valueColor = isPaid ? 'var(--success)' : 'var(--warning)';
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td style="font-size: 0.85rem;">${dt}</td>
                <td style="font-weight: 600;">${it.customer_name || 'Cliente'}</td>
                <td style="font-size: 0.85rem; color: var(--text-secondary);">${it.customer_phone || '-'}</td>
                <td style="font-size: 0.85rem;">${it.product_name || '-'}</td>
                <td style="text-align:center; font-family: 'Outfit', sans-serif;">${it.qty || 0}</td>
                <td style="text-align:right; font-weight: 700; color: ${valueColor};">${fmtBR(it.total_amount)}</td>
            `;
            tbody.appendChild(tr);
        });
    }

    function downloadExtractCSV() {
        if (!lastExtractData || !Array.isArray(lastExtractData.items) || !lastExtractData.items.length) {
            alert('Sem dados para baixar.');
            return;
        }
        const isPaid = extractKind === 'paid';
        const dateColName = isPaid ? 'Data do Pagamento' : 'Data da Cobrança';
        const headers = [dateColName, 'Cliente', 'Telefone', 'Produto', 'Pecas', 'Valor (R$)'];
        const rows = lastExtractData.items.map(it => {
            const rawDate = isPaid ? it.paid_at : it.created_at;
            return [
                rawDate ? new Date(rawDate).toLocaleString('pt-BR') : '',
                it.customer_name || '',
                it.customer_phone || '',
                (it.product_name || '').replace(/"/g, '""'),
                it.qty || 0,
                (Number(it.total_amount || 0)).toFixed(2).replace('.', ','),
            ];
        });
        const csv = [headers, ...rows]
            .map(r => r.map(c => `"${String(c)}"`).join(';'))
            .join('\n');
        const blob = new Blob(['\ufeff' + csv], { type: 'text/csv;charset=utf-8;' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        const from = lastExtractData.date_from || 'all';
        const to = lastExtractData.date_to || 'all';
        const prefix = isPaid ? 'extrato_pagos' : 'extrato_em_debito';
        a.href = url;
        a.download = `${prefix}_${from}_a_${to}.csv`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    }

    async function openExtractModal() {
        const modal = document.getElementById('extractModal');
        if (!modal) return;

        // Default: últimos 30 dias se nada preenchido
        const dateFrom = document.getElementById('extractDateFrom');
        const dateTo = document.getElementById('extractDateTo');
        if (dateFrom && !dateFrom.value) {
            const d = new Date();
            d.setDate(d.getDate() - 30);
            dateFrom.value = d.toISOString().slice(0, 10);
        }
        if (dateTo && !dateTo.value) {
            dateTo.value = new Date().toISOString().slice(0, 10);
        }

        modal.hidden = false;
        const tbody = document.getElementById('extract-table-body');
        if (tbody) tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; padding: 1rem;">Carregando...</td></tr>';
        try {
            const data = await loadExtractData();
            renderExtractData(data);
        } catch (e) {
            if (tbody) tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; padding: 1rem;">Erro ao carregar extrato.</td></tr>';
        }
    }

    const closedSearchInput = document.getElementById('closed-packages-search');
    if (closedSearchInput) {
        closedSearchInput.addEventListener('focus', () => {
            closedSearchInput.style.width = '180px';
        });
        closedSearchInput.addEventListener('blur', () => {
            if (!closedSearchInput.value) {
                closedSearchInput.style.width = '120px';
            }
        });
        closedSearchInput.addEventListener('input', (e) => {
            currentClosedSearch = e.target.value;
            if (lastMetricsData && lastMetricsData.votos && lastMetricsData.votos.packages) {
                renderPackageRankWithActions('rank-packages-closed-today', lastMetricsData.votos.packages.closed_today || []);
            }
        });
    }

    // Filter Buttons
    document.querySelectorAll('.filter-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            currentFinanceFilter = btn.getAttribute('data-filter');
            currentFinancePage = 1;
            loadFinanceData(1);
        });
    });

    // Initialize Dashboard
    setupConfirmedEditDropzones();
    initDashboard();

    async function initDashboard() {
        try {
            const data = await fetchMetrics();
            updateUI(data);
            loader.style.opacity = '0';
            setTimeout(() => loader.style.display = 'none', 500);
        } catch (error) {
            console.error('Error initializing dashboard:', error);
            loader.style.display = 'none';
        }
    }

    async function fetchMetrics() {
        const response = await fetch('/api/metrics');
        return await response.json();
    }

    // F-053: verificar modo de teste e atualizar banner
    async function checkTestMode() {
        try {
            const r = await fetch('/api/test-mode');
            const d = await r.json();
            const banner = document.getElementById('testModeBanner');
            const text = document.getElementById('testModeBannerText');
            if (banner) {
                banner.hidden = !d.active;
                if (text && d.label) text.textContent = d.label;
            }
            // Adicionar/remover classe no body pra estilização global
            document.body.classList.toggle('test-mode-active', !!d.active);
        } catch (e) { /* silencioso */ }
    }

    function updateUI(data) {
        if (!data || data.error) return;
        checkTestMode();  // F-053

        lastMetricsData = data; // Cache for filtering and manual re-renders
        if (data.customers_map) {
            globalCustomersMap = data.customers_map;
        }

        // Metadados
        if (data.generated_at) {
            const date = new Date(data.generated_at);
            document.getElementById('genAt').textContent = date.toLocaleTimeString('pt-BR');
        }

        // KPI Update
        const v = data.votos;
        const e = data.enquetes;

        // F-044: Votos Hoje — card único com 3 comparações temporais
        // (vs ontem / vs semana passada / vs média mensal), tudo hora-a-hora.
        // Importante: passa null (não 0) quando o backend retorna null
        // para que o setSmallDiff mostre "—" (sem dados) em vez de "0%".
        document.getElementById('kpi-votos-hoje').textContent = v.today || 0;
        const nullOrNum = (x) => (x === null || x === undefined ? null : Number(x));
        setSmallDiff('kpi-votos-diff-yesterday', nullOrNum(v.pct_vs_yesterday_same_hour), 'em relação a ontem');
        setSmallDiff('kpi-votos-diff-lastweek', nullOrNum(v.pct_vs_last_week_same_weekday), 'em relação à semana passada');
        setSmallDiff('kpi-votos-diff-monthly', nullOrNum(v.pct_vs_monthly_avg), 'em relação à média mensal');

        // F-047: "Enquetes Ativas" = enquetes com status='open' AGORA (não "criadas hoje").
        // Comparativos "vs ontem" e "vs média 7 dias" vêm do snapshot horário (enquetes_open),
        // calculados no backend; ficam null até o histórico ter cobertura suficiente.
        document.getElementById('kpi-enquetes-hoje').textContent = (e.active_now != null ? e.active_now : 0);
        setSmallDiff('kpi-enquetes-diff-yesterday', nullOrNum(e.pct_vs_yesterday), 'em relação a ontem');
        setSmallDiff('kpi-enquetes-diff-7days', nullOrNum(e.pct_vs_7d_avg), 'em relação à média 7 dias');
        // 3ª linha: contagem absoluta de pacotes (closed/approved) nessas enquetes ativas
        setSmallCount('kpi-enquetes-diff-closedpkgs', e.closed_packages_on_active, 'pacotes fechados dessas enquetes');

        // Pacotes Confirmados Hoje KPI (counts from confirmed_today / confirmed summary)
        const confirmedTodayCount = (v.packages && v.packages.confirmed_today) ? v.packages.confirmed_today.length : (v.packages_summary_confirmed ? v.packages_summary_confirmed.today : 0);
        document.getElementById('kpi-pacotes-fechados-hoje').textContent = confirmedTodayCount || 0;
        const cps = v.packages_summary_confirmed || {};

        // F-050: termômetro + média histórica de closed/dia (valor cheio) + breakdown 72h
        renderSalesTemperature(cps.sales_temperature);
        const dowNames = {seg:'segunda',ter:'terça',qua:'quarta',qui:'quinta',sex:'sexta','sáb':'sábado',dom:'domingo'};
        const dowFull = dowNames[cps.dow_name] || cps.dow_name || '';
        const avgLabel = dowFull ? `média de fechamentos (${dowFull})` : 'média de fechamentos diários';
        setSmallCount('kpi-pacotes-fechados-diff-avg', cps.daily_avg_closed_historic, avgLabel);
        const closed72h = (cps.closed_72h_still_closed != null) ? cps.closed_72h_still_closed : 0;
        const approvedUnpaid72h = (cps.approved_72h_unpaid != null) ? cps.approved_72h_unpaid : 0;
        setWaitingBreakdown('kpi-pacotes-fechados-diff-waiting', closed72h, approvedUnpaid72h);

        // Rankings Update
        renderPollRank('rank-polls', v.by_poll_today || {});
        renderCustomerRank('rank-customers', v.by_customer_today || {});
        renderCustomerRank('rank-customers-week', v.by_customer_week || {});

        // Packages Update
        if (v.packages) {
            renderPackageRank('rank-packages-open', v.packages.open || []);
            renderPackageRankWithActions('rank-packages-closed-today', v.packages.closed_today || []);
            renderPackageRankConfirmed('rank-packages-confirmed-today', v.packages.confirmed_today || []);
            // rank-packages-rejected-today removido — agora está na aba Estoque
        }

        // Chart Update
        renderChart(v.by_hour || {});

        // Adjust scrollable package lists after render
        setTimeout(() => {
            setDynamicListHeight();
            enablePackageListScroll();
        }, 0);
    }

    function updateDiff(elementId, pct, suffix) {
        const el = document.getElementById(elementId);
        if (!el) return;
        const raw = pct;
        const val = Number.isFinite(raw) ? raw : null;
        // manage classes without stomping other classes
        el.classList.remove('diff-up', 'diff-down', 'diff-zero');
        if (val === null) el.classList.add('diff-zero');
        else if (val > 0) el.classList.add('diff-up');
        else if (val < 0) el.classList.add('diff-down');
        else el.classList.add('diff-zero');

        el.innerHTML = '';
        const spanVal = document.createElement('span');
        spanVal.className = 'kpi-diff-value';
        if (val === null) {
            spanVal.textContent = '—';
        } else {
            const sign = val > 0 ? '+' : (val < 0 ? '-' : '');
            spanVal.textContent = `${sign}${Math.abs(Math.round(val))}%`;
        }
        const spanLabel = document.createElement('span');
        spanLabel.className = 'kpi-diff-label';
        spanLabel.textContent = ' ' + suffix;
        el.appendChild(spanVal);
        el.appendChild(spanLabel);
    }

    // F-050: renderiza o termômetro de vendas (frio/morno/quente/pelando).
    // O HTML do elemento já tem o <button id=btn-refresh-temperature> embutido,
    // só substituímos o valor (emoji + label) e mantemos o botão no spanLabel.
    function renderSalesTemperature(temp) {
        const el = document.getElementById('kpi-pacotes-fechados-diff-temperature');
        if (!el) return;
        const spanVal = el.querySelector('.kpi-diff-value');
        const spanLabel = el.querySelector('.kpi-diff-label');
        el.classList.remove('diff-up', 'diff-down', 'diff-zero', 'temp-cold', 'temp-warm', 'temp-hot', 'temp-blazing');
        if (!temp || !temp.label) {
            if (spanVal) spanVal.textContent = '—';
            el.classList.add('diff-zero');
            return;
        }
        if (spanVal) {
            spanVal.textContent = `${temp.emoji || ''} ${temp.label}`.trim();
        }
        el.classList.add(`temp-${temp.tone || 'warm'}`);
        // tooltip com contexto completo
        const computed = temp.computed_at ? new Date(temp.computed_at).toLocaleTimeString('pt-BR') : '';
        const sampleH = temp.sample_window_hours || 3;
        const avg = temp.avg_same_window != null ? temp.avg_same_window : '—';
        const days = temp.days_with_data || '—';
        el.title = `Últimas ${sampleH}h: ${temp.closed_in_window || 0} fechados | Média nesse horário: ${avg} fechados (${days} dias de dados) | ${temp.ratio_pct}% da média | ${computed}`;
    }

    // F-050: par "X fechados • Y a pagar" na 3ª linha do card.
    // Escopo 72h pra casar com o tema do card "Pacotes Confirmados (72h)":
    //   closedCount = pacotes que fecharam nas últimas 72h e ainda não foram confirmados
    //   paymentCount = pacotes confirmados nas últimas 72h cujo pagamento ainda não é paid
    function setWaitingBreakdown(elementId, closedCount, paymentCount) {
        const el = document.getElementById(elementId);
        if (!el) return;
        el.classList.remove('diff-up', 'diff-down', 'diff-zero');
        el.classList.add('diff-zero');
        el.classList.add('small');
        el.innerHTML = '';
        const spanVal = document.createElement('span');
        spanVal.className = 'kpi-diff-value';
        spanVal.textContent = `${closedCount} • ${paymentCount}`;
        const spanLabel = document.createElement('span');
        spanLabel.className = 'kpi-diff-label';
        spanLabel.textContent = 'fechados · a pagar';
        el.appendChild(spanVal);
        el.appendChild(spanLabel);
    }

    // Variação de setSmallDiff pra mostrar uma CONTAGEM absoluta em vez de um %.
    // Usado em "N pacotes fechados nelas" no card Enquetes Ativas.
    function setSmallCount(elementId, count, label) {
        const el = document.getElementById(elementId);
        if (!el) return;
        const val = (count === null || count === undefined || !Number.isFinite(Number(count))) ? null : Number(count);
        el.classList.remove('diff-up', 'diff-down', 'diff-zero');
        el.classList.add('diff-zero'); // neutro: é contagem, não delta
        el.classList.add('small');
        el.innerHTML = '';
        const spanVal = document.createElement('span');
        spanVal.className = 'kpi-diff-value';
        spanVal.textContent = val === null ? '—' : String(val);
        const spanLabel = document.createElement('span');
        spanLabel.className = 'kpi-diff-label';
        spanLabel.textContent = label;
        el.appendChild(spanVal);
        el.appendChild(spanLabel);
    }

    function setSmallDiff(elementId, pct, label) {
        const el = document.getElementById(elementId);
        if (!el) return;
        const raw = pct;
        const val = Number.isFinite(raw) ? raw : null;
        // ensure classes
        el.classList.remove('diff-up', 'diff-down', 'diff-zero');
        if (val === null) el.classList.add('diff-zero');
        else if (val > 0) el.classList.add('diff-up');
        else if (val < 0) el.classList.add('diff-down');
        else el.classList.add('diff-zero');
        el.classList.add('small');

        el.innerHTML = '';
        const spanVal = document.createElement('span');
        spanVal.className = 'kpi-diff-value';
        if (val === null) {
            spanVal.textContent = '—';
        } else {
            const sign = val > 0 ? '+' : (val < 0 ? '-' : '');
            spanVal.textContent = `${sign}${Math.abs(Math.round(val))}%`;
        }
        const spanLabel = document.createElement('span');
        spanLabel.className = 'kpi-diff-label';
        spanLabel.textContent = label;
        el.appendChild(spanVal);
        el.appendChild(spanLabel);
    }

    function renderPollRank(containerId, pollData) {
        const container = document.getElementById(containerId);
        container.innerHTML = '';

        const sorted = Object.values(pollData).sort((a, b) => b.qty - a.qty).slice(0, 5);

        sorted.forEach((item, index) => {
            const div = document.createElement('div');
            div.className = 'rank-item rank-item-poll';

            const num = document.createElement('div');
            num.className = 'rank-number';
            num.textContent = `0${index + 1}`;

            // Thumbnail (imagem do produto)
            const thumb = document.createElement('div');
            thumb.className = 'rank-thumb';
            if (item.image) {
                const img = document.createElement('img');
                img.src = item.image;
                img.alt = item.title || '';
                img.loading = 'lazy';
                thumb.appendChild(img);
            } else {
                thumb.classList.add('rank-thumb-placeholder');
                thumb.innerHTML = '<i class="fas fa-image"></i>';
            }

            const content = document.createElement('div');
            content.className = 'rank-content';

            const name = document.createElement('div');
            name.className = 'rank-name';
            name.textContent = item.title || 'Enquete sem título';

            const meta = document.createElement('div');
            meta.className = 'rank-meta';
            const pkgCount = item.package_count || 0;
            meta.textContent = pkgCount > 0
                ? `${pkgCount} pacote${pkgCount !== 1 ? 's' : ''} fechado${pkgCount !== 1 ? 's' : ''}`
                : '';

            content.appendChild(name);
            if (meta.textContent) content.appendChild(meta);

            const badge = document.createElement('div');
            badge.className = 'rank-badge';
            badge.textContent = item.qty;

            div.appendChild(num);
            div.appendChild(thumb);
            div.appendChild(content);
            div.appendChild(badge);
            container.appendChild(div);
        });
    }

    // Conjunto global de IDs de pacotes expandidos — sobrevive aos re-renders
    if (!window._expandedPackages) window._expandedPackages = new Set();

    function buildPackageItem(pkg, index, options) {
        const { showActions = false, showConfirmedEdit = false, showSyncButton = false } = options || {};
        const div = document.createElement('div');
        // expose pkg id for DOM lookups and animations
        if (pkg && pkg.id) div.setAttribute('data-pkgid', pkg.id);
        div.className = 'rank-item clickable';
        if (pkg.rejected) div.classList.add('rejected');
        if (pkg.is_test_group) div.classList.add('test-group-item');
        // Restaurar estado expandido (se a pessoa tinha clicado pra ver membros)
        if (pkg && pkg.id && window._expandedPackages.has(pkg.id)) {
            div.classList.add('active');
        }
        div.onclick = (e) => {
            if (e.target.closest('.package-actions')) return;
            const willExpand = !div.classList.contains('active');
            div.classList.toggle('active');
            if (pkg && pkg.id) {
                if (willExpand) window._expandedPackages.add(pkg.id);
                else window._expandedPackages.delete(pkg.id);
            }
        };

        const header = document.createElement('div');
        header.style.display = 'flex';
        header.style.alignItems = 'center';
        header.style.width = '100%';
        header.style.gap = '1rem';

        const content = document.createElement('div');
        content.className = 'rank-content';

        const nameRow = document.createElement('div');
        nameRow.className = 'rank-name-row';

        const name = document.createElement('div');
        name.className = 'rank-name';
        name.textContent = pkg.poll_title;
        nameRow.appendChild(name);

        // Badge de grupo removido dos pacotes — poluía visualmente sem agregar info

        const meta = document.createElement('div');
        meta.className = 'rank-meta';

        const formatDate = (ds) => {
            if (!ds) return '';
            const d = new Date(ds);
            if (isNaN(d.getTime())) return '';
            const date = d.toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit' });
            const time = d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });
            return `${date} ${time}`;
        };

        const openedDate = formatDate(pkg.opened_at);
        const openedStr = openedDate ? `Aberto ${openedDate}` : '';

        // status priority: status field -> confirmed_at -> rejected -> closed -> awaiting
        if (pkg.status === 'confirmed' || pkg.confirmed_at) {
            const dateStr = formatDate(pkg.confirmed_at || pkg.closed_at);
            meta.textContent = `Confirmado ${dateStr}${openedStr ? ' • ' + openedStr : ''}`;
        } else if (pkg.status === 'rejected' || pkg.rejected) {
            meta.textContent = `Rejeitado${openedStr ? ' • ' + openedStr : ''}`;
        } else if (pkg.status === 'closed' || pkg.closed_at) {
            const dateStr = formatDate(pkg.closed_at);
            meta.textContent = `Fechado ${dateStr}${openedStr ? ' • ' + openedStr : ''}`;
        } else {
            meta.textContent = openedStr || 'Aguardando';
        }

        content.appendChild(nameRow);
        content.appendChild(meta);

        // Linha "Fornecedor" — disponível em pacotes abertos (separado de tag/tipo de peça)
        if (!showActions && !showConfirmedEdit && (pkg.poll_id || pkg.enquete_id)) {
            const supplierRow = document.createElement('div');
            supplierRow.className = 'pkg-supplier-row';
            const fornText = (pkg && pkg.fornecedor !== undefined && pkg.fornecedor !== null && String(pkg.fornecedor).trim() !== '')
                ? String(pkg.fornecedor).trim()
                : 'fornecedor';
            const hasForn = String(pkg.fornecedor || '').trim() !== '';
            supplierRow.innerHTML = `<i class="fas fa-truck"></i> <span class="pkg-supplier-text${hasForn ? '' : ' empty'}">${fornText}</span>`;
            supplierRow.title = hasForn ? 'Clique para editar fornecedor' : 'Clique para adicionar fornecedor';
            supplierRow.style.cursor = 'pointer';
            supplierRow.onclick = (e) => {
                e.stopPropagation();
                setEnqueteFornecedor(pkg);
            };
            content.appendChild(supplierRow);
        }

        if (pkg.payment_error) {
            const errBadge = document.createElement('div');
            errBadge.style.color = '#ef4444';
            errBadge.style.fontSize = '0.75rem';
            errBadge.style.marginTop = '0.25rem';
            errBadge.style.display = 'flex';
            errBadge.style.alignItems = 'center';
            errBadge.style.gap = '0.25rem';
            errBadge.innerHTML = `<i class="fas fa-exclamation-circle"></i> <span style="cursor:help" title="${pkg.payment_error}">Erro de Pagamento</span>`;
            
            errBadge.onclick = (e) => {
                e.stopPropagation();
                alert("Erro ao confirmar pacote:\\n" + pkg.payment_error);
            };
            content.appendChild(errBadge);
        }

        // thumbnail or placeholder (validate src and fallback on error)
        let thumb;
        const initials = (pkg.poll_title || pkg.id || '')
            .split(' ')
            .filter(Boolean)
            .slice(0,2)
            .map(s => s[0].toUpperCase())
            .join('') || 'P';

        function makePlaceholder() {
            const ph = document.createElement('div');
            ph.className = 'rank-thumb placeholder';
            ph.textContent = initials;
            return ph;
        }

        // prefer generated thumbnail if available, otherwise fallback to original image
        const thumbSrcCandidate = (pkg.image_thumb && typeof pkg.image_thumb === 'string' && pkg.image_thumb.trim())
            ? pkg.image_thumb.trim()
            : (pkg.image && typeof pkg.image === 'string' ? pkg.image.trim() : '');

        if (thumbSrcCandidate) {
            const src = thumbSrcCandidate;
            // basic check for http(s) or data URI
            if (/^(https?:\/\/|data:|\/static\/|\/files\/)/i.test(src)) {
                const img = document.createElement('img');
                img.className = 'rank-thumb';
                img.alt = pkg.poll_title || 'Produto';
                img.loading = 'lazy';
                img.src = src;
                // on error, replace image with placeholder to avoid broken icon
                img.addEventListener('error', () => {
                    const ph = makePlaceholder();
                    img.replaceWith(ph);
                });
                thumb = img;
            } else {
                thumb = makePlaceholder();
            }
        } else {
            thumb = makePlaceholder();
        }
        // do not render numeric index (removed by design)

        const badge = document.createElement('div');
        badge.className = 'rank-badge';

        // Nos pacotes fechados e confirmados, mostramos a TAG no lugar do "24 pçs".
        // Se não houver tag, exibimos "Sem tag". Clicar no badge edita a tag.
        if (showActions || showConfirmedEdit) {
            const tagText = (pkg && pkg.tag !== undefined && pkg.tag !== null && String(pkg.tag).trim() !== '')
                ? String(pkg.tag).trim()
                : 'Sem tag';
            badge.textContent = tagText;
            badge.classList.add('tag-badge');
            badge.title = 'Clique para editar a tag';
            badge.style.cursor = 'pointer';
            badge.onclick = (e) => {
                e.stopPropagation();
                setPackageTag(pkg);
            };
        } else {
            badge.textContent = `${pkg.qty} pçs`;
        }

        // header-right contains actions above the badge
        const headerRight = document.createElement('div');
        headerRight.className = 'header-right';

        if (showActions) {
            const actions = document.createElement('div');
            actions.className = 'package-actions';
            
            const btnApprove = document.createElement('button');
                btnApprove.type = 'button';
                btnApprove.className = 'btn-package-action';
                btnApprove.setAttribute('aria-label', 'Aprovar pacote');
                btnApprove.innerHTML = '<i class="fas fa-check" aria-hidden="true"></i>';
                btnApprove.onclick = (e) => { e.stopPropagation(); openConfirmModal('approve', pkg, div); };

                const btnEdit = document.createElement('button');
                btnEdit.type = 'button';
                btnEdit.className = 'btn-package-action';
                btnEdit.setAttribute('aria-label', 'Editar pacote');
                btnEdit.innerHTML = '<i class="fas fa-pen" aria-hidden="true"></i>';
                btnEdit.onclick = (e) => {
                    e.stopPropagation();
                    const newTitle = prompt('Novo título do pacote:', pkg.poll_title || '');
                    if (newTitle === null) return;
                    // optimistic UI update: visual + objeto em memória
                    const nameEl = div.querySelector('.rank-name');
                    if (nameEl) nameEl.textContent = newTitle;
                    pkg.poll_title = newTitle;  // fix: atualiza o objeto pra modal/ações subsequentes lerem o título novo
                    // try to persist change to backend (best-effort; endpoint may not exist)
                    if (pkg && pkg.id) {
                        fetch(`/api/packages/${pkg.id}/edit`, {
                            method: 'PATCH',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ poll_title: newTitle })
                        }).then(res => res.json().catch(() => ({})))
                          .then(resp => {
                              if (resp && resp.status !== 'success') console.warn('Edit não persistido:', resp);
                          }).catch(err => console.warn('Erro ao editar pacote:', err));
                    }
                };

                const btnReject = document.createElement('button');
                btnReject.type = 'button';
                btnReject.className = 'btn-package-action';
                btnReject.setAttribute('aria-label', 'Rejeitar pacote');
                btnReject.innerHTML = '<i class="fas fa-times" aria-hidden="true"></i>';
                btnReject.onclick = (e) => { e.stopPropagation(); openConfirmModal('reject', pkg, div); };

                const btnEditMembers = document.createElement('button');
                btnEditMembers.type = 'button';
                btnEditMembers.className = 'btn-package-action';
                btnEditMembers.setAttribute('aria-label', 'Editar membros do pacote');
                btnEditMembers.title = 'Trocar membros com a fila da enquete';
                btnEditMembers.innerHTML = '<i class="fas fa-users" aria-hidden="true"></i>';
                btnEditMembers.onclick = (e) => { e.stopPropagation(); openConfirmedPackageEditModal(pkg, 'closed'); };

                actions.appendChild(btnApprove);
                actions.appendChild(btnEdit);
                actions.appendChild(btnEditMembers);
                actions.appendChild(btnReject);
                headerRight.appendChild(actions);
            } else if (showConfirmedEdit) {
            const actions = document.createElement('div');
            actions.className = 'package-actions';

            // Download PDF ao lado do editar: 📦 antes de ✏️ (somente API por id do pacote)
            if (pkg.id) {
                const btnDownload = document.createElement('a');
                btnDownload.href = `/api/packages/${encodeURIComponent(pkg.id)}/pdf`;
                btnDownload.target = '_blank';
                btnDownload.rel = 'noopener noreferrer';
                btnDownload.className = 'btn-package-action';
                btnDownload.style.textDecoration = 'none';
                btnDownload.style.display = 'flex';
                btnDownload.style.alignItems = 'center';
                btnDownload.style.justifyContent = 'center';
                btnDownload.title = 'Baixar PDF do Estoque';
                btnDownload.setAttribute('aria-label', 'Baixar PDF do Estoque');
                btnDownload.innerHTML = '<i class="fas fa-file-pdf" aria-hidden="true"></i>';
                btnDownload.onclick = (e) => e.stopPropagation();
                actions.appendChild(btnDownload);
            }

            const btnEditConfirmed = document.createElement('button');
            btnEditConfirmed.type = 'button';
            btnEditConfirmed.className = 'btn-package-action';
            btnEditConfirmed.setAttribute('aria-label', 'Editar pacote confirmado');
            btnEditConfirmed.title = 'Editar votos do pacote confirmado';
            btnEditConfirmed.innerHTML = '<i class="fas fa-pen" aria-hidden="true"></i>';
            btnEditConfirmed.onclick = (e) => {
                e.stopPropagation();
                openConfirmedPackageEditModal(pkg);
            };
            actions.appendChild(btnEditConfirmed);

            const btnCancel = document.createElement('button');
            btnCancel.type = 'button';
            btnCancel.className = 'btn-package-action';
            btnCancel.setAttribute('aria-label', 'Cancelar pacote');
            btnCancel.title = 'Cancelar pacote (estoque esgotou, etc)';
            btnCancel.innerHTML = '<i class="fas fa-ban" aria-hidden="true"></i>';
            btnCancel.onclick = (e) => {
                e.stopPropagation();
                cancelConfirmedPackage(pkg);
            };
            actions.appendChild(btnCancel);
            headerRight.appendChild(actions);
        } else if (showSyncButton && (pkg.enquete_id || pkg.poll_id)) {
            // Botão de sincronização manual via WHAPI (apenas enquetes abertas)
            const actions = document.createElement('div');
            actions.className = 'package-actions';
            const btnSync = document.createElement('button');
            btnSync.type = 'button';
            btnSync.className = 'btn-package-action';
            btnSync.setAttribute('aria-label', 'Sincronizar votos com WHAPI');
            btnSync.title = 'Sincronizar votos com WhatsApp';
            const ICON_SYNC_IDLE = '<i class="fas fa-sync-alt" aria-hidden="true"></i>';
            const ICON_SYNC_LOADING = '<i class="fas fa-spinner fa-spin" aria-hidden="true"></i>';
            const ICON_SYNC_OK = '<i class="fas fa-check" aria-hidden="true"></i>';
            const ICON_SYNC_ERR = '<i class="fas fa-times" aria-hidden="true"></i>';
            btnSync.innerHTML = ICON_SYNC_IDLE;
            btnSync.onclick = async (e) => {
                e.stopPropagation();
                const enqueteId = pkg.enquete_id || pkg.poll_id;
                btnSync.innerHTML = ICON_SYNC_LOADING;
                btnSync.disabled = true;
                try {
                    const r = await fetch(`/api/admin/polls/${encodeURIComponent(enqueteId)}/resync`, { method: 'POST' });
                    const data = await r.json();
                    if (!r.ok) throw new Error(data.detail || 'Erro no servidor');
                    const ins = data.applied || 0;
                    const rem = data.removed || 0;
                    if (ins + rem === 0) {
                        btnSync.innerHTML = ICON_SYNC_OK;
                        btnSync.title = 'Já estava sincronizado';
                    } else {
                        btnSync.innerHTML = ICON_SYNC_OK;
                        btnSync.title = `${ins > 0 ? ins + ' votos inseridos' : ''}${rem > 0 ? (ins > 0 ? ', ' : '') + rem + ' removidos' : ''}`;
                        // Recarrega métricas pra refletir
                        if (typeof loadMetrics === 'function') loadMetrics();
                    }
                } catch (err) {
                    console.error('Sync error:', err);
                    btnSync.innerHTML = ICON_SYNC_ERR;
                    btnSync.title = 'Erro ao sincronizar: ' + err.message;
                } finally {
                    btnSync.disabled = false;
                    setTimeout(() => { btnSync.innerHTML = ICON_SYNC_IDLE; btnSync.title = 'Sincronizar votos com WhatsApp'; }, 5000);
                }
            };
            actions.appendChild(btnSync);
            headerRight.appendChild(actions);
        } else {
            const placeholderActions = document.createElement('div');
            placeholderActions.className = 'package-actions placeholder';
            placeholderActions.setAttribute('aria-hidden', 'true');
            placeholderActions.tabIndex = -1;
            headerRight.appendChild(placeholderActions);
        }

        // Download PDF em outras colunas (ex.: fechados) — pacotes confirmados usam o bloco showConfirmedEdit acima
        if (pkg.pdf_file_name && pkg.id && !showConfirmedEdit) {
            const btnDownload = document.createElement('a');
            btnDownload.href = `/api/packages/${encodeURIComponent(pkg.id)}/pdf`;
            btnDownload.target = '_blank';
            btnDownload.rel = 'noopener noreferrer';
            btnDownload.className = 'btn-package-action';
            btnDownload.style.textDecoration = 'none';
            btnDownload.style.display = 'flex';
            btnDownload.style.alignItems = 'center';
            btnDownload.style.justifyContent = 'center';
            btnDownload.title = 'Baixar PDF do Estoque';
            btnDownload.innerHTML = '<i class="fas fa-file-pdf" aria-hidden="true"></i>';
            btnDownload.onclick = (e) => e.stopPropagation();

            let targetActions = headerRight.querySelector('.package-actions:not(.placeholder)');
            if (!targetActions) {
                const placeholder = headerRight.querySelector('.package-actions.placeholder');
                if (placeholder) placeholder.remove();

                targetActions = document.createElement('div');
                targetActions.className = 'package-actions';
                headerRight.prepend(targetActions);
            }
            targetActions.appendChild(btnDownload);
        }

        headerRight.appendChild(badge);

        // assemble header: thumb, content, header-right
        header.appendChild(thumb);
        header.appendChild(content);
        header.appendChild(headerRight);

        const details = document.createElement('div');
        details.className = 'package-details';
        const votesSorted = (pkg.votes || []).slice().sort((a, b) => b.qty - a.qty);
        votesSorted.forEach(v => {
            const member = document.createElement('div');
            member.className = 'member-item';
            const info = document.createElement('div');
            info.className = 'member-info';
            const spanName = document.createElement('span');
            spanName.textContent = getCustomerName(v.phone, v.name);
            const spanPhone = document.createElement('span');
            spanPhone.className = 'member-phone';
            spanPhone.textContent = v.phone || '';
            info.appendChild(spanName);
            info.appendChild(spanPhone);
            const qtyDiv = document.createElement('div');
            qtyDiv.className = 'member-qty';
            qtyDiv.textContent = `${v.qty} pçs`;
            member.appendChild(info);
            member.appendChild(qtyDiv);
            details.appendChild(member);
        });

        div.appendChild(header);
        div.appendChild(details);
        return div;
    }

    function renderPackageRank(containerId, packageData) {
        const container = document.getElementById(containerId);
        if (!container) return;
        container.innerHTML = '';

        // Filter packages that have no start date (opened_at)
        const filtered = (packageData || []).filter(pkg => pkg.opened_at);

        if (!filtered.length) {
            container.innerHTML = '<div class="rank-item" style="opacity: 0.5; justify-content: center;">Nenhum pacote</div>';
            return;
        }

        filtered.forEach((pkg, index) => {
            container.appendChild(buildPackageItem(pkg, index, { showActions: false, showSyncButton: true }));
        });
    }

    function renderPackageRankWithActions(containerId, packageData) {
        const container = document.getElementById(containerId);
        if (!container) return;
        container.innerHTML = '';

        if (!packageData || !packageData.length) {
            container.innerHTML = '<div class="rank-item" style="opacity: 0.5; justify-content: center;">Nenhum pacote</div>';
            return;
        }

        // Apply filters: opened_at is required, and search term if present
        let filtered = packageData.filter(pkg => pkg.opened_at);

        if (currentClosedSearch) {
            const s = currentClosedSearch.toLowerCase();
            filtered = filtered.filter(pkg => (pkg.poll_title || '').toLowerCase().includes(s));
        }

        if (filtered.length === 0) {
            container.innerHTML = '<div class="rank-item" style="opacity: 0.5; justify-content: center;">Nenhum resultado</div>';
            return;
        }

        const sorted = [...filtered].sort((a, b) => {
            // Keep rejected packages at the bottom.
            const rejectedDiff = (a.rejected ? 1 : 0) - (b.rejected ? 1 : 0);
            if (rejectedDiff !== 0) return rejectedDiff;

            // Closed packages: mais recentes primeiro (no topo).
            const dateA = new Date(a.closed_at || a.confirmed_at || a.opened_at || 0).getTime();
            const dateB = new Date(b.closed_at || b.confirmed_at || b.opened_at || 0).getTime();
            return dateB - dateA;
        });
        sorted.forEach((pkg, index) => {
            container.appendChild(buildPackageItem(pkg, index, { showActions: true }));
        });
    }

    function renderPackageRankConfirmed(containerId, packageData) {
        const container = document.getElementById(containerId);
        if (!container) return;
        container.innerHTML = '';

        // Filter packages that have no start date (opened_at)
        const filtered = (packageData || []).filter(pkg => pkg.opened_at);

        if (!filtered.length) {
            container.innerHTML = '<div class="rank-item" style="opacity: 0.5; justify-content: center;">Nenhum pacote</div>';
            return;
        }

        const sorted = [...filtered].sort((a, b) => {
            // Confirmed packages: newest first on top.
            const dateA = new Date(a.confirmed_at || a.closed_at || 0).getTime();
            const dateB = new Date(b.confirmed_at || b.closed_at || 0).getTime();
            return dateB - dateA;
        });

        sorted.forEach((pkg, index) => {
            container.appendChild(buildPackageItem(pkg, index, { showActions: false, showConfirmedEdit: true }));
        });
    }

    function getSelectedTotalQty() {
        return editSelectedVotes.reduce((sum, v) => sum + (parseInt(v.qty || 0, 10) || 0), 0);
    }

    function renderConfirmedEditLists() {
        const availableEl = document.getElementById('confirmed-edit-available');
        const selectedEl = document.getElementById('confirmed-edit-selected');
        const selectedTitle = document.getElementById('confirmed-edit-selected-title');
        if (!availableEl || !selectedEl || !selectedTitle) return;

        selectedTitle.textContent = `Votos do Pacote (${getSelectedTotalQty()}/24)`;
        availableEl.innerHTML = '';
        selectedEl.innerHTML = '';

        const renderVoteCard = (vote) => {
            const el = document.createElement('div');
            el.className = 'confirmed-edit-vote';
            el.draggable = true;
            el.dataset.id = String(vote._drag_id || '');
            el.innerHTML = `
                <div>
                    <div style="font-weight:700;">${vote.name || 'Cliente'}</div>
                    <div class="confirmed-edit-vote-meta">${vote.phone || ''}</div>
                </div>
                <div class="rank-badge">${vote.qty || 0} pçs</div>
            `;
            const dragId = String(vote._drag_id || '');
            el.addEventListener('dragstart', (e) => {
                draggedVoteId = dragId;
                e.dataTransfer.effectAllowed = 'move';
                e.dataTransfer.setData('text/plain', dragId);
                e.dataTransfer.setData('text', dragId); // Fallback
                console.log(`Drag start ID: ${dragId}`);
                el.classList.add('dragging');
            });
            el.addEventListener('dragend', () => {
                console.log(`Drag end ID: ${draggedVoteId}`);
                el.classList.remove('dragging');
                // Don't reset to null here yet, drop might still be processing
                setTimeout(() => { draggedVoteId = null; }, 100);
            });
            return el;
        };

        if (!editAvailableVotes.length) {
            availableEl.innerHTML = '<div class="rank-item" style="opacity: 0.6; justify-content: center;">Nenhum voto disponível</div>';
        } else {
            editAvailableVotes.forEach(vote => availableEl.appendChild(renderVoteCard(vote)));
        }

        if (!editSelectedVotes.length) {
            selectedEl.innerHTML = '<div class="rank-item" style="opacity: 0.6; justify-content: center;">Nenhum voto no pacote</div>';
        } else {
            editSelectedVotes.forEach(vote => selectedEl.appendChild(renderVoteCard(vote)));
        }
    }

    function setupConfirmedEditDropzones() {
        const availableEl = document.getElementById('confirmed-edit-available');
        const selectedEl = document.getElementById('confirmed-edit-selected');
        if (!availableEl || !selectedEl) return;

        const setupZone = (zone, target) => {
            zone.addEventListener('dragover', (e) => {
                e.preventDefault();
                e.dataTransfer.dropEffect = 'move';
                zone.classList.add('drag-over');
            });
            zone.addEventListener('dragleave', () => zone.classList.remove('drag-over'));
            zone.addEventListener('drop', (e) => {
                e.preventDefault();
                e.stopPropagation();
                zone.classList.remove('drag-over');
                
                const dropId = e.dataTransfer.getData('text/plain') || e.dataTransfer.getData('text') || draggedVoteId;
                console.log(`Drop na zona: ${target}, ID detectado: ${dropId}`);
                
                if (!dropId) {
                    console.warn('Nenhum ID detectado no drop.');
                    return;
                }

                const isTargetSelected = (target === 'selected');
                const source = isTargetSelected ? editAvailableVotes : editSelectedVotes;
                const destination = isTargetSelected ? editSelectedVotes : editAvailableVotes;
                
                const idx = source.findIndex(v => String(v._drag_id) === String(dropId));
                if (idx < 0) {
                    console.warn(`Voto com ID ${dropId} não encontrado na fonte (${isTargetSelected ? 'disponíveis' : 'pacote'}).`);
                    return;
                }
                
                const vote = source[idx];

                if (isTargetSelected) {
                    const nextTotal = getSelectedTotalQty() + (parseInt(vote.qty || 0, 10) || 0);
                    if (nextTotal > 24) {
                        alert('Não é possível ultrapassar 24 peças no pacote.');
                        return;
                    }
                }

                console.log(`Movendo ${dropId} para ${target}`);
                source.splice(idx, 1);
                destination.push(vote);
                renderConfirmedEditLists();
            });
        };

        setupZone(availableEl, 'available');
        setupZone(selectedEl, 'selected');
    }

    async function openConfirmedPackageEditModal(pkg, mode) {
        // mode: 'confirmed' (default) ou 'closed' — decide qual par de endpoints usar.
        const editMode = mode === 'closed' ? 'closed' : 'confirmed';
        const modal = document.getElementById('confirmedPackageEditModal');
        const desc = document.getElementById('confirmedPackageEditDesc');
        if (!modal || !pkg || !pkg.id) return;

        currentEditingPackageId = pkg.id;
        currentEditingMode = editMode;
        modal.hidden = false;
        if (desc) desc.textContent = 'Carregando votos para edição...';

        const dataUrl = editMode === 'closed'
            ? `/api/packages/${pkg.id}/edit-data-closed`
            : `/api/packages/${pkg.id}/edit-data`;

        try {
            const response = await fetch(dataUrl);
            const payload = await response.json().catch(() => ({}));
            if (!response.ok || payload.status !== 'success') {
                throw new Error(payload.detail || 'Falha ao carregar dados de edição.');
            }

            let voteCounter = 0;
            const newAvailable = (payload.data?.available_votes || []).map((v) => ({ 
                ...v, 
                _drag_id: `avail_${voteCounter++}_${Date.now()}` 
            }));
            const newSelected = (payload.data?.selected_votes || []).map((v) => ({ 
                ...v, 
                _drag_id: `sel_${voteCounter++}_${Date.now()}` 
            }));
            
            editAvailableVotes.splice(0, editAvailableVotes.length, ...newAvailable);
            editSelectedVotes.splice(0, editSelectedVotes.length, ...newSelected);
            if (desc) desc.textContent = 'Arraste os votos entre as colunas. O pacote precisa fechar em 24 peças.';
            renderConfirmedEditLists();
        } catch (error) {
            console.error(error);
            alert('Erro ao carregar dados de edição do pacote.');
            modal.hidden = true;
            currentEditingPackageId = null;
            currentEditingMode = null;
        }
    }

    async function saveConfirmedPackageEdition() {
        if (!currentEditingPackageId) return;
        const total = getSelectedTotalQty();
        if (total !== 24) {
            alert('O pacote deve conter exatamente 24 peças para salvar.');
            return;
        }

        // Captura os dados necessários antes de fechar o modal e limpar o estado
        const pkgId = currentEditingPackageId;
        const mode = currentEditingMode || 'confirmed';
        const votesToSave = [...editSelectedVotes];

        // Fecha o modal imediatamente para uma melhor experiência do usuário
        closeConfirmedEditModal();

        const updateUrl = mode === 'closed'
            ? `/api/packages/${pkgId}/update-closed`
            : `/api/packages/${pkgId}/update-confirmed`;

        const submitUpdate = async (confirmPaidRemoval) => {
            const body = mode === 'closed'
                ? { votes: votesToSave }
                : { votes: votesToSave, confirm_paid_removal: !!confirmPaidRemoval };
            const response = await fetch(updateUrl, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            const payload = await response.json().catch(() => ({}));
            return { response, payload };
        };

        try {
            let { response, payload } = await submitUpdate(false);

            // 409: há cobranças pagas sendo removidas — exige confirmação
            if (response.status === 409) {
                const detail = payload.detail || payload;
                const code = (detail && detail.code) || '';
                if (code === 'paid_removal_requires_confirmation') {
                    const paid = (detail.paid_removals || []);
                    const names = paid.map(p => `• ${p.name} (${p.phone}) — ${p.qty} pçs`).join('\n');
                    const msg =
                        'Atenção: as pessoas abaixo já pagaram a parte delas nesse pacote:\n\n' +
                        names + '\n\n' +
                        'Se você continuar, elas sairão do pacote mas a cobrança continuará no portal delas marcada como paga. ' +
                        'Você terá que resolver manualmente com cada uma (reembolso) e depois cancelar a cobrança no Financeiro se quiser.\n\n' +
                        'Deseja continuar mesmo assim?';
                    if (!confirm(msg)) {
                        return;
                    }
                    ({ response, payload } = await submitUpdate(true));
                }
            }

            if (!response.ok || payload.status !== 'success') {
                const msg = (payload && payload.detail && typeof payload.detail === 'object')
                    ? (payload.detail.message || 'Falha ao salvar edição do pacote.')
                    : (payload.detail || 'Falha ao salvar edição do pacote.');
                throw new Error(msg);
            }
            if (payload.data) updateUI(payload.data);
        } catch (error) {
            console.error(error);
            alert('Erro ao salvar edição do pacote: ' + error.message);
        }
    }

    function closeConfirmedEditModal() {
        const modal = document.getElementById('confirmedPackageEditModal');
        if (modal) modal.hidden = true;
        currentEditingPackageId = null;
        currentEditingMode = null;
        editAvailableVotes.length = 0;
        editSelectedVotes.length = 0;
        draggedVoteId = null;
    }

    async function cancelConfirmedPackage(pkg) {
        // Fluxo em dois passos: tenta sem force; se 409 (tem pagos),
        // mostra aviso detalhado e re-tenta com force=true.
        const label = pkg.poll_title || pkg.id || 'Pacote';
        if (!confirm(`Cancelar o pacote "${label}"?\n\nOs pedidos de todos os clientes deste pacote serão cancelados, e ele também será marcado como CANCELADO no Estoque.`)) {
            return;
        }

        async function doCancel(force) {
            const resp = await fetch(`/api/packages/${encodeURIComponent(pkg.id)}/cancel`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ force: !!force }),
            });
            const body = await resp.json().catch(() => ({}));
            return { status: resp.status, body };
        }

        try {
            const { status, body } = await doCancel(false);

            if (status === 409 && body.status === 'blocked_paid') {
                const lista = (body.paid_clients || []).map(c => {
                    const nome = c.cliente_nome || '(sem nome)';
                    const valor = typeof c.total_amount === 'number' ? c.total_amount.toFixed(2) : c.total_amount;
                    return `  • ${nome} — R$ ${valor} (${c.qty} pç)`;
                }).join('\n');
                const msg = `⚠️ ATENÇÃO — ${body.paid_count} cliente(s) já pagaram este pacote:\n\n${lista}\n\nSe você cancelar assim mesmo:\n  • Os pedidos pagos NÃO serão estornados automaticamente.\n  • Ficarão visíveis como PAGO e você precisa tratar cada caso manualmente (estorno, reaproveitamento, etc).\n  • Os demais pedidos (pendentes) serão cancelados.\n\nQuer continuar?`;
                if (!confirm(msg)) return;

                const forced = await doCancel(true);
                if (forced.status !== 200) {
                    alert('Erro ao cancelar pacote: ' + (forced.body.detail || forced.status));
                    return;
                }
                alert(`Pacote cancelado. ${forced.body.cancelled_sales} pedido(s) cancelado(s). ${forced.body.preserved_paid} pagamento(s) pago(s) preservados — trate esses clientes manualmente.`);
            } else if (status !== 200) {
                alert('Erro ao cancelar pacote: ' + (body.detail || status));
                return;
            } else {
                alert(`Pacote cancelado. ${body.cancelled_sales} pedido(s) cancelado(s).`);
            }

            if (typeof loadMetrics === 'function') loadMetrics();
        } catch (err) {
            console.error('cancelConfirmedPackage error:', err);
            alert('Erro de rede ao cancelar pacote: ' + err.message);
        }
    }

    let pendingModalAction = null;

    function openConfirmModal(action, pkg, itemEl) {
        const modal = document.getElementById('packageConfirmModal');
        const desc = document.getElementById('modalDesc');
        const title = document.getElementById('modalTitle');
        const confirmBtn = document.getElementById('modalConfirm');

        const label = pkg.poll_title || pkg.id || 'Pacote';
        if (action === 'approve') {
            title.textContent = 'Aprovar pacote';
            desc.textContent = `Deseja aprovar o pacote "${label}"? Ele será movido para Pacotes Confirmados.`;
        } else if (action === 'reject') {
            title.textContent = 'Rejeitar pacote';
            desc.textContent = `Deseja rejeitar o pacote "${label}"? Ele será movido para o final da fila com marcação de rejeitado.`;
        } else if (action === 'revert') {
            title.textContent = 'Reverter confirmação';
            desc.textContent = `Deseja reverter a confirmação do pacote \"${label}\"? Ele será movido de volta para Pacotes Fechados.`;
        }
        pendingModalAction = { action, pkg, itemEl };
        modal.hidden = false;
        confirmBtn.focus();
    }

    function animateFlip(oldRect, newEl) {
        try {
            const newRect = newEl.getBoundingClientRect();
            const dx = oldRect.left - newRect.left;
            const dy = oldRect.top - newRect.top;
            const dw = oldRect.width / newRect.width;
            const dh = oldRect.height / newRect.height;
            newEl.style.transition = 'none';
            newEl.style.transformOrigin = 'top left';
            newEl.style.transform = `translate(${dx}px, ${dy}px) scale(${dw}, ${dh})`;
            requestAnimationFrame(() => {
                newEl.style.transition = 'transform 360ms cubic-bezier(.2,.9,.3,1)';
                newEl.style.transform = '';
                newEl.addEventListener('transitionend', () => {
                    newEl.style.transition = '';
                    newEl.style.transform = '';
                }, { once: true });
            });
        } catch (err) {
            // ignore animation errors
        }
    }

    function setCreatePackageStep(step) {
        window.__createPackageStep = step;
        const stepChoose = document.getElementById('create-package-step-choose');
        const stepPolls = document.getElementById('create-package-step-polls');
        const stepVotes = document.getElementById('create-package-step-votes');
        const stepPreview = document.getElementById('create-package-step-preview');
        const backBtn = document.getElementById('createPackageBack');
        const saveBtn = document.getElementById('createPackageSave');
        const confirmBtn = document.getElementById('createPackageConfirm');
        if (stepChoose) stepChoose.hidden = step !== 'choose';
        if (stepPolls) stepPolls.hidden = step !== 'polls';
        if (stepVotes) stepVotes.hidden = step !== 'votes';
        if (stepPreview) stepPreview.hidden = step !== 'preview';
        if (backBtn) backBtn.hidden = step === 'polls' || step === 'choose';
        if (saveBtn) {
            saveBtn.hidden = step !== 'votes';
            saveBtn.textContent = 'Revisar';
        }
        if (confirmBtn) confirmBtn.hidden = step !== 'preview';
        // Steps adhoc ficam escondidos quando o fluxo é poll/choose
        ['adhoc-step-product', 'adhoc-step-votes', 'adhoc-step-preview'].forEach((id) => {
            const el = document.getElementById(id);
            if (el) el.hidden = true;
        });
    }

    function resetCreatePackageModal() {
        const listEl = document.getElementById('create-package-poll-list');
        const loadingEl = document.getElementById('create-package-polls-loading');
        const emptyEl = document.getElementById('create-package-polls-empty');
        const saveBtn = document.getElementById('createPackageSave');
        const valMsg = document.getElementById('create-package-validation-msg');
        const previewBody = document.getElementById('create-package-preview-body');
        if (listEl) listEl.innerHTML = '';
        if (loadingEl) {
            loadingEl.hidden = false;
            loadingEl.textContent = 'Carregando enquetes…';
        }
        if (emptyEl) emptyEl.hidden = true;
        if (saveBtn) {
            saveBtn.disabled = true;
            saveBtn.textContent = 'Revisar';
        }
        if (valMsg) {
            valMsg.hidden = true;
            valMsg.textContent = '';
        }
        if (previewBody) previewBody.innerHTML = '';
        window.__createPackageSelected = null;
        window.__createPackagePolls = [];
        window.__pollsOffset = 0;
        window.__pollsHasMore = true;
        window.__pollsLoadingMore = false;
        window.__manualPreviewPayload = null;
        const rows = document.getElementById('create-package-vote-rows');
        if (rows) rows.innerHTML = '';
        const header = document.getElementById('create-package-selected-header');
        if (header) header.innerHTML = '';
        setCreatePackageStep('polls');
    }

    const PHONE_BR_RE = /^55\d{10,11}$/;

    function normalizePhoneDigits(v) {
        return (v || '').toString().replace(/\D/g, '');
    }

    function appendPollCard(listEl, p) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'create-package-poll-card';
        btn.dataset.pollId = p.pollId;
        let thumbEl;
        if (p.thumbUrl) {
            const img = document.createElement('img');
            img.className = 'create-package-poll-thumb';
            img.src = p.thumbUrl;
            img.alt = '';
            img.loading = 'lazy';
            img.onerror = () => {
                img.replaceWith(makeCreatePackageThumbPlaceholder());
            };
            thumbEl = img;
        } else {
            thumbEl = makeCreatePackageThumbPlaceholder();
        }
        const title = document.createElement('div');
        title.className = 'create-package-poll-title';
        title.textContent = p.title || p.pollId || 'Sem título';
        btn.appendChild(thumbEl);
        btn.appendChild(title);
        btn.addEventListener('click', () => selectCreatePackagePoll(p, btn));
        listEl.appendChild(btn);
    }

    // Paginação clássica do modal Criar Pacote (substitui o antigo infinite scroll).
    const CREATE_PACKAGE_PAGE_SIZE = 8;
    let pollSearchTerm = '';
    let createPackagePage = 1;
    let createPackageTotal = 0;
    let createPackageSearchTimer = null;

    async function loadPollsPage(pageArg) {
        const loadingEl = document.getElementById('create-package-polls-loading');
        const emptyEl = document.getElementById('create-package-polls-empty');
        const listEl = document.getElementById('create-package-poll-list');
        const paginationEl = document.getElementById('create-package-pagination');
        const prevBtn = document.getElementById('create-package-prev');
        const nextBtn = document.getElementById('create-package-next');
        const infoEl = document.getElementById('create-package-page-info');
        if (!listEl) return;
        if (window.__pollsLoadingMore) return;

        window.__pollsLoadingMore = true;
        const page = Math.max(1, parseInt(pageArg, 10) || 1);
        createPackagePage = page;
        const offset = (page - 1) * CREATE_PACKAGE_PAGE_SIZE;

        listEl.innerHTML = '';
        if (loadingEl) {
            loadingEl.hidden = false;
            loadingEl.textContent = 'Carregando enquetes…';
        }
        if (emptyEl) emptyEl.hidden = true;
        if (paginationEl) paginationEl.hidden = true;

        // Easter egg: "[ENQUETE DE TESTE]" mostra toggle do modo teste
        if (pollSearchTerm && pollSearchTerm.toUpperCase().includes('[ENQUETE DE TESTE]')) {
            if (loadingEl) loadingEl.hidden = true;
            window.__pollsLoadingMore = false;
            const testModeR = await fetch('/api/test-mode');
            const testModeData = await testModeR.json();
            const isActive = testModeData.active;
            listEl.innerHTML = `
                <div style="text-align:center; padding:2rem 1rem;">
                    <div style="font-size:2rem; margin-bottom:0.5rem;">${isActive ? '🧪' : '🔒'}</div>
                    <div style="font-weight:700; font-size:1.1rem; margin-bottom:0.5rem;">
                        Modo de Teste: ${isActive ? 'ATIVO' : 'DESATIVADO'}
                    </div>
                    <p style="font-size:0.85rem; color:#5C4A4A; margin-bottom:1rem;">
                        ${isActive
                            ? 'Cobranças no sandbox, mensagens para contato de teste.'
                            : 'Cobranças reais (Asaas prod), mensagens para clientes.'}
                    </p>
                    <button type="button" id="testModeToggleBtn"
                        style="padding:0.7rem 2rem; border-radius:8px; border:none; cursor:pointer;
                               font-weight:700; font-size:0.95rem;
                               background:${isActive ? '#e74c3c' : '#3E5A44'}; color:#fff;">
                        ${isActive ? 'Desativar modo teste' : 'Ativar modo teste'}
                    </button>
                </div>
            `;
            document.getElementById('testModeToggleBtn')?.addEventListener('click', async () => {
                const btn = document.getElementById('testModeToggleBtn');
                if (btn) btn.textContent = 'Aguarde...';
                try {
                    await fetch('/api/test-mode/toggle', { method: 'POST' });
                    loadPollsPage(1); // recarrega pra mostrar novo estado
                    checkTestMode(); // atualiza banner
                } catch (e) { console.error(e); }
            });
            return;
        }

        try {
            const searchParam = pollSearchTerm ? `&search=${encodeURIComponent(pollSearchTerm)}` : '';
            const r = await fetch(`/api/polls/recent?limit=${CREATE_PACKAGE_PAGE_SIZE}&offset=${offset}${searchParam}`);
            if (!r.ok) throw new Error('Falha ao carregar enquetes');
            const data = await r.json();
            const polls = data.polls || [];
            createPackageTotal = Number(data.total || polls.length);

            if (loadingEl) loadingEl.hidden = true;
            if (!polls.length) {
                if (emptyEl) {
                    emptyEl.textContent = pollSearchTerm
                        ? 'Nenhuma enquete encontrada para essa busca.'
                        : 'Nenhuma enquete encontrada nas últimas 72 horas.';
                    emptyEl.hidden = false;
                }
                return;
            }

            polls.forEach((p) => appendPollCard(listEl, p));

            const totalPages = Math.max(1, Math.ceil(createPackageTotal / CREATE_PACKAGE_PAGE_SIZE));
            if (infoEl) {
                infoEl.textContent = `Página ${createPackagePage} de ${totalPages} (${createPackageTotal} enquetes)`;
            }
            if (prevBtn) prevBtn.disabled = createPackagePage <= 1;
            if (nextBtn) nextBtn.disabled = createPackagePage >= totalPages;
            if (paginationEl) paginationEl.hidden = totalPages <= 1;
        } catch (err) {
            console.error(err);
            if (loadingEl) loadingEl.textContent = 'Erro ao carregar. Tente novamente.';
        } finally {
            window.__pollsLoadingMore = false;
        }
    }

    function bindCreatePackagePaginationControls() {
        const prevBtn = document.getElementById('create-package-prev');
        const nextBtn = document.getElementById('create-package-next');
        const searchInput = document.getElementById('create-package-poll-search');
        if (prevBtn && !prevBtn.dataset.bound) {
            prevBtn.dataset.bound = '1';
            prevBtn.addEventListener('click', () => {
                if (createPackagePage > 1) loadPollsPage(createPackagePage - 1);
            });
        }
        if (nextBtn && !nextBtn.dataset.bound) {
            nextBtn.dataset.bound = '1';
            nextBtn.addEventListener('click', () => {
                const totalPages = Math.max(1, Math.ceil(createPackageTotal / CREATE_PACKAGE_PAGE_SIZE));
                if (createPackagePage < totalPages) loadPollsPage(createPackagePage + 1);
            });
        }
        if (searchInput && !searchInput.dataset.bound) {
            searchInput.dataset.bound = '1';
            searchInput.addEventListener('input', (e) => {
                pollSearchTerm = String(e.target.value || '').trim();
                if (createPackageSearchTimer) clearTimeout(createPackageSearchTimer);
                createPackageSearchTimer = setTimeout(() => loadPollsPage(1), 250);
            });
        }
    }

    function openCreatePackageModal() {
        const modal = document.getElementById('createPackageModal');
        if (modal) modal.hidden = false;
        // Reseta state antes de mostrar step 0 pra limpar dados de abertura anterior
        resetCreatePackageModal();
        // resetCreatePackageModal() termina em setCreatePackageStep('polls').
        // Sobrescreve pra começar no step 0 (escolha).
        setCreatePackageStep('choose');
    }

    function makeCreatePackageThumbPlaceholder() {
        const ph = document.createElement('div');
        ph.className = 'create-package-poll-thumb placeholder';
        ph.innerHTML = '<i class="fas fa-image"></i>';
        return ph;
    }

    function selectCreatePackagePoll(poll, cardEl) {
        window.__createPackageSelected = poll;
        document.querySelectorAll('.create-package-poll-card').forEach((c) => c.classList.remove('selected'));
        if (cardEl) cardEl.classList.add('selected');
        setCreatePackageStep('votes');
        const header = document.getElementById('create-package-selected-header');
        if (header) {
            header.innerHTML = '';
            if (poll.thumbUrl) {
                const img = document.createElement('img');
                img.className = 'create-package-poll-thumb';
                img.src = poll.thumbUrl;
                img.alt = '';
                header.appendChild(img);
            } else {
                header.appendChild(makeCreatePackageThumbPlaceholder());
            }
            const t = document.createElement('div');
            t.className = 'create-package-poll-title';
            t.textContent = poll.title || poll.pollId || '';
            header.appendChild(t);
        }
        const rows = document.getElementById('create-package-vote-rows');
        if (rows) {
            rows.innerHTML = '';
            appendCreatePackageVoteRow(rows);
        }
        updateCreatePackageTotals();
    }

    function syncCreatePackageRemoveButtons() {
        const rows = document.querySelectorAll('#create-package-vote-rows .create-package-vote-row');
        const multi = rows.length > 1;
        rows.forEach((row) => {
            const rm = row.querySelector('.create-package-remove-row');
            if (rm) rm.hidden = !multi;
        });
    }

    function appendCreatePackageVoteRow(container) {
        const row = document.createElement('div');
        row.className = 'create-package-vote-row';
        row.innerHTML = `
            <div style="flex:2">
                <label>Celular do cliente</label>
                <input type="tel" class="create-package-phone search-input" placeholder="(55) 62 99999-0001" autocomplete="tel" inputmode="numeric" />
            </div>
            <div style="flex:1">
                <label>Peças</label>
                <select class="create-package-qty">
                    <option value="3">3 pçs</option>
                    <option value="6">6 pçs</option>
                    <option value="9">9 pçs</option>
                    <option value="12">12 pçs</option>
                    <option value="24">24 pçs</option>
                </select>
            </div>
            <div>
                <label>&nbsp;</label>
                <button type="button" class="create-package-remove-row" title="Remover" hidden><i class="fas fa-times"></i></button>
            </div>
        `;
        const qty = row.querySelector('.create-package-qty');
        const phone = row.querySelector('.create-package-phone');
        const rm = row.querySelector('.create-package-remove-row');
        qty.addEventListener('change', updateCreatePackageTotals);
        // F-052: máscara de telefone BR igual ao cadastro de cliente (F-043)
        phone.addEventListener('input', (e) => {
            const digits = sanitizeBrPhoneDigits(e.target.value);
            e.target.value = formatBrPhoneDisplay(digits);
            updateCreatePackageTotals();
        });
        phone.addEventListener('blur', (e) => {
            const digits = sanitizeBrPhoneDigits(e.target.value);
            if (digits.length > 0 && !validateBrPhone(digits)) {
                e.target.style.borderColor = '#e74c3c';
                e.target.title = 'Formato: (55) DDD 9XXXX-XXXX';
            } else {
                e.target.style.borderColor = '';
                e.target.title = '';
            }
        });
        if (rm) {
            rm.addEventListener('click', () => {
                if (container.querySelectorAll('.create-package-vote-row').length <= 1) return;
                row.remove();
                syncCreatePackageRemoveButtons();
                updateCreatePackageTotals();
            });
        }
        container.appendChild(row);
        syncCreatePackageRemoveButtons();
    }

    function getCreatePackageVotePayload() {
        const rows = document.querySelectorAll('#create-package-vote-rows .create-package-vote-row');
        const votes = [];
        for (const row of rows) {
            const qtyIn = row.querySelector('.create-package-qty');
            const phoneIn = row.querySelector('.create-package-phone');
            const qty = parseInt(qtyIn && qtyIn.value, 10);
            const phone = normalizePhoneDigits(phoneIn && phoneIn.value);
            if (!Number.isFinite(qty) || qty < 1) continue;
            votes.push({ qty, phone });
        }
        return votes;
    }

    function updateCreatePackageTotals() {
        const rows = document.querySelectorAll('#create-package-vote-rows .create-package-vote-row');
        let sum = 0;
        let msg = '';
        let valid = rows.length > 0;

        for (const row of rows) {
            const qty = parseInt(row.querySelector('.create-package-qty').value, 10);
            const phone = normalizePhoneDigits(row.querySelector('.create-package-phone').value);
            if (!Number.isFinite(qty) || qty < 1) {
                valid = false;
                msg = 'Selecione uma quantidade válida em cada linha.';
                break;
            }
            if (!phone) {
                valid = false;
                msg = 'Preencha o celular em cada linha.';
                break;
            }
            if (!PHONE_BR_RE.test(phone)) {
                valid = false;
                msg = 'Celular deve estar no formato 55 + DDD + número (ex.: 558188458637 ou 5598188458637).';
                break;
            }
            sum += qty;
        }

        if (valid && sum !== 24) {
            valid = false;
            if (sum > 24) {
                msg = 'A soma das quantidades não pode ultrapassar 24 peças.';
            } else {
                msg = `Faltam ${24 - sum} peças para completar o pacote.`;
            }
        }

        const countEl = document.getElementById('create-package-piece-count');
        if (countEl) countEl.textContent = String(sum);
        const saveBtn = document.getElementById('createPackageSave');
        const valMsg = document.getElementById('create-package-validation-msg');
        if (saveBtn) saveBtn.disabled = !valid;
        if (valMsg) {
            if (!valid && msg) {
                valMsg.textContent = msg;
                valMsg.hidden = false;
            } else {
                valMsg.hidden = true;
                valMsg.textContent = '';
            }
        }
    }

    function closeModal() {
        document.getElementById('packageConfirmModal').hidden = true;
        document.getElementById('customerModal').hidden = true;
        closeConfirmedEditModal();
        const queueModal = document.getElementById('queueModal');
        if (queueModal) queueModal.hidden = true;
        const createPackageModal = document.getElementById('createPackageModal');
        if (createPackageModal) {
            createPackageModal.hidden = true;
            resetCreatePackageModal();
        }
        if (queueRefreshTimer) {
            clearInterval(queueRefreshTimer);
            queueRefreshTimer = null;
        }
        pendingModalAction = null;
    }

    function executePackageAction() {
        if (!pendingModalAction) return;
        const { action, pkg, itemEl } = pendingModalAction;
        const pkgId = pkg.id;
        let url = null;
        if (action === 'approve') url = `/api/packages/${pkgId}/confirm`;
        else if (action === 'reject') url = `/api/packages/${pkgId}/reject`;
        else if (action === 'revert') url = `/api/packages/${pkgId}/revert`;
        
        if (!url) {
            alert('Ação desconhecida');
            closeModal();
            return;
        }

        // Close modal immediately for better UX responsiveness
        closeModal();

        // Visual feedback on the item being processed
        if (itemEl) {
            itemEl.classList.add('processing-item');
            itemEl.style.pointerEvents = 'none';
        }

        // Capture source bounding rect for FLIP animation before UI updates
        const sourceRect = itemEl ? itemEl.getBoundingClientRect() : null;

        const fetchOptions = { method: 'POST' };
        if (action === 'approve') {
            const tag = prompt('Tag do pacote (substitui "peças" na etiqueta). Ex: itens, conjuntos, pares', (pkg && pkg.tag) ? String(pkg.tag) : '');
            // Se cancelar, segue sem tag (não bloqueia confirmação)
            if (tag !== null) {
                fetchOptions.headers = { 'Content-Type': 'application/json' };
                fetchOptions.body = JSON.stringify({ tag });
            }
        }

        fetch(url, fetchOptions)
            .then(r => r.json())
            .then(result => {
                if (result.status === 'success' && result.data) {
                    // Re-render UI with latest data
                    updateUI(result.data);

                    // Try FLIP animation to visually move the item
                    if (sourceRect && pkgId) {
                        let targetSelector = null;
                        if (action === 'approve') targetSelector = '#rank-packages-confirmed-today';
                        else if (action === 'reject') targetSelector = '#rank-packages-closed-today';
                        else if (action === 'revert') targetSelector = '#rank-packages-closed-today';

                        if (targetSelector) {
                            // find the new element by data attribute
                            const targetEl = document.querySelector(`${targetSelector} [data-pkgid="${pkgId}"]`);
                            if (targetEl) animateFlip(sourceRect, targetEl);
                        }
                    }
                } else {
                    // Restore item state on failure
                    if (itemEl) {
                        itemEl.classList.remove('processing-item');
                        itemEl.style.pointerEvents = '';
                    }
                    alert('Erro: ' + (result.detail || 'Operação falhou'));
                }
            })
            .catch(err => {
                console.error(err);
                if (itemEl) {
                    itemEl.classList.remove('processing-item');
                    itemEl.style.pointerEvents = '';
                }
                alert('Falha na conexão com o servidor.');
            });
    }

    async function setPackageTag(pkg) {
        if (!pkg || !pkg.id) return;
        const tag = prompt('Definir/editar tag do pacote (substitui "peças" na etiqueta):', (pkg && pkg.tag) ? String(pkg.tag) : '');
        if (tag === null) return;
        try {
            const r = await fetch(`/api/packages/${encodeURIComponent(pkg.id)}/tag`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ tag }),
            });
            const payload = await r.json().catch(() => ({}));
            if (!r.ok || payload.status !== 'success') {
                alert('Erro ao salvar tag.');
                return;
            }
            if (payload.data) updateUI(payload.data);
        } catch (err) {
            console.error(err);
            alert('Falha na conexão com o servidor.');
        }
    }

    async function setEnqueteFornecedor(pkg) {
        // Fornecedor da enquete — propaga pros pacotes que vierem dela
        const pollId = pkg && (pkg.poll_id || pkg.enquete_id);
        if (!pollId) return;
        const fornecedor = prompt('Fornecedor desta enquete (vai aparecer em todos os pacotes que fecharem a partir dela):', (pkg && pkg.fornecedor) ? String(pkg.fornecedor) : '');
        if (fornecedor === null) return;
        try {
            const r = await fetch(`/api/enquetes/${encodeURIComponent(pollId)}/fornecedor`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ tag: fornecedor }),
            });
            const payload = await r.json().catch(() => ({}));
            if (!r.ok || payload.status !== 'success') {
                alert('Erro ao salvar fornecedor.');
                return;
            }
            performAutoRefresh();
        } catch (err) {
            console.error(err);
            alert('Falha na conexão com o servidor.');
        }
    }

    // ===== Aba Estoque =====
    let invState = {
        page: 1,
        pageSize: 30,
        from: '',
        to: '',
        status: 'all',
        tag: '',
        search: '',
        searchTimer: null,
        total: 0,
    };

    function fmtISO(d) {
        const y = d.getFullYear();
        const m = String(d.getMonth() + 1).padStart(2, '0');
        const day = String(d.getDate()).padStart(2, '0');
        return `${y}-${m}-${day}`;
    }

    function applyPreset(preset) {
        const now = new Date();
        let from = '', to = '';
        if (preset === 'today') {
            from = to = fmtISO(now);
        } else if (preset === 'week') {
            // Semana começando no domingo
            const start = new Date(now);
            start.setDate(now.getDate() - now.getDay());
            from = fmtISO(start);
            to = fmtISO(now);
        } else if (preset === 'month') {
            const start = new Date(now.getFullYear(), now.getMonth(), 1);
            const end = new Date(now.getFullYear(), now.getMonth() + 1, 0);
            from = fmtISO(start);
            to = fmtISO(end);
        } else if (preset === 'last-month') {
            const start = new Date(now.getFullYear(), now.getMonth() - 1, 1);
            const end = new Date(now.getFullYear(), now.getMonth(), 0);
            from = fmtISO(start);
            to = fmtISO(end);
        } else if (preset === 'all') {
            from = '';
            to = '';
        }
        invState.from = from;
        invState.to = to;
        const fromInput = document.getElementById('inv-from');
        const toInput = document.getElementById('inv-to');
        if (fromInput) fromInput.value = from;
        if (toInput) toInput.value = to;
        document.querySelectorAll('#inv-presets .inv-chip').forEach(c => {
            c.classList.toggle('active', c.dataset.preset === preset);
        });
    }

    function clearPresetActive() {
        document.querySelectorAll('#inv-presets .inv-chip').forEach(c => c.classList.remove('active'));
    }

    function fmtDateBR(iso) {
        if (!iso) return '';
        try {
            const d = new Date(iso);
            return d.toLocaleDateString('pt-BR') + ' ' + d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });
        } catch { return ''; }
    }

    async function loadInventory() {
        const grid = document.getElementById('inv-grid');
        const loading = document.getElementById('inv-loading');
        const empty = document.getElementById('inv-empty');
        const pag = document.getElementById('inv-pagination');
        if (!grid) return;

        loading.hidden = false;
        empty.hidden = true;
        grid.innerHTML = '';
        if (pag) pag.hidden = true;

        try {
            const params = new URLSearchParams({
                start: invState.from,
                end: invState.to,
                status: invState.status,
                tag: invState.tag,
                search: invState.search,
                page: String(invState.page),
                page_size: String(invState.pageSize),
            });
            const r = await fetch('/api/inventory/packages?' + params.toString());
            const data = await r.json();
            loading.hidden = true;

            // Resumo
            const sum = data.summary || {};
            document.getElementById('inv-count-approved').textContent = sum.approved_count || 0;
            document.getElementById('inv-count-cancelled').textContent = sum.cancelled_count || 0;
            document.getElementById('inv-count-pieces').textContent = sum.total_pieces || 0;

            // Atualiza dropdown de fornecedores se mudou
            const tagSelect = document.getElementById('inv-tag');
            if (tagSelect && Array.isArray(data.tags_available)) {
                const current = tagSelect.value;
                const newOptions = ['<option value="">Todos</option>']
                    .concat(data.tags_available.map(t => `<option value="${t.replace(/"/g, '&quot;')}">${t}</option>`));
                if (tagSelect.dataset.options !== JSON.stringify(data.tags_available)) {
                    tagSelect.innerHTML = newOptions.join('');
                    tagSelect.dataset.options = JSON.stringify(data.tags_available);
                    tagSelect.value = current;
                }
            }

            invState.total = data.total || 0;

            const items = data.items || [];
            if (items.length === 0) {
                empty.hidden = false;
            } else {
                items.forEach(it => grid.appendChild(buildInventoryCard(it)));
            }

            // Paginação
            const totalPages = Math.max(1, Math.ceil((data.total || 0) / invState.pageSize));
            if (pag) {
                pag.hidden = totalPages <= 1;
                document.getElementById('inv-pagination-summary').textContent =
                    `Página ${data.page} de ${totalPages} (${data.total} pacote${data.total !== 1 ? 's' : ''})`;
                document.getElementById('inv-page-prev').disabled = !data.has_prev;
                document.getElementById('inv-page-next').disabled = !data.has_next;
                const sel = document.getElementById('inv-page-jump');
                if (sel && sel.dataset.total !== String(totalPages)) {
                    sel.innerHTML = '';
                    for (let p = 1; p <= totalPages; p++) {
                        const opt = document.createElement('option');
                        opt.value = String(p);
                        opt.textContent = `Pág. ${p}`;
                        sel.appendChild(opt);
                    }
                    sel.dataset.total = String(totalPages);
                }
                if (sel) sel.value = String(data.page);
            }
        } catch (err) {
            loading.hidden = true;
            console.error('Erro ao carregar estoque:', err);
            empty.hidden = false;
        }
    }

    function buildInventoryCard(it) {
        const card = document.createElement('div');
        card.className = 'inv-card' + (it.status === 'cancelled' ? ' is-cancelled' : '');
        const statusLabel = it.status === 'approved' ? 'Confirmado' : (it.status === 'cancelled' ? 'Cancelado' : it.status);
        const statusClass = it.status === 'approved' ? 'approved' : 'cancelled';
        const dateStr = fmtDateBR(it.approved_at || it.cancelled_at || it.closed_at);

        const imgHtml = it.image
            ? `<img src="${it.image}" alt="" loading="lazy" onerror="this.style.display='none'; this.nextElementSibling.style.display='flex';">
               <div class="inv-card-img-placeholder" style="display:none;"><i class="fas fa-image"></i></div>`
            : `<div class="inv-card-img-placeholder"><i class="fas fa-image"></i></div>`;

        // Mostra fornecedor (organização) — não a tag de tipo de peça
        const fornHtml = it.fornecedor
            ? `<div class="inv-card-tag"><i class="fas fa-truck"></i> ${it.fornecedor}</div>`
            : '';

        card.innerHTML = `
            <div class="inv-card-img">
                ${imgHtml}
                <div class="inv-card-status ${statusClass}">${statusLabel}</div>
            </div>
            <div class="inv-card-body">
                <div class="inv-card-title">${it.title || 'Pacote'}</div>
                <div class="inv-card-meta">
                    <span><i class="far fa-calendar"></i> ${dateStr}</span>
                    <span><i class="fas fa-cube"></i> ${it.qty} peça${it.qty !== 1 ? 's' : ''}</span>
                    <span><i class="fas fa-users"></i> ${it.participants}</span>
                </div>
                ${fornHtml}
                <div class="inv-card-actions">
                    <a class="inv-card-btn primary" href="${it.pdf_url}" target="_blank" download>
                        <i class="fas fa-file-pdf"></i> Baixar etiqueta
                    </a>
                </div>
            </div>
        `;
        return card;
    }

    // Inicialização da aba Estoque
    function initInventoryTab() {
        const fromInput = document.getElementById('inv-from');
        const toInput = document.getElementById('inv-to');
        if (!fromInput || !toInput) return;

        // Default: este mês
        applyPreset('month');

        document.querySelectorAll('#inv-presets .inv-chip').forEach(chip => {
            chip.addEventListener('click', () => {
                applyPreset(chip.dataset.preset);
                invState.page = 1;
                loadInventory();
            });
        });

        const onDateChange = () => {
            invState.from = fromInput.value || '';
            invState.to = toInput.value || '';
            clearPresetActive();
            invState.page = 1;
            loadInventory();
        };
        fromInput.addEventListener('change', onDateChange);
        toInput.addEventListener('change', onDateChange);

        document.querySelectorAll('#inv-status-chips .inv-chip').forEach(chip => {
            chip.addEventListener('click', () => {
                document.querySelectorAll('#inv-status-chips .inv-chip').forEach(c => c.classList.remove('active'));
                chip.classList.add('active');
                invState.status = chip.dataset.status;
                invState.page = 1;
                loadInventory();
            });
        });

        document.getElementById('inv-tag')?.addEventListener('change', (e) => {
            invState.tag = e.target.value;
            invState.page = 1;
            loadInventory();
        });

        document.getElementById('inv-search')?.addEventListener('input', (e) => {
            clearTimeout(invState.searchTimer);
            invState.searchTimer = setTimeout(() => {
                invState.search = e.target.value.trim();
                invState.page = 1;
                loadInventory();
            }, 350);
        });

        document.getElementById('inv-page-prev')?.addEventListener('click', () => {
            if (invState.page > 1) { invState.page--; loadInventory(); }
        });
        document.getElementById('inv-page-next')?.addEventListener('click', () => {
            invState.page++;
            loadInventory();
        });
        document.getElementById('inv-page-jump')?.addEventListener('change', (e) => {
            const p = parseInt(e.target.value, 10);
            if (Number.isFinite(p) && p >= 1 && p !== invState.page) {
                invState.page = p;
                loadInventory();
            }
        });
    }

    initInventoryTab();
    // Carregar dados quando entrar na aba Estoque pela primeira vez
    let invLoaded = false;
    document.querySelectorAll('.nav-item[data-target="inventory"]').forEach(link => {
        link.addEventListener('click', () => {
            if (!invLoaded) {
                invLoaded = true;
                loadInventory();
            }
        });
    });

    document.getElementById('modalCancel')?.addEventListener('click', closeModal);
    document.getElementById('modalConfirm')?.addEventListener('click', executePackageAction);
    document.getElementById('btn-open-extract-modal')?.addEventListener('click', openExtractModal);
    document.getElementById('extractModalClose')?.addEventListener('click', () => {
        const m = document.getElementById('extractModal');
        if (m) m.hidden = true;
    });
    document.getElementById('extractApplyBtn')?.addEventListener('click', async () => {
        const tbody = document.getElementById('extract-table-body');
        if (tbody) tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; padding: 1rem;">Carregando...</td></tr>';
        try {
            const data = await loadExtractData();
            renderExtractData(data);
        } catch (e) {
            if (tbody) tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; padding: 1rem;">Erro ao carregar.</td></tr>';
        }
    });
    document.getElementById('extractDownloadBtn')?.addEventListener('click', downloadExtractCSV);

    // Botão "Sincronizar agora" — dispara sync manual com Asaas
    const btnSyncAsaas = document.getElementById('btn-sync-asaas');
    if (btnSyncAsaas) {
        btnSyncAsaas.addEventListener('click', async () => {
            const originalHTML = btnSyncAsaas.innerHTML;
            btnSyncAsaas.disabled = true;
            btnSyncAsaas.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Sincronizando...';
            try {
                const resp = await fetch('/api/finance/sync-asaas', { method: 'POST' });
                const data = await resp.json().catch(() => ({}));
                if (resp.ok) {
                    const count = data.updated || 0;
                    btnSyncAsaas.innerHTML = count > 0
                        ? `<i class="fas fa-check"></i> ${count} atualizado${count > 1 ? 's' : ''}`
                        : '<i class="fas fa-check"></i> Atualizado';
                    // Reload finance data sem piscar
                    loadFinanceData(currentFinancePage, { silent: true });
                } else {
                    btnSyncAsaas.innerHTML = '<i class="fas fa-exclamation-triangle"></i> Erro';
                }
            } catch (err) {
                btnSyncAsaas.innerHTML = '<i class="fas fa-exclamation-triangle"></i> Erro';
            }
            setTimeout(() => {
                btnSyncAsaas.innerHTML = originalHTML;
                btnSyncAsaas.disabled = false;
            }, 2500);
        });
    }

    // Toggle Pagos / Em débito
    document.querySelectorAll('.extract-tab').forEach(btn => {
        btn.addEventListener('click', async () => {
            const kind = btn.dataset.kind;
            if (kind === extractKind) return;
            extractKind = kind;
            // Estilo visual do toggle
            document.querySelectorAll('.extract-tab').forEach(b => {
                if (b.dataset.kind === kind) {
                    b.classList.add('active');
                    b.style.background = 'var(--primary)';
                    b.style.color = '#fff';
                } else {
                    b.classList.remove('active');
                    b.style.background = 'transparent';
                    b.style.color = 'var(--text-secondary)';
                }
            });
            const tbody = document.getElementById('extract-table-body');
            if (tbody) tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; padding: 1rem;">Carregando...</td></tr>';
            try {
                const data = await loadExtractData();
                renderExtractData(data);
            } catch (e) {
                if (tbody) tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; padding: 1rem;">Erro ao carregar.</td></tr>';
            }
        });
    });
    document.getElementById('confirmedEditCancel')?.addEventListener('click', closeConfirmedEditModal);
    document.getElementById('confirmedEditSave')?.addEventListener('click', saveConfirmedPackageEdition);
    document.getElementById('packageConfirmModal')?.addEventListener('click', (e) => {
        if (e.target.id === 'packageConfirmModal') closeModal();
    });
    document.getElementById('customerModal')?.addEventListener('click', (e) => {
        if (e.target.id === 'customerModal') closeModal();
    });
    document.getElementById('extractModal')?.addEventListener('click', (e) => {
        if (e.target.id === 'extractModal') {
            document.getElementById('extractModal').hidden = true;
        }
    });
    document.getElementById('confirmedPackageEditModal')?.addEventListener('click', (e) => {
        if (e.target.id === 'confirmedPackageEditModal') closeConfirmedEditModal();
    });
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') closeModal();
    });

    document.getElementById('btn-create-package')?.addEventListener('click', openCreatePackageModal);

    // Handlers dos cards do step 0 (choose)
    document.addEventListener('click', (e) => {
        const card = e.target.closest('.create-package-choice-card');
        if (!card) return;
        const mode = card.dataset.adhocMode;
        if (mode === 'poll') {
            // Dispara fluxo existente (enquete 72h)
            setCreatePackageStep('polls');
            const searchInput = document.getElementById('create-package-poll-search');
            if (searchInput) searchInput.value = '';
            if (typeof loadPollsPage === 'function') loadPollsPage(1);
        } else if (mode === 'adhoc') {
            // Dispara fluxo novo — adhoc_package.js escuta este evento
            document.dispatchEvent(new CustomEvent('adhoc:start'));
        }
    });

    document.getElementById('createPackageCancel')?.addEventListener('click', closeModal);
    // Listener de busca vive agora em bindCreatePackagePaginationControls(),
    // chamado em openCreatePackageModal(). Os controles de paginação (prev/next)
    // são bindados na mesma função.
    function escapeHtmlManual(s) {
        const d = document.createElement('div');
        d.textContent = s == null ? '' : String(s);
        return d.innerHTML;
    }

    function renderManualPreviewContent(preview) {
        const el = document.getElementById('create-package-preview-body');
        if (!el || !preview) return;
        const lines = [];
        lines.push(`<div class="create-package-preview-title"><strong>${escapeHtmlManual(preview.poll_title)}</strong></div>`);
        lines.push(`<div class="create-package-preview-line">Total: <strong>${preview.total_qty}</strong> peças</div>`);
        if (preview.valor_col != null && String(preview.valor_col).trim() !== '') {
            lines.push(`<div class="create-package-preview-line">Valor: ${escapeHtmlManual(preview.valor_col)}</div>`);
        }
        lines.push('<ul class="create-package-preview-votes">');
        (preview.votes || []).forEach((v) => {
            const name = v.name && String(v.name).trim() ? v.name : '(sem nome no cadastro)';
            lines.push(
                `<li>${escapeHtmlManual(name)} — ${escapeHtmlManual(v.phone)} — ${v.qty} pçs</li>`
            );
        });
        lines.push('</ul>');
        el.innerHTML = lines.join('');
    }

    document.getElementById('createPackageBack')?.addEventListener('click', () => {
        const step = window.__createPackageStep || 'polls';
        if (step === 'preview') {
            setCreatePackageStep('votes');
            return;
        }
        setCreatePackageStep('polls');
        window.__createPackageSelected = null;
        document.querySelectorAll('.create-package-poll-card').forEach((c) => c.classList.remove('selected'));
        const saveBtn = document.getElementById('createPackageSave');
        if (saveBtn) saveBtn.disabled = true;
        const valMsg = document.getElementById('create-package-validation-msg');
        if (valMsg) {
            valMsg.hidden = true;
            valMsg.textContent = '';
        }
    });
    document.getElementById('createPackageAddRow')?.addEventListener('click', () => {
        const c = document.getElementById('create-package-vote-rows');
        if (c) {
            appendCreatePackageVoteRow(c);
            updateCreatePackageTotals();
        }
    });
    document.getElementById('createPackageSave')?.addEventListener('click', async () => {
        const sel = window.__createPackageSelected;
        if (!sel || !sel.pollId) return;
        const votes = getCreatePackageVotePayload();
        if (votes.length === 0 || votes.reduce((s, v) => s + v.qty, 0) !== 24) return;
        const saveBtn = document.getElementById('createPackageSave');
        if (saveBtn) {
            saveBtn.disabled = true;
            saveBtn.textContent = 'Carregando…';
        }
        try {
            const response = await fetch('/api/packages/manual/preview', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ pollId: sel.pollId, votes }),
            });
            const result = await response.json().catch(() => ({}));
            if (response.ok && result.status === 'success' && result.preview) {
                window.__manualPreviewPayload = result.preview;
                renderManualPreviewContent(result.preview);
                setCreatePackageStep('preview');
            } else {
                const d = result.detail;
                alert(typeof d === 'string' ? d : (Array.isArray(d) ? d.map((x) => x.msg || x).join(' ') : JSON.stringify(d || result)));
            }
        } catch (e) {
            console.error(e);
            alert('Falha ao gerar preview.');
        } finally {
            if (saveBtn) {
                saveBtn.textContent = 'Revisar';
            }
            updateCreatePackageTotals();
        }
    });

    document.getElementById('createPackageConfirm')?.addEventListener('click', async () => {
        const sel = window.__createPackageSelected;
        if (!sel || !sel.pollId) return;
        const votes = getCreatePackageVotePayload();
        if (votes.length === 0 || votes.reduce((s, v) => s + v.qty, 0) !== 24) return;
        const confirmBtn = document.getElementById('createPackageConfirm');
        if (confirmBtn) {
            confirmBtn.disabled = true;
            confirmBtn.textContent = 'Confirmando…';
        }
        try {
            const response = await fetch('/api/packages/manual/confirm', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ pollId: sel.pollId, votes }),
            });
            const result = await response.json().catch(() => ({}));
            if (response.ok && result.status === 'success' && result.data) {
                closeModal();
                updateUI(result.data);
            } else {
                const d = result.detail;
                alert(typeof d === 'string' ? d : (Array.isArray(d) ? d.map((x) => x.msg || x).join(' ') : JSON.stringify(d || result)));
            }
        } catch (e) {
            console.error(e);
            alert('Falha ao confirmar pacote.');
        } finally {
            if (confirmBtn) {
                confirmBtn.disabled = false;
                confirmBtn.textContent = 'Confirmar pacote';
            }
        }
    });
    document.getElementById('createPackageModal')?.addEventListener('click', (e) => {
        if (e.target.id === 'createPackageModal') closeModal();
    });

    function renderCustomerRank(containerId, customerData) {
        const container = document.getElementById(containerId);
        container.innerHTML = '';

        const sorted = Object.values(customerData).sort((a, b) => b.qty - a.qty).slice(0, 5);

        sorted.forEach((item, index) => {
            const div = document.createElement('div');
            div.className = 'rank-item';
            const num = document.createElement('div');
            num.className = 'rank-number';
            num.textContent = `0${index + 1}`;
            const content = document.createElement('div');
            content.className = 'rank-content';
            const name = document.createElement('div');
            name.className = 'rank-name';
            name.textContent = getCustomerName(item.phone, item.name);
            const meta = document.createElement('div');
            meta.className = 'rank-meta';
            meta.textContent = item.phone || 'Sem telefone';
            content.appendChild(name);
            content.appendChild(meta);
            const badge = document.createElement('div');
            badge.className = 'rank-badge';
            badge.textContent = `${item.qty} pçs`;
            div.appendChild(num);
            div.appendChild(content);
            div.appendChild(badge);
            container.appendChild(div);
        });
    }

    function renderChart(hourData) {
        const ctx = document.getElementById('hourlyChart').getContext('2d');

        // Gráfico rotativo: começa em (agora+1) e vai 24h pra frente (= 24h pra trás).
        // Assim a ponta direita é sempre a hora atual.
        const nowHour = new Date().getHours();
        const startHour = (nowHour + 1) % 24;
        const labels = Array.from({ length: 24 }, (_, i) => {
            const h = (startHour + i) % 24;
            return `${h}:00`;
        });
        const values = labels.map((_, i) => {
            const h = (startHour + i) % 24;
            return hourData[h] || 0;
        });

        if (chartInstance) chartInstance.destroy();

        // New gradient for the line
        const gradient = ctx.createLinearGradient(0, 0, 0, 400);
        gradient.addColorStop(0, 'rgba(255, 255, 255, 0.4)');
        gradient.addColorStop(1, 'rgba(255, 255, 255, 0)');

        chartInstance = new Chart(ctx, {
            type: 'line',
            data: {
                labels: labels,
                datasets: [{
                    label: 'Votos',
                    data: values,
                    borderColor: '#FFFFFF',
                    borderWidth: 3,
                    backgroundColor: gradient,
                    fill: true,
                    tension: 0.4,
                    pointRadius: 0,
                    pointHoverRadius: 6,
                    pointHoverBackgroundColor: '#FFFFFF',
                    pointHoverBorderColor: '#BA9797',
                    pointHoverBorderWidth: 3
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                resizeDelay: 200,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        mode: 'index',
                        intersect: false,
                        backgroundColor: 'rgba(255, 255, 255, 0.95)',
                        titleColor: '#2D1F1F',
                        bodyColor: '#2D1F1F',
                        titleFont: { family: 'Outfit', size: 12, weight: '700' },
                        bodyFont: { family: 'Montserrat', size: 11 },
                        padding: 10,
                        cornerRadius: 8,
                        displayColors: false,
                        borderWidth: 1,
                        borderColor: 'rgba(0,0,0,0.05)'
                    }
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        grid: { color: 'rgba(255, 255, 255, 0.1)', drawBorder: false },
                        ticks: { color: 'rgba(255, 255, 255, 0.8)', font: { size: 10 } }
                    },
                    x: {
                        grid: { display: false },
                        ticks: {
                            color: 'rgba(255, 255, 255, 0.8)',
                            font: { size: 10 },
                            maxRotation: 0,
                            autoSkip: true,
                            maxTicksLimit: 12
                        }
                    }
                },
                interaction: {
                    intersect: false,
                    mode: 'nearest'
                }
            }
        });
    }

    // Dynamically compute item height and enable scrollable lists for packages
    function setDynamicListHeight() {
        try {
            const sample = document.querySelector('#rank-packages-open .rank-item') ||
                document.querySelector('#rank-packages-closed-today .rank-item') ||
                document.querySelector('#rank-packages-confirmed-today .rank-item') ||
                document.querySelector('#rank-packages-rejected-today .rank-item');
            if (!sample) return;
            const itemHeight = Math.ceil(sample.getBoundingClientRect().height);
            document.documentElement.style.setProperty('--rank-item-height', `${itemHeight}px`);
        } catch (err) {
            // ignore
        }
    }

    function enablePackageListScroll() {
        const ids = ['rank-packages-open', 'rank-packages-closed-today', 'rank-packages-confirmed-today', 'rank-packages-rejected-today'];
        ids.forEach(id => {
            const el = document.getElementById(id);
            if (!el) return;
            el.classList.add('rank-list-scrollable');
            el.setAttribute('role', 'list');
            el.setAttribute('tabindex', '0');
        });
    }

    // Fade handlers removed — fades were disabled per user request

    // Customers Logic
    let allCustomers = [];
    let currentCustomersSearch = '';
    let currentCustomersPage = 1;
    const customersPageSize = 50;
    let customersHasPrevPage = false;
    let customersHasNextPage = false;
    let customersTotalItems = 0;
    let customersSearchTimer = null;

    async function loadCustomersData(pageOrOpts = currentCustomersPage, options = {}) {
        const tbody = document.getElementById('customers-table-body');
        if (!tbody) return;

        // Compatibilidade: aceitar loadCustomersData({silent: true}) ou loadCustomersData(page, {silent: true})
        let page = currentCustomersPage;
        let opts = options;
        if (typeof pageOrOpts === 'object' && pageOrOpts !== null) {
            opts = pageOrOpts;
        } else if (pageOrOpts !== undefined) {
            page = pageOrOpts;
        }

        const silent = opts.silent === true;
        const scrollY = silent ? window.scrollY : 0;

        if (!silent) {
            tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; padding: 2rem;">Carregando...</td></tr>';
        }
        currentCustomersPage = Math.max(1, page || 1);

        try {
            const params = new URLSearchParams({
                page: String(currentCustomersPage),
                page_size: String(customersPageSize)
            });
            if (currentCustomersSearch) {
                params.set('search', currentCustomersSearch);
            }
            const response = await fetch(`/api/customers/?${params.toString()}`);
            if (!response.ok) throw new Error('Erro ao carregar clientes');

            const customersPayload = await response.json();
            if (Array.isArray(customersPayload)) {
                allCustomers = customersPayload;
                customersHasPrevPage = false;
                customersHasNextPage = false;
                customersTotalItems = allCustomers.length;
            } else {
                allCustomers = Array.isArray(customersPayload?.items) ? customersPayload.items : [];
                currentCustomersPage = Number(customersPayload?.page || currentCustomersPage || 1);
                customersHasPrevPage = Boolean(customersPayload?.has_prev);
                customersHasNextPage = Boolean(customersPayload?.has_next);
                customersTotalItems = Number(customersPayload?.total || 0);
            }
            renderCustomersTable(tbody, allCustomers);
            renderCustomersPagination();
            if (silent && scrollY > 0) window.scrollTo(0, scrollY);
        } catch (error) {
            console.error('Error loading customers data:', error);
            if (!silent) {
                tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; padding: 2rem;">Erro ao carregar dados de clientes.</td></tr>';
            }
        }
    }

    function renderCustomersTable(tbody, customers) {
        tbody.innerHTML = '';

        if (!customers || customers.length === 0) {
            tbody.innerHTML = '<tr><td colspan="7" style="text-align:center; padding: 2rem;">Nenhum cliente cadastrado.</td></tr>';
            return;
        }

        const formatter = new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' });
        const formatClick = (raw) => {
            if (!raw) return '—';
            const d = new Date(raw);
            if (isNaN(d.getTime())) return '—';
            return d.toLocaleString('pt-BR', {
                day: '2-digit', month: '2-digit', year: 'numeric',
                hour: '2-digit', minute: '2-digit',
            });
        };

        customers.forEach(customer => {
            const tr = document.createElement('tr');
            const displayName = getCustomerName(customer.phone, customer.name);
            const totalDebt = Number(customer.total_debt || 0);
            const debtClass = totalDebt > 0 ? 'style="color: #c65555; font-weight: 700;"' : 'style="color: #8a8a8a;"';
            const lastClick = formatClick(customer.last_pay_click_at);
            const lastClickStyle = customer.last_pay_click_at
                ? 'color: var(--text-primary);'
                : 'color: #8a8a8a;';
            tr.innerHTML = `
                <td style="font-weight: 600;">${customer.phone || ''}</td>
                <td>${displayName}</td>
                <td style="text-align: center; font-family: 'Outfit', sans-serif;">${customer.qty || 0}</td>
                <td style="text-align: center; font-family: 'Outfit', sans-serif;" ${debtClass}>${formatter.format(totalDebt)}</td>
                <td style="text-align: center; font-family: 'Outfit', sans-serif; font-weight: 600;">${formatter.format(customer.total_paid || 0)}</td>
                <td style="text-align: center; font-family: 'Outfit', sans-serif; font-size: 0.85rem; ${lastClickStyle}">${lastClick}</td>
                <td style="text-align: right;">
                    <button class="btn-package-action edit-customer-btn" data-phone="${customer.phone}" data-name="${displayName}" title="Editar Nome" aria-label="Editar cliente ${displayName}">
                        <i class="fas fa-pen"></i>
                    </button>
                </td>
            `;
            tbody.appendChild(tr);
        });

        tbody.querySelectorAll('.edit-customer-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                const phone = btn.getAttribute('data-phone');
                const currentName = btn.getAttribute('data-name');

                isEditingCustomer = true;
                customerModalTitle.textContent = 'Editar Cliente';
                // F-043: mostra telefone formatado mesmo no modo editar (read-only)
                const formatBr = (typeof formatBrPhoneDisplay === 'function') ? formatBrPhoneDisplay : (x) => x;
                customerPhoneInput.value = formatBr(phone);
                customerPhoneInput.disabled = true;
                if (customerPhoneHint) {
                    customerPhoneHint.textContent = 'Telefone não pode ser alterado. Para trocar, cadastre um novo cliente.';
                    customerPhoneHint.style.color = 'var(--text-secondary)';
                }
                customerNameInput.value = currentName;
                customerModal.hidden = false;
                setTimeout(() => customerNameInput.focus(), 50);
            });
        });
    }

    function renderCustomersPagination() {
        const summary = document.getElementById('customers-pagination-summary');
        const prevBtn = document.getElementById('customers-page-prev');
        const nextBtn = document.getElementById('customers-page-next');
        const jumpSelect = document.getElementById('customers-page-jump');
        const totalPages = Math.max(1, Math.ceil((customersTotalItems || 0) / customersPageSize));
        if (summary) {
            const searchLabel = currentCustomersSearch ? `, busca: "${currentCustomersSearch}"` : '';
            summary.textContent = `Pagina ${currentCustomersPage} de ${totalPages} (${customersTotalItems || allCustomers.length} itens)${searchLabel}`;
        }
        if (jumpSelect) {
            const current = String(currentCustomersPage);
            if (jumpSelect.dataset.total !== String(totalPages)) {
                jumpSelect.innerHTML = '';
                for (let p = 1; p <= totalPages; p++) {
                    const opt = document.createElement('option');
                    opt.value = String(p);
                    opt.textContent = `Pág. ${p}`;
                    jumpSelect.appendChild(opt);
                }
                jumpSelect.dataset.total = String(totalPages);
            }
            jumpSelect.value = current;
            jumpSelect.disabled = totalPages <= 1;
        }
        if (prevBtn) prevBtn.disabled = !customersHasPrevPage;
        if (nextBtn) nextBtn.disabled = !customersHasNextPage;
    }

    const customersSearchInput = document.getElementById('customers-search');
    if (customersSearchInput) {
        customersSearchInput.addEventListener('input', (e) => {
            currentCustomersSearch = e.target.value;
            currentCustomersPage = 1;
            if (customersSearchTimer) clearTimeout(customersSearchTimer);
            customersSearchTimer = setTimeout(() => loadCustomersData(1), 250);
        });
    }

    document.getElementById('customers-page-prev')?.addEventListener('click', () => {
        if (!customersHasPrevPage) return;
        loadCustomersData(currentCustomersPage - 1);
    });

    document.getElementById('customers-page-next')?.addEventListener('click', () => {
        if (!customersHasNextPage) return;
        loadCustomersData(currentCustomersPage + 1);
    });

    document.getElementById('customers-page-jump')?.addEventListener('change', (e) => {
        const target = parseInt(e.target.value, 10);
        if (Number.isFinite(target) && target >= 1 && target !== currentCustomersPage) {
            loadCustomersData(target);
        }
    });

    // Customer Modal Logic
    const customerModal = document.getElementById('customerModal');
    const customerModalTitle = document.getElementById('customerModalTitle');
    const customerPhoneInput = document.getElementById('customer-phone');
    const customerNameInput = document.getElementById('customer-name');
    const customerPhoneHint = document.getElementById('customer-phone-hint');
    const btnAddCustomer = document.getElementById('btn-add-customer');
    const btnCustomerModalCancel = document.getElementById('customerModalCancel');
    const btnCustomerModalConfirm = document.getElementById('customerModalConfirm');

    let isEditingCustomer = false;

    // --- Phone helpers ---
    // F-043: sanitiza entrada removendo tudo que não é dígito.
    // Aceita formatos: "11962432447", "(11) 9 6243-2447", "5511962432447",
    // "+55 (11) 96243-2447", "5562993353390", etc.
    // Retorna sempre com DDI 55 no começo quando possível.
    function sanitizeBrPhoneDigits(raw) {
        let d = String(raw || '').replace(/\D/g, '');
        // Se começar com 0 (ex: 011...), tira o zero
        d = d.replace(/^0+/, '');
        // Se tem DDI 55 no começo e >= 12 dígitos, mantém
        // Se tem 10 ou 11 dígitos (DDD + número com ou sem 9), prepend 55
        if (d.length >= 10 && d.length <= 11 && !d.startsWith('55')) {
            d = '55' + d;
        }
        return d;
    }

    // Formata visualmente: "5511962432447" → "+55 (11) 9 6243-2447"
    function formatBrPhoneDisplay(raw) {
        const d = String(raw || '').replace(/\D/g, '');
        if (!d) return '';
        // Até 2 dígitos: mostra só
        if (d.length <= 2) return '+' + d;
        // DDI + DDD parcial
        if (d.length <= 4) return `+${d.slice(0, 2)} (${d.slice(2)}`;
        // DDI + DDD completo
        if (d.length <= 5) return `+${d.slice(0, 2)} (${d.slice(2, 4)}) ${d.slice(4)}`;
        // DDI + DDD + "9" + parte do número
        if (d.length <= 9) {
            const ddi = d.slice(0, 2);
            const ddd = d.slice(2, 4);
            const rest = d.slice(4);
            if (rest.length === 0) return `+${ddi} (${ddd})`;
            if (rest.length <= 4) return `+${ddi} (${ddd}) ${rest}`;
            // separa o 9 se tiver
            if (rest.length === 5) return `+${ddi} (${ddd}) ${rest.slice(0, 1)} ${rest.slice(1)}`;
            return `+${ddi} (${ddd}) ${rest.slice(0, 1)} ${rest.slice(1, 5)}-${rest.slice(5)}`;
        }
        // Formato final "+55 (11) 9 6243-2447" (13 chars = DDI+DDD+9+8)
        const ddi = d.slice(0, 2);
        const ddd = d.slice(2, 4);
        const rest = d.slice(4);
        // Celular com 9 (9 dígitos depois do DDD)
        if (rest.length === 9) {
            return `+${ddi} (${ddd}) ${rest.slice(0, 1)} ${rest.slice(1, 5)}-${rest.slice(5)}`;
        }
        // Celular sem 9 ou fixo (8 dígitos depois do DDD)
        if (rest.length === 8) {
            return `+${ddi} (${ddd}) ${rest.slice(0, 4)}-${rest.slice(4)}`;
        }
        // Caso de dígitos a mais, mostra como está
        return `+${ddi} (${ddd}) ${rest}`;
    }

    function validateBrPhone(digits) {
        // Com DDI 55: 12 (fixo) ou 13 (celular com 9) dígitos
        if (!digits || digits.length < 10) return false;
        if (digits.startsWith('55')) {
            return digits.length === 12 || digits.length === 13;
        }
        return digits.length === 10 || digits.length === 11;
    }

    if (customerPhoneInput) {
        // Filtro em tempo real: só dígitos, com formatação visual
        customerPhoneInput.addEventListener('input', (e) => {
            const raw = e.target.value;
            const digits = String(raw || '').replace(/\D/g, '');
            e.target.value = formatBrPhoneDisplay(digits);

            if (customerPhoneHint) {
                if (!digits) {
                    customerPhoneHint.textContent = 'DDD + número. O código 55 do Brasil é adicionado automaticamente.';
                    customerPhoneHint.style.color = 'var(--text-secondary)';
                } else if (validateBrPhone(sanitizeBrPhoneDigits(digits))) {
                    customerPhoneHint.textContent = '✓ Número válido';
                    customerPhoneHint.style.color = '#059669';
                } else {
                    const normalized = sanitizeBrPhoneDigits(digits);
                    customerPhoneHint.textContent = `Digite mais números (${normalized.length}/12 ou 13)`;
                    customerPhoneHint.style.color = '#c65555';
                }
            }
        });

        // Paste handler: sanitiza conteúdo colado
        customerPhoneInput.addEventListener('paste', (e) => {
            e.preventDefault();
            const pasted = (e.clipboardData || window.clipboardData).getData('text') || '';
            const digits = pasted.replace(/\D/g, '');
            customerPhoneInput.value = formatBrPhoneDisplay(digits);
            customerPhoneInput.dispatchEvent(new Event('input', { bubbles: true }));
        });
    }

    if (btnAddCustomer) {
        btnAddCustomer.addEventListener('click', () => {
            isEditingCustomer = false;
            customerModalTitle.textContent = 'Cadastrar Cliente';
            customerPhoneInput.value = '';
            customerPhoneInput.disabled = false;
            customerNameInput.value = '';
            if (customerPhoneHint) {
                customerPhoneHint.textContent = 'DDD + número. O código 55 do Brasil é adicionado automaticamente.';
                customerPhoneHint.style.color = 'var(--text-secondary)';
            }
            customerModal.hidden = false;
            // F-043: foca no celular primeiro (era no nome, o que causava confusão)
            setTimeout(() => customerPhoneInput.focus(), 50);
        });
    }

    if (btnCustomerModalCancel) {
        btnCustomerModalCancel.addEventListener('click', () => {
            customerModal.hidden = true;
        });
    }

    if (btnCustomerModalConfirm) {
        btnCustomerModalConfirm.addEventListener('click', async () => {
            // F-043: sanitiza + normaliza pra formato backend (só dígitos, com DDI 55)
            const phoneDigits = sanitizeBrPhoneDigits(customerPhoneInput.value);
            const name = customerNameInput.value.trim();

            // Detecção de swap: se o nome parece um telefone e o phone parece um nome, oferece trocar
            const nameLooksLikePhone = /^\+?[\d\s().-]{8,}$/.test(name) && name.replace(/\D/g, '').length >= 10;
            const phoneLooksLikeName = /[a-zA-ZÀ-ÿ]{3,}/.test(customerPhoneInput.value);
            if (nameLooksLikePhone && phoneLooksLikeName) {
                if (confirm('Parece que os campos estão invertidos. Quer trocar automaticamente?')) {
                    const tempName = customerPhoneInput.value;
                    customerPhoneInput.value = formatBrPhoneDisplay(name.replace(/\D/g, ''));
                    customerNameInput.value = tempName;
                    customerPhoneInput.dispatchEvent(new Event('input', { bubbles: true }));
                    return; // usuário clica Salvar de novo após a correção
                }
            }

            if (!validateBrPhone(phoneDigits)) {
                alert('Por favor, insira um celular válido. Formato esperado: DDD + número (10 a 13 dígitos no total, ex: (11) 9 6243-2447).');
                customerPhoneInput.focus();
                return;
            }
            if (!name) {
                alert('Por favor, insira o nome do cliente.');
                customerNameInput.focus();
                return;
            }

            const phone = phoneDigits;

            btnCustomerModalConfirm.disabled = true;
            btnCustomerModalConfirm.textContent = 'Salvando...';

            try {
                const method = isEditingCustomer ? 'PATCH' : 'POST';
                const url = isEditingCustomer ? `/api/customers/${phone}` : '/api/customers/';
                const body = isEditingCustomer ? { name } : { phone, name };

                const response = await fetch(url, {
                    method: method,
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body)
                });
                const payload = await response.json().catch(() => ({}));

                if (response.ok) {
                    // Update local memory instantly for late binding
                    globalCustomersMap[phone] = name;
                    
                    customerModal.hidden = true;
                    if (payload.simulated && Array.isArray(payload.customers)) {
                        allCustomers = payload.customers;
                        const tbody = document.getElementById('customers-table-body');
                        if (tbody) renderCustomersTable(tbody, allCustomers);
                    } else {
                        loadCustomersData();
                        performAutoRefresh();
                    }
                } else {
                    alert('Erro ao salvar: ' + (payload.detail || 'Falha desconhecida'));
                }
            } catch (error) {
                console.error('Error saving customer:', error);
                alert('Falha na conexão com o servidor.');
            } finally {
                btnCustomerModalConfirm.disabled = false;
                btnCustomerModalConfirm.textContent = 'Salvar Cliente';
            }
        });
    }

    // Refresh Logic (Automatic polling for near real-time updates)
    async function performAutoRefresh() {
        if (metricsRefreshInFlight || document.hidden) return;
        metricsRefreshInFlight = true;
        try {
            const response = await fetch('/api/metrics');
            const data = await response.json().catch(() => null);

            if (response.ok && data) {
                updateUI(data);
                // Also refresh finance if it's currently visible (silent: sem piscar)
                const financeSection = document.getElementById('section-finance');
                if (financeSection && financeSection.style.display !== 'none') {
                    loadFinanceData(currentFinancePage, { silent: true });
                }
                // E também a aba de clientes
                const customersSection = document.getElementById('section-customers');
                if (customersSection && customersSection.style.display !== 'none') {
                    loadCustomersData({ silent: true });
                }
            } else {
                console.warn('Metrics auto-refresh failed');
            }
        } catch (error) {
            console.error('Refresh failed:', error);
        } finally {
            metricsRefreshInFlight = false;
        }
    }

    // Initial sync with SSE and polling fallback
    startAutoRefreshPolling();
    connectRealtimeStream();

    // Manual Sync Button
    const btnSync = document.getElementById('btnSync');
    if (btnSync) {
        btnSync.addEventListener('click', async () => {
            if (btnSync.classList.contains('syncing')) return;

            btnSync.classList.add('syncing');
            btnSync.classList.remove('success', 'error');

            try {
                const response = await fetch('/api/refresh', { method: 'POST' });
                const result = await response.json().catch(() => ({}));

                if (response.ok && result.status === 'success') {
                    updateUI(result.data);
                    loadFinanceData();
                    btnSync.classList.add('success');
                } else {
                    btnSync.classList.add('error');
                    console.error('Sync failed:', result.detail || result.message);
                }
            } catch (error) {
                btnSync.classList.add('error');
                console.error('Sync error:', error);
            } finally {
                btnSync.classList.remove('syncing');
                setTimeout(() => btnSync.classList.remove('success', 'error'), 2000);
            }
        });
    }

    // F-050: botão de refresh do termômetro de vendas
    const btnRefreshTemp = document.getElementById('btn-refresh-temperature');
    if (btnRefreshTemp) {
        btnRefreshTemp.addEventListener('click', async (ev) => {
            ev.stopPropagation();
            if (btnRefreshTemp.classList.contains('spinning')) return;
            btnRefreshTemp.classList.add('spinning');
            try {
                const response = await fetch('/api/metrics/temperature/refresh', { method: 'POST' });
                const result = await response.json().catch(() => ({}));
                if (response.ok && result.temperature) {
                    renderSalesTemperature(result.temperature);
                }
            } catch (err) {
                console.error('temperature refresh error:', err);
            } finally {
                setTimeout(() => btnRefreshTemp.classList.remove('spinning'), 600);
            }
        });
    }

    // Update greeting based on time
    const hour = new Date().getHours();
    const greetingEl = document.getElementById('greeting');
    if (greetingEl) {
        if (hour < 12) greetingEl.textContent = 'Bom dia';
        else if (hour < 18) greetingEl.textContent = 'Boa tarde';
        else greetingEl.textContent = 'Boa noite';
    }

    document.addEventListener('visibilitychange', () => {
        if (document.hidden) return;
        if (!realtimeSource) {
            connectRealtimeStream();
        }
        performAutoRefresh();
    });

    window.addEventListener('beforeunload', () => {
        clearRealtimeReconnectTimer();
        closeRealtimeStream();
        stopAutoRefreshPolling();
    });
});
