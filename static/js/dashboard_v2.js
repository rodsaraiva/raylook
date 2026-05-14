// Dashboard V2 — rail vertical + lista/detalhe.
// Usa /api/dashboard/packages e helpers de static/dashboard/lib.js.
(async () => {
    const L = window.RaylookDashboard;
    let data = null;
    let activeState = null;
    let selectedId = null;
    let search = "";
    let filter = { preset: "all", since: null, until: null };
    let listPage = 1;
    const LIST_PAGE_SIZE = 20;

    const DESCS = {
        aberto: "Formando", fechado: "Aguardando gerente",
        confirmado: "PIX em aberto", pago: "Validar pagamento",
        pendente: "Pronto pra separar",
        separado: "Pronto pra despachar", enviado: "Finalizado",
    };

    function greeting() {
        const h = new Date().getHours();
        if (h < 12) return "Bom dia";
        if (h < 18) return "Boa tarde";
        return "Boa noite";
    }
    document.getElementById("greeting").textContent = `${greeting()}, Raylook`;

    async function load() {
        try {
            data = await L.fetchData({ since: filter.since, until: filter.until });
        } catch (err) {
            document.getElementById("list").innerHTML =
                `<div class="empty-state">Erro: ${L.escapeHtml(err.message)}</div>`;
            return;
        }
        if (!activeState) {
            activeState = L.STATES.find(s => (data.packages_by_state[s] || []).length > 0) || "aberto";
        }
        const pkgs = currentItems();
        if (!selectedId || !pkgs.find(p => p.id === selectedId)) {
            selectedId = pkgs[0] ? pkgs[0].id : null;
        }
        render();
    }
    window.RaylookReload = load;

    // ── Filtro de período ──────────────────────────────────────────────────
    function todayBRT() {
        const now = new Date();
        const brt = new Date(now.getTime() - 3 * 3600 * 1000);
        return brt.toISOString().slice(0, 10);
    }
    function isoMinusDays(iso, days) {
        const d = new Date(iso + "T00:00:00Z");
        d.setUTCDate(d.getUTCDate() - days);
        return d.toISOString().slice(0, 10);
    }
    function firstDayOfMonthBRT() {
        return todayBRT().slice(0, 7) + "-01";
    }
    function formatBR(iso) {
        const [y, m, d] = iso.split("-");
        return `${d}/${m}/${y}`;
    }
    function presetRange(preset) {
        const t = todayBRT();
        switch (preset) {
            case "today": return { since: t, until: t };
            case "yesterday": { const y = isoMinusDays(t, 1); return { since: y, until: y }; }
            case "7d": return { since: isoMinusDays(t, 6), until: t };
            case "month": return { since: firstDayOfMonthBRT(), until: t };
            default: return { since: null, until: null };
        }
    }
    function updateFilterSummary() {
        const el = document.getElementById("filter-summary");
        if (!filter.since && !filter.until) { el.textContent = ""; return; }
        if (filter.since === filter.until) {
            el.textContent = `Mostrando pacotes criados em ${formatBR(filter.since)}`;
        } else {
            el.textContent = `Mostrando pacotes criados entre ${formatBR(filter.since)} e ${formatBR(filter.until)}`;
        }
    }
    function setFilterPreset(preset) {
        filter.preset = preset;
        document.querySelectorAll(".filter-pill").forEach(b =>
            b.classList.toggle("active", b.dataset.filter === preset)
        );
        const custom = document.getElementById("filter-custom");
        if (preset === "custom") {
            custom.classList.add("visible");
            // Default dos inputs: hoje. Não aplica até clicar em "Aplicar".
            const t = todayBRT();
            if (!document.getElementById("filter-since").value)
                document.getElementById("filter-since").value = t;
            if (!document.getElementById("filter-until").value)
                document.getElementById("filter-until").value = t;
            return;  // sem reload até clicar em "Aplicar"
        }
        custom.classList.remove("visible");
        const r = presetRange(preset);
        filter.since = r.since;
        filter.until = r.until;
        selectedId = null;
        activeState = null;
        listPage = 1;
        updateFilterSummary();
        load();
    }
    document.querySelectorAll(".filter-pill").forEach(btn =>
        btn.addEventListener("click", () => setFilterPreset(btn.dataset.filter))
    );
    document.getElementById("filter-apply").addEventListener("click", () => {
        const s = document.getElementById("filter-since").value;
        const u = document.getElementById("filter-until").value;
        if (!s || !u) { alert("Preencha as duas datas."); return; }
        if (s > u) { alert("Data inicial não pode ser maior que a final."); return; }
        filter.since = s;
        filter.until = u;
        selectedId = null;
        activeState = null;
        listPage = 1;
        updateFilterSummary();
        load();
    });

    function currentItems() {
        if (activeState === "cancelled") return data.cancelled || [];
        return data.packages_by_state[activeState] || [];
    }

    // Dropdowns Comercial / Estoque (Financeiro tem header próprio).
    // `labels` sobrescreve L.STATE_LABELS dentro daquele grupo apenas.
    // `extras` injeta items depois dos states do grupo — usado pra colocar
    // "Cancelados" dentro do Comercial só visualmente; o fluxo continua igual.
    const RAIL_GROUPS = [
        {
            id: "comercial",
            label: "Comercial",
            states: ["aberto", "fechado", "confirmado", "pago"],
            extras: ["cancelled"],
        },
        {
            id: "estoque",
            label: "Estoque",
            states: ["pago", "pendente", "separado", "enviado"],
            labels: { pago: "Fila de separação" },
        },
    ];

    const groupOpen = { comercial: true, estoque: false };

    function renderRail() {
        const rail = document.getElementById("rail");
        const groupsHtml = RAIL_GROUPS.map(g => {
            const open = groupOpen[g.id];
            const totalCount = g.states.reduce((sum, s) => sum + (data.counts[s] || 0), 0);
            const stepsHtml = g.states.map((s, i) => {
                const label = g.labels?.[s] ?? L.STATE_LABELS[s];
                return `
                <div class="rail-step ${s === activeState ? "active" : ""}" data-state="${s}">
                    <div class="num">${i + 1}</div>
                    <div>
                        <div class="label">${label}</div>
                        <div class="sub">${DESCS[s]}</div>
                    </div>
                    <div class="count">${data.counts[s] || 0}</div>
                </div>`;
            }).join("");
            const extrasHtml = (g.extras || []).map(s => {
                if (s !== "cancelled") return "";
                return `
                <div class="rail-step rail-cancelled ${activeState === "cancelled" ? "active" : ""}" data-state="cancelled">
                    <div class="num" style="background:rgba(248,113,113,0.15);color:var(--danger);">×</div>
                    <div><div class="label">Cancelados</div><div class="sub">histórico</div></div>
                    <div class="count">${data.counts.cancelled || 0}</div>
                </div>`;
            }).join("");
            return `
                <div class="rail-group ${open ? "open" : ""}" data-group="${g.id}">
                    <div class="rail-group-header" data-toggle="${g.id}">
                        <span class="rail-group-label">${g.label}</span>
                        <span class="rail-group-total">${totalCount}</span>
                        <i class="fas fa-chevron-down rail-group-chevron"></i>
                    </div>
                    <div class="rail-group-body">${stepsHtml}${extrasHtml}</div>
                </div>`;
        }).join("");

        rail.innerHTML = groupsHtml;

        rail.querySelectorAll(".rail-group-header").forEach(h =>
            h.addEventListener("click", () => {
                const id = h.dataset.toggle;
                groupOpen[id] = !groupOpen[id];
                renderRail();
            })
        );
        rail.querySelectorAll(".rail-step").forEach(el =>
            el.addEventListener("click", () => {
                if (window._financeOpen) window.toggleFinanceView();
                activeState = el.dataset.state;
                listPage = 1;
                const pkgs = currentItems();
                selectedId = pkgs[0] ? pkgs[0].id : null;
                render();
            })
        );
    }

    function renderPagination(total) {
        const el = document.getElementById("list-pagination");
        if (!el) return;
        const totalPages = Math.ceil(total / LIST_PAGE_SIZE);
        if (totalPages <= 1) { el.innerHTML = ""; return; }
        el.innerHTML = `
            <button class="list-pg-btn" id="list-pg-prev" ${listPage <= 1 ? "disabled" : ""}>← Anterior</button>
            <span class="list-pg-info">Página ${listPage} de ${totalPages}</span>
            <button class="list-pg-btn" id="list-pg-next" ${listPage >= totalPages ? "disabled" : ""}>Próxima →</button>`;
        el.querySelector("#list-pg-prev").addEventListener("click", () => { listPage--; renderList(); });
        el.querySelector("#list-pg-next").addEventListener("click", () => { listPage++; renderList(); });
    }

    function renderList() {
        const q = search.trim().toLowerCase();
        const all = currentItems();
        const wrap = document.getElementById("list");
        const titleEl = document.getElementById("list-title");
        const summaryEl = document.getElementById("list-summary");

        titleEl.textContent = activeState === "cancelled" ? "Cancelados" : L.STATE_LABELS[activeState];

        // Linha = pacote (todos os estados)
        const filtered = q ? all.filter(p =>
            (p.produto_name || "").toLowerCase().includes(q)
            || (p.clientes || []).some(c => (c.name || "").toLowerCase().includes(q))
            || (p.external_poll_id || "").toLowerCase().includes(q)
        ) : all;
        const totalCount = filtered.length;
        const paged = filtered.slice((listPage - 1) * LIST_PAGE_SIZE, listPage * LIST_PAGE_SIZE);
        const totalPieces = filtered.reduce((a, p) => a + (Math.min(p.total_qty, p.capacidade_total) || 0), 0);
        const totalValue = filtered.reduce((a, p) => a + (p.total_value || 0), 0);
        summaryEl.textContent =
            `${totalCount} pacote${totalCount === 1 ? "" : "s"} · ${totalPieces} peças · ${L.moneyFull(totalValue)}`;
        if (!totalCount) {
            wrap.innerHTML = `<div class="empty-state">Nenhum pacote.</div>`;
            renderPagination(0);
            return;
        }
        wrap.innerHTML = paged.map(p => {
            const meta = L.parsePollTitle(p.produto_name);
            const thumb = p.image
                ? `<img src="${L.escapeHtml(p.image)}" alt="" loading="lazy" onerror="this.replaceWith(Object.assign(document.createElement('span'),{textContent:'${L.productEmoji(meta.item)}'}))">`
                : `<span>${L.productEmoji(meta.item)}</span>`;
            const subBits = [meta.tecido, meta.tamanhos, meta.categoria].filter(Boolean).join(" · ");
            const state = p.state || activeState;
            const action = L.primaryActionFor(state);
            const confirmAttr = action.confirmText ? ` data-confirm="${L.escapeHtml(action.confirmText)}"` : "";
            const actionBtn = (state === "aberto" || !action.action)
                ? ""
                : `<button class="row-action" data-action="${action.action}" data-id="${p.id}"${confirmAttr}>${L.escapeHtml(action.label)}</button>`;
            const backBtn = (state === "aberto" || state === "fechado" || state === "cancelled")
                ? ""
                : `<button class="row-back" data-action="regress" data-id="${p.id}" title="Voltar pra etapa anterior">←</button>`;
            const valueLabel = p.total_value ? L.moneyFull(p.total_value)
                : (meta.valor != null ? `${L.money(meta.valor)} <span class="row-unit">/un</span>` : "—");
            // "Cancelar pacote" aparece em fechado/confirmado (não em aberto, cancelled).
            const cancelBtn = (state === "fechado" || state === "confirmado")
                ? `<button class="row-action danger" data-action="cancel" data-id="${p.id}" title="Cancelar pacote inteiro">Cancelar</button>`
                : "";
            // "Restaurar" aparece apenas em cancelled — volta o pacote pra fechado.
            const restoreBtn = (state === "cancelled")
                ? `<button class="row-action" data-action="restore" data-id="${p.id}" title="Voltar pra fechado">Restaurar</button>`
                : "";
            return `
            <div class="pkg-row ${p.id === selectedId ? "selected" : ""}" data-id="${p.id}">
                <div class="pkg-thumb">${thumb}</div>
                <div class="pkg-row-main">
                    <div class="name">${L.escapeHtml(meta.item)}</div>
                    <div class="sub">${L.escapeHtml(subBits || L.clientesShort(p.clientes, 2))} · ${p.total_qty}/${p.capacidade_total}</div>
                </div>
                <div class="pkg-row-meta">${valueLabel}<div class="sub">há ${L.age(p.state_since)}</div></div>
                <div class="pkg-row-actions">${backBtn}${actionBtn}${cancelBtn}${restoreBtn}</div>
            </div>`;
        }).join("");
        wrap.querySelectorAll(".pkg-row").forEach(row =>
            row.addEventListener("click", (e) => {
                if (e.target.closest("[data-action]")) return;
                selectedId = row.dataset.id;
                render();
                window.RaylookModal?.open(row.dataset.id);
            })
        );
        wrap.querySelectorAll("[data-action]").forEach(btn =>
            btn.addEventListener("click", async (e) => {
                e.stopPropagation();
                if (btn.dataset.action === "drill") {
                    window.RaylookModal?.open(btn.dataset.id);
                    return;
                }
                btn.disabled = true;
                let opts = {};
                if (btn.dataset.confirm) {
                    opts = { confirmText: btn.dataset.confirm };
                } else if (btn.dataset.action === "regress") {
                    opts = { confirmText: "Voltar esse pacote pra etapa anterior?" };
                } else if (btn.dataset.action === "cancel") {
                    opts = { confirmText: "Cancelar esse pacote inteiro? Não pode ser desfeito.", okLabel: "Cancelar pacote", danger: true };
                } else if (btn.dataset.action === "restore") {
                    opts = { confirmText: "Restaurar esse pacote pra 'fechado'?", okLabel: "Restaurar" };
                }
                await L.doAction(btn.dataset.id, btn.dataset.action, opts);
            })
        );
        renderPagination(totalCount);
    }

    function renderDetail() {
        const detail = document.getElementById("detail");
        const p = currentItems().find(x => x.id === selectedId);
        if (!p) {
            detail.innerHTML = `<div class="empty-state">Selecione um pacote</div>`;
            return;
        }
        const state = p.state || activeState;
        const isCancelled = state === "cancelled";
        const idx = isCancelled ? -1 : L.STATES.indexOf(state);
        const stepsHtml = isCancelled ? "" : L.STATES.map((s, i) => {
            let cls = "";
            if (i < idx) cls = "done";
            else if (i === idx) cls = "current";
            return `
            <div class="vtl-step ${cls}">
                <div><span class="dot-ck">✓</span><div class="label">${i + 1} · ${L.STATE_LABELS[s]}</div>
                <span class="note">${stepNote(p, s, i, idx)}</span></div>
                <div class="time">${stepTime(p, s)}</div>
            </div>`;
        }).join("");

        const meta = L.parsePollTitle(p.produto_name);
        const headImg = p.image
            ? `<img src="${L.escapeHtml(p.image)}" alt="" loading="lazy" onerror="this.replaceWith(Object.assign(document.createElement('span'),{textContent:'${L.productEmoji(meta.item)}'}))">`
            : `<span>${L.productEmoji(meta.item)}</span>`;
        const canAdvance = state !== "enviado" && state !== "cancelled";
        const chips = [
            meta.tecido && { l: "Tecido", v: meta.tecido },
            meta.tamanhos && { l: "Tamanhos", v: meta.tamanhos },
            meta.categoria && { l: "Categoria", v: meta.categoria },
        ].filter(Boolean);
        const chipsHtml = chips.length
            ? `<div class="meta-chips">${chips.map(c => `<span class="meta-chip"><b>${c.l}</b> ${L.escapeHtml(c.v)}</span>`).join("")}</div>`
            : "";
        const valorUnit = meta.valor != null ? `${L.money(meta.valor)} <span class="unit-tag">/un</span>` : "—";
        const canRegress = state !== "aberto" && state !== "fechado" && state !== "cancelled";

        detail.innerHTML = `
            <div class="head">
                <div class="head-img">${headImg}</div>
                <h2>${L.escapeHtml(meta.item)} <span class="seq">#${p.sequence_no ?? "?"}</span></h2>
                <div class="subtitle">${L.pill(state)} · ${L.escapeHtml(p.external_poll_id || "")}</div>
                ${chipsHtml}
            </div>
            <div class="summary-grid">
                <div class="summary-cell"><div class="l">Peças</div><div class="v">${Math.min(p.total_qty, p.capacidade_total)}/${p.capacidade_total}</div></div>
                <div class="summary-cell"><div class="l">Clientes</div><div class="v">${p.participants_count}</div></div>
                <div class="summary-cell"><div class="l">Valor unit.</div><div class="v money">${valorUnit}</div></div>
                <div class="summary-cell"><div class="l">No estado há</div><div class="v">${L.age(p.state_since)}</div></div>
            </div>
            ${isCancelled ? "" : `<div class="vtl-title">Jornada do pacote</div><div class="vtl">${stepsHtml}</div>`}
            <div class="detail-actions">
                ${canAdvance ? `<button class="btn-primary" data-advance>${primaryLabel(state)}</button>` : ""}
                ${canRegress ? `<button class="btn-ghost" data-regress>← Voltar pra etapa anterior</button>` : ""}
                <button class="btn-ghost" data-drill>Ver detalhes completos</button>
                ${canAdvance ? `<button class="btn-ghost" data-cancel style="color:var(--danger);">Cancelar pacote</button>` : ""}
            </div>`;

        detail.querySelector("[data-advance]")?.addEventListener("click", async () => {
            const action = L.primaryActionFor(state);
            if (action.action === "drill") {
                window.RaylookModal?.open(p.id);
                return;
            }
            await L.doAction(p.id, "advance", action.confirmText ? { confirmText: action.confirmText } : {});
        });
        detail.querySelector("[data-regress]")?.addEventListener("click", async () => {
            await L.doAction(p.id, "regress", { confirmText: "Voltar esse pacote pra etapa anterior?" });
        });
        detail.querySelector("[data-drill]")?.addEventListener("click", () => window.RaylookModal?.open(p.id));
        detail.querySelector("[data-cancel]")?.addEventListener("click", async () => {
            await L.doAction(p.id, "cancel", { confirmText: "Cancelar esse pacote?", okLabel: "Cancelar pacote", danger: true });
        });
    }

    function primaryLabel(state) {
        const action = L.primaryActionFor(state);
        if (action.action === "advance") return `⏭️ ${action.label}`;
        return action.label;
    }

    function stepTime(p, s) {
        const mapping = {
            aberto: p.created_at, fechado: null, confirmado: null,
            pago: null, pendente: null,
            separado: p.pdf_sent_at, enviado: p.shipped_at,
        };
        const ts = mapping[s];
        return ts ? `há ${L.age(ts)}` : "—";
    }
    function stepNote(p, s, i, idx) {
        if (i > idx) return "—";
        if (s === "aberto") return `${p.participants_count} candidato(s) na enquete`;
        if (s === "fechado") return `atingiu ${p.capacidade_total}/${p.capacidade_total} peças`;
        if (s === "confirmado") {
            const aberto = p.pagamentos.sent + p.pagamentos.created;
            return aberto ? `aprovado · ${aberto} cobrança(s) em aberto` : `aprovado pelo gerente`;
        }
        if (s === "pago") return `${p.pagamentos.paid}/${p.pagamentos.total} pagos · aguardando validação`;
        if (s === "pendente") return `validado, aguardando separação`;
        if (s === "separado") return p.pdf_sent_at ? `PDF enviado ao estoque` : `aguardando PDF`;
        if (s === "enviado") return p.shipped_at ? `despachado` : `—`;
        return "";
    }

    document.getElementById("search").addEventListener("input", e => {
        search = e.target.value;
        listPage = 1;
        renderList();
    });

    function render() { renderRail(); renderList(); renderDetail(); }
    load();
})();
