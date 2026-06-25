// Render compartilhado dos cards de acúmulo Bernardo.
// Usado pela página standalone /bernardo e pela view #section-bernardo do dashboard.
// Strings de usuário escapadas antes do DOM.
(function () {
  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, c => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
    ));
  }

  const STATUS_MSG = {
    no_votes: "Sem votos pra fechar.",
    not_session: "Enquete não pertence à sessão.",
    not_found: "Enquete não encontrada.",
    no_product: "Enquete sem produto associado.",
    rpc_error: "Falha ao fechar o pacote (tente de novo).",
  };

  async function render(containerEl, session) {
    if (!containerEl) return;
    containerEl.className = "bn-empty";
    containerEl.textContent = "Carregando…";
    let data;
    try {
      const res = await fetch(`/api/bernardo/sessions/${session}`, { credentials: "same-origin" });
      data = await res.json();
    } catch (e) {
      containerEl.textContent = "Erro ao carregar.";
      return;
    }
    if (!data.enquetes || !data.enquetes.length) {
      containerEl.textContent = "Nenhuma enquete Bernardo ativa.";
      return;
    }
    containerEl.className = "";
    containerEl.innerHTML = "";
    for (const enq of data.enquetes) {
      const parts = (enq.participants || [])
        .map(p => `${escapeHtml(p.nome)}: ${escapeHtml(String(p.qty))}`)
        .join(" · ") || "—";
      const card = document.createElement("div");
      card.className = "bn-card";
      card.innerHTML =
        `<div class="bn-card-title">${escapeHtml(enq.titulo)}</div>` +
        `<div class="bn-card-meta">Acúmulo: <b>${escapeHtml(String(enq.total_qty))}</b> peças · ` +
          `${escapeHtml(String(enq.participants_count))} cliente(s)</div>` +
        `<div class="bn-card-meta">${parts}</div>`;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "bn-btn";
      btn.textContent = "Fechar pacote";
      btn.disabled = (enq.total_qty || 0) <= 0;
      btn.onclick = async () => {
        btn.disabled = true;
        try {
          const r = await fetch(`/api/bernardo/sessions/${session}/close`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            credentials: "same-origin",
            body: JSON.stringify({ enquete_id: enq.enquete_id }),
          });
          const out = await r.json();
          if (out.status === "ok") {
            render(containerEl, session);
          } else {
            alert(STATUS_MSG[out.status] || ("Não foi possível fechar: " + (out.status || "erro")));
            btn.disabled = false;
          }
        } catch (e) {
          alert("Erro: " + e.message);
          btn.disabled = false;
        }
      };
      card.appendChild(btn);
      containerEl.appendChild(card);
    }
  }

  window.BernardoCards = { render };
})();
