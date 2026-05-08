(async () => {
    const L = RaylookMockups;
    let data = null;
    let active = null;

    const COLORS = {
        aberto: "var(--step-aberto)", fechado: "var(--step-fechado)",
        confirmado: "var(--step-confirmado)", pendente: "var(--step-pendente)",
        separado: "var(--step-separado)", enviado: "var(--step-enviado)",
    };

    async function load() {
        data = await L.fetchData().catch(() => null);
        if (!data) {
            document.getElementById("grid").innerHTML = `<div class="empty-state">Erro.</div>`;
            return;
        }
        if (!active) active = L.STATES.find(s => data.counts[s] > 0) || "aberto";
        render();
    }
    window.RaylookReload = load;

    function renderBar() {
        const bar = document.getElementById("flow-bar");
        const total = L.STATES.reduce((a, s) => a + (data.counts[s] || 0), 0) || 1;
        bar.innerHTML = L.STATES.map((s, i) => {
            const count = data.counts[s] || 0;
            // Largura proporcional (mínimo 0.8 pra não sumir um segmento vazio)
            const flex = Math.max(count / total, 0.1) * 10;
            const bg = `rgba(255,255,255,${count ? 0.04 : 0.02})`;
            return `
            <div class="flow-seg ${s === active ? "active" : ""}"
                 data-state="${s}"
                 style="--fw:${flex.toFixed(2)};--seg-color:${COLORS[s]};--seg-bg:${bg};">
                <div class="top">
                    <span class="num-tag">${i + 1}</span>
                    <span>${count === 0 ? "vazio" : count + (count === 1 ? " pac." : " pacs.")}</span>
                </div>
                <div>
                    <div class="count">${count}</div>
                    <div class="name">${L.STATE_LABELS[s]}</div>
                </div>
            </div>`;
        }).join("");
        bar.querySelectorAll(".flow-seg").forEach(el =>
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
        if (!pkgs.length) {
            g.innerHTML = `<div class="empty-state">Nenhum pacote em <strong>${L.STATE_LABELS[active]}</strong>.</div>`;
            return;
        }
        g.innerHTML = pkgs.map(p => {
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
                    <div class="inv-card-meta">${p.participants_count} ${p.state === "aberto" ? "candidatos" : "clientes"} · ${L.age(p.state_since)}</div>
                    <div class="inv-card-row">
                        <span class="price">${L.money(p.total_value)}</span>
                        <span style="font-size:11px;color:var(--text-muted);">${p.total_qty}/${p.capacidade_total}</span>
                    </div>
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

    function render() { renderBar(); renderGrid(); }
    load();
})();
