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
        // injeta estilos do modal de voto manual (idempotente)
        if (!document.getElementById("enq-voto-styles")) {
            const s = document.createElement("style");
            s.id = "enq-voto-styles";
            s.textContent = `
                .btn-add-voto{width:100%;padding:9px 16px;background:var(--surface,#313244);
                  border:1px dashed var(--border,#585b70);border-radius:8px;
                  color:var(--accent,#89b4fa);font-size:13px;font-weight:600;cursor:pointer;
                  display:flex;align-items:center;justify-content:center;gap:6px;margin-bottom:12px;}
                .btn-add-voto:hover{border-color:var(--accent,#89b4fa);opacity:.85;}
                #enq-voto-modal-overlay{position:fixed;inset:0;background:rgba(0,0,0,.55);
                  display:flex;align-items:center;justify-content:center;z-index:9999;}
                .enq-voto-modal{background:var(--bg-card,#1e1e2e);border:1px solid var(--border,#313244);
                  border-radius:12px;padding:20px;width:340px;max-width:90vw;}
                .enq-voto-modal h4{margin:0 0 14px;font-size:15px;color:var(--text,#cdd6f4);}
                .enq-voto-lbl{font-size:11px;color:var(--text-muted,#6c7086);
                  text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px;}
                .enq-voto-input{width:100%;padding:8px 10px;background:var(--surface,#313244);
                  border:1px solid var(--border,#45475a);border-radius:6px;
                  color:var(--text,#cdd6f4);font-size:13px;box-sizing:border-box;outline:none;margin-bottom:10px;}
                .enq-voto-input:focus{border-color:var(--accent,#89b4fa);}
                .enq-voto-found{background:var(--surface,#313244);border-radius:6px;
                  padding:8px 10px;font-size:12px;color:#a6e3a1;margin-bottom:10px;display:flex;gap:6px;}
                .enq-voto-new{background:rgba(249,226,175,.05);border:1px dashed #f9e2af;
                  border-radius:6px;padding:10px;margin-bottom:10px;}
                .enq-voto-new .warn{font-size:11px;color:#f9e2af;margin-bottom:8px;}
                .enq-voto-qty-chips{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:14px;}
                .enq-voto-qty-chip{padding:4px 10px;border-radius:6px;font-size:12px;cursor:pointer;
                  background:var(--surface,#313244);border:1px solid var(--border,#45475a);
                  color:var(--text,#cdd6f4);}
                .enq-voto-qty-chip.selected{background:var(--accent,#89b4fa);
                  border-color:var(--accent,#89b4fa);color:#1e1e2e;font-weight:700;}
                .enq-voto-footer{display:flex;gap:8px;justify-content:flex-end;}
                .enq-voto-cancel{padding:7px 14px;background:transparent;
                  border:1px solid var(--border,#45475a);border-radius:6px;
                  color:var(--text-muted,#6c7086);font-size:12px;cursor:pointer;}
                .enq-voto-confirm{padding:7px 14px;background:var(--accent,#89b4fa);
                  border:none;border-radius:6px;color:#1e1e2e;font-size:12px;
                  font-weight:700;cursor:pointer;}
                .enq-voto-confirm:disabled{opacity:.4;cursor:default;}
                .enq-voto-error{font-size:12px;color:#f87171;margin-bottom:10px;}
            `;
            document.head.appendChild(s);
        }
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
        window._bernardoClose?.();
        window._railCollapseGroups?.();
        document.getElementById("enquetes-group")?.classList.add("open");
        refresh();
    }

    function closeEnquetes() {
        state.open = false;
        window._enquetesOpen = false;
        document.getElementById("section-enquetes")?.classList.remove("active");
        document.getElementById("enquetes-group")?.classList.remove("open");
        if (!window._financeOpen && !window._clientesOpen && !window._bernardoOpen) {
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
            <button type="button" class="btn-add-voto" id="enq-add-voto-btn">＋ Adicionar Voto</button>
            <div class="enq-pacotes-list">${pacotesHtml}</div>
        `;
        detail.querySelector("#enq-add-voto-btn")?.addEventListener("click", () => openVotoModal());
    }

    // ---- modal voto manual ----

    function openVotoModal() {
        if (document.getElementById("enq-voto-modal-overlay")) return;

        // Fix 1: Local HTML escape helper
        function escHtml(s) {
            return String(s == null ? "" : s)
                .replace(/&/g, "&amp;")
                .replace(/</g, "&lt;")
                .replace(/>/g, "&gt;")
                .replace(/"/g, "&quot;");
        }

        // Fix 3: Snapshot enqueteId at modal open time
        const enqueteId = state.selectedId;

        let selectedCliente = null;
        let selectedQty = null;
        let searchTimer = null;

        const overlay = document.createElement("div");
        overlay.id = "enq-voto-modal-overlay";
        overlay.innerHTML = `
            <div class="enq-voto-modal">
                <h4>Adicionar Voto</h4>
                <div class="enq-voto-lbl">Buscar cliente (nome ou telefone)</div>
                <input class="enq-voto-input" id="enq-voto-busca" placeholder="Nome ou celular..." autocomplete="off">
                <div id="enq-voto-busca-result"></div>
                <div class="enq-voto-lbl">Quantidade de peças</div>
                <div class="enq-voto-qty-chips" id="enq-voto-qty-chips">
                    ${[3,4,6,8,9,12,16,20,24].map(q =>
                        `<button type="button" class="enq-voto-qty-chip" data-qty="${q}">${q}</button>`
                    ).join("")}
                </div>
                <div id="enq-voto-error" class="enq-voto-error" style="display:none"></div>
                <div class="enq-voto-footer">
                    <button type="button" class="enq-voto-cancel" id="enq-voto-cancel">Cancelar</button>
                    <button type="button" class="enq-voto-confirm" id="enq-voto-confirm" disabled>Confirmar</button>
                </div>
            </div>`;
        document.body.appendChild(overlay);

        const buscaInput = overlay.querySelector("#enq-voto-busca");
        const resultDiv = overlay.querySelector("#enq-voto-busca-result");
        const confirmBtn = overlay.querySelector("#enq-voto-confirm");
        const errorDiv = overlay.querySelector("#enq-voto-error");

        function updateConfirm() {
            const newNomeEl = overlay.querySelector("#enq-voto-new-nome");
            const newCelularEl = overlay.querySelector("#enq-voto-new-celular");
            const newNome = newNomeEl ? newNomeEl.value.trim() : null;
            const newCelular = newCelularEl ? newCelularEl.value.trim() : null;
            const canSubmit = selectedQty !== null && (selectedCliente !== null || (newNome && newCelular));
            confirmBtn.disabled = !canSubmit;
            confirmBtn.textContent = (selectedCliente || !newNomeEl) ? "Confirmar" : "Criar e Votar";
        }

        overlay.querySelectorAll(".enq-voto-qty-chip").forEach(chip => {
            chip.addEventListener("click", () => {
                overlay.querySelectorAll(".enq-voto-qty-chip").forEach(c => c.classList.remove("selected"));
                chip.classList.add("selected");
                selectedQty = parseInt(chip.dataset.qty, 10);
                updateConfirm();
            });
        });

        buscaInput.addEventListener("input", () => {
            clearTimeout(searchTimer);
            selectedCliente = null;
            updateConfirm();
            const q = buscaInput.value.trim();
            if (!q) {
                resultDiv.innerHTML = "";
                return;
            }
            searchTimer = setTimeout(async () => {
                try {
                    const r = await fetch(`/api/dashboard/clientes?q=${encodeURIComponent(q)}`, { credentials: "same-origin" });
                    if (!r.ok) throw new Error(`Busca falhou: ${r.status}`);
                    const data = await r.json();
                    const found = data[0] || null;
                    if (found) {
                        selectedCliente = found;
                        resultDiv.innerHTML = `<div class="enq-voto-found">✓ ${escHtml(found.nome || "")} · ${escHtml(found.celular || "")}</div>`;
                    } else {
                        selectedCliente = null;
                        resultDiv.innerHTML = `
                            <div class="enq-voto-new">
                                <div class="warn">⚠ Cliente não encontrado. Preencha para cadastrar:</div>
                                <div class="enq-voto-lbl">Nome completo</div>
                                <input class="enq-voto-input" id="enq-voto-new-nome" value="${escHtml(q)}">
                                <div class="enq-voto-lbl">Celular</div>
                                <input class="enq-voto-input" id="enq-voto-new-celular" placeholder="Ex: 62999991234">
                            </div>`;
                        overlay.querySelector("#enq-voto-new-nome")?.addEventListener("input", updateConfirm);
                        overlay.querySelector("#enq-voto-new-celular")?.addEventListener("input", updateConfirm);
                    }
                } catch (_) {
                    resultDiv.innerHTML = "";
                    selectedCliente = null;
                }
                updateConfirm();
            }, 300);
        });

        confirmBtn.addEventListener("click", async () => {
            confirmBtn.disabled = true;
            errorDiv.style.display = "none";
            const q = buscaInput.value.trim();
            const body = { busca: q, qty: selectedQty };
            if (!selectedCliente) {
                body.nome = (overlay.querySelector("#enq-voto-new-nome")?.value || "").trim();
                body.celular = (overlay.querySelector("#enq-voto-new-celular")?.value || "").trim();
            }
            try {
                const r = await fetch(`/api/dashboard/enquetes/${encodeURIComponent(enqueteId)}/votos`, {
                    method: "POST",
                    credentials: "same-origin",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(body),
                });
                const data = await r.json();
                if (!r.ok) {
                    errorDiv.textContent = data.detail || `Erro ${r.status}`;
                    errorDiv.style.display = "";
                    confirmBtn.disabled = false;
                    return;
                }
                if (data.found === false) {
                    errorDiv.textContent = "Cliente não encontrado. Preencha nome e celular.";
                    errorDiv.style.display = "";
                    confirmBtn.disabled = false;
                    return;
                }
                _closeVotoModal();
                loadDetail(state.selectedId);
            } catch (e) {
                errorDiv.textContent = `Erro: ${e.message}`;
                errorDiv.style.display = "";
                confirmBtn.disabled = false;
            }
        });

        overlay.querySelector("#enq-voto-cancel").addEventListener("click", _closeVotoModal);
        overlay.addEventListener("click", (e) => { if (e.target === overlay) _closeVotoModal(); });
        buscaInput.focus();
    }

    function _closeVotoModal() {
        document.getElementById("enq-voto-modal-overlay")?.remove();
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
