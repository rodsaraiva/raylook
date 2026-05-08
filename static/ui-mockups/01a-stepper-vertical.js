(async () => {
    const L = RaylookMockups;
    let data = null;
    let activeState = null;

    const DESCS = {
        aberto: "Formando", fechado: "Aguardando gerente",
        confirmado: "PIX em aberto", pendente: "Pronto pra separar",
        separado: "Pronto pra despachar", enviado: "Finalizado",
    };

    async function load() {
        data = await L.fetchData().catch(() => null);
        if (!data) {
            document.getElementById("grid").innerHTML =
                `<div class="empty-state">Erro ao carregar dados.</div>`;
            return;
        }
        if (!activeState) {
            activeState = L.STATES.find(s => (data.packages_by_state[s] || []).length > 0) || "aberto";
        }
        render();
    }
    window.RaylookReload = load;

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
             <div class="rail-step" data-state="cancelled" style="opacity:.7;">
                <div class="num" style="background:rgba(248,113,113,0.15);color:var(--danger);">×</div>
                <div><div class="label">Cancelados</div><div class="sub">histórico</div></div>
                <div class="count">${data.counts.cancelled || 0}</div>
             </div>`;
        rail.querySelectorAll(".rail-step").forEach(el =>
            el.addEventListener("click", () => { activeState = el.dataset.state; render(); })
        );
    }

    function renderContent() {
        const pkgs = (activeState === "cancelled"
            ? data.cancelled
            : data.packages_by_state[activeState]) || [];
        const totalPieces = pkgs.reduce((a, p) => a + (Math.min(p.total_qty, p.capacidade_total) || 0), 0);
        const totalValue = pkgs.reduce((a, p) => a + (p.total_value || 0), 0);
        const label = activeState === "cancelled" ? "Cancelados" : L.STATE_LABELS[activeState];

        document.getElementById("section-title").innerHTML =
            `${label} <span class="muted">${pkgs.length} pacote${pkgs.length === 1 ? "" : "s"} · ${totalPieces} peças · ${L.moneyFull(totalValue)}</span>`;
        document.getElementById("summary").innerHTML = `
            <div class="inv-summary-item"><div class="inv-summary-label">Pacotes</div><div class="inv-summary-value">${pkgs.length}</div></div>
            <div class="inv-summary-item"><div class="inv-summary-label">Peças</div><div class="inv-summary-value" style="color:var(--text-primary);">${totalPieces}</div></div>
            <div class="inv-summary-item"><div class="inv-summary-label">Valor</div><div class="inv-summary-value" style="color:var(--success);">${L.moneyFull(totalValue)}</div></div>
        `;

        const g = document.getElementById("grid");
        if (!pkgs.length) {
            g.innerHTML = `<div class="empty-state">Nenhum pacote em <strong>${label}</strong>.</div>`;
            return;
        }
        g.innerHTML = pkgs.map(p => {
            const pct = Math.min(100, Math.round((p.total_qty / p.capacidade_total) * 100));
            const paidInfo = p.pagamentos.total
                ? `${p.pagamentos.paid}/${p.pagamentos.total} pagos`
                : `${p.participants_count} candidatos`;
            const advBtn = (p.state === "enviado" || p.state === "cancelled") ? ""
                : `<button class="advance-btn" data-advance="${p.id}" title="Avançar etapa">⏭</button>`;
            return `
            <div class="inv-card" data-drill="${p.id}">
                <div class="inv-card-img">
                    <span>${L.productEmoji(p.produto_name)}</span>
                    <div class="inv-card-badge">${L.pill(p.state)}</div>
                    ${advBtn}
                </div>
                <div class="inv-card-body">
                    <div class="inv-card-title">${L.escapeHtml(p.produto_name || "?")} — seq #${p.sequence_no ?? "?"}</div>
                    <div class="inv-card-meta">${L.escapeHtml(p.external_poll_id || "")} · ${p.participants_count} clientes</div>
                    <div class="progress"><span style="width:${pct}%"></span></div>
                    <div class="inv-card-meta">${paidInfo} · ${L.age(p.state_since)}</div>
                </div>
                <div class="inv-card-footer">
                    <div class="price">${L.money(p.total_value)}</div>
                    <div class="pieces">${p.total_qty} / ${p.capacidade_total}</div>
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

    function render() { renderRail(); renderContent(); }
    load();
})();
