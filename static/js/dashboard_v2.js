// Dashboard V2 — rail vertical + lista/detalhe.
// Usa /api/dashboard/packages e helpers de static/dashboard/lib.js.
(async () => {
    const L = window.RaylookDashboard;
    let data = null;
    let activeState = null;
    let selectedId = null;
    let search = "";
    let filter = { preset: "today", since: null, until: null };
    // Expõe pro finance.js consumir nos fetches (since/until da barra de cima).
    window.dashboardFilter = filter;
    let listPage = 1;
    const LIST_PAGE_SIZE = 20;

    // Role do usuário logado (filtra rail e botões). Default admin se /api/me falhar.
    let currentRole = "admin";
    let visibleGroups = new Set(["comercial", "estoque", "logistica", "financeiro"]);
    try {
        const r = await fetch("/api/me", { credentials: "same-origin" });
        if (r.ok) {
            const me = await r.json();
            currentRole = me.role || "admin";
            visibleGroups = new Set(me.visible_groups || []);
        }
    } catch (_) { /* mantém admin */ }
    window.currentRole = currentRole;

    // Esconde o bloco Financeiro pra não-admin (só admin enxerga).
    if (!visibleGroups.has("financeiro")) {
        document.getElementById("fin-group")?.style.setProperty("display", "none");
    }

    const DESCS = {
        aberto: "Formando", fechado: "Aguardando gerente",
        confirmado: "PIX em aberto", pago: "Validar pagamento",
        pendente: "Pronto pra separar",
        separado: "Pronto pra despachar", enviado: "Finalizado",
    };

    // Estados em estoque/logística — voltar deles exige senha de admin.
    const STOCK_LOG_STATES = new Set(["pago", "pendente", "separado", "enviado"]);

    // Modal de motivo pra Pendente (seleção única via dropdown). Retorna
    // {reasons: [valor], observations} ou null se cancelado.
    function promptPendingReasons() {
        return new Promise((resolve) => {
            const ov = document.getElementById("pending-reasons-overlay");
            const md = document.getElementById("pending-reasons-modal");
            const select = document.getElementById("pending-reason-select");
            const obsWrap = document.getElementById("pending-obs-wrap");
            const obs = document.getElementById("pending-reasons-obs");
            const ok = document.getElementById("pending-reasons-ok");
            const cancel = document.getElementById("pending-reasons-cancel");
            const err = document.getElementById("pending-reasons-error");
            if (!ov || !md || !select || !ok) { resolve(null); return; }

            select.value = "";
            obs.value = "";
            obsWrap.hidden = true;
            err.textContent = "";
            ov.classList.add("open");
            md.classList.add("open");
            setTimeout(() => select.focus(), 30);

            function onChange() {
                const isOutros = select.value === "outros";
                obsWrap.hidden = !isOutros;
                if (isOutros) setTimeout(() => obs.focus(), 30);
                err.textContent = "";
            }
            function cleanup() {
                ov.classList.remove("open");
                md.classList.remove("open");
                ok.removeEventListener("click", onOk);
                cancel.removeEventListener("click", onCancel);
                ov.removeEventListener("click", onCancel);
                select.removeEventListener("change", onChange);
            }
            function onOk() {
                const reason = select.value;
                if (!reason) { err.textContent = "Selecione um motivo."; return; }
                const observations = obs.value.trim();
                if (reason === "outros" && !observations) {
                    err.textContent = "Descreva o motivo no campo de observação.";
                    return;
                }
                cleanup();
                resolve({ reasons: [reason], observations });
            }
            function onCancel() { cleanup(); resolve(null); }
            select.addEventListener("change", onChange);
            ok.addEventListener("click", onOk);
            cancel.addEventListener("click", onCancel);
            ov.addEventListener("click", onCancel);
        });
    }

    // RBAC frontend (espelho de auth_service.can_advance no backend).
    function canDoAdvance(fromState, toState) {
        if (currentRole === "admin") return true;
        const target = toState || null;
        if (currentRole === "estoque") {
            if (fromState === "pago" && (target === null || target === "pendente" || target === "separado")) return true;
            if (fromState === "pendente" && (target === null || target === "separado")) return true;
            return false;
        }
        if (currentRole === "logistica") {
            if (fromState === "separado" && (target === null || target === "enviado")) return true;
            return false;
        }
        return false;
    }

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
        if (window._financeOpen) window.financeRefresh?.();
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
        if (window._financeOpen) window.financeRefresh?.();
    });

    function currentItems() {
        if (activeState === "cancelled") return data.cancelled || [];
        return data.packages_by_state[activeState] || [];
    }

    // Dropdowns Comercial / Estoque / Logística (Financeiro tem header próprio).
    // `labels` sobrescreve L.STATE_LABELS dentro daquele grupo apenas.
    // `extras` injeta items depois dos states do grupo — usado pra colocar
    // "Cancelados" dentro do Comercial só visualmente; o fluxo continua igual.
    // Mesmo estado pode aparecer em mais de um grupo (ex: "separado" em
    // Estoque e Logística): é apenas re-exposição visual, o estado é o mesmo.
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
            states: ["pago", "pendente", "separado"],
            labels: { pago: "Fila de separação" },
        },
        {
            id: "logistica",
            label: "Logística",
            states: ["separado", "enviado"],
        },
    ];

    // Estado inicial: abre o primeiro grupo visível pro role.
    const groupOpen = { comercial: false, estoque: false, logistica: false };
    if (visibleGroups.has("comercial")) groupOpen.comercial = true;
    else if (visibleGroups.has("estoque")) groupOpen.estoque = true;
    else if (visibleGroups.has("logistica")) groupOpen.logistica = true;

    // Exposto pro finance-toggle.js fechar os dropdowns ao abrir o financeiro.
    window._railCollapseGroups = function () {
        Object.keys(groupOpen).forEach(k => { groupOpen[k] = false; });
        renderRail();
    };

    function renderRail() {
        const rail = document.getElementById("rail");
        const groupsHtml = RAIL_GROUPS.filter(g => visibleGroups.has(g.id)).map(g => {
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
                const willOpen = !groupOpen[id];
                // Acordeon: só um grupo aberto por vez.
                Object.keys(groupOpen).forEach(k => { groupOpen[k] = false; });
                groupOpen[id] = willOpen;
                // Abrir um dropdown comercial/estoque deve fechar Financeiro e Clientes.
                if (willOpen && window._financeOpen) window.toggleFinanceView();
                if (willOpen && window._clientesOpen) window._clientesClose?.();
                if (willOpen) {
                    // Seleciona o primeiro estado do grupo — mesma UX que
                    // Financeiro/Clientes já têm (abrir = ir pra primeira aba).
                    const group = RAIL_GROUPS.find(g => g.id === id);
                    const firstState = group?.states?.[0];
                    if (firstState && firstState !== activeState) {
                        activeState = firstState;
                        listPage = 1;
                        const pkgs = currentItems();
                        selectedId = pkgs[0] ? pkgs[0].id : null;
                    }
                    render();
                } else {
                    renderRail();
                }
            })
        );
        rail.querySelectorAll(".rail-step").forEach(el =>
            el.addEventListener("click", () => {
                if (window._financeOpen) window.toggleFinanceView();
                if (window._clientesOpen) window._clientesClose?.();
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
            // "Pago" tem duas ações: Marcar pendente (advance 1 step) e Gerar etiqueta
            // (advance pulando pra "separado"). As demais fases usam a ação primária única.
            let actionBtn = "";
            if (state === "pago") {
                if (canDoAdvance("pago", "pendente"))
                    actionBtn += `<button class="row-action" data-action="advance" data-to="pendente" data-id="${p.id}" title="Validar pagamento e mover pra fila Pendente">Marcar pendente</button>`;
                if (canDoAdvance("pago", "separado"))
                    actionBtn += `<button class="row-action" data-action="advance" data-to="separado" data-id="${p.id}" title="Gerar etiqueta e pular pra Separado">Gerar etiqueta</button>`;
            } else if (state !== "aberto" && action.action && canDoAdvance(state, null)) {
                actionBtn = `<button class="row-action" data-action="${action.action}" data-id="${p.id}"${confirmAttr}>${L.escapeHtml(action.label)}</button>`;
            }
            // Voltar / Cancelar / Restaurar são exclusivos do admin.
            // Botão de download da etiqueta — aparece quando pdf_sent_at está set
            // (a partir de "separado"). Qualquer role logado pode baixar.
            const etiquetaBtn = p.pdf_sent_at
                ? `<a class="row-action" href="/api/dashboard/packages/${p.id}/etiqueta.pdf" target="_blank" rel="noopener" title="Baixar PDF da etiqueta">📄 Etiqueta</a>`
                : "";
            const isAdmin = currentRole === "admin";
            const backBtn = (!isAdmin || state === "aberto" || state === "fechado" || state === "cancelled")
                ? ""
                : `<button class="row-back" data-action="regress" data-state="${state}" data-id="${p.id}" title="Voltar pra etapa anterior">←</button>`;
            const valueLabel = p.total_value ? L.moneyFull(p.total_value)
                : (meta.valor != null ? `${L.money(meta.valor)} <span class="row-unit">/un</span>` : "—");
            const cancelBtn = (isAdmin && (state === "fechado" || state === "confirmado"))
                ? `<button class="row-action danger" data-action="cancel" data-id="${p.id}" title="Cancelar pacote inteiro">Cancelar</button>`
                : "";
            const restoreBtn = (isAdmin && state === "cancelled")
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
                <div class="pkg-row-actions">${backBtn}${etiquetaBtn}${actionBtn}${cancelBtn}${restoreBtn}</div>
            </div>`;
        }).join("");
        wrap.querySelectorAll(".pkg-row").forEach(row => {
            row.addEventListener("click", (e) => {
                if (e.target.closest("[data-action]")) return;
                const id = row.dataset.id;
                if (id === selectedId) {
                    window.RaylookModal?.open(id);
                    return;
                }
                selectedId = id;
                render();
            });
            row.addEventListener("dblclick", (e) => {
                if (e.target.closest("[data-action]")) return;
                window.RaylookModal?.open(row.dataset.id);
            });
        });
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
                if (btn.dataset.to) opts.to = btn.dataset.to;
                // Mover pra pendente exige modal de motivos (estoque clicou "Marcar pendente").
                if (opts.to === "pendente") {
                    const result = await promptPendingReasons();
                    if (!result) { btn.disabled = false; return; }
                    opts.body = result;
                    delete opts.confirmText;  // já tem o modal de motivos
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
        const isAdmin = currentRole === "admin";
        const canAdvance = state !== "enviado" && state !== "cancelled";
        const chips = [
            meta.tecido && { l: "Tecido", v: meta.tecido },
            meta.tamanhos && { l: "Tamanhos", v: meta.tamanhos },
            meta.categoria && { l: "Categoria", v: meta.categoria },
        ].filter(Boolean);
        const chipsHtml = chips.length
            ? `<div class="meta-chips">${chips.map(c => `<span class="meta-chip"><b>${c.l}</b> ${L.escapeHtml(c.v)}</span>`).join("")}</div>`
            : "";
        const REASON_LABELS = {
            faltando_pecas: "Faltando peças",
            tamanhos_trocados: "Tamanhos trocados",
            cores_trocadas: "Cores trocadas",
            modelo_errado: "Modelo errado",
            pacote_com_defeito: "Pacote com defeito",
            cancelado_fornecedor: "Cancelado pelo fornecedor",
            outros: "Outros",
        };
        const pendingReasons = Array.isArray(p.pending_reasons) ? p.pending_reasons : [];
        const pendingHtml = (state === "pendente" && pendingReasons.length) ? `
            <div class="meta-chips" style="margin-top:8px;">
                ${pendingReasons.map(r => `<span class="meta-chip" style="background:rgba(248,113,113,0.10);color:#f87171;border-color:rgba(248,113,113,0.25);">${L.escapeHtml(REASON_LABELS[r] || r)}</span>`).join("")}
            </div>
            ${p.pending_observations ? `<div style="margin-top:6px;font-size:0.78rem;color:var(--text-muted);font-style:italic;">"${L.escapeHtml(p.pending_observations)}"</div>` : ""}
        ` : "";
        const valorUnit = meta.valor != null ? `${L.money(meta.valor)} <span class="unit-tag">/un</span>` : "—";
        const canRegress = isAdmin && state !== "aberto" && state !== "fechado" && state !== "cancelled";

        detail.innerHTML = `
            <div class="head">
                <div class="head-img">${headImg}</div>
                <h2>${L.escapeHtml(meta.item)} <span class="seq">#${p.sequence_no ?? "?"}</span></h2>
                <div class="subtitle">${L.pill(state)} · ${L.escapeHtml(p.external_poll_id || "")}</div>
                ${chipsHtml}
                ${pendingHtml}
            </div>
            <div class="summary-grid">
                <div class="summary-cell"><div class="l">Peças</div><div class="v">${Math.min(p.total_qty, p.capacidade_total)}/${p.capacidade_total}</div></div>
                <div class="summary-cell"><div class="l">Clientes</div><div class="v">${p.participants_count}</div></div>
                <div class="summary-cell"><div class="l">Valor unit.</div><div class="v money">${valorUnit}</div></div>
                <div class="summary-cell"><div class="l">No estado há</div><div class="v">${L.age(p.state_since)}</div></div>
            </div>
            ${isCancelled ? "" : `<div class="vtl-title">Jornada do pacote</div><div class="vtl">${stepsHtml}</div>`}
            <div class="detail-actions">
                ${state === "pago" ? `
                    ${canDoAdvance("pago", "pendente") ? `<button class="btn-primary" data-advance data-to="pendente">⏭️ Marcar pendente</button>` : ""}
                    ${canDoAdvance("pago", "separado") ? `<button class="btn-primary" data-advance data-to="separado">🏷️ Gerar etiqueta</button>` : ""}
                ` : ((canAdvance && canDoAdvance(state, null)) ? `<button class="btn-primary" data-advance>${primaryLabel(state)}</button>` : "")}
                ${canRegress ? `<button class="btn-ghost" data-regress>← Voltar pra etapa anterior</button>` : ""}
                ${p.pdf_sent_at ? `<a class="btn-ghost" href="/api/dashboard/packages/${p.id}/etiqueta.pdf" target="_blank" rel="noopener" style="text-decoration:none;">📄 Baixar etiqueta</a>` : ""}
                <button class="btn-ghost" data-drill>Ver detalhes completos</button>
                ${(isAdmin && canAdvance) ? `<button class="btn-ghost" data-cancel style="color:var(--danger);">Cancelar pacote</button>` : ""}
            </div>`;

        detail.querySelectorAll("[data-advance]").forEach(btn => btn.addEventListener("click", async () => {
            const action = L.primaryActionFor(state);
            if (action.action === "drill" && !btn.dataset.to) {
                window.RaylookModal?.open(p.id);
                return;
            }
            const opts = action.confirmText ? { confirmText: action.confirmText } : {};
            if (btn.dataset.to) opts.to = btn.dataset.to;
            if (opts.to === "pendente") {
                const result = await promptPendingReasons();
                if (!result) return;
                opts.body = result;
                delete opts.confirmText;
            }
            await L.doAction(p.id, "advance", opts);
        }));
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
    // Boot: aplica o preset default ('hoje') que popula since/until e dispara load().
    setFilterPreset(filter.preset);
})();
