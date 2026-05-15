/* Aba Financeiro — Contas a Receber */
(function () {
    "use strict";

    const BUCKET_COLORS = {
        "0-7": "#22c55e",
        "8-15": "#eab308",
        "16-30": "#f97316",
        "30+": "#ef4444",
    };
    const fmtMoney = (v) =>
        "R$ " + Number(v || 0).toFixed(2).replace(".", ",");

    const state = {
        tab: "receivable",      // "receivable" | "paid"
        // A receber
        mode: "by-client",
        filter: "all",
        search: "",
        receivables: [],
        page: 1,
        pageSize: 25,
        // Pagos
        paidMode: "by-client",
        paidFilter: "all",
        paidSearch: "",
        paid: [],
        paidPage: 1,
    };

    function el(id) { return document.getElementById(id); }

    function _filterQS() {
        const f = window.dashboardFilter || {};
        const p = new URLSearchParams();
        if (f.since) p.set("since", f.since);
        if (f.until) p.set("until", f.until);
        const s = p.toString();
        return s ? "?" + s : "";
    }

    // ---- KPIs ----
    async function loadAgingSummary() {
        const res = await fetch("/api/finance/aging-summary" + _filterQS(), { credentials: "same-origin" });
        if (!res.ok) return;
        const s = await res.json();

        el("finance-receivable-total").textContent = fmtMoney(s.total_receivable);
        el("finance-receivable-meta").textContent =
            `${s.count} cobranças · ${s.clients_count} clientes`;

        const total = s.total_receivable || 1;
        const bar = el("finance-aging-bar");
        bar.querySelectorAll(".aging-bucket").forEach((seg) => {
            const b = seg.dataset.bucket;
            const amount = (s.buckets[b] || {}).amount || 0;
            seg.style.width = ((amount / total) * 100).toFixed(1) + "%";
            seg.title = `${b}: ${fmtMoney(amount)} (${(s.buckets[b] || {}).count || 0})`;
        });
        el("finance-aging-legend").innerHTML = ["0-7", "8-15", "16-30", "30+"]
            .map((b) => {
                const item = s.buckets[b] || {};
                return `<span class="aging-legend-item" style="--c:${BUCKET_COLORS[b]}">
                    <span class="aging-legend-dot"></span>${b}d: ${fmtMoney(item.amount)}
                </span>`;
            }).join("");

        el("finance-avg-age").textContent = (s.avg_age_days || 0).toFixed(0) + "d";
        el("finance-paid-rate").textContent =
            ((s.paid_rate_30d || 0) * 100).toFixed(0) + "%";
    }

    // ---- Receivables ----
    async function loadReceivables() {
        const res = await fetch("/api/finance/receivables" + _filterQS(), { credentials: "same-origin" });
        if (!res.ok) return;
        state.receivables = await res.json();
        render();
    }

    function filterRows() {
        let rows = state.receivables;
        if (state.filter === "written_off") {
            return [];  // TODO Fase 5: endpoint /receivables?include_written_off
        }
        if (state.filter !== "all") {
            rows = rows.filter((r) => r.bucket === state.filter);
        }
        if (state.search) {
            const q = state.search.toLowerCase();
            rows = rows.filter((r) =>
                (r.nome || "").toLowerCase().includes(q) ||
                (r.celular_last4 || "").includes(q)
            );
        }
        return rows;
    }

    function render() {
        const tbody = el("finance-table-body");
        const rows = filterRows();
        const start = (state.page - 1) * state.pageSize;
        const pageRows = rows.slice(start, start + state.pageSize);
        tbody.innerHTML = "";

        if (state.mode === "by-charge") {
            renderByCharge(tbody, pageRows);
        } else {
            renderByClient(tbody, pageRows);
        }

        el("finance-pagination-summary").textContent =
            `Página ${state.page} de ${Math.max(1, Math.ceil(rows.length / state.pageSize))} (${rows.length} resultados)`;
    }

    function renderByClient(tbody, rows) {
        rows.forEach((r) => {
            const tr = document.createElement("tr");
            tr.className = "client-row";
            tr.innerHTML = `
                <td>${escapeHtml(r.nome)}</td>
                <td>***${escapeHtml(r.celular_last4 || "")}</td>
                <td>${fmtMoney(r.total)}</td>
                <td>${r.count}</td>
                <td><span class="aging-badge bucket-${r.bucket.replace("+","plus")}">${r.oldest_age_days}d</span></td>
                <td style="text-align:right;">
                    <button class="btn-expand" data-cliente="${r.cliente_id}"><i class="fas fa-chevron-right"></i></button>
                </td>
            `;
            tbody.appendChild(tr);

            const expandTr = document.createElement("tr");
            expandTr.className = "client-expand";
            expandTr.dataset.cliente = r.cliente_id;
            expandTr.style.display = "none";
            expandTr.innerHTML = `
                <td colspan="6">
                    <table class="charges-mini">
                        <thead>
                            <tr><th>Pacote</th><th>Valor</th><th>Idade</th><th>Status</th><th></th></tr>
                        </thead>
                        <tbody>
                            ${r.charges.map((c) => `
                                <tr>
                                    <td>${escapeHtml(c.enquete_titulo) || c.pacote_id}</td>
                                    <td>${fmtMoney(c.valor)}</td>
                                    <td>${c.age_days}d</td>
                                    <td>${c.status}</td>
                                    <td>
                                        <button class="btn-history" data-pag="${c.pagamento_id}" title="Histórico"><i class="fas fa-scroll"></i></button>
                                        <button class="btn-writeoff" data-pag="${c.pagamento_id}" data-cliente-nome="${escapeHtml(r.nome)}" data-valor="${c.valor}" data-pacote="${escapeHtml(c.enquete_titulo)}" title="Marcar como perdido"><i class="fas fa-times-circle"></i></button>
                                    </td>
                                </tr>
                            `).join("")}
                        </tbody>
                    </table>
                </td>
            `;
            tbody.appendChild(expandTr);
        });
    }

    function renderByCharge(tbody, rows) {
        const charges = [];
        rows.forEach((r) => r.charges.forEach((c) =>
            charges.push({ ...c, nome: r.nome, celular_last4: r.celular_last4 })
        ));
        charges.forEach((c) => {
            const tr = document.createElement("tr");
            tr.innerHTML = `
                <td>${escapeHtml(c.nome)}</td>
                <td>${escapeHtml(c.enquete_titulo) || c.pacote_id}</td>
                <td>${fmtMoney(c.valor)}</td>
                <td>${c.status}</td>
                <td>${c.age_days}d</td>
                <td style="text-align:right;">
                    <button class="btn-history" data-pag="${c.pagamento_id}"><i class="fas fa-scroll"></i></button>
                    <button class="btn-writeoff" data-pag="${c.pagamento_id}" data-cliente-nome="${escapeHtml(c.nome)}" data-valor="${c.valor}" data-pacote="${escapeHtml(c.enquete_titulo)}"><i class="fas fa-times-circle"></i></button>
                </td>
            `;
            tbody.appendChild(tr);
        });
    }

    function escapeHtml(s) {
        return String(s || "").replace(/[&<>"']/g, (c) =>
            ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
    }

    // ---- Click handlers (delegated) ----
    document.addEventListener("click", (ev) => {
        const expandBtn = ev.target.closest(".btn-expand[data-cliente]");
        if (expandBtn) {
            const id = expandBtn.dataset.cliente;
            const row = document.querySelector(`.client-expand[data-cliente="${id}"]`);
            if (row) {
                const open = row.style.display !== "none";
                row.style.display = open ? "none" : "table-row";
                expandBtn.querySelector("i").className =
                    "fas " + (open ? "fa-chevron-right" : "fa-chevron-down");
            }
            return;
        }

        const wo = ev.target.closest(".btn-writeoff");
        if (wo) {
            openWriteOffModal(wo.dataset);
            return;
        }

        const hist = ev.target.closest(".btn-history");
        if (hist) {
            openHistoryModal(hist.dataset.pag);
            return;
        }
    });

    // ---- Toggle modo + filtros + busca ----
    document.querySelectorAll(".view-toggle .toggle-btn").forEach((btn) => {
        btn.addEventListener("click", () => {
            document.querySelectorAll(".view-toggle .toggle-btn")
                .forEach((b) => b.classList.toggle("active", b === btn));
            state.mode = btn.dataset.mode;
            state.page = 1;
            updateHead();
            render();
        });
    });

    function updateHead() {
        const thead = el("finance-thead");
        if (state.mode === "by-charge") {
            thead.innerHTML = `<tr><th>Cliente</th><th>Pacote</th><th>Valor</th><th>Status</th><th>Idade</th><th></th></tr>`;
        } else {
            thead.innerHTML = `<tr><th>Cliente</th><th>Celular</th><th>Total devido</th><th>Cobranças</th><th>Idade do mais antigo</th><th></th></tr>`;
        }
    }

    document.querySelectorAll("#section-finance .filter-btn").forEach((btn) => {
        btn.addEventListener("click", () => {
            document.querySelectorAll("#section-finance .filter-btn")
                .forEach((b) => b.classList.toggle("active", b === btn));
            state.filter = btn.dataset.filter;
            state.page = 1;
            render();
        });
    });

    const search = el("finance-search");
    if (search) {
        search.addEventListener("input", () => {
            state.search = search.value.trim();
            state.page = 1;
            render();
        });
    }

    el("finance-page-prev")?.addEventListener("click", () => {
        if (state.page > 1) { state.page -= 1; render(); }
    });
    el("finance-page-next")?.addEventListener("click", () => {
        state.page += 1; render();
    });

    // ---- Modals: write-off + history ----
    function openWriteOffModal(data) {
        const reason = prompt(
            `Marcar como perdido?\n\nCliente: ${data.clienteNome}\nPacote: ${data.pacote}\nValor: ${fmtMoney(data.valor)}\n\nMotivo (obrigatório):`
        );
        if (!reason || !reason.trim()) return;
        fetch(`/api/finance/pagamentos/${data.pag}/write-off`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            credentials: "same-origin",
            body: JSON.stringify({ reason: reason.trim() }),
        }).then((r) => {
            if (!r.ok) { alert("Erro ao marcar como perdido"); return; }
            return refreshAll();
        });
    }

    async function openHistoryModal(pagId) {
        const res = await fetch(`/api/finance/pagamentos/${pagId}/history`, { credentials: "same-origin" });
        if (!res.ok) { alert("Erro ao carregar histórico"); return; }
        const events = await res.json();
        const html = events.map((e) => `
            <div class="history-event">
                <strong>${escapeHtml(e.label)}</strong>
                <span class="history-ts">${formatTs(e.timestamp)}</span>
                ${e.reason ? `<div class="history-reason">${escapeHtml(e.reason)}</div>` : ""}
            </div>
        `).join("") || "<p>Sem eventos registrados.</p>";
        showModal("Histórico do pagamento", html);
    }

    function formatTs(iso) {
        if (!iso) return "—";
        const d = new Date(iso);
        return d.toLocaleDateString("pt-BR") + " " + d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
    }

    function showModal(title, bodyHtml) {
        let backdrop = document.getElementById("finance-modal-backdrop");
        if (!backdrop) {
            backdrop = document.createElement("div");
            backdrop.id = "finance-modal-backdrop";
            backdrop.className = "modal-backdrop";
            backdrop.innerHTML = `
                <div class="modal-card">
                    <div class="modal-head"><h3 id="finance-modal-title"></h3>
                        <button class="modal-close">&times;</button></div>
                    <div class="modal-body" id="finance-modal-body"></div>
                </div>`;
            document.body.appendChild(backdrop);
            backdrop.querySelector(".modal-close").addEventListener("click",
                () => backdrop.style.display = "none");
        }
        document.getElementById("finance-modal-title").textContent = title;
        document.getElementById("finance-modal-body").innerHTML = bodyHtml;
        backdrop.style.display = "flex";
    }

    // ---- Aba Pagos ----
    async function loadPaidSummary() {
        const res = await fetch("/api/finance/paid-summary" + _filterQS(), { credentials: "same-origin" });
        if (!res.ok) return;
        const s = await res.json();
        el("finance-paid-total").textContent = fmtMoney(s.total_paid);
        el("finance-paid-meta").textContent =
            `${s.count} cobranças · ${s.clients_count} clientes`;
        el("finance-paid-count").textContent = String(s.count || 0);
        el("finance-paid-clients").textContent =
            `${s.clients_count || 0} cliente${s.clients_count === 1 ? "" : "s"}`;
        el("finance-paid-avg-ticket").textContent = fmtMoney(s.avg_ticket);
        el("finance-paid-commission").textContent = fmtMoney(s.total_commission);
    }

    async function loadPaid() {
        const res = await fetch("/api/finance/paid" + _filterQS(), { credentials: "same-origin" });
        if (!res.ok) return;
        state.paid = await res.json();
        renderPaid();
    }

    function filterPaidRows() {
        let rows = state.paid;
        if (state.paidFilter !== "all") {
            rows = rows.filter((r) => r.bucket === state.paidFilter);
        }
        if (state.paidSearch) {
            const q = state.paidSearch.toLowerCase();
            rows = rows.filter((r) =>
                (r.nome || "").toLowerCase().includes(q) ||
                (r.celular_last4 || "").includes(q)
            );
        }
        return rows;
    }

    function renderPaid() {
        const tbody = el("finance-paid-table-body");
        const rows = filterPaidRows();
        const start = (state.paidPage - 1) * state.pageSize;
        const pageRows = rows.slice(start, start + state.pageSize);
        tbody.innerHTML = "";

        if (state.paidMode === "by-charge") {
            renderPaidByCharge(tbody, pageRows);
        } else {
            renderPaidByClient(tbody, pageRows);
        }

        el("finance-paid-pagination-summary").textContent =
            `Página ${state.paidPage} de ${Math.max(1, Math.ceil(rows.length / state.pageSize))} (${rows.length} resultados)`;
    }

    function fmtDate(iso) {
        if (!iso) return "—";
        try { return new Date(iso).toLocaleDateString("pt-BR"); }
        catch (_) { return "—"; }
    }

    function renderPaidByClient(tbody, rows) {
        rows.forEach((r) => {
            const tr = document.createElement("tr");
            tr.className = "client-row";
            tr.innerHTML = `
                <td>${escapeHtml(r.nome)}</td>
                <td>***${escapeHtml(r.celular_last4 || "")}</td>
                <td>${fmtMoney(r.total)}</td>
                <td>${r.count}</td>
                <td>${fmtDate(r.last_paid_at)} <span class="aging-badge bucket-${(r.bucket||"0-7").replace("+","plus")}">${r.newest_age_days}d</span></td>
                <td style="text-align:right;">
                    <button class="btn-expand" data-paid-cliente="${r.cliente_id}"><i class="fas fa-chevron-right"></i></button>
                </td>
            `;
            tbody.appendChild(tr);

            const expandTr = document.createElement("tr");
            expandTr.className = "client-expand";
            expandTr.dataset.paidCliente = r.cliente_id;
            expandTr.style.display = "none";
            expandTr.innerHTML = `
                <td colspan="6">
                    <table class="charges-mini">
                        <thead>
                            <tr><th>Pacote</th><th>Valor</th><th>Pago em</th><th></th></tr>
                        </thead>
                        <tbody>
                            ${r.charges.map((c) => `
                                <tr>
                                    <td>${escapeHtml(c.enquete_titulo) || c.pacote_id}</td>
                                    <td>${fmtMoney(c.valor)}</td>
                                    <td>${fmtDate(c.paid_at)} <span class="muted">(${c.age_days}d)</span></td>
                                    <td>
                                        <button class="btn-history" data-pag="${c.pagamento_id}" title="Histórico"><i class="fas fa-scroll"></i></button>
                                    </td>
                                </tr>
                            `).join("")}
                        </tbody>
                    </table>
                </td>
            `;
            tbody.appendChild(expandTr);
        });
    }

    function renderPaidByCharge(tbody, rows) {
        const charges = [];
        rows.forEach((r) => r.charges.forEach((c) =>
            charges.push({ ...c, nome: r.nome, celular_last4: r.celular_last4 })
        ));
        charges.forEach((c) => {
            const tr = document.createElement("tr");
            tr.innerHTML = `
                <td>${escapeHtml(c.nome)}</td>
                <td>${escapeHtml(c.enquete_titulo) || c.pacote_id}</td>
                <td>${fmtMoney(c.valor)}</td>
                <td>${fmtDate(c.paid_at)}</td>
                <td>${c.age_days}d</td>
                <td style="text-align:right;">
                    <button class="btn-history" data-pag="${c.pagamento_id}"><i class="fas fa-scroll"></i></button>
                </td>
            `;
            tbody.appendChild(tr);
        });
    }

    function updatePaidHead() {
        const thead = el("finance-paid-thead");
        if (state.paidMode === "by-charge") {
            thead.innerHTML = `<tr><th>Cliente</th><th>Pacote</th><th>Valor</th><th>Pago em</th><th>Idade</th><th></th></tr>`;
        } else {
            thead.innerHTML = `<tr><th>Cliente</th><th>Celular</th><th>Total pago</th><th>Cobranças</th><th>Último pagamento</th><th></th></tr>`;
        }
    }

    // Handlers Pagos (mode toggle, filtros, busca, paginação, expand)
    document.querySelectorAll('#finance-view-paid .toggle-btn').forEach((btn) => {
        btn.addEventListener("click", () => {
            document.querySelectorAll('#finance-view-paid .toggle-btn')
                .forEach((b) => b.classList.toggle("active", b === btn));
            state.paidMode = btn.dataset.paidMode;
            state.paidPage = 1;
            updatePaidHead();
            renderPaid();
        });
    });

    document.querySelectorAll('#finance-view-paid .filter-btn').forEach((btn) => {
        btn.addEventListener("click", () => {
            document.querySelectorAll('#finance-view-paid .filter-btn')
                .forEach((b) => b.classList.toggle("active", b === btn));
            state.paidFilter = btn.dataset.paidFilter;
            state.paidPage = 1;
            renderPaid();
        });
    });

    const paidSearch = el("finance-paid-search");
    if (paidSearch) {
        paidSearch.addEventListener("input", () => {
            state.paidSearch = paidSearch.value.trim();
            state.paidPage = 1;
            renderPaid();
        });
    }

    el("finance-paid-page-prev")?.addEventListener("click", () => {
        if (state.paidPage > 1) { state.paidPage -= 1; renderPaid(); }
    });
    el("finance-paid-page-next")?.addEventListener("click", () => {
        state.paidPage += 1; renderPaid();
    });

    // Expand handler isolado pra Pagos (key data-paid-cliente)
    document.addEventListener("click", (ev) => {
        const btn = ev.target.closest(".btn-expand[data-paid-cliente]");
        if (!btn) return;
        const id = btn.dataset.paidCliente;
        const row = document.querySelector(`.client-expand[data-paid-cliente="${id}"]`);
        if (row) {
            const open = row.style.display !== "none";
            row.style.display = open ? "none" : "table-row";
            btn.querySelector("i").className =
                "fas " + (open ? "fa-chevron-right" : "fa-chevron-down");
        }
    });

    // Troca de view (chamada pelo dropdown na sidebar — finance-toggle.js)
    window.financeSetView = function (view) {
        if (view !== "receivable" && view !== "paid") return;
        if (state.tab === view) return;
        state.tab = view;
        document.querySelectorAll(".finance-view")
            .forEach((v) => v.classList.toggle("active", v.id === `finance-view-${view}`));
        refreshAll();
    };

    // ---- Refresh ----
    async function refreshAll() {
        if (state.tab === "paid") {
            await Promise.all([loadPaidSummary(), loadPaid()]);
        } else {
            await Promise.all([loadAgingSummary(), loadReceivables()]);
        }
    }

    // ---- Trigger on nav ----
    const navItem = document.querySelector('.nav-item[data-target="finance"]');
    if (navItem) {
        navItem.addEventListener("click", () => {
            setTimeout(refreshAll, 50);
        });
    }

    if (document.getElementById("section-finance")?.style.display !== "none") {
        refreshAll();
    }

    window.financeRefresh = refreshAll;
})();
