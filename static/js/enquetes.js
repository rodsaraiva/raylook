/* Aba Enquetes — granularidade por enquete (pacotes + participantes). */
(function () {
    "use strict";

    const state = {
        items: [],
        total: 0,
        since: "",
        until: "",
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

    function statePill(stateName) {
        if (!stateName) return "";
        const label = ({
            aberto: "Aberto", fechado: "Fechado", confirmado: "Confirmado",
            pago: "Pago", pendente: "Pendente", separado: "Separado",
            enviado: "Enviado", cancelled: "Cancelado",
        })[stateName] || stateName;
        return `<span class="pkg-state" data-state="${escape(stateName)}">${escape(label)}</span>`;
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
    async function loadList() {
        const p = new URLSearchParams();
        if (state.since) p.set("since", state.since);
        if (state.until) p.set("until", state.until);
        if (state.search) p.set("q", state.search);
        const url = "/api/dashboard/enquetes" + (p.toString() ? "?" + p.toString() : "");
        try {
            const r = await fetch(url, { credentials: "same-origin" });
            if (!r.ok) throw new Error(`HTTP ${r.status}`);
            const data = await r.json();
            state.items = data.items || [];
            state.total = data.total || 0;
            renderList();
            // Atualiza contadores do sidebar.
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
        // Destaca linha ativa.
        document.querySelectorAll("#enquetes-table-body tr.enq-row").forEach((tr) =>
            tr.classList.toggle("active", tr.dataset.enqId === enqId));
    }

    async function refresh() {
        await loadList();
    }

    // ---- render ----
    function renderList() {
        const tbody = el("enquetes-table-body");
        if (!tbody) return;
        if (!state.items.length) {
            tbody.innerHTML = `<tr><td colspan="6" style="text-align:center;padding:24px;color:var(--text-muted);">Nenhuma enquete</td></tr>`;
            el("enquetes-meta").textContent = "0 enquetes";
            return;
        }
        tbody.innerHTML = state.items.map((e) => {
            const fech = e.pacotes_fechados || 0;
            const total = e.pacotes_total || 0;
            return `
                <tr class="enq-row ${state.selectedId === e.id ? "active" : ""}" data-enq-id="${escape(e.id)}">
                    <td>${escape(fmtDate(e.created_at))}</td>
                    <td title="${escape(e.titulo || "")}">${escape((e.titulo || "—").slice(0, 60))}</td>
                    <td>${escape(e.fornecedor || "—")}</td>
                    <td>${total}</td>
                    <td>${fech}</td>
                    <td>${escape(e.status || "—")}</td>
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
        const pacotesHtml = (d.pacotes || []).map((pk) => {
            const totalQty = pk.total_qty || 0;
            const capacidade = pk.capacidade_total || 24;
            const friendly = pk.friendly_id || pk.sequence_no || pk.id.slice(0, 8);
            const clientesHtml = (pk.clientes || []).map((c) => `
                <div class="enq-cliente">
                    <div>
                        <span class="name">${escape(c.nome || "?")}</span>
                        <span class="phone">${escape(c.celular || "")}</span>
                    </div>
                    <div>
                        <span class="qty">${c.qty}p</span>
                        <span class="state" data-state="${escape(c.state || "")}">${escape(c.state || "—")}</span>
                    </div>
                </div>
            `).join("") || `<div style="font-size:11px;color:var(--text-muted);font-style:italic;">Sem participantes</div>`;
            return `
                <div class="enq-pacote">
                    <div class="enq-pacote-head">
                        <span class="pkg-id">#${escape(friendly)} · ${totalQty}/${capacidade}p</span>
                        ${statePill(pk.state)}
                    </div>
                    ${clientesHtml}
                </div>`;
        }).join("") || `<div style="font-size:12px;color:var(--text-muted);font-style:italic;">Nenhum pacote ainda</div>`;

        detail.innerHTML = `
            <h3>${escape(d.titulo || "Enquete")}</h3>
            <div class="enq-meta">
                ${escape(fmtDateTime(d.created_at))}
                ${d.fornecedor ? " · " + escape(d.fornecedor) : ""}
                ${d.produto?.nome ? " · " + escape(d.produto.nome) : ""}
            </div>
            <div class="enq-stats">
                <div class="enq-stat"><div class="lbl">Pacotes</div><div class="val">${d.pacotes_total || 0}</div></div>
                <div class="enq-stat"><div class="lbl">Fechados</div><div class="val">${d.pacotes_fechados || 0}</div></div>
                <div class="enq-stat"><div class="lbl">Abertos</div><div class="val">${byStatus.open || 0}</div></div>
                <div class="enq-stat"><div class="lbl">Cancelados</div><div class="val">${byStatus.cancelled || 0}</div></div>
            </div>
            ${pacotesHtml}
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
                loadList();
            }, 200);
        });

        const onDateChange = () => {
            state.since = el("enquetes-since")?.value || "";
            state.until = el("enquetes-until")?.value || "";
            loadList();
        };
        el("enquetes-since")?.addEventListener("change", onDateChange);
        el("enquetes-until")?.addEventListener("change", onDateChange);
        el("enquetes-clear")?.addEventListener("click", () => {
            if (el("enquetes-since")) el("enquetes-since").value = "";
            if (el("enquetes-until")) el("enquetes-until").value = "";
            state.since = ""; state.until = "";
            loadList();
        });
    });
})();
