(async () => {
    const L = RaylookMockups;
    let data = null;
    let active = null;

    const STEP_COLORS = {
        aberto: "var(--step-aberto)", fechado: "var(--step-fechado)",
        confirmado: "var(--step-confirmado)", pendente: "var(--step-pendente)",
        separado: "var(--step-separado)", enviado: "var(--step-enviado)",
    };

    async function load() {
        data = await L.fetchData().catch(() => null);
        if (!data) { document.getElementById("grid").innerHTML = `<div class="empty-state">Erro.</div>`; return; }
        if (!active) active = L.STATES.find(s => data.counts[s] > 0) || "aberto";
        render();
    }
    window.RaylookReload = load;

    function computeKpis() {
        let activeCount = 0, enviadoCount = 0;
        let totalFaturado = 0, totalPago = 0;
        let numClientes = 0;

        for (const s of L.STATES) {
            const pkgs = data.packages_by_state[s] || [];
            if (s !== "enviado" && s !== "cancelled") activeCount += pkgs.length;
            if (s === "enviado") enviadoCount += pkgs.length;
            for (const p of pkgs) {
                totalFaturado += p.total_value || 0;
                if (s === "separado" || s === "enviado" || s === "pendente") {
                    totalPago += p.total_value || 0;
                }
                numClientes += p.participants_count || 0;
            }
        }
        const ticket = numClientes > 0 ? totalFaturado / numClientes : 0;
        const convRate = (activeCount + enviadoCount) > 0
            ? Math.round(enviadoCount / (activeCount + enviadoCount) * 100) : 0;
        return { activeCount, enviadoCount, totalFaturado, totalPago, ticket, convRate };
    }

    function renderKpis() {
        const k = computeKpis();
        document.getElementById("kpis").innerHTML = `
            <div class="kpi" style="--kpi-color:var(--accent);">
                <div class="kpi-label">Faturamento no fluxo</div>
                <div class="kpi-value">${L.money(k.totalFaturado)}</div>
                <div class="kpi-extra">${L.money(k.totalPago)} pagos</div>
            </div>
            <div class="kpi" style="--kpi-color:var(--success);">
                <div class="kpi-label">Pacotes ativos</div>
                <div class="kpi-value">${k.activeCount}</div>
                <div class="kpi-extra neutral">+${k.enviadoCount} finalizados</div>
            </div>
            <div class="kpi" style="--kpi-color:var(--step-separado);">
                <div class="kpi-label">Ticket médio</div>
                <div class="kpi-value">${L.money(k.ticket)}</div>
                <div class="kpi-extra neutral">por cliente</div>
            </div>
            <div class="kpi" style="--kpi-color:var(--warning);">
                <div class="kpi-label">Taxa de envio</div>
                <div class="kpi-value">${k.convRate}<span class="unit">%</span></div>
                <div class="kpi-extra neutral">${k.enviadoCount} de ${k.activeCount + k.enviadoCount}</div>
            </div>
        `;
    }

    function renderStepper() {
        const wrap = document.getElementById("stepper");
        wrap.innerHTML = L.STATES.map(s => `
            <div class="step ${s === active ? "active" : ""}" data-state="${s}" style="--step-c:${STEP_COLORS[s]};">
                <div class="step-label"><span class="step-dot"></span>${L.STATE_LABELS[s]}</div>
                <div class="step-count">${data.counts[s] || 0}</div>
            </div>
        `).join("");
        wrap.querySelectorAll(".step").forEach(el =>
            el.addEventListener("click", () => { active = el.dataset.state; render(); })
        );
    }

    function renderGrid() {
        const pkgs = data.packages_by_state[active] || [];
        const pieces = pkgs.reduce((a, p) => a + Math.min(p.total_qty, p.capacidade_total), 0);
        const value = pkgs.reduce((a, p) => a + (p.total_value || 0), 0);
        document.getElementById("state-title").innerHTML =
            `${L.STATE_LABELS[active]} <span class="muted">${pkgs.length} pacote${pkgs.length === 1 ? "" : "s"} · ${pieces} peças · ${L.moneyFull(value)}</span>`;

        const g = document.getElementById("grid");
        if (!pkgs.length) { g.innerHTML = `<div class="empty-state">Vazio.</div>`; return; }
        g.innerHTML = pkgs.map(p => {
            const pct = Math.min(100, Math.round((p.total_qty / p.capacidade_total) * 100));
            const advBtn = (p.state === "enviado" || p.state === "cancelled") ? ""
                : `<button class="advance-btn" data-advance="${p.id}" title="Avançar" style="position:static;width:26px;height:26px;font-size:11px;">⏭</button>`;
            return `
            <div class="inv-card" data-drill="${p.id}">
                <div class="inv-card-head">
                    <div>
                        <div class="inv-card-title">${L.escapeHtml(p.produto_name || "?")}</div>
                        <div class="inv-card-sub">seq #${p.sequence_no ?? "?"} · ${p.participants_count} clientes</div>
                    </div>
                    ${L.pill(p.state)}
                </div>
                <div class="progress"><span style="width:${pct}%"></span></div>
                <div class="inv-card-foot">
                    <span class="price">${L.money(p.total_value)}</span>
                    ${advBtn || `<span style="font-size:10px;color:var(--text-muted);">${L.age(p.state_since)}</span>`}
                </div>
            </div>`;
        }).join("");
        wire();
    }

    function wire() {
        document.querySelectorAll("[data-drill]").forEach(card =>
            card.addEventListener("click", (e) => {
                if (e.target.closest("[data-advance]")) return;
                RaylookModal.open(card.dataset.drill);
            })
        );
        document.querySelectorAll("[data-advance]").forEach(btn =>
            btn.addEventListener("click", async (e) => {
                e.stopPropagation();
                btn.disabled = true;
                await L.doAction(btn.dataset.advance, "advance");
            })
        );
    }

    function render() { renderKpis(); renderStepper(); renderGrid(); }
    load();
})();
