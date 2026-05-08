(async () => {
    const L = RaylookMockups;
    let data = null;
    const ALL = "todos";
    let active = ALL;

    const STATE_COLORS = {
        aberto: "var(--step-aberto)",
        fechado: "var(--step-fechado)",
        confirmado: "var(--step-confirmado)",
        pendente: "var(--step-pendente)",
        separado: "var(--step-separado)",
        enviado: "var(--step-enviado)",
    };

    async function load() {
        data = await L.fetchData().catch(err => {
            document.querySelector("#tbody").innerHTML =
                `<tr class="empty-row"><td colspan="7">Erro: ${err.message}</td></tr>`;
            return null;
        });
        if (!data) return;
        render();
    }
    window.RaylookReload = load;

    function renderTabs() {
        const total = L.STATES.reduce((a, s) => a + (data.counts[s] || 0), 0);
        const all = [{ key: ALL, label: "Todos", color: "#aaa", count: total }]
            .concat(L.STATES.map(s => ({
                key: s, label: L.STATE_LABELS[s], color: STATE_COLORS[s],
                count: data.counts[s] || 0,
            })));
        document.getElementById("tabs").innerHTML = all.map(t =>
            `<button class="state-tab ${t.key === active ? "active" : ""}" data-state="${t.key}" style="--dot-color:${t.color};">
                <span class="dot"></span>${t.label}
                <span class="count">${t.count}</span>
            </button>`
        ).join("");
        document.querySelectorAll(".state-tab").forEach(b =>
            b.addEventListener("click", () => { active = b.dataset.state; render(); })
        );
    }

    function renderRows() {
        const rows = active === ALL
            ? L.STATES.flatMap(s => (data.packages_by_state[s] || []).map(p => ({ ...p, _state: s })))
            : (data.packages_by_state[active] || []).map(p => ({ ...p, _state: active }));
        const tb = document.getElementById("tbody");
        if (!rows.length) {
            tb.innerHTML = `<tr class="empty-row"><td colspan="7">Nenhum pacote nesse filtro.</td></tr>`;
            return;
        }
        tb.innerHTML = rows.map(rowHtml).join("");
        wire();
    }

    function rowHtml(p) {
        const idx = L.STATES.indexOf(p._state);
        const flowHtml = L.STATES.map((s, i) => {
            if (i < idx) return `<span class="on"></span>`;
            if (i === idx) return `<span class="current"></span>`;
            return `<span></span>`;
        }).join("");
        const agingClass = L.agingBucket(p.state_since);
        const clients = p.clientes && p.clientes.length ? L.clientesShort(p.clientes, 2) : "—";
        const pieces = `${Math.min(p.total_qty, p.capacidade_total)} / ${p.capacidade_total}`;
        const action = L.primaryActionFor(p._state);
        const value = L.moneyFull(p.total_value || (p.total_qty * (p.unit_price || 0)));
        const canAdvance = p._state !== "enviado" && p._state !== "cancelled";
        return `
        <tr data-drill="${p.id}" style="cursor:pointer;">
            <td>
                <div class="product">${L.escapeHtml(p.produto_name || "?")}</div>
                <div class="sub">${L.escapeHtml(p.external_poll_id || "")} · seq #${p.sequence_no ?? "?"}</div>
            </td>
            <td>
                ${L.escapeHtml(clients)}
                <div class="sub">${p.participants_count} ${p._state === "aberto" ? "candidatos" : "clientes"}</div>
            </td>
            <td>
                <div class="flow-mini">${flowHtml}</div>
                <div class="sub">${L.STATE_LABELS[p._state]}</div>
            </td>
            <td><span class="aging ${agingClass}">${L.age(p.state_since)}</span></td>
            <td class="num">${pieces}</td>
            <td class="num"><span class="money">${value}</span></td>
            <td class="num" style="white-space:nowrap;">
                ${canAdvance ? `<button class="action-btn" data-advance="${p.id}" title="Avançar etapa" style="margin-right:4px;">⏭</button>` : ""}
                <button class="action-btn ${action.ghost ? "" : "primary"}" data-primary="${p.id}" data-endpoint="${action.action || ""}" data-drilldown="${action.drilldown ? "1" : ""}">${action.label}</button>
            </td>
        </tr>`;
    }

    function wire() {
        document.querySelectorAll("tr[data-drill]").forEach(row =>
            row.addEventListener("click", (e) => {
                if (e.target.closest("button")) return;
                RaylookModal.open(row.dataset.drill);
            })
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

    function render() { renderTabs(); renderRows(); }
    load();
})();
