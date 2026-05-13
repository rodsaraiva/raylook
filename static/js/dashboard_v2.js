// Dashboard V2 — rail vertical + lista/detalhe.
// Usa /api/dashboard/packages e helpers de static/dashboard/lib.js.
(async () => {
    const L = window.RaylookDashboard;
    let data = null;
    let activeState = null;
    let selectedId = null;
    let search = "";
    let filter = { preset: "all", since: null, until: null };

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
        updateFilterSummary();
        load();
    });

    const CLIENT_STATES = new Set(["pago", "pendente", "separado", "enviado"]);

    function isClientState(state) { return CLIENT_STATES.has(state); }

    function currentItems() {
        if (activeState === "cancelled") return data.cancelled || [];
        if (isClientState(activeState)) return data.clients_by_state?.[activeState] || [];
        return data.packages_by_state[activeState] || [];
    }

    function renderRail() {
        const rail = document.getElementById("rail");
        rail.innerHTML = `<div class="rail-title">Fluxo</div>` +
            L.STATES.map((s, i) => `
                <div class="rail-step ${s === activeState ? "active" : ""}" data-state="${s}">
                    <div class="num">${i + 1}</div>
                    <div>
                        <div class="label">${L.STATE_LABELS[s]}</div>
                        <div class="sub">${DESCS[s]}</div>
                    </div>
                    <div class="count">${data.counts[s] || 0}</div>
                </div>
            `).join("") +
            `<div class="rail-divider"></div>
             <div class="rail-step ${activeState === "cancelled" ? "active" : ""}" data-state="cancelled" style="opacity:.85;">
                <div class="num" style="background:rgba(248,113,113,0.15);color:var(--danger);">×</div>
                <div><div class="label">Cancelados</div><div class="sub">histórico</div></div>
                <div class="count">${data.counts.cancelled || 0}</div>
             </div>`;
        rail.querySelectorAll(".rail-step").forEach(el =>
            el.addEventListener("click", () => {
                if (window._financeOpen) window.toggleFinanceView();
                activeState = el.dataset.state;
                const pkgs = currentItems();
                selectedId = pkgs[0] ? pkgs[0].id : null;
                render();
            })
        );

    }

    function clientItemKey(c) { return `${c.pacote_id}:${c.cliente_id}`; }

    function renderList() {
        const q = search.trim().toLowerCase();
        const all = currentItems();
        const wrap = document.getElementById("list");
        const titleEl = document.getElementById("list-title");
        const summaryEl = document.getElementById("list-summary");

        titleEl.textContent = activeState === "cancelled" ? "Cancelados" : L.STATE_LABELS[activeState];

        if (isClientState(activeState)) {
            // Linha = cliente individual
            const filtered = q ? all.filter(c =>
                (c.nome || "").toLowerCase().includes(q)
                || (c.celular || "").includes(q)
                || (c.produto_name || "").toLowerCase().includes(q)
            ) : all;
            const totalPieces = filtered.reduce((a, c) => a + (c.qty || 0), 0);
            const totalValue = filtered.reduce((a, c) => a + (c.valor || 0), 0);
            summaryEl.textContent =
                `${filtered.length} cliente${filtered.length === 1 ? "" : "s"} · ${totalPieces} peças · ${L.moneyFull(totalValue)}`;
            if (!filtered.length) {
                wrap.innerHTML = `<div class="empty-state">Nenhum cliente nessa fase.</div>`;
                return;
            }
            wrap.innerHTML = filtered.map(c => {
                const meta = L.parsePollTitle(c.produto_name);
                const thumb = c.image
                    ? `<img src="${L.escapeHtml(c.image)}" alt="" loading="lazy">`
                    : `<span>${L.productEmoji(meta.item)}</span>`;
                const key = clientItemKey(c);
                // Em "pago", botões de transição. Avanço aplica no pacote inteiro
                // (granularidade fina exigiria coluna por cliente — fora do escopo).
                let actionsHtml = "<span></span>";
                const cliAttrs = `data-pkg="${c.pacote_id}" data-cli="${c.cliente_id}"`;
                const nomeAttrs = `data-cliente-nome="${L.escapeHtml(c.nome || '')}" data-cliente-celular="${L.escapeHtml(c.celular || '')}"`;
                const cancelBtn = `<button class="row-action danger" data-cli-cancel ${cliAttrs} ${nomeAttrs} title="Cancelar esse cliente do pacote">Cancelar</button>`;
                if (activeState === "pago") {
                    actionsHtml = `<div class="pkg-row-actions">
                        <button class="row-action warning" data-cli-advance ${cliAttrs} data-to="separado" title="Validar e gerar etiqueta">→ Separar</button>
                        <button class="row-action ghost" data-cli-advance ${cliAttrs} data-to="pendente" title="Validar pagamento">Validar</button>
                        ${cancelBtn}
                    </div>`;
                } else if (activeState === "pendente") {
                    actionsHtml = `<div class="pkg-row-actions">
                        <button class="row-action" data-cli-advance ${cliAttrs} data-to="separado" title="Gerar etiqueta de separação">Gerar etiqueta</button>
                        ${cancelBtn}
                    </div>`;
                } else if (activeState === "separado") {
                    actionsHtml = `<div class="pkg-row-actions">
                        <button class="row-action" data-cli-advance ${cliAttrs} data-to="enviado" title="Marcar como despachado">Marcar enviado</button>
                        ${cancelBtn}
                    </div>`;
                } else if (activeState === "enviado") {
                    actionsHtml = `<div class="pkg-row-actions">${cancelBtn}</div>`;
                }
                return `
                <div class="pkg-row ${key === selectedId ? "selected" : ""}" data-id="${key}" data-pacote-id="${c.pacote_id}">
                    <div class="pkg-thumb">${thumb}</div>
                    <div class="pkg-row-main">
                        <div class="name">${L.escapeHtml(c.nome || "?")}</div>
                        <div class="sub">${L.escapeHtml(c.celular || "")} · ${c.qty} peças · ${L.escapeHtml(meta.item)} #${c.pacote_sequence_no ?? "?"}</div>
                    </div>
                    <div class="pkg-row-meta">${L.moneyFull(c.valor)}<div class="sub">há ${L.age(c.state_since)}</div></div>
                    ${actionsHtml}
                </div>`;
            }).join("");
            wrap.querySelectorAll(".pkg-row").forEach(row =>
                row.addEventListener("click", (e) => {
                    if (e.target.closest("[data-cli-advance]") || e.target.closest("[data-cli-cancel]")) return;
                    selectedId = row.dataset.id;
                    render();
                })
            );
            wrap.querySelectorAll("[data-cli-cancel]").forEach(btn =>
                btn.addEventListener("click", async (e) => {
                    e.stopPropagation();
                    const pkgId = btn.dataset.pkg;
                    const cliId = btn.dataset.cli;
                    const nome = btn.dataset.clienteNome || "esse cliente";
                    const celular = btn.dataset.clienteCelular || "";
                    const label = celular ? `${nome} (${celular})` : nome;
                    if (!await (window.RaylookModal?.confirm(
                            `Cancelar ${label} do pacote? A venda e o pagamento desse cliente serão apagados.`,
                            { okLabel: "Cancelar cliente", danger: true }
                        ) ?? Promise.resolve(window.confirm(`Cancelar ${label}?`)))) return;
                    btn.disabled = true;
                    try {
                        const resp = await fetch(`/api/dashboard/packages/${pkgId}/clients/${cliId}`,
                            { method: "DELETE", credentials: "include" });
                        if (!resp.ok) {
                            const err = await resp.json().catch(() => ({ detail: "Falha" }));
                            throw new Error(err.detail || "Falha");
                        }
                        window.RaylookModal?.toast("Cliente removido do pacote", "success");
                        if (window.RaylookReload) await window.RaylookReload();
                    } catch (err) {
                        window.RaylookModal?.toast(`Erro: ${err.message}`, "error");
                        btn.disabled = false;
                    }
                })
            );
            wrap.querySelectorAll("[data-cli-advance]").forEach(btn =>
                btn.addEventListener("click", async (e) => {
                    e.stopPropagation();
                    const pkgId = btn.dataset.pkg;
                    const cliId = btn.dataset.cli;
                    const to = btn.dataset.to;
                    const confirms = {
                        pendente: { msg: "Validar pagamento desse cliente?", ok: "Validar" },
                        separado: { msg: "Gerar etiqueta de separação desse cliente?", ok: "Gerar etiqueta" },
                        enviado: { msg: "Marcar esse cliente como despachado?", ok: "Marcar enviado" },
                    };
                    const c = confirms[to] || { msg: "Avançar esse cliente?", ok: "Avançar" };
                    const confirmText = c.msg;
                    const okLabel = c.ok;
                    if (!await (window.RaylookModal?.confirm(confirmText, { okLabel })
                                ?? Promise.resolve(window.confirm(confirmText)))) return;
                    btn.disabled = true;
                    try {
                        const path = `clients/${cliId}/advance${to ? `?to=${encodeURIComponent(to)}` : ""}`;
                        const resp = await fetch(`/api/dashboard/packages/${pkgId}/${path}`,
                            { method: "POST", credentials: "include" });
                        if (!resp.ok) {
                            const err = await resp.json().catch(() => ({ detail: "Falha" }));
                            throw new Error(err.detail || "Falha");
                        }
                        const payload = await resp.json();
                        window.RaylookModal?.toast(`Cliente agora em "${payload.new_state}"`, "success");
                        if (window.RaylookReload) await window.RaylookReload();
                    } catch (err) {
                        window.RaylookModal?.toast(`Erro: ${err.message}`, "error");
                        btn.disabled = false;
                    }
                })
            );
            return;
        }

        // Linha = pacote (estados aberto/fechado/confirmado/cancelled)
        const filtered = q ? all.filter(p =>
            (p.produto_name || "").toLowerCase().includes(q)
            || (p.clientes || []).some(c => (c.name || "").toLowerCase().includes(q))
            || (p.external_poll_id || "").toLowerCase().includes(q)
        ) : all;
        const totalPieces = filtered.reduce((a, p) => a + (Math.min(p.total_qty, p.capacidade_total) || 0), 0);
        const totalValue = filtered.reduce((a, p) => a + (p.total_value || 0), 0);
        summaryEl.textContent =
            `${filtered.length} pacote${filtered.length === 1 ? "" : "s"} · ${totalPieces} peças · ${L.moneyFull(totalValue)}`;
        if (!filtered.length) {
            wrap.innerHTML = `<div class="empty-state">Nenhum pacote.</div>`;
            return;
        }
        wrap.innerHTML = filtered.map(p => {
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
    }

    function renderDetail() {
        const detail = document.getElementById("detail");
        if (isClientState(activeState)) {
            const c = currentItems().find(x => clientItemKey(x) === selectedId);
            renderClientDetail(detail, c);
            return;
        }
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

    function renderClientDetail(detail, c) {
        if (!c) {
            detail.innerHTML = `<div class="empty-state">Selecione um cliente</div>`;
            return;
        }
        const meta = L.parsePollTitle(c.produto_name);
        const headImg = c.image
            ? `<img src="${L.escapeHtml(c.image)}" alt="" loading="lazy">`
            : `<span>${L.productEmoji(meta.item)}</span>`;
        const idx = L.STATES.indexOf(activeState);
        const stepsHtml = L.STATES.map((s, i) => {
            let cls = "";
            if (i < idx) cls = "done";
            else if (i === idx) cls = "current";
            return `
            <div class="vtl-step ${cls}">
                <div><span class="dot-ck">✓</span><div class="label">${i + 1} · ${L.STATE_LABELS[s]}</div></div>
            </div>`;
        }).join("");
        detail.innerHTML = `
            <div class="head">
                <div class="head-img">${headImg}</div>
                <h2>${L.escapeHtml(c.nome || "?")}</h2>
                <div class="subtitle">${L.escapeHtml(c.celular || "")} · ${L.pill(activeState)}</div>
            </div>
            <div class="summary-grid">
                <div class="summary-cell"><div class="l">Peças</div><div class="v">${c.qty}</div></div>
                <div class="summary-cell"><div class="l">Valor</div><div class="v money">${L.moneyFull(c.valor)}</div></div>
                <div class="summary-cell"><div class="l">Produto</div><div class="v" style="font-size:13px;">${L.escapeHtml(meta.item)}</div></div>
                <div class="summary-cell"><div class="l">No estado há</div><div class="v">${L.age(c.state_since)}</div></div>
            </div>
            <div class="vtl-title">Pacote pai</div>
            <div class="meta-chips" style="margin-bottom:18px;">
                <span class="meta-chip"><b>Pacote</b> #${c.pacote_sequence_no ?? "?"}</span>
                <span class="meta-chip"><b>Estado</b> ${L.STATE_LABELS[c.pacote_state] || c.pacote_state}</span>
            </div>
            <div class="vtl-title">Jornada</div>
            <div class="vtl">${stepsHtml}</div>
            `;
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
        renderList();
    });

    function render() { renderRail(); renderList(); renderDetail(); }
    load();
})();
