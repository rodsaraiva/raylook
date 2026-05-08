(async () => {
    const L = RaylookMockups;
    let data = null;
    let active = "todos";

    const COLORS = {
        todos: "var(--accent)",
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
        render();
    }
    window.RaylookReload = load;

    function renderPills() {
        const total = L.STATES.reduce((a, s) => a + (data.counts[s] || 0), 0);
        const items = [{ key: "todos", label: "Todos", count: total }]
            .concat(L.STATES.map(s => ({ key: s, label: L.STATE_LABELS[s], count: data.counts[s] || 0 })));
        document.getElementById("pills").innerHTML = items.map(p => `
            <button class="pill ${p.key === active ? "active" : ""}" data-state="${p.key}" style="--dot-c:${COLORS[p.key]};">
                ${p.key !== "todos" ? `<span class="dot"></span>` : ""}
                ${p.label}
                <span class="count">${p.count}</span>
            </button>
        `).join("");
        document.querySelectorAll(".pill").forEach(b =>
            b.addEventListener("click", () => { active = b.dataset.state; render(); })
        );
    }

    function renderGrid() {
        const all = active === "todos"
            ? L.STATES.flatMap(s => (data.packages_by_state[s] || []).map(p => ({ ...p, _s: s })))
            : (data.packages_by_state[active] || []).map(p => ({ ...p, _s: active }));
        const pieces = all.reduce((a, p) => a + Math.min(p.total_qty, p.capacidade_total), 0);
        const value = all.reduce((a, p) => a + (p.total_value || 0), 0);

        document.getElementById("state-name").textContent =
            active === "todos" ? "Todos os pacotes" : L.STATE_LABELS[active];
        document.getElementById("state-sum").innerHTML =
            `<strong>${all.length}</strong> pacote${all.length === 1 ? "" : "s"} · <strong>${pieces}</strong> peças · <strong>${L.moneyFull(value)}</strong>`;

        const g = document.getElementById("grid");
        if (!all.length) {
            g.innerHTML = `<div class="empty-state">Nenhum pacote nesse filtro.</div>`;
            return;
        }
        g.innerHTML = all.map(p => {
            const pct = Math.min(100, Math.round((p.total_qty / p.capacidade_total) * 100));
            return `
            <div class="inv-card" data-drill="${p.id}">
                <div class="inv-card-head">
                    <div>
                        <div class="inv-card-title">${L.escapeHtml(p.produto_name || "?")}</div>
                        <div class="inv-card-sub">seq #${p.sequence_no ?? "?"} · ${p.participants_count} ${p._s === "aberto" ? "candidatos" : "clientes"}</div>
                    </div>
                    ${L.pill(p._s)}
                </div>
                <div class="inv-card-progress">
                    <div class="bar"><span style="width:${pct}%"></span></div>
                    <div class="bar-label"><span>${p.total_qty}/${p.capacidade_total} peças</span><span>${L.age(p.state_since)}</span></div>
                </div>
                <div class="inv-card-foot">
                    <span class="price">${L.money(p.total_value)}</span>
                    ${(p._s === "enviado" || p._s === "cancelled") ? `<span style="font-size:10px;color:var(--text-muted);">finalizado</span>`
                        : `<button class="advance-btn" data-advance="${p.id}" title="Avançar" style="position:static;width:26px;height:26px;font-size:11px;">⏭</button>`}
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

    function render() { renderPills(); renderGrid(); }
    load();
})();
