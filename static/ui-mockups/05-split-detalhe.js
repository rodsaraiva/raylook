(async () => {
    const L = RaylookMockups;
    let data = null;
    let selectedId = null;
    let search = "";

    async function load() {
        data = await L.fetchData().catch(err => {
            document.getElementById("list").innerHTML = `<div class="empty-state">Erro: ${err.message}</div>`;
            return null;
        });
        if (!data) return;
        const allPkgs = flatPkgs();
        if (!selectedId || !allPkgs.find(x => x.id === selectedId)) {
            selectedId = allPkgs[0] ? allPkgs[0].id : null;
        }
        render();
    }
    window.RaylookReload = load;

    function flatPkgs() {
        return L.STATES.flatMap(s =>
            (data.packages_by_state[s] || []).map(p => ({ ...p, _state: s }))
        ).sort((a, b) => (b.state_since || "").localeCompare(a.state_since || ""));
    }

    function renderList() {
        const q = search.trim().toLowerCase();
        const all = flatPkgs();
        const filtered = q ? all.filter(p =>
            (p.produto_name || "").toLowerCase().includes(q)
            || (p.clientes || []).some(c => (c.name || "").toLowerCase().includes(q))
        ) : all;
        const wrap = document.getElementById("list");
        if (!filtered.length) { wrap.innerHTML = `<div class="empty-state">Nenhum pacote.</div>`; return; }
        wrap.innerHTML = filtered.map(p => `
            <div class="pkg-row ${p.id === selectedId ? "selected" : ""}" data-id="${p.id}">
                <div class="pkg-row-main">
                    <div class="name">${L.escapeHtml(p.produto_name || "?")} — seq #${p.sequence_no ?? "?"}</div>
                    <div class="sub">${L.escapeHtml(L.clientesShort(p.clientes, 3))} · ${p.total_qty}/${p.capacidade_total} peças</div>
                </div>
                ${L.pill(p._state)}
                <div class="pkg-row-meta">${L.money(p.total_value)}<div class="sub">há ${L.age(p.state_since)}</div></div>
            </div>
        `).join("");
        wrap.querySelectorAll(".pkg-row").forEach(row =>
            row.addEventListener("click", () => { selectedId = row.dataset.id; render(); })
        );
    }

    function renderDetail() {
        const p = flatPkgs().find(x => x.id === selectedId);
        const detail = document.getElementById("detail");
        if (!p) { detail.innerHTML = `<div class="empty-state">Selecione um pacote</div>`; return; }

        const idx = L.STATES.indexOf(p._state);
        const stepsHtml = L.STATES.map((s, i) => {
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

        const canAdvance = p._state !== "enviado" && p._state !== "cancelled";

        detail.innerHTML = `
            <div class="head">
                <h2>${L.escapeHtml(p.produto_name || "?")} — seq #${p.sequence_no ?? "?"}</h2>
                <div class="subtitle">${L.escapeHtml(p.external_poll_id || "")} · ${L.STATE_LABELS[p._state]}</div>
            </div>
            <div class="summary-grid">
                <div class="summary-cell"><div class="l">Peças</div><div class="v">${Math.min(p.total_qty, p.capacidade_total)}</div></div>
                <div class="summary-cell"><div class="l">Clientes</div><div class="v">${p.participants_count}</div></div>
                <div class="summary-cell"><div class="l">Valor</div><div class="v money">${L.money(p.total_value)}</div></div>
                <div class="summary-cell"><div class="l">No estado há</div><div class="v">${L.age(p.state_since)}</div></div>
            </div>
            <div class="vtl-title">Jornada do pacote</div>
            <div class="vtl">${stepsHtml}</div>
            <div class="detail-actions">
                ${canAdvance ? `<button class="btn-primary" data-advance>${primaryLabel(p)}</button>` : ""}
                <button class="btn-ghost" data-drill>Ver detalhes completos</button>
                ${canAdvance ? `<button class="btn-ghost" data-cancel style="color:var(--danger);">Cancelar pacote</button>` : ""}
            </div>`;

        detail.querySelector("[data-advance]")?.addEventListener("click", async () => {
            await L.doAction(p.id, "advance");
        });
        detail.querySelector("[data-drill]")?.addEventListener("click", () => RaylookModal.open(p.id));
        detail.querySelector("[data-cancel]")?.addEventListener("click", async () => {
            await L.doAction(p.id, "cancel", { confirmText: "Cancelar esse pacote?" });
        });
    }

    function primaryLabel(p) {
        const action = L.primaryActionFor(p._state);
        if (action.action === "advance") return `⏭️ ${action.label}`;
        if (action.action === "resend-pix") return action.label;
        return action.label;
    }

    function stepTime(p, s) {
        const mapping = {
            aberto: p.created_at, fechado: null, confirmado: null, pendente: null,
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
        if (s === "pendente") return `todos pagos, aguardando separação`;
        if (s === "separado") return p.pdf_sent_at ? `PDF enviado ao estoque` : `aguardando PDF`;
        if (s === "enviado") return p.shipped_at ? `despachado` : `—`;
        return "";
    }

    document.getElementById("search").addEventListener("input", e => {
        search = e.target.value; renderList();
    });

    function render() { renderList(); renderDetail(); }
    load();
})();
