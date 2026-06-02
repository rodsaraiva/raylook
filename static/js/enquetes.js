/* Aba Enquetes — granularidade por enquete (pacotes + participantes).
 * Filtro de data vem do window.dashboardFilter (filtro global da topbar).
 */
(function () {
    "use strict";

    const state = {
        items: [],
        total: 0,
        page: 1,
        pageSize: 10,
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

    function fmtMoney(v) {
        const n = Number(v || 0);
        try {
            return n.toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
        } catch (_) { return "R$ " + n.toFixed(2); }
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
            el("enquetes-total-count").textContent = String(state.total);
            el("enquetes-count-list").textContent = String(state.total);
        } catch (e) {
            el("enquetes-table-body").innerHTML =
                `<tr><td colspan="7" style="text-align:center;padding:24px;color:#f87171;">Erro: ${escape(e.message)}</td></tr>`;
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
        document.querySelectorAll("#enquetes-table-body .enq-card").forEach((card) =>
            card.classList.toggle("active", card.dataset.enqId === enqId));
    }

    async function refresh() {
        await loadList();
    }
    window.enquetesRefresh = refresh;

    // ---- render ----
    function thumbCell(url, size) {
        const klass = size === "hero" ? "enq-hero-img" : "enq-thumb";
        const phClass = size === "hero" ? "enq-hero-img-placeholder" : "enq-thumb-placeholder";
        if (!url) return `<div class="${phClass}">📷</div>`;
        return `<img class="${klass}" loading="lazy" src="${escape(url)}" alt=""
            onerror="this.outerHTML='<div class=\\'${phClass}\\'>📷</div>'">`;
    }

    function renderList() {
        const container = el("enquetes-table-body");
        if (!container) return;
        const totalPages = Math.max(1, Math.ceil(state.total / state.pageSize));
        el("enquetes-pagination-summary").textContent =
            `Página ${state.page} de ${totalPages} (${state.total} resultados)`;
        el("enquetes-page-prev").disabled = state.page <= 1;
        el("enquetes-page-next").disabled = state.page >= totalPages;

        if (!state.items.length) {
            container.innerHTML = `<div class="enq-empty-list">Nenhuma enquete no período</div>`;
            el("enquetes-meta").textContent = "0 enquetes";
            return;
        }
        container.innerHTML = state.items.map((e) => {
            const fech = e.pacotes_fechados || 0;
            const total = e.pacotes_total || 0;
            const prodNome = (e.produto?.nome || "").trim() || (e.titulo || "Sem nome");
            const valor = Number(e.produto?.valor_unitario || 0);
            const valorStr = valor > 0 ? fmtMoney(valor) : "";
            const titleAttr = e.titulo ? ` title="${escape(e.titulo)}"` : "";
            const fechClass = fech > 0 ? "ok" : "";
            return `
                <button type="button" class="enq-card ${state.selectedId === e.id ? "active" : ""}"
                        data-enq-id="${escape(e.id)}"${titleAttr}>
                    ${thumbCell(e.image)}
                    <div class="enq-card-body">
                        <div class="enq-card-line1">
                            <span class="enq-prod-name">${escape(prodNome)}</span>
                            ${valorStr ? `<span class="enq-prod-price">${escape(valorStr)}</span>` : ""}
                        </div>
                        <div class="enq-card-line2">
                            <span>${escape(fmtDate(e.created_at))}</span>
                            ${e.fornecedor ? `<span class="sep">·</span><span class="fornecedor">${escape(e.fornecedor)}</span>` : ""}
                            <span class="sep">·</span>
                            ${statusBadge(e.status)}
                        </div>
                    </div>
                    <div class="enq-card-stats">
                        <div class="enq-mini-stat ${fechClass}"><div class="num">${fech}</div><div class="lbl">fechados</div></div>
                    </div>
                </button>`;
        }).join("");
        el("enquetes-meta").textContent = `${state.total} enquete${state.total === 1 ? "" : "s"}`;
        container.querySelectorAll(".enq-card").forEach((card) =>
            card.addEventListener("click", () => loadDetail(card.dataset.enqId)));
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
            const pct = Math.min(100, Math.round((totalQty / Math.max(1, capacidade)) * 100));
            const fullClass = totalQty >= capacidade ? "full" : "";
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
                    <div class="enq-pacote-progress ${fullClass}"><div class="fill" style="width:${pct}%"></div></div>
                    ${clientesHtml}
                </div>`;
        }).join("") || `<div style="font-size:12px;color:var(--text-muted);font-style:italic;text-align:center;padding:16px 0;">Nenhum pacote nessa enquete ainda</div>`;

        const prodNome = (d.produto?.nome || "").trim() || d.titulo || "Enquete";
        const valor = Number(d.produto?.valor_unitario || 0);
        const valorStr = valor > 0 ? fmtMoney(valor) : "";
        detail.innerHTML = `
            <div class="enq-detail-head">
                <div class="enq-hero">
                    ${thumbCell(d.image, "hero")}
                    <div class="enq-hero-body">
                        <h3 title="${escape(d.titulo || "")}">${escape(prodNome)}</h3>
                        <div class="enq-meta">
                            <span>${escape(fmtDateTime(d.created_at))}</span>
                            ${statusBadge(d.status)}
                            ${d.fornecedor ? `<span>· ${escape(d.fornecedor)}</span>` : ""}
                        </div>
                        ${valorStr ? `<div class="enq-hero-price">${escape(valorStr)} <span style="font-size:11px;color:var(--text-muted);font-weight:400;">por peça</span></div>` : ""}
                    </div>
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
