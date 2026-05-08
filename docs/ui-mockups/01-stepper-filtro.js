(async () => {
    const L = RaylookMockups;
    let data = null;
    let activeState = null;

    async function load() {
        data = await L.fetchData().catch(err => {
            document.getElementById("grid").innerHTML =
                `<div class="empty-state">Erro: ${err.message}</div>`;
            return null;
        });
        if (!data) return;
        // na primeira carga, seleciona um estado com pacotes
        if (!activeState) {
            activeState = "confirmado";
            const anyNonEmpty = L.STATES.find(s => (data.packages_by_state[s] || []).length > 0);
            if (!(data.packages_by_state[activeState] || []).length && anyNonEmpty) activeState = anyNonEmpty;
        }
        render();
    }
    window.RaylookReload = load;

    function renderStepper() {
        const wrap = document.getElementById("stepper");
        wrap.innerHTML = L.STATES.map((s, i) => {
            const count = data.counts[s] || 0;
            return `
            <div class="step ${s === activeState ? "active" : ""}" data-state="${s}">
                <div class="step-head"><div class="step-num">${i + 1}</div><div class="step-label">${L.STATE_LABELS[s]}</div></div>
                <div class="step-count">${count}</div>
                <div class="step-sub">${subLabel(s, count)}</div>
            </div>`;
        }).join("");
        wrap.querySelectorAll(".step").forEach(el => {
            el.addEventListener("click", () => { activeState = el.dataset.state; render(); });
        });
    }
    function subLabel(state, count) {
        if (state === "aberto" && count) return `${count} acumulando`;
        if (state === "fechado" && count) return `aguardando gerente`;
        if (state === "confirmado" && count) return `PIX em aberto`;
        if (state === "pendente" && count) return `pronto pra separar`;
        if (state === "separado" && count) return `pronto pra despachar`;
        if (state === "enviado" && count) return `finalizado`;
        return count ? "—" : "vazio";
    }

    function renderSummary() {
        const pkgs = data.packages_by_state[activeState] || [];
        const totalPieces = pkgs.reduce((a, p) => a + (Math.min(p.total_qty, p.capacidade_total) || 0), 0);
        const totalValue = pkgs.reduce((a, p) => a + (p.total_value || 0), 0);
        document.getElementById("section-title").innerHTML =
            `${L.STATE_LABELS[activeState]} <span class="muted">${pkgs.length} pacote${pkgs.length === 1 ? "" : "s"} · ${totalPieces} peças · ${L.moneyFull(totalValue)}</span>`;
        document.getElementById("summary").innerHTML = `
            <div class="inv-summary-item"><div class="inv-summary-label">Pacotes</div><div class="inv-summary-value">${pkgs.length}</div></div>
            <div class="inv-summary-item"><div class="inv-summary-label">Peças</div><div class="inv-summary-value" style="color: var(--primary);">${totalPieces}</div></div>
            <div class="inv-summary-item"><div class="inv-summary-label">Valor</div><div class="inv-summary-value" style="color: var(--success);">${L.moneyFull(totalValue)}</div></div>
        `;
    }

    function renderGrid() {
        const pkgs = data.packages_by_state[activeState] || [];
        const g = document.getElementById("grid");
        if (!pkgs.length) {
            g.innerHTML = `<div class="empty-state">Nenhum pacote em <strong>${L.STATE_LABELS[activeState]}</strong>.</div>`;
            return;
        }
        g.innerHTML = pkgs.map(p => {
            const pct = Math.min(100, Math.round((p.total_qty / p.capacidade_total) * 100));
            const paidInfo = p.pagamentos.total
                ? `${p.pagamentos.paid}/${p.pagamentos.total} pagos`
                : `${p.participants_count} candidatos`;
            const action = L.primaryActionFor(p.state);
            const advanceBtn = (p.state === "enviado" || p.state === "cancelled") ? ""
                : `<button class="advance-btn" data-advance="${p.id}" title="Avançar etapa">⏭</button>`;
            return `
            <div class="inv-card" data-drill="${p.id}">
                <div class="inv-card-img">
                    <span>${L.productEmoji(p.produto_name)}</span>
                    <div class="inv-card-badge">${L.pill(p.state)}</div>
                    ${advanceBtn}
                </div>
                <div class="inv-card-body">
                    <div class="inv-card-title">${L.escapeHtml(p.produto_name || "Sem produto")} — seq #${p.sequence_no ?? "?"}</div>
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
        g.querySelectorAll("[data-drill]").forEach(card => {
            card.addEventListener("click", (e) => {
                if (e.target.closest("[data-advance]")) return;
                RaylookModal.open(card.dataset.drill);
            });
        });
        g.querySelectorAll("[data-advance]").forEach(btn => {
            btn.addEventListener("click", async (e) => {
                e.stopPropagation();
                btn.disabled = true;
                await L.doAction(btn.dataset.advance, "advance");
            });
        });
    }

    function render() { renderStepper(); renderSummary(); renderGrid(); }

    load();
})();
