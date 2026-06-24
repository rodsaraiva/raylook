/* Aba Sessões → Bernardo — acúmulo ao vivo por enquete da sessão Bernardo,
 * com botão "Fechar pacote" por enquete. Espelha o padrão de enquetes.js.
 */
(function () {
    "use strict";

    const state = { open: false };

    function el(id) { return document.getElementById(id); }

    function escape(s) {
        return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
            ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
    }

    const STATUS_LABELS = {
        ok: "fechado",
        no_votes: "sem votos para fechar",
        no_product: "enquete sem produto",
        not_found: "enquete não encontrada",
        not_session: "enquete não pertence à sessão",
    };

    // ---- toggle / view ----
    function openBernardo() {
        state.open = true;
        window._bernardoOpen = true;
        document.getElementById("packages-area")?.classList.add("retracted");
        document.getElementById("section-bernardo")?.classList.add("active");
        // fecha as outras seções (mesmo mecanismo que enquetes/clientes/finance usam entre si)
        document.getElementById("section-finance")?.classList.remove("active");
        document.getElementById("fin-group")?.classList.remove("open");
        window._financeOpen = false;
        window._clientesClose?.();
        window._enquetesClose?.();
        window._railCollapseGroups?.();
        document.getElementById("bernardo-group")?.classList.add("open");
        refresh();
    }

    function closeBernardo() {
        state.open = false;
        window._bernardoOpen = false;
        document.getElementById("section-bernardo")?.classList.remove("active");
        document.getElementById("bernardo-group")?.classList.remove("open");
        if (!window._financeOpen && !window._clientesOpen && !window._enquetesOpen) {
            document.getElementById("packages-area")?.classList.remove("retracted");
        }
    }
    window._bernardoOpen = false;
    window._bernardoClose = closeBernardo;

    // ---- fetch / render ----
    async function refresh() {
        const wrap = el("bernardo-cards");
        if (!wrap) return;
        wrap.innerHTML = `<div class="enq-empty-list">Carregando…</div>`;
        try {
            const r = await fetch("/api/dashboard/sessions/Bernardo", { credentials: "same-origin" });
            if (!r.ok) throw new Error(`HTTP ${r.status}`);
            const data = await r.json();
            renderCards(data.enquetes || []);
        } catch (e) {
            wrap.innerHTML = `<div class="enq-empty-list" style="color:#f87171;">Erro: ${escape(e.message)}</div>`;
        }
    }
    window.bernardoRefresh = refresh;

    function renderCards(enquetes) {
        const wrap = el("bernardo-cards");
        if (!wrap) return;
        wrap.innerHTML = "";
        if (!enquetes.length) {
            wrap.innerHTML = `<div class="enq-empty-list">Nenhuma enquete Bernardo ativa.</div>`;
            const meta = el("bernardo-meta");
            if (meta) meta.textContent = "0 enquetes";
            return;
        }
        const meta = el("bernardo-meta");
        if (meta) meta.textContent = `${enquetes.length} enquete${enquetes.length === 1 ? "" : "s"}`;

        for (const enq of enquetes) {
            const totalQty = enq.total_qty || 0;
            const partsCount = enq.participants_count || 0;
            const parts = (enq.participants || [])
                .map((p) => `${escape(p.nome || "—")}: ${escape(String(p.qty))}`)
                .join(" · ") || "—";

            const card = document.createElement("div");
            card.className = "enq-pacote";
            card.innerHTML = `
                <div class="enq-pacote-head">
                    <div><span class="pkg-id">${escape(enq.titulo || "Enquete")}</span></div>
                </div>
                <div class="enq-card-line2" style="margin:6px 0;">
                    Acúmulo: <b>${totalQty}</b> peças · ${partsCount} cliente(s)
                </div>
                <div class="enq-card-line2" style="color:var(--text-muted);">${parts}</div>`;

            const btn = document.createElement("button");
            btn.type = "button";
            btn.className = "btn-add-voto";
            btn.style.marginTop = "12px";
            btn.textContent = "Fechar pacote";
            btn.disabled = totalQty <= 0;
            btn.addEventListener("click", () => closePackage(enq.enquete_id, btn));
            card.appendChild(btn);
            wrap.appendChild(card);
        }
    }

    async function closePackage(enqueteId, btn) {
        btn.disabled = true;
        try {
            const r = await fetch("/api/dashboard/sessions/Bernardo/close", {
                method: "POST",
                credentials: "same-origin",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ enquete_id: enqueteId }),
            });
            const out = await r.json();
            if (out.status === "ok") {
                refresh();
            } else {
                const reason = STATUS_LABELS[out.status] || out.status || "erro";
                alert("Não foi possível fechar: " + reason);
                btn.disabled = false;
            }
        } catch (e) {
            alert("Não foi possível fechar: " + e.message);
            btn.disabled = false;
        }
    }

    // ---- handlers ----
    document.addEventListener("DOMContentLoaded", function () {
        el("bernardo-group-header")?.addEventListener("click", () => {
            if (state.open) closeBernardo();
            else openBernardo();
        });
        document.querySelectorAll('#bernardo-group .rail-step[data-session-view]').forEach((step) => {
            step.addEventListener("click", (e) => {
                e.stopPropagation();
                openBernardo();
            });
        });
    });
})();
