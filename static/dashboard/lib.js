// Lib compartilhada do dashboard v2.
// Busca /api/dashboard/packages (URL legada) e expõe helpers de formatação.

const RaylookDashboard = (() => {
    const STATES = ["aberto", "fechado", "confirmado", "pago", "pendente", "separado", "enviado"];
    const STATE_LABELS = {
        aberto: "Aberto",
        fechado: "Fechado",
        confirmado: "Aguardando Pagamento",
        pago: "Pago",
        pendente: "Pendente",
        separado: "Separado",
        enviado: "Enviado",
        cancelled: "Cancelado",
    };

    // Labels dos status de pagamento (valores do backend em inglês).
    const PAY_LABELS = {
        paid: "Pago",
        sent: "Cobrança gerada",
        created: "Aberto",
        cancelled: "Cancelado",
        failed: "Falhou",
    };
    function payLabel(status) {
        return PAY_LABELS[status] || status || "—";
    }

    async function fetchData(params = {}) {
        const qs = new URLSearchParams();
        if (params.since) qs.set("since", params.since);
        if (params.until) qs.set("until", params.until);
        const url = "/api/dashboard/packages" + (qs.toString() ? `?${qs}` : "");
        const resp = await fetch(url, {
            credentials: "include",
            headers: { "Accept": "application/json" },
        });
        if (!resp.ok) {
            throw new Error(`/api/dashboard/packages → ${resp.status}`);
        }
        return resp.json();
    }

    function money(value) {
        if (value === null || value === undefined) return "—";
        const n = Number(value);
        if (n >= 1000) return `R$ ${(n / 1000).toFixed(1).replace(".", ",")}k`;
        return `R$ ${n.toFixed(2).replace(".", ",")}`;
    }

    function moneyFull(value) {
        if (value === null || value === undefined) return "—";
        return `R$ ${Number(value).toLocaleString("pt-BR",
            { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    }

    function age(iso) {
        if (!iso) return "—";
        const dt = new Date(iso);
        const secs = Math.floor((Date.now() - dt.getTime()) / 1000);
        if (secs < 60) return `${secs}s`;
        if (secs < 3600) return `${Math.floor(secs / 60)} min`;
        if (secs < 86400) return `${Math.floor(secs / 3600)} h`;
        return `${Math.floor(secs / 86400)} d`;
    }

    function agingBucket(iso) {
        // "fresh" < 2h, "warn" 2-8h, "stale" > 8h (ou dias)
        if (!iso) return "fresh";
        const hrs = (Date.now() - new Date(iso).getTime()) / 3600000;
        if (hrs < 2) return "fresh";
        if (hrs < 8) return "warn";
        return "stale";
    }

    function pill(state) {
        const label = STATE_LABELS[state] || state;
        return `<span class="status-pill status-${state}">${label}</span>`;
    }

    function productEmoji(name) {
        const n = (name || "").toLowerCase();
        if (n.includes("blusa")) return "👚";
        if (n.includes("vestido")) return "👗";
        if (n.includes("conjunto")) return "🧥";
        if (n.includes("saia")) return "🩱";
        if (n.includes("calça")) return "👖";
        return "📦";
    }

    function clientesShort(clientes, limit = 2) {
        if (!clientes || !clientes.length) return "—";
        const names = clientes
            .map(c => (c.name || "").split(" ")[0])
            .filter(Boolean);
        if (names.length <= limit) return names.join(", ");
        return `${names.slice(0, limit).join(", ")} · +${names.length - limit}`;
    }

    function initials(name) {
        const parts = (name || "").trim().split(/\s+/);
        if (!parts.length || !parts[0]) return "?";
        const first = parts[0][0] || "";
        const last = parts.length > 1 ? parts[parts.length - 1][0] : "";
        return (first + last).toUpperCase();
    }

    // Parser do título canônico das enquetes:
    //   📝 *ITEM=* REGATAS
    //   💰 *VALOR=$* 21
    //   🔖 *TECIDO=* CANCELADO
    //   📏 *TAMANHOS=* PMG
    //   📍 *CATEGORIA=* IMPORTADO
    function parsePollTitle(raw) {
        const text = String(raw || "");
        const grab = (key) => {
            const re = new RegExp(`\\*${key}=\\$?\\*\\s*([^\\n*_]+)`, "i");
            const m = text.match(re);
            return m ? m[1].trim().replace(/\s+/g, " ") : "";
        };
        const item = grab("ITEM");
        const valor = grab("VALOR");
        return {
            item: item || text.split("\n")[0].replace(/[*_]/g, "").trim(),
            valor: valor ? Number(String(valor).replace(",", ".").replace(/[^\d.]/g, "")) || null : null,
            tecido: grab("TECIDO"),
            tamanhos: grab("TAMANHOS"),
            categoria: grab("CATEGORIA"),
        };
    }

    function escapeHtml(s) {
        return String(s == null ? "" : s)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;");
    }

    // -----------------------------------------------------------------
    // Ações: chama endpoint, toast de feedback, chama reload global.
    // -----------------------------------------------------------------
    async function doAction(pacoteId, action, opts = {}) {
        const { confirmText, successText, danger, okLabel, to, body } = opts;
        if (confirmText) {
            const ask = window.RaylookModal?.confirm
                ? window.RaylookModal.confirm(confirmText, { danger: danger || action === "cancel", okLabel })
                : Promise.resolve(window.confirm(confirmText));
            if (!await ask) return false;
        }
        try {
            const qs = to ? `?to=${encodeURIComponent(to)}` : "";
            const init = { method: "POST", credentials: "include" };
            if (body !== undefined) {
                init.headers = { "Content-Type": "application/json" };
                init.body = JSON.stringify(body);
            }
            const resp = await fetch(`/api/dashboard/packages/${pacoteId}/${action}${qs}`, init);
            if (!resp.ok) {
                const e = await resp.json().catch(() => ({ detail: `HTTP ${resp.status}` }));
                throw new Error(e.detail || "Falha");
            }
            const payload = await resp.json();
            if (window.RaylookModal) {
                window.RaylookModal.toast(successText || msgForAction(action, payload), "success");
            }
            if (window.RaylookReload) await window.RaylookReload();
            return payload;
        } catch (err) {
            if (window.RaylookModal) window.RaylookModal.toast(`Erro: ${err.message}`, "error");
            return false;
        }
    }

    // Modal de senha admin pra ações restritas (regress de estoque/logística).
    // Retorna a senha digitada, ou null se o usuário cancelou.
    function promptAdminPassword() {
        return new Promise((resolve) => {
            const ov = document.getElementById("admin-pwd-overlay");
            const md = document.getElementById("admin-pwd-modal");
            const inp = document.getElementById("admin-pwd-input");
            const err = document.getElementById("admin-pwd-error");
            const ok = document.getElementById("admin-pwd-ok");
            const cancel = document.getElementById("admin-pwd-cancel");
            if (!ov || !md || !inp || !ok || !cancel) {
                resolve(window.prompt("Senha de administrador:") || null);
                return;
            }
            inp.value = "";
            err.textContent = "";
            ov.classList.add("open");
            md.classList.add("open");
            setTimeout(() => inp.focus(), 30);

            function cleanup() {
                ov.classList.remove("open");
                md.classList.remove("open");
                ok.removeEventListener("click", onOk);
                cancel.removeEventListener("click", onCancel);
                ov.removeEventListener("click", onCancel);
                inp.removeEventListener("keydown", onKey);
            }
            function onOk() {
                const v = inp.value || "";
                if (!v) { err.textContent = "Digite a senha."; return; }
                cleanup();
                resolve(v);
            }
            function onCancel() { cleanup(); resolve(null); }
            function onKey(e) {
                if (e.key === "Enter") { e.preventDefault(); onOk(); }
                if (e.key === "Escape") onCancel();
            }
            ok.addEventListener("click", onOk);
            cancel.addEventListener("click", onCancel);
            ov.addEventListener("click", onCancel);
            inp.addEventListener("keydown", onKey);
        });
    }

    function msgForAction(action, payload) {
        if (action === "advance") return `Avançou para "${payload.new_state}"`;
        if (action === "regress") return `Voltou para "${payload.new_state}"`;
        if (action === "cancel") return "Pacote cancelado";
        if (action === "restore") return "Pacote restaurado para fechado";
        return "Ação realizada";
    }

    // Mapa estado → ação primária do card. Usado pelos 5 mockups.
    function primaryActionFor(state) {
        switch (state) {
            case "aberto":     return { label: "Ver detalhes", action: null, drilldown: true, ghost: true };
            case "fechado":    return { label: "Confirmar", action: "advance" };
            case "confirmado": return { label: "Gerenciar pagamentos", action: "drill" };
            case "pago":       return { label: "Validar pagamento", action: "advance" };
            case "pendente":   return { label: "Gerar etiqueta", action: "advance" };
            case "separado":   return { label: "Marcar enviado", action: "advance" };
            case "enviado":    return { label: "Detalhes", action: null, drilldown: true, ghost: true };
            case "cancelled":  return { label: "Detalhes", action: null, drilldown: true, ghost: true };
        }
        return { label: "Ver", action: null, drilldown: true, ghost: true };
    }

    return {
        STATES, STATE_LABELS, PAY_LABELS, payLabel, fetchData,
        money, moneyFull, age, agingBucket,
        pill, productEmoji, clientesShort, initials, escapeHtml,
        parsePollTitle,
        doAction, primaryActionFor, promptAdminPassword,
    };
})();

// Expose no window pra ficar acessível por outros scripts via window.*
// (top-level `const` não atribui ao window automaticamente em script clássico).
window.RaylookDashboard = RaylookDashboard;
