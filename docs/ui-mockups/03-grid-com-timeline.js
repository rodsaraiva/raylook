(async () => {
    const L = RaylookMockups;
    let data = null;
    const ALL = "todos";
    let active = ALL;

    async function load() {
        data = await L.fetchData().catch(err => {
            document.getElementById("grid").innerHTML = `<div class="empty-state">Erro: ${err.message}</div>`;
            return null;
        });
        if (!data) return;
        render();
    }
    window.RaylookReload = load;

    function renderChips() {
        const total = L.STATES.reduce((a, s) => a + (data.counts[s] || 0), 0);
        const wrap = document.getElementById("chip-group");
        const chips = [
            { key: ALL, label: `Todos (${total})` },
            ...L.STATES.map(s => ({ key: s, label: `${L.STATE_LABELS[s]} (${data.counts[s] || 0})` })),
        ];
        wrap.innerHTML = chips.map(c =>
            `<button class="inv-chip ${c.key === active ? "active" : ""}" data-state="${c.key}">${c.label}</button>`
        ).join("");
        wrap.querySelectorAll(".inv-chip").forEach(btn => {
            btn.addEventListener("click", () => { active = btn.dataset.state; render(); });
        });
    }

    function renderGrid() {
        const all = active === ALL
            ? L.STATES.flatMap(s => (data.packages_by_state[s] || []).map(p => ({ ...p, _state: s })))
            : (data.packages_by_state[active] || []).map(p => ({ ...p, _state: active }));
        const g = document.getElementById("grid");
        if (!all.length) {
            g.innerHTML = `<div class="empty-state">Nenhum pacote nesse filtro.</div>`;
            return;
        }
        g.innerHTML = all.map(cardHtml).join("");
        wire();
    }

    function cardHtml(p) {
        const idx = L.STATES.indexOf(p._state);
        const stepsHtml = L.STATES.map((s, i) => {
            let cls = "";
            if (i < idx) cls = "done";
            else if (i === idx) cls = "current";
            const dotContent = cls === "done" ? "✓" : (i + 1);
            return `<div class="mt-step ${cls}"><div class="mt-dot">${dotContent}</div><div class="mt-label">${L.STATE_LABELS[s].slice(0, 7)}</div></div>`;
        }).join("");
        const pw = idx === 0 ? "0px" : `calc(${(idx / 5) * 100}% - ${(idx / 5) * 40}px)`;

        const piecesMetric = p._state === "aberto"
            ? `${p.total_qty}/${p.capacidade_total}`
            : `${p.capacidade_total}`;
        const money = p._state === "aberto"
            ? `est. ${L.money(p.total_qty * (p.unit_price || 0))}`
            : L.money(p.total_value);
        const moneyLabel = p._state === "pendente" ? "aberto" : (p._state === "aberto" ? "est." : "total");

        const action = L.primaryActionFor(p._state);
        const age = `${p._state === "fechado" ? "parado há" : ""} ${L.age(p.state_since)}`.trim();
        const canAdvance = p._state !== "enviado" && p._state !== "cancelled";

        return `
        <div class="pkg-card" data-id="${p.id}">
            <div class="pkg-card-head">
                <div data-drill="${p.id}" style="flex:1;cursor:pointer;">
                    <div class="title">${L.escapeHtml(p.produto_name || "Sem produto")} — seq #${p.sequence_no ?? "?"}</div>
                    <div class="subtitle">${L.escapeHtml(p.external_poll_id || "")}</div>
                </div>
                ${L.pill(p._state)}
            </div>
            <div class="pkg-card-metrics" data-drill="${p.id}" style="cursor:pointer;">
                <div><div class="v">${piecesMetric}</div><div class="l">peças</div></div>
                <div><div class="v">${p.participants_count}</div><div class="l">${p._state === "aberto" ? "candidatos" : "clientes"}</div></div>
                <div><div class="v money">${money}</div><div class="l">${moneyLabel}</div></div>
            </div>
            <div class="mini-timeline" style="--progress-width: ${pw}">${stepsHtml}</div>
            <div class="pkg-card-foot">
                <span class="age">${age}</span>
                <div style="display:flex;gap:6px;">
                    ${canAdvance ? `<button class="btn-action" data-advance="${p.id}" title="Avançar etapa" style="padding:6px 10px;">⏭</button>` : ""}
                    <button class="btn-action ${action.ghost ? "ghost" : ""}" data-primary="${p.id}" data-endpoint="${action.action || ""}" data-drilldown="${action.drilldown ? "1" : ""}">${action.label}</button>
                </div>
            </div>
        </div>`;
    }

    function wire() {
        document.querySelectorAll("[data-drill]").forEach(el =>
            el.addEventListener("click", () => RaylookModal.open(el.dataset.drill))
        );
        document.querySelectorAll("[data-advance]").forEach(btn =>
            btn.addEventListener("click", async (e) => {
                e.stopPropagation();
                btn.disabled = true;
                await L.doAction(btn.dataset.advance, "advance");
            })
        );
        document.querySelectorAll("[data-primary]").forEach(btn =>
            btn.addEventListener("click", async (e) => {
                e.stopPropagation();
                const id = btn.dataset.primary;
                if (btn.dataset.drilldown === "1") { RaylookModal.open(id); return; }
                if (!btn.dataset.endpoint) return;
                btn.disabled = true;
                await L.doAction(id, btn.dataset.endpoint);
            })
        );
    }

    function render() { renderChips(); renderGrid(); }
    load();
})();
