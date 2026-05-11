// Modal compartilhado entre os 5 mockups. Abre drill-down de um pacote.
//   RaylookModal.open(pacoteId)    → abre modal e carrega dados
//   RaylookModal.close()           → fecha
//   RaylookModal.toast(msg, kind)  → notificação rápida

const RaylookModal = (() => {
    let overlay = null;
    let toastEl = null;

    function ensureDom() {
        if (!overlay) {
            overlay = document.createElement("div");
            overlay.className = "rl-modal-overlay";
            overlay.innerHTML = `<div class="rl-modal" role="dialog" aria-modal="true"></div>`;
            document.body.appendChild(overlay);
            overlay.addEventListener("click", (e) => {
                if (e.target === overlay) close();
            });
            document.addEventListener("keydown", (e) => {
                if (e.key === "Escape") close();
            });
        }
        if (!toastEl) {
            toastEl = document.createElement("div");
            toastEl.className = "rl-toast";
            document.body.appendChild(toastEl);
        }
    }

    async function open(pacoteId) {
        ensureDom();
        overlay.classList.add("open");
        const body = overlay.querySelector(".rl-modal");
        body.innerHTML = `<div class="rl-modal-loading">Carregando detalhes…</div>`;
        try {
            const resp = await fetch(`/api/mockups/packages/${pacoteId}`, { credentials: "include" });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const data = await resp.json();
            body.innerHTML = render(data);
            wire(body, pacoteId, data);
        } catch (err) {
            body.innerHTML = `<button class="rl-modal-close" data-close>&times;</button>
                              <div class="rl-modal-error">Falha ao carregar: ${escape(err.message)}</div>`;
            body.querySelector("[data-close]")?.addEventListener("click", close);
        }
    }

    function close() {
        if (overlay) overlay.classList.remove("open");
    }

    function toast(msg, kind = "") {
        ensureDom();
        toastEl.textContent = msg;
        toastEl.className = `rl-toast show ${kind}`;
        clearTimeout(toast._t);
        toast._t = setTimeout(() => {
            toastEl.classList.remove("show");
        }, 2200);
    }

    let confirmDialog = null;
    function confirmModal(message, opts = {}) {
        const { okLabel = "Confirmar", cancelLabel = "Cancelar", danger = false } = opts;
        if (!confirmDialog) {
            confirmDialog = document.createElement("div");
            confirmDialog.className = "rl-confirm-overlay";
            confirmDialog.innerHTML = `
                <div class="rl-confirm" role="alertdialog" aria-modal="true">
                    <div class="rl-confirm-msg"></div>
                    <div class="rl-confirm-actions">
                        <button class="rl-confirm-cancel" data-no></button>
                        <button class="rl-confirm-ok" data-yes></button>
                    </div>
                </div>`;
            document.body.appendChild(confirmDialog);
        }
        confirmDialog.querySelector(".rl-confirm-msg").textContent = message;
        const okBtn = confirmDialog.querySelector("[data-yes]");
        const cancelBtn = confirmDialog.querySelector("[data-no]");
        okBtn.textContent = okLabel;
        cancelBtn.textContent = cancelLabel;
        okBtn.classList.toggle("danger", !!danger);

        return new Promise(resolve => {
            const cleanup = (val) => {
                confirmDialog.classList.remove("open");
                okBtn.onclick = null;
                cancelBtn.onclick = null;
                confirmDialog.onclick = null;
                document.removeEventListener("keydown", onKey);
                resolve(val);
            };
            const onKey = (e) => {
                if (e.key === "Escape") cleanup(false);
                else if (e.key === "Enter") cleanup(true);
            };
            okBtn.onclick = () => cleanup(true);
            cancelBtn.onclick = () => cleanup(false);
            confirmDialog.onclick = (e) => { if (e.target === confirmDialog) cleanup(false); };
            document.addEventListener("keydown", onKey);
            confirmDialog.classList.add("open");
            cancelBtn.focus();
        });
    }

    function render(data) {
        const L = window.RaylookDashboard;
        const canEdit = data.state !== "enviado" && data.state !== "cancelled";
        const canAdd = canEdit && data.state === "aberto";
        const canSwap = canEdit && data.state !== "aberto";

        const isConfirmado = data.state === "confirmado";
        const rows = data.clientes.map(cli => {
            let pill;
            if (cli.is_voter_only) {
                pill = `<span class="rl-pay-pill" style="background:rgba(250,204,21,0.16);color:var(--step-aberto);">candidato</span>`;
            } else if (cli.pagamento_status) {
                pill = `<span class="rl-pay-pill ${cli.pagamento_status}">${L.payLabel(cli.pagamento_status)}</span>`;
            } else {
                pill = "—";
            }
            const amount = cli.is_voter_only
                ? `<span style="color:var(--text-muted);">est. ${L.moneyFull(cli.total_amount)}</span>`
                : L.moneyFull(cli.total_amount);
            const canMarkPaid = isConfirmado && cli.pagamento_status && cli.pagamento_status !== "paid";
            const markPaidBtn = canMarkPaid
                ? `<button class="rl-btn-mark-paid" data-mark-paid="${cli.cliente_id}" data-cliente-nome="${escape(cli.nome || '')}" data-cliente-celular="${escape(cli.celular || '')}">Marcar pago</button>`
                : "";
            const actions = canEdit ? `
                <div class="rl-row-actions">
                    ${markPaidBtn}
                    ${canSwap ? `<button class="rl-btn-swap" data-swap="${cli.cliente_id}" data-cliente-nome="${escape(cli.nome || '')}" data-cliente-celular="${escape(cli.celular || '')}">Substituir</button>` : ""}
                    <button class="danger" title="Remover do pacote" data-remove="${cli.cliente_id}" data-cliente-nome="${escape(cli.nome || '')}" data-cliente-celular="${escape(cli.celular || '')}">Remover</button>
                </div>` : "";
            return `<tr>
                <td>
                    <div class="name">${escape(cli.nome || "?")}</div>
                    <div class="phone">${escape(cli.celular || "")}</div>
                </td>
                <td class="num">${cli.qty}</td>
                <td class="num">${amount}</td>
                <td>${pill}</td>
                <td class="num">${actions}</td>
            </tr>`;
        }).join("") || `<tr><td colspan="5" style="text-align:center;color:var(--text-muted);font-style:italic;">Sem clientes associados ainda.</td></tr>`;

        const clientesSectionTitle = data.clientes.length && data.clientes[0].is_voter_only
            ? "Candidatos na enquete (ainda não consolidados)"
            : "Clientes no pacote";

        const addBlock = canAdd
            ? `<button class="rl-add-client-btn" data-add-client>+ Adicionar cliente</button>
               <div class="rl-client-form" data-add-form hidden>
                   <label>Cliente</label>
                   <select data-add-select><option value="">Carregando…</option></select>
                   <label>Peças</label>
                   <select data-add-qty>
                       <option value="3">3</option>
                       <option value="6" selected>6</option>
                       <option value="9">9</option>
                       <option value="12">12</option>
                   </select>
                   <button class="confirm" data-add-confirm>Adicionar</button>
                   <button class="cancel" data-add-cancel>Cancelar</button>
               </div>`
            : "";

        const tl = data.timeline.map(t => {
            const when = new Date(t.at).toLocaleString("pt-BR", {
                day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
            });
            return `<li>
                <strong>${L.STATE_LABELS[t.state] || t.state}</strong>
                <span class="rl-tl-time">${when}</span>
                <span class="rl-tl-note">${escape(t.note || "")}</span>
            </li>`;
        }).join("") || `<li style="list-style:none;padding:0;color:var(--text-muted);font-style:italic;">Sem transições registradas.</li>`;

        const prod = data.produto || {};
        const title = prod.nome
            ? `${escape(prod.nome)} — seq #${data.sequence_no ?? "?"}`
            : `Pacote #${data.sequence_no ?? "?"}`;
        const totalValue = data.clientes.reduce((a, c) => a + (c.total_amount || 0), 0);

        const isFinal = data.state === "enviado" || data.state === "cancelled";

        return `
            <button class="rl-modal-close" data-close>&times;</button>
            <h2>${title}</h2>
            <div class="rl-sub">
                ${L.pill(data.state)}
                <span>${data.total_qty ?? data.capacidade_total} / ${data.capacidade_total} peças</span>
                ${data.enquete?.external_poll_id ? `<span>· ${escape(data.enquete.external_poll_id)}</span>` : ""}
            </div>

            <div class="rl-section">
                <div class="rl-metrics">
                    <div class="rl-metric"><div class="l">Clientes</div><div class="v">${data.clientes.length}</div></div>
                    <div class="rl-metric"><div class="l">Peças</div><div class="v">${Math.min(data.total_qty || 0, data.capacidade_total) || data.capacidade_total}</div></div>
                    <div class="rl-metric"><div class="l">Valor total</div><div class="v money">${L.moneyFull(totalValue)}</div></div>
                    <div class="rl-metric"><div class="l">PDF etiqueta</div><div class="v" style="font-size:12px;">${data.pdf_file_name ? "✓ gerado" : "—"}</div></div>
                </div>
            </div>

            <div class="rl-section">
                <h3>${clientesSectionTitle}</h3>
                <table class="rl-table">
                    <thead><tr><th>Nome</th><th class="num">Peças</th><th class="num">Valor</th><th>Pagamento</th><th></th></tr></thead>
                    <tbody>${rows}</tbody>
                </table>
                ${addBlock}
            </div>

            <div class="rl-section">
                <h3>Histórico</h3>
                <ul class="rl-timeline">${tl}</ul>
            </div>

            <div class="rl-actions">
                <button class="rl-btn-primary" data-action="advance" ${isFinal ? "disabled" : ""}>
                    ⏭️ Avançar etapa
                </button>
                ${!isFinal ? `<button class="rl-btn-danger" data-action="cancel">Cancelar pacote</button>` : ""}
                <button class="rl-btn-ghost" data-close>Fechar</button>
            </div>
        `;
    }

    function wire(body, pacoteId, data) {
        body.querySelectorAll("[data-close]").forEach(el => el.addEventListener("click", close));

        body.querySelectorAll("[data-action]").forEach(btn => {
            btn.addEventListener("click", async () => {
                const action = btn.dataset.action;
                if (action === "cancel" && !await confirmModal("Cancelar esse pacote?", { okLabel: "Cancelar pacote", danger: true })) return;
                btn.disabled = true;
                const old = btn.textContent;
                btn.textContent = "…";
                try {
                    const resp = await fetch(`/api/mockups/packages/${pacoteId}/${action}`,
                        { method: "POST", credentials: "include" });
                    if (!resp.ok) {
                        const err = await resp.json().catch(() => ({ detail: `HTTP ${resp.status}` }));
                        throw new Error(err.detail || "Falha");
                    }
                    const payload = await resp.json();
                    toast(messageFor(action, payload), "success");
                    close();
                    if (window.RaylookReload) await window.RaylookReload();
                } catch (err) {
                    toast(`Erro: ${err.message}`, "error");
                    btn.disabled = false;
                    btn.textContent = old;
                }
            });
        });

        // Marcar pagamento individual como pago
        body.querySelectorAll("[data-mark-paid]").forEach(btn => {
            btn.addEventListener("click", async () => {
                const label = clienteLabel(btn.dataset.clienteNome, btn.dataset.clienteCelular);
                if (!await confirmModal(`Marcar pagamento de ${label} como pago?`, { okLabel: "Marcar pago" })) return;
                btn.disabled = true;
                await clientAction(pacoteId, `clients/${btn.dataset.markPaid}/mark-paid`, "POST");
            });
        });

        // Remover cliente
        body.querySelectorAll("[data-remove]").forEach(btn => {
            btn.addEventListener("click", async () => {
                const label = clienteLabel(btn.dataset.clienteNome, btn.dataset.clienteCelular);
                if (!await confirmModal(`Remover ${label} do pacote?`, { okLabel: "Remover", danger: true })) return;
                btn.disabled = true;
                await clientAction(pacoteId, `clients/${btn.dataset.remove}`, "DELETE");
            });
        });

        // Substituir cliente por outro voto da mesma enquete
        body.querySelectorAll("[data-swap]").forEach(btn => {
            btn.addEventListener("click", () =>
                openSwapForm(body, pacoteId, btn.dataset.swap, btn.dataset.clienteNome, btn.dataset.clienteCelular));
        });

        // Adicionar cliente
        const addBtn = body.querySelector("[data-add-client]");
        const addForm = body.querySelector("[data-add-form]");
        if (addBtn && addForm) {
            addBtn.addEventListener("click", async () => {
                addBtn.hidden = true;
                addForm.hidden = false;
                await populateClienteSelect(addForm.querySelector("[data-add-select]"), pacoteId);
            });
            addForm.querySelector("[data-add-cancel]").addEventListener("click", () => {
                addForm.hidden = true; addBtn.hidden = false;
            });
            addForm.querySelector("[data-add-confirm]").addEventListener("click", async () => {
                const cliente_id = addForm.querySelector("[data-add-select]").value;
                const qty = parseInt(addForm.querySelector("[data-add-qty]").value, 10);
                if (!cliente_id) { toast("Selecione um cliente", "error"); return; }
                await clientAction(pacoteId, "clients", "POST", { cliente_id, qty });
            });
        }
    }

    async function populateClienteSelect(selectEl, pacoteId) {
        try {
            const resp = await fetch(`/api/mockups/clientes?exclude_pacote=${pacoteId}`,
                { credentials: "include" });
            const list = await resp.json();
            selectEl.innerHTML = `<option value="">— escolher —</option>` +
                list.map(c => `<option value="${c.id}">${escape(c.nome || "?")} (${escape(c.celular || "")})</option>`).join("");
        } catch (e) {
            selectEl.innerHTML = `<option value="">Erro ao carregar</option>`;
        }
    }

    async function openSwapForm(body, pacoteId, currentClienteId, currentNome, currentCelular) {
        const existing = body.querySelector("[data-swap-form]");
        if (existing) existing.remove();
        const currentLabel = clienteLabel(currentNome, currentCelular);
        const form = document.createElement("div");
        form.className = "rl-client-form rl-swap-form";
        form.dataset.swapForm = "1";
        form.innerHTML = `
            <div class="rl-swap-head">
                Substituir <strong>${escape(currentLabel)}</strong> por
                outro voto da mesma enquete.
            </div>
            <div data-swap-list><div class="rl-swap-loading">Buscando candidatos…</div></div>
            <div class="rl-swap-actions">
                <button class="cancel" data-swap-cancel>Cancelar</button>
            </div>
        `;
        const section = body.querySelector(".rl-section:nth-of-type(2)") || body.querySelector(".rl-section");
        section.appendChild(form);
        form.querySelector("[data-swap-cancel]").addEventListener("click", () => form.remove());

        const list = form.querySelector("[data-swap-list]");
        try {
            const resp = await fetch(`/api/mockups/packages/${pacoteId}/swap-candidates/${currentClienteId}`,
                { credentials: "include" });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const candidates = await resp.json();
            if (!candidates.length) {
                list.innerHTML = `<div class="rl-swap-empty">
                    Nenhum outro cliente votou nessa enquete com a mesma quantidade.
                </div>`;
                return;
            }
            list.innerHTML = candidates.map(c => `
                <button class="rl-swap-candidate" data-swap-pick="${c.id}">
                    <div class="rl-swap-candidate-info">
                        <div class="name">${escape(c.nome || "?")}</div>
                        <div class="phone">${escape(c.celular || "")}</div>
                    </div>
                    <div class="rl-swap-candidate-qty">${c.qty} peças</div>
                </button>
            `).join("");
            list.querySelectorAll("[data-swap-pick]").forEach(btn =>
                btn.addEventListener("click", async () => {
                    const newLabel = clienteLabel(
                        btn.querySelector(".name").textContent,
                        btn.querySelector(".phone").textContent,
                    );
                    if (!await confirmModal(`Substituir ${currentLabel} por ${newLabel}?`, { okLabel: "Substituir" })) return;
                    list.querySelectorAll("button").forEach(b => b.disabled = true);
                    await clientAction(pacoteId, `clients/${currentClienteId}`, "PATCH",
                                       { new_cliente_id: btn.dataset.swapPick });
                })
            );
        } catch (e) {
            list.innerHTML = `<div class="rl-swap-empty">Erro: ${escape(e.message)}</div>`;
        }
    }

    async function clientAction(pacoteId, path, method, body) {
        try {
            const opts = { method, credentials: "include", headers: { "Content-Type": "application/json" } };
            if (body) opts.body = JSON.stringify(body);
            const resp = await fetch(`/api/mockups/packages/${pacoteId}/${path}`, opts);
            if (!resp.ok) {
                const e = await resp.json().catch(() => ({ detail: `HTTP ${resp.status}` }));
                throw new Error(e.detail || "Falha");
            }
            const payload = await resp.json();
            toast(clientActionMessage(payload), "success");
            // recarrega modal e lista-raiz
            await open(pacoteId);
            if (window.RaylookReload) window.RaylookReload();
        } catch (err) {
            toast(`Erro: ${err.message}`, "error");
        }
    }

    function clientActionMessage(payload) {
        if (payload.action === "voto_added") return "Cliente adicionado ao pacote";
        if (payload.action === "voto_removed") return "Cliente removido";
        if (payload.action === "pacote_cliente_removed") return "Cliente removido do pacote";
        if (payload.action === "swapped") return "Cliente trocado";
        if (payload.action === "client_marked_paid") return "Pagamento marcado como pago";
        return "OK";
    }

    function messageFor(action, payload) {
        if (action === "advance") return `Avançou para "${payload.new_state}"`;
        if (action === "regress") return `Voltou para "${payload.new_state}"`;
        if (action === "cancel") return "Pacote cancelado";
        return "Ação realizada";
    }

    function clienteLabel(nome, celular) {
        const n = (nome || "").trim() || "cliente";
        const c = (celular || "").trim();
        return c ? `${n} (${c})` : n;
    }

    function escape(s) {
        return String(s == null ? "" : s)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;");
    }

    return { open, close, toast, confirm: confirmModal };
})();

window.RaylookModal = RaylookModal;
