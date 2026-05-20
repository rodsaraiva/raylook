/* Aba Enquetes — granularidade por enquete (pacotes + participantes).
 * Filtro de data vem do window.dashboardFilter (filtro global da topbar).
 */
(function () {
    "use strict";

    const state = {
        items: [],
        total: 0,
        page: 1,
        pageSize: 50,
        search: "",
        selectedId: null,
        detail: null,
        open: false,
    };

    function el(id) { return document.getElementById(id); }

    function escape(s) {
        return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
            ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
    }

    function fmtDate(iso) {
        if (!iso) return "—";
        try { return new Date(iso).toLocaleDateString("pt-BR"); }
        catch (_) { return "—"; }
    }

    function fmtDateTime(iso) {
        if (!iso) return "—";
        try {
            const d = new Date(iso);
            return d.toLocaleDateString("pt-BR") + " " + d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
        } catch (_) { return "—"; }
    }

    const STATE_LABELS = {
        aberto: "Aberto", fechado: "Fechado", confirmado: "Confirmado",
        pago: "Pago", pendente: "Pendente", separado: "Separado",
        enviado: "Enviado", cancelled: "Cancelado",
    };

    function statePill(stateName) {
        if (!stateName) return "";
        const label = STATE_LABELS[stateName] || stateName;
        return `<span class="pkg-state" data-state="${escape(stateName)}">${escape(label)}</span>`;
    }

    function statusBadge(status) {
        if (!status) return "—";
        const lbl = status === "open" ? "Aberta" : status === "closed" ? "Fechada"
            : status === "cancelled" ? "Cancelada" : status;
        return `<span class="enq-status-badge" data-status="${escape(status)}">${escape(lbl)}</span>`;
    }

    // ---- toggle / view ----
    function openEnquetes() {
        state.open = true;
        window._enquetesOpen = true;
        document.getElementById("packages-area")?.classList.add("retracted");
        document.getElementById("section-enquetes")?.classList.add("active");
        document.getElementById("section-finance")?.classList.remove("active");
        document.getElementById("fin-group")?.classList.remove("open");
        document.getElementById("section-clientes")?.classList.remove("active");
        document.getElementById("clientes-group")?.classList.remove("open");
        window._financeOpen = false;
        window._clientesOpen = false;
        window._railCollapseGroups?.();
        document.getElementById("enquetes-group")?.classList.add("open");
        refresh();
    }

    function closeEnquetes() {
        state.open = false;
        window._enquetesOpen = false;
        document.getElementById("section-enquetes")?.classList.remove("active");
        document.getElementById("enquetes-group")?.classList.remove("open");
        if (!window._financeOpen && !window._clientesOpen) {
            document.getElementById("packages-area")?.classList.remove("retracted");
        }
    }
    window._enquetesClose = closeEnquetes;

    // ---- fetch ----
    function globalFilterQS() {
        const f = window.dashboardFilter || {};
        const p = new URLSearchParams();
        if (f.since) p.set("since", f.since);
        if (f.until) p.set("until", f.until);
        return p;
    }

    async function loadList() {
        const p = globalFilterQS();
        if (state.search) p.set("q", state.search);
        p.set("page", String(state.page));
        p.set("page_size", String(state.pageSize));
        try {
            const r = await fetch("/api/dashboard/enquetes?" + p.toString(),
                { credentials: "same-origin" });
            if (!r.ok) throw new Error(`HTTP ${r.status}`);
            const data = await r.json();
            state.items = data.items || [];
            state.total = data.total || 0;
            renderList();
            renderKpis();
            el("enquetes-total-count").textContent = String(state.total);
            el("enquetes-count-list").textContent = String(state.total);
        } catch (e) {
            el("enquetes-table-body").innerHTML =
                `<tr><td colspan="6" style="text-align:center;padding:24px;color:#f87171;">Erro: ${escape(e.message)}</td></tr>`;
        }
    }

    async function loadDetail(enqId) {
        state.selectedId = enqId;
        const detail = el("enquetes-detail");
        detail.innerHTML = `<div class="empty-state">Carregando…</div>`;
        try {
            const r = await fetch(`/api/dashboard/enquetes/${encodeURIComponent(enqId)}`,
                { credentials: "same-origin" });
            if (!r.ok) throw new Error(`HTTP ${r.status}`);
            state.detail = await r.json();
            renderDetail();
        } catch (e) {
            detail.innerHTML = `<div class="empty-state" style="color:#f87171;">Erro: ${escape(e.message)}</div>`;
        }
        document.querySelectorAll("#enquetes-table-body tr.enq-row").forEach((tr) =>
            tr.classList.toggle("active", tr.dataset.enqId === enqId));
    }

    async function refresh() {
        await loadList();
    }
    window.enquetesRefresh = refresh;

    // ---- render ----
    function renderKpis() {
        let openP = 0, closedP = 0, cancelledP = 0;
        state.items.forEach((e) => {
            const s = e.pacotes_by_status || {};
            openP += s.open || 0;
            closedP += (s.closed || 0) + (s.approved || 0);
            cancelledP += s.cancelled || 0;
        });
        el("enquetes-kpi-total").textContent = String(state.total);
        el("enquetes-kpi-fechados").textContent = String(closedP);
        el("enquetes-kpi-abertos").textContent = String(openP);
        el("enquetes-kpi-cancelled").textContent = String(cancelledP);
        const f = window.dashboardFilter || {};
        if (f.since && f.until) {
            el("enquetes-kpi-meta").textContent = f.since === f.until
                ? `criadas em ${f.since}`
                : `criadas de ${f.since} a ${f.until}`;
        } else {
            el("enquetes-kpi-meta").textContent = "sem filtro de data";
        }
    }

    function renderList() {
        const tbody = el("enquetes-table-body");
        if (!tbody) return;
        const totalPages = Math.max(1, Math.ceil(state.total / state.pageSize));
        el("enquetes-pagination-summary").textContent =
            `Página ${state.page} de ${totalPages} (${state.total} resultados)`;
        el("enquetes-page-prev").disabled = state.page <= 1;
        el("enquetes-page-next").disabled = state.page >= totalPages;

        if (!state.items.length) {
            tbody.innerHTML = `<tr><td colspan="6" style="text-align:center;padding:24px;color:var(--text-muted);">Nenhuma enquete no período</td></tr>`;
            el("enquetes-meta").textContent = "0 enquetes";
            return;
        }
        tbody.innerHTML = state.items.map((e) => {
            const fech = e.pacotes_fechados || 0;
            const total = e.pacotes_total || 0;
            const title = e.titulo || "—";
            return `
                <tr class="enq-row ${state.selectedId === e.id ? "active" : ""}" data-enq-id="${escape(e.id)}">
                    <td style="white-space:nowrap;">${escape(fmtDate(e.created_at))}</td>
                    <td title="${escape(title)}">${escape(title.length > 60 ? title.slice(0, 60) + "…" : title)}</td>
                    <td>${escape(e.fornecedor || "—")}</td>
                    <td class="pkg-num">${total}</td>
                    <td class="pkg-num">${fech}</td>
                    <td>${statusBadge(e.status)}</td>
                </tr>`;
        }).join("");
        el("enquetes-meta").textContent = `${state.total} enquete${state.total === 1 ? "" : "s"}`;
        tbody.querySelectorAll("tr.enq-row").forEach((tr) =>
            tr.addEventListener("click", () => loadDetail(tr.dataset.enqId)));
    }

    function renderDetail() {
        const detail = el("enquetes-detail");
        const d = state.detail;
        if (!d) {
            detail.innerHTML = `<div class="empty-state">Selecione uma enquete</div>`;
            return;
        }
        const byStatus = d.pacotes_by_status || {};
        const fechados = (byStatus.closed || 0) + (byStatus.approved || 0);
        const total = d.pacotes_total || 0;
        const cancelClass = (byStatus.cancelled || 0) > 0 ? "warn" : "";
        const fechClass = fechados > 0 ? "ok" : "";

        const pacotesHtml = (d.pacotes || []).map((pk) => {
            const totalQty = pk.total_qty || 0;
            const capacidade = pk.capacidade_total || 24;
            const friendly = pk.friendly_id || (pk.sequence_no ? "#" + pk.sequence_no : "#" + (pk.id || "").slice(0, 6));
            const clientesHtml = (pk.clientes || []).map((c) => `
                <div class="enq-cliente">
                    <div class="cli-info">
                        <span class="name">${escape(c.nome || "?")}</span>
                        <span class="phone">${escape(c.celular || "")}</span>
                    </div>
                    <div class="cli-right">
                        <span class="qty">${c.qty}p</span>
                        <span class="state" data-state="${escape(c.state || "")}">${escape(STATE_LABELS[c.state] || c.state || "—")}</span>
                    </div>
                </div>
            `).join("") || `<div style="font-size:11px;color:var(--text-muted);font-style:italic;padding-top:4px;">Sem participantes registrados</div>`;
            return `
                <div class="enq-pacote">
                    <div class="enq-pacote-head">
                        <div>
                            <span class="pkg-id">${escape(friendly)}</span>
                            <span class="pkg-fill">${totalQty}/${capacidade} peças</span>
                        </div>
                        ${statePill(pk.state)}
                    </div>
                    ${clientesHtml}
                </div>`;
        }).join("") || `<div style="font-size:12px;color:var(--text-muted);font-style:italic;text-align:center;padding:16px 0;">Nenhum pacote nessa enquete ainda</div>`;

        detail.innerHTML = `
            <div class="enq-detail-head">
                <h3>${escape(d.titulo || "Enquete")}</h3>
                <div class="enq-meta">
                    <span>${escape(fmtDateTime(d.created_at))}</span>
                    ${statusBadge(d.status)}
                    ${d.fornecedor ? `<span>· ${escape(d.fornecedor)}</span>` : ""}
                    ${d.produto?.nome ? `<span>· ${escape(d.produto.nome)}</span>` : ""}
                </div>
            </div>
            <div class="enq-stats">
                <div class="enq-stat"><div class="lbl">Pacotes</div><div class="val">${total}</div></div>
                <div class="enq-stat ${fechClass}"><div class="lbl">Fechados</div><div class="val">${fechados}</div></div>
                <div class="enq-stat"><div class="lbl">Abertos</div><div class="val">${byStatus.open || 0}</div></div>
                <div class="enq-stat ${cancelClass}"><div class="lbl">Cancelados</div><div class="val">${byStatus.cancelled || 0}</div></div>
            </div>
            <div class="enq-pacotes-list">${pacotesHtml}</div>
        `;
    }

    // ---- handlers ----
    document.addEventListener("DOMContentLoaded", function () {
        el("enquetes-group-header")?.addEventListener("click", () => {
            if (state.open) closeEnquetes();
            else openEnquetes();
        });
        document.querySelectorAll('#enquetes-group .rail-step[data-enquetes-view]').forEach((step) => {
            step.addEventListener("click", (e) => {
                e.stopPropagation();
                openEnquetes();
            });
        });

        let searchTimer;
        el("enquetes-search")?.addEventListener("input", (e) => {
            clearTimeout(searchTimer);
            const v = e.target.value.trim();
            searchTimer = setTimeout(() => {
                state.search = v;
                state.page = 1;
                loadList();
            }, 200);
        });

        el("enquetes-page-prev")?.addEventListener("click", () => {
            if (state.page > 1) { state.page -= 1; loadList(); }
        });
        el("enquetes-page-next")?.addEventListener("click", () => {
            const totalPages = Math.max(1, Math.ceil(state.total / state.pageSize));
            if (state.page < totalPages) { state.page += 1; loadList(); }
        });
    });
})();
