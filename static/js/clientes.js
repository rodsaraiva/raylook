/* Aba Clientes — listagem + renomear */
(function () {
    "use strict";

    const VIEWS = new Set(["all", "complete", "pending"]);
    const VIEW_TITLES = {
        all: "Todos os clientes",
        complete: "Cadastros completos",
        pending: "Clientes pendentes",
    };

    const state = {
        view: "all",        // "all" | "complete" | "pending"
        search: "",
        page: 1,
        pageSize: 50,
        items: [],
        total: 0,
        open: false,
    };

    function el(id) { return document.getElementById(id); }

    function escapeHtml(s) {
        return String(s || "").replace(/[&<>"']/g, (c) =>
            ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
    }

    function fmtDate(iso) {
        if (!iso) return "—";
        try {
            const d = new Date(iso);
            return d.toLocaleDateString("pt-BR");
        } catch (_) { return "—"; }
    }

    // ---- toggle / view ----
    function openClientes(view) {
        state.open = true;
        window._clientesOpen = true;
        document.getElementById("packages-area")?.classList.add("retracted");
        document.getElementById("section-clientes")?.classList.add("active");
        // fecha outras seções
        document.getElementById("section-finance")?.classList.remove("active");
        document.getElementById("fin-group")?.classList.remove("open");
        window._financeOpen = false;
        window._railCollapseGroups?.();
        // abre nosso grupo
        document.getElementById("clientes-group")?.classList.add("open");
        if (view) setView(view);
        else refresh();
    }

    function closeClientes() {
        state.open = false;
        window._clientesOpen = false;
        document.getElementById("packages-area")?.classList.remove("retracted");
        document.getElementById("section-clientes")?.classList.remove("active");
        document.getElementById("clientes-group")?.classList.remove("open");
    }
    window._clientesClose = closeClientes;

    function setView(view) {
        if (!VIEWS.has(view)) return;
        const changed = state.view !== view;
        state.view = view;
        state.page = 1;
        document.querySelectorAll("#clientes-group .rail-step").forEach((s) => {
            s.classList.toggle("active", s.dataset.clientesView === view);
        });
        el("clientes-view-title").textContent = VIEW_TITLES[view];
        if (changed || !state.items.length) refresh();
    }

    // ---- fetch ----
    async function loadStats() {
        try {
            const r = await fetch("/api/dashboard/clientes/stats", { credentials: "same-origin" });
            if (!r.ok) return;
            const s = await r.json();
            el("clientes-total-count").textContent = String(s.total || 0);
            el("clientes-count-all").textContent = String(s.total || 0);
            el("clientes-count-complete").textContent = String(s.complete || 0);
            el("clientes-count-pending").textContent = String(s.pending || 0);
        } catch (_) { /* silencia — endpoint pode estar fora */ }
    }

    async function loadList() {
        const p = new URLSearchParams();
        if (state.view !== "all") p.set("status", state.view);
        if (state.search) p.set("q", state.search);
        p.set("page", String(state.page));
        p.set("page_size", String(state.pageSize));
        const r = await fetch("/api/dashboard/clientes/list?" + p.toString(), { credentials: "same-origin" });
        if (!r.ok) return;
        const data = await r.json();
        state.items = data.items || [];
        state.total = data.total || 0;
        render();
    }

    async function refresh() {
        await Promise.all([loadStats(), loadList()]);
    }

    function render() {
        const tbody = el("clientes-table-body");
        if (!tbody) return;
        tbody.innerHTML = "";
        if (!state.items.length) {
            tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;padding:24px;color:var(--text-muted);">Nenhum cliente encontrado</td></tr>`;
            el("clientes-pagination-summary").textContent = "Página 1 de 1 (0 resultados)";
            el("clientes-view-meta").textContent = "0 cliente";
            return;
        }
        state.items.forEach((c) => {
            const nomeRaw = (c.nome || "").trim();
            const nomeBad = nomeRaw.toLowerCase() === "cliente";
            const nomeStyle = nomeBad ? ' style="color:#f87171;font-style:italic;"' : "";
            const cpfBad = !((c.cpf_cnpj || "").trim());
            const cpfStyle = cpfBad ? ' style="color:#f87171;"' : "";
            const tr = document.createElement("tr");
            tr.innerHTML = `
                <td${nomeStyle}>${escapeHtml(nomeRaw || "—")}</td>
                <td>${escapeHtml(c.celular || "—")}</td>
                <td${cpfStyle}>${escapeHtml(c.cpf_cnpj || "—")}</td>
                <td>${fmtDate(c.created_at)}</td>
                <td style="text-align:right;">
                    <button class="btn-history btn-rename"
                            data-cliente-id="${escapeHtml(c.id)}"
                            data-nome="${escapeHtml(nomeRaw)}"
                            title="Renomear">
                        <i class="fas fa-pen"></i>
                    </button>
                </td>
            `;
            tbody.appendChild(tr);
        });
        const totalPages = Math.max(1, Math.ceil(state.total / state.pageSize));
        el("clientes-pagination-summary").textContent =
            `Página ${state.page} de ${totalPages} (${state.total} resultados)`;
        el("clientes-view-meta").textContent =
            `${state.total} cliente${state.total === 1 ? "" : "s"}`;
    }

    // ---- rename modal ----
    function openRename(id, nome) {
        const ov = el("cliente-rename-overlay");
        const md = el("cliente-rename-modal");
        const inp = el("cliente-rename-input");
        const err = el("cliente-rename-error");
        const help = el("cliente-rename-help");
        if (!ov || !md || !inp) return;
        inp.value = nome || "";
        err.textContent = "";
        help.textContent = nome
            ? `Editar nome (atual: ${nome})`
            : "Cliente sem nome cadastrado";
        md.dataset.clienteId = id;
        ov.classList.add("open");
        md.classList.add("open");
        setTimeout(() => inp.focus(), 50);
    }
    function closeRename() {
        el("cliente-rename-overlay")?.classList.remove("open");
        el("cliente-rename-modal")?.classList.remove("open");
    }
    async function submitRename() {
        const md = el("cliente-rename-modal");
        const inp = el("cliente-rename-input");
        const err = el("cliente-rename-error");
        const id = md?.dataset.clienteId;
        const nome = (inp?.value || "").trim();
        if (!id) return;
        if (!nome) { err.textContent = "Nome não pode ser vazio."; return; }
        try {
            const r = await fetch(`/api/dashboard/clientes/${id}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                credentials: "same-origin",
                body: JSON.stringify({ nome }),
            });
            if (!r.ok) {
                const data = await r.json().catch(() => ({}));
                err.textContent = data.detail || "Erro ao salvar.";
                return;
            }
            closeRename();
            await refresh();
        } catch (_) {
            err.textContent = "Falha de rede.";
        }
    }

    // ---- handlers ----
    document.addEventListener("DOMContentLoaded", function () {
        // Header do dropdown — abre/fecha a seção
        el("clientes-group-header")?.addEventListener("click", () => {
            if (state.open) closeClientes();
            else openClientes("all");
        });

        // Sub-itens (Todos / A revisar)
        document.querySelectorAll('#clientes-group .rail-step[data-clientes-view]').forEach((step) => {
            step.addEventListener("click", (e) => {
                e.stopPropagation();
                openClientes(step.dataset.clientesView);
            });
        });

        // Busca (debounce 200ms)
        let searchTimer;
        el("clientes-search")?.addEventListener("input", (e) => {
            clearTimeout(searchTimer);
            const v = e.target.value.trim();
            searchTimer = setTimeout(() => {
                state.search = v;
                state.page = 1;
                loadList();
            }, 200);
        });

        // Paginação
        el("clientes-page-prev")?.addEventListener("click", () => {
            if (state.page > 1) { state.page -= 1; loadList(); }
        });
        el("clientes-page-next")?.addEventListener("click", () => {
            const totalPages = Math.max(1, Math.ceil(state.total / state.pageSize));
            if (state.page < totalPages) { state.page += 1; loadList(); }
        });

        // Rename (delegado — botões dentro de tbody renderizado dinamicamente)
        document.addEventListener("click", (e) => {
            const btn = e.target.closest(".btn-rename[data-cliente-id]");
            if (!btn) return;
            openRename(btn.dataset.clienteId, btn.dataset.nome || "");
        });

        // Modal
        el("cliente-rename-cancel")?.addEventListener("click", closeRename);
        el("cliente-rename-overlay")?.addEventListener("click", closeRename);
        el("cliente-rename-ok")?.addEventListener("click", submitRename);
        el("cliente-rename-input")?.addEventListener("keydown", (e) => {
            if (e.key === "Enter") submitRename();
            if (e.key === "Escape") closeRename();
        });

        // Contadores do dropdown carregados ao iniciar pra ficar tudo amarrado
        loadStats();
    });
})();
