(async () => {
    const L = RaylookMockups;
    let data = null;

    async function load() {
        data = await L.fetchData().catch(err => {
            document.getElementById("lanes").innerHTML = `<div class="glass-card">Erro: ${err.message}</div>`;
            return null;
        });
        if (!data) return;
        render();
    }
    window.RaylookReload = load;

    const DESCS = {
        aberto: "Pacotes coletando votos",
        fechado: "Aguardando aprovação do gerente",
        confirmado: "Aprovado, aguardando clientes pagarem",
        pendente: "Pagamentos feitos, aguardando estoque separar",
        separado: "Etiqueta PDF gerada, pronto pra despachar",
        enviado: "Despachado para as clientes",
    };

    function render() {
        const wrap = document.getElementById("lanes");
        wrap.innerHTML = L.STATES.map((state, i) => {
            const pkgs = data.packages_by_state[state] || [];
            const cards = pkgs.length
                ? pkgs.map(cardHtml).join("")
                : `<div class="lane-empty">Sem pacotes neste estado.</div>`;
            return `
            <div class="lane lane-${i + 1}">
                <div class="lane-head">
                    <div class="lane-name">${i + 1} · ${L.STATE_LABELS[state]}</div>
                    <div class="lane-desc">${DESCS[state]}</div>
                    <div class="lane-count">${data.counts[state] || 0}</div>
                </div>
                <div class="lane-body">${cards}</div>
            </div>`;
        }).join("");
        wire();
    }

    function cardHtml(p) {
        const prod = L.escapeHtml(p.produto_name || "?");
        const meta = p.state === "confirmado"
            ? `${p.participants_count} cliente(s) · ${p.pagamentos.sent + p.pagamentos.created} cobrança(s) em aberto`
            : p.state === "aberto"
                ? `${p.participants_count} candidato(s) · ${L.clientesShort(p.clientes, 3)}`
                : `${p.participants_count} cliente(s) · ${L.clientesShort(p.clientes, 3)}`;
        const bottomLeft = p.state === "aberto"
            ? `${p.total_qty}/${p.capacidade_total} peças`
            : L.money(p.total_value);
        const ageText = p.state === "fechado" ? `${L.age(p.state_since)} parado` : L.age(p.state_since);
        const advanceBtn = (p.state === "enviado" || p.state === "cancelled") ? ""
            : `<button class="advance-btn" data-advance="${p.id}" title="Avançar etapa" style="top:6px;right:6px;width:26px;height:26px;font-size:12px;">⏭</button>`;
        return `
        <div class="pkg" data-drill="${p.id}" style="position:relative;padding-right:${advanceBtn ? "38px" : "14px"};">
            <div class="pkg-title">${prod} — seq #${p.sequence_no ?? "?"}</div>
            <div class="pkg-meta">${L.escapeHtml(meta)}</div>
            <div class="pkg-row">
                <span class="pkg-value">${bottomLeft}</span>
                <span class="pkg-age">${ageText}</span>
            </div>
            ${advanceBtn}
        </div>`;
    }

    function wire() {
        document.querySelectorAll("[data-drill]").forEach(card => {
            card.addEventListener("click", (e) => {
                if (e.target.closest("[data-advance]")) return;
                RaylookModal.open(card.dataset.drill);
            });
        });
        document.querySelectorAll("[data-advance]").forEach(btn => {
            btn.addEventListener("click", async (e) => {
                e.stopPropagation();
                btn.disabled = true;
                await L.doAction(btn.dataset.advance, "advance");
            });
        });
    }

    load();
})();
