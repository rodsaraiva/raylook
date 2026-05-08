(async () => {
    const L = RaylookMockups;
    let data = null;
    let active = null;

    // Hex legível em SVG (CSS vars não funcionam em stroke de <circle> em todos browsers)
    const COLORS = {
        aberto: "#facc15", fechado: "#d4af37", confirmado: "#4ade80",
        pendente: "#f59e0b", separado: "#a1a1aa", enviado: "#fde047",
    };

    async function load() {
        data = await L.fetchData().catch(() => null);
        if (!data) { document.getElementById("grid").innerHTML = `<div class="empty-state">Erro.</div>`; return; }
        if (!active) active = L.STATES.find(s => data.counts[s] > 0) || "aberto";
        render();
    }
    window.RaylookReload = load;

    function renderDonut() {
        const total = L.STATES.reduce((a, s) => a + (data.counts[s] || 0), 0) || 1;
        const cx = 50, cy = 50, r = 36;
        const circ = 2 * Math.PI * r;
        let offset = 0;
        const svg = document.getElementById("donut-svg");
        svg.innerHTML = `<circle cx="${cx}" cy="${cy}" r="${r}" fill="none"
                                 stroke="rgba(255,255,255,0.06)" stroke-width="10"/>`;
        for (const s of L.STATES) {
            const count = data.counts[s] || 0;
            if (!count) continue;
            const len = (count / total) * circ;
            const dashArray = `${len} ${circ - len}`;
            const color = COLORS[s];
            const dimmed = s !== active ? "opacity:0.55;" : "";
            svg.innerHTML += `
                <circle class="slice" data-state="${s}"
                        cx="${cx}" cy="${cy}" r="${r}" fill="none"
                        stroke="${color}" stroke-width="10"
                        stroke-dasharray="${dashArray}"
                        stroke-dashoffset="${-offset}"
                        style="${dimmed}cursor:pointer;transition:opacity .2s;"
                        transform="rotate(-90 ${cx} ${cy})"/>
            `;
            offset += len;
        }
        svg.querySelectorAll(".slice").forEach(el =>
            el.addEventListener("click", () => { active = el.dataset.state; render(); })
        );
        document.getElementById("donut-total").textContent = total;
        document.getElementById("donut-active").innerHTML =
            `<strong style="color:var(--accent);">${data.counts[active] || 0}</strong> em <strong>${L.STATE_LABELS[active]}</strong>`;
    }

    function renderLegend() {
        const total = L.STATES.reduce((a, s) => a + (data.counts[s] || 0), 0) || 1;
        document.getElementById("legend").innerHTML = L.STATES.map(s => {
            const count = data.counts[s] || 0;
            const pct = Math.round((count / total) * 100);
            return `
            <div class="legend-item ${s === active ? "active" : ""}" data-state="${s}" style="--lc:${COLORS[s]};">
                <span class="sw"></span>
                <div>
                    <span class="name">${L.STATE_LABELS[s]}</span>
                    <span class="pct">${pct}% do total</span>
                </div>
                <span class="val">${count}</span>
            </div>`;
        }).join("");
        document.querySelectorAll(".legend-item").forEach(el =>
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
        if (!pkgs.length) { g.innerHTML = `<div class="empty-state">Nenhum pacote em <strong>${L.STATE_LABELS[active]}</strong>.</div>`; return; }
        g.innerHTML = pkgs.map(p => {
            const pct = Math.min(100, Math.round((p.total_qty / p.capacidade_total) * 100));
            const advBtn = (p.state === "enviado" || p.state === "cancelled") ? ""
                : `<button class="advance-btn" data-advance="${p.id}" title="Avançar" style="position:static;width:26px;height:26px;font-size:11px;">⏭</button>`;
            return `
            <div class="inv-card" data-drill="${p.id}">
                <div class="inv-card-body">
                    <div class="inv-card-head">
                        <div>
                            <div class="inv-card-title">${L.escapeHtml(p.produto_name || "?")}</div>
                            <div class="inv-card-sub">seq #${p.sequence_no ?? "?"} · ${p.participants_count} ${p.state === "aberto" ? "candidatos" : "clientes"}</div>
                        </div>
                        ${L.pill(p.state)}
                    </div>
                    <div class="progress"><span style="width:${pct}%"></span></div>
                    <div class="inv-card-foot">
                        <span class="price">${L.money(p.total_value)}</span>
                        ${advBtn || `<span style="color:var(--text-muted);font-size:11px;">${L.age(p.state_since)}</span>`}
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

    function render() { renderDonut(); renderLegend(); renderGrid(); }
    load();
})();
