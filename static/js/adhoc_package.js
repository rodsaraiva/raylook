(function () {
  'use strict';

  const CONTAINERS = {
    PRODUCT: 'adhoc-step-product',
    VOTES: 'adhoc-step-votes',
    PREVIEW: 'adhoc-step-preview',
  };
  const OLD_STEPS = [
    'create-package-step-choose',
    'create-package-step-polls',
    'create-package-step-votes',
    'create-package-step-preview',
  ];
  const MODAL_BUTTONS = [
    'createPackageBack',
    'createPackageSave',
    'createPackageConfirm',
    'createPackageCancel',
  ];

  const state = {
    product: { name: '', unit_price: 0, drive_file_id: null, full_url: null },
    votes: [],
    preview: null,
  };

  function hideOldSteps() {
    OLD_STEPS.forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.hidden = true;
    });
    MODAL_BUTTONS.forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.hidden = true;
    });
  }

  function hideAdhocSteps() {
    Object.values(CONTAINERS).forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.hidden = true;
    });
  }

  function showAdhoc(containerId) {
    hideOldSteps();
    hideAdhocSteps();
    const el = document.getElementById(containerId);
    if (el) el.hidden = false;
  }

  function closeModal() {
    const modal = document.getElementById('createPackageModal');
    if (modal) modal.hidden = true;
    // Reset state
    state.product = { name: '', unit_price: 0, drive_file_id: null, full_url: null };
    state.votes = [];
    state.preview = null;
    // Restaura botões pra próxima abertura
    MODAL_BUTTONS.forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.hidden = false;
    });
    hideAdhocSteps();
  }

  document.addEventListener('adhoc:start', () => {
    renderProductStep();
  });

  function renderProductStep() {
    const root = document.getElementById(CONTAINERS.PRODUCT);
    if (!root) return;
    root.innerHTML = `
      <h3 style="margin-top:0;">Produto novo</h3>
      <label style="display:block;margin-bottom:0.75rem;">
        <span style="display:block;font-size:0.85rem;margin-bottom:0.25rem;">Nome</span>
        <input id="adhoc-product-name" maxlength="120" required style="width:100%;padding:0.5rem;">
      </label>
      <label style="display:block;margin-bottom:0.75rem;">
        <span style="display:block;font-size:0.85rem;margin-bottom:0.25rem;">Preço por peça (sem comissão)</span>
        <input id="adhoc-product-price" type="number" step="0.01" min="0.01" required style="width:100%;padding:0.5rem;">
      </label>
      <div id="adhoc-price-calc" style="font-size:0.85rem;color:#666;margin-bottom:0.75rem;"></div>
      <label style="display:block;margin-bottom:0.75rem;">
        <span style="display:block;font-size:0.85rem;margin-bottom:0.25rem;">Imagem</span>
        <input id="adhoc-product-image" type="file" accept="image/jpeg,image/png,image/webp" required>
      </label>
      <div id="adhoc-image-preview" style="margin-bottom:1rem;"></div>
      <div style="display:flex;gap:0.5rem;justify-content:flex-end;">
        <button type="button" class="btn-modal btn-cancel" id="adhoc-cancel">Cancelar</button>
        <button type="button" class="btn-modal btn-confirm" id="adhoc-next-to-votes" disabled>Próximo</button>
      </div>
    `;
    showAdhoc(CONTAINERS.PRODUCT);

    const nameEl = document.getElementById('adhoc-product-name');
    const priceEl = document.getElementById('adhoc-product-price');
    const imgEl = document.getElementById('adhoc-product-image');
    const calcEl = document.getElementById('adhoc-price-calc');
    const nextEl = document.getElementById('adhoc-next-to-votes');

    // Restaura valores do state se voltar do step 2
    if (state.product.name) nameEl.value = state.product.name;
    if (state.product.unit_price) priceEl.value = state.product.unit_price;
    if (state.product.full_url) {
      document.getElementById('adhoc-image-preview').innerHTML =
        `<img src="${state.product.full_url}" alt="" style="max-width:200px"> ✓`;
    }

    function recalc() {
      const price = parseFloat(priceEl.value || '0');
      if (price > 0) {
        const subtotal = price * 24;
        const commission = subtotal * 0.13;
        calcEl.textContent =
          `Total do pacote: R$ ${subtotal.toFixed(2)} (24 × ${price.toFixed(2)}) ` +
          `+ comissão 13% = R$ ${(subtotal + commission).toFixed(2)}`;
      } else {
        calcEl.textContent = '';
      }
      nextEl.disabled = !(
        nameEl.value.trim().length >= 3 &&
        price > 0 &&
        state.product.drive_file_id
      );
    }

    nameEl.addEventListener('input', () => { state.product.name = nameEl.value.trim(); recalc(); });
    priceEl.addEventListener('input', () => { state.product.unit_price = parseFloat(priceEl.value || '0'); recalc(); });

    imgEl.addEventListener('change', async () => {
      const file = imgEl.files && imgEl.files[0];
      if (!file) return;
      const previewEl = document.getElementById('adhoc-image-preview');
      previewEl.innerHTML = '<span style="color:#666;">Enviando…</span>';
      const fd = new FormData();
      fd.append('image', file);
      try {
        const resp = await fetch('/api/packages/adhoc/upload-image', { method: 'POST', body: fd });
        if (!resp.ok) {
          const err = await resp.json().catch(() => ({}));
          throw new Error(err.detail || `HTTP ${resp.status}`);
        }
        const data = await resp.json();
        state.product.drive_file_id = data.drive_file_id;
        state.product.full_url = data.full_url;
        previewEl.innerHTML = `<img src="${data.full_url}" alt="" style="max-width:200px"> ✓`;
        recalc();
      } catch (err) {
        previewEl.innerHTML =
          `<span style="color:#c00;">Falha: ${err.message}.</span>`;
        state.product.drive_file_id = null;
        state.product.full_url = null;
        recalc();
      }
    });

    document.getElementById('adhoc-cancel').addEventListener('click', closeModal);
    document.getElementById('adhoc-next-to-votes').addEventListener('click', renderVotesStep);

    recalc();
  }

  function renderVotesStep() {
    const root = document.getElementById(CONTAINERS.VOTES);
    if (!root) return;
    root.innerHTML = `
      <h3 style="margin-top:0;">Clientes</h3>
      <div id="adhoc-votes-list"></div>
      <button type="button" id="adhoc-add-vote" style="margin-top:0.5rem;">+ Adicionar cliente</button>
      <div id="adhoc-votes-counter" style="margin-top:0.75rem;font-weight:600;"></div>
      <div style="display:flex;gap:0.5rem;justify-content:flex-end;margin-top:1rem;">
        <button type="button" class="btn-modal btn-cancel" id="adhoc-back-to-product">Voltar</button>
        <button type="button" class="btn-modal btn-confirm" id="adhoc-next-to-preview" disabled>Revisar</button>
      </div>
    `;
    showAdhoc(CONTAINERS.VOTES);

    if (state.votes.length === 0) {
      state.votes.push({ phone: '', qty: 0, customer_id: null, name: '' });
    }
    renderVoteRows();

    document.getElementById('adhoc-add-vote').addEventListener('click', () => {
      state.votes.push({ phone: '', qty: 0, customer_id: null, name: '' });
      renderVoteRows();
    });
    document.getElementById('adhoc-back-to-product').addEventListener('click', renderProductStep);
    document.getElementById('adhoc-next-to-preview').addEventListener('click', renderPreviewStep);
  }

  function renderVoteRows() {
    const list = document.getElementById('adhoc-votes-list');
    if (!list) return;
    list.innerHTML = '';
    state.votes.forEach((v, i) => {
      const row = document.createElement('div');
      row.className = 'adhoc-vote-row';
      row.style.cssText = 'display:flex;gap:0.5rem;margin-bottom:0.5rem;align-items:flex-start;';
      row.innerHTML = `
        <div style="position:relative;flex:1;">
          <input class="adhoc-vote-search" data-i="${i}" placeholder="Nome ou telefone"
                 value="${escapeHtml(v.name || v.phone || '')}" autocomplete="off" style="width:100%;padding:0.4rem;">
          <div class="adhoc-autocomplete-results" data-i="${i}"
               style="position:absolute;top:100%;left:0;right:0;background:#fff;border:1px solid #ddd;max-height:200px;overflow-y:auto;z-index:10;display:none;"></div>
        </div>
        <input class="adhoc-vote-qty" data-i="${i}" type="number" min="1" max="24"
               value="${v.qty || ''}" placeholder="qty" style="width:80px;padding:0.4rem;">
        <button type="button" class="adhoc-vote-remove" data-i="${i}" style="padding:0.4rem 0.6rem;">×</button>
      `;
      list.appendChild(row);
    });
    wireVoteRows();
    updateCounter();
  }

  function escapeHtml(s) {
    return (s || '').toString()
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function wireVoteRows() {
    document.querySelectorAll('.adhoc-vote-search').forEach((el) => {
      el.addEventListener('input', debounce(async (e) => {
        const i = Number(e.target.dataset.i);
        const q = e.target.value.trim();
        const resultsEl = document.querySelector(`.adhoc-autocomplete-results[data-i="${i}"]`);
        if (!resultsEl) return;
        if (q.length < 2) {
          resultsEl.innerHTML = '';
          resultsEl.style.display = 'none';
          // Atualiza phone/name do state mesmo se incompleto
          state.votes[i].phone = (q.match(/\d/g) || []).length >= 10 ? q.replace(/\D/g,'') : state.votes[i].phone;
          state.votes[i].name = !/^\d+$/.test(q) ? q : state.votes[i].name;
          return;
        }
        try {
          const resp = await fetch(`/api/customers/search?q=${encodeURIComponent(q)}`);
          const { results } = await resp.json();
          const picks = (results || []).map((r) =>
            `<button type="button" class="adhoc-pick" data-i="${i}" data-phone="${escapeHtml(r.phone)}" data-name="${escapeHtml(r.name)}" style="display:block;width:100%;text-align:left;padding:0.4rem;border:none;background:#fff;cursor:pointer;">${escapeHtml(r.name)} — ${escapeHtml(r.phone)}</button>`
          ).join('');
          const newOpt = `<button type="button" class="adhoc-pick-new" data-i="${i}" data-raw="${escapeHtml(q)}" style="display:block;width:100%;text-align:left;padding:0.4rem;border:none;background:#e8f5e9;cursor:pointer;">+ Usar "${escapeHtml(q)}" como novo</button>`;
          resultsEl.innerHTML = picks + newOpt;
          resultsEl.style.display = 'block';
        } catch (err) {
          resultsEl.innerHTML = `<div style="padding:0.4rem;color:#c00;">Erro na busca</div>`;
          resultsEl.style.display = 'block';
        }
      }, 250));
    });

    document.querySelectorAll('.adhoc-vote-qty').forEach((el) => {
      el.addEventListener('input', (e) => {
        const i = Number(e.target.dataset.i);
        state.votes[i].qty = parseInt(e.target.value || '0', 10);
        updateCounter();
      });
    });

    document.querySelectorAll('.adhoc-vote-remove').forEach((el) => {
      el.addEventListener('click', (e) => {
        const i = Number(e.target.dataset.i);
        state.votes.splice(i, 1);
        if (state.votes.length === 0) state.votes.push({ phone: '', qty: 0, customer_id: null, name: '' });
        renderVoteRows();
      });
    });
  }

  // Delegação pra clique nos itens do autocomplete (adicionar só uma vez)
  let _autocompleteDelegated = false;
  function ensureAutocompleteDelegation() {
    if (_autocompleteDelegated) return;
    _autocompleteDelegated = true;
    document.body.addEventListener('click', (e) => {
      const pick = e.target.closest('.adhoc-pick');
      if (pick) {
        const i = Number(pick.dataset.i);
        state.votes[i].phone = pick.dataset.phone;
        state.votes[i].name = pick.dataset.name;
        state.votes[i].customer_id = null;
        renderVoteRows();
        return;
      }
      const pickNew = e.target.closest('.adhoc-pick-new');
      if (pickNew) {
        const i = Number(pickNew.dataset.i);
        const raw = pickNew.dataset.raw || '';
        const digits = raw.replace(/\D/g, '');
        if (digits.length >= 10) {
          state.votes[i].phone = digits.startsWith('55') ? digits : ('55' + digits);
          state.votes[i].name = '';
        } else {
          state.votes[i].name = raw;
        }
        state.votes[i].customer_id = null;
        renderVoteRows();
      }
    });
  }
  ensureAutocompleteDelegation();

  function updateCounter() {
    const total = state.votes.reduce((s, v) => s + (parseInt(v.qty || 0, 10)), 0);
    const el = document.getElementById('adhoc-votes-counter');
    const nextBtn = document.getElementById('adhoc-next-to-preview');
    if (!el || !nextBtn) return;
    if (total < 24) {
      el.textContent = `Faltam ${24 - total} peças`;
      el.style.color = '#c60';
      nextBtn.disabled = true;
    } else if (total > 24) {
      el.textContent = `Ultrapassa em ${total - 24} peças`;
      el.style.color = '#c00';
      nextBtn.disabled = true;
    } else {
      const allValidPhone = state.votes.every((v) => /^55\d{10,11}$/.test((v.phone || '').replace(/\D/g, '')));
      el.textContent = allValidPhone ? `✓ Pacote fechado (24/24)` : `Pacote fechado, mas 1+ celular inválido`;
      el.style.color = allValidPhone ? '#080' : '#c00';
      nextBtn.disabled = !allValidPhone;
    }
  }

  function debounce(fn, ms) {
    let t;
    return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
  }

  async function renderPreviewStep() {
    const root = document.getElementById(CONTAINERS.PREVIEW);
    if (!root) return;
    root.innerHTML = '<p>Carregando preview…</p>';
    showAdhoc(CONTAINERS.PREVIEW);

    const payload = {
      product: {
        name: state.product.name,
        unit_price: state.product.unit_price,
        image: { drive_file_id: state.product.drive_file_id },
      },
      votes: state.votes.map((v) => ({
        phone: (v.phone || '').replace(/\D/g, ''),
        qty: v.qty,
        customer_id: v.customer_id,
        name: v.name,
      })),
    };

    try {
      const resp = await fetch('/api/packages/adhoc/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${resp.status}`);
      }
      state.preview = await resp.json();
    } catch (err) {
      root.innerHTML = `
        <p style="color:#c00;">Erro no preview: ${escapeHtml(err.message)}</p>
        <button type="button" class="btn-modal btn-cancel" id="adhoc-preview-back">Voltar</button>
      `;
      document.getElementById('adhoc-preview-back').addEventListener('click', renderVotesStep);
      return;
    }

    const p = state.preview;
    const votesHtml = p.votes_resolved.map((v) => `
      <li style="padding:0.25rem 0;">${escapeHtml(v.name || '(sem nome)')} — ${escapeHtml(v.phone)} — ${v.qty} peças — R$ ${(v.qty * p.product.unit_price).toFixed(2)}</li>
    `).join('');

    // Aviso de duplicidade: clientes já em outro pacote approved/closed ativo
    let dupHtml = '';
    const dupWarnings = Array.isArray(p.duplicate_warnings) ? p.duplicate_warnings : [];
    if (dupWarnings.length > 0) {
      const items = dupWarnings.map((w) => {
        const pkgsList = (w.existing_packages || []).map((ep) => {
          const statusLabel = ep.package_status === 'approved' ? 'confirmado' : 'fechado';
          return `<li>${escapeHtml(ep.poll_title || '(sem título)')} — <em>${statusLabel}</em> — ${ep.qty} pç</li>`;
        }).join('');
        return `
          <div style="margin-bottom:0.6rem;">
            <strong>${escapeHtml(w.name || '(sem nome)')}</strong> (${escapeHtml(w.phone)}) já está em:
            <ul style="margin:0.2rem 0 0 1rem;padding:0;">${pkgsList}</ul>
          </div>
        `;
      }).join('');
      dupHtml = `
        <div style="background:#fff8e1;border:1px solid #ffb300;border-radius:8px;padding:0.8rem 1rem;margin-bottom:1rem;">
          <div style="font-weight:700;color:#9a6700;margin-bottom:0.5rem;">
            ⚠️ Clientes já em outro pacote ativo
          </div>
          ${items}
          <div style="font-size:0.85rem;color:#6b5200;margin-top:0.4rem;">
            Se confirmar, o cliente ficará em <strong>dois pacotes</strong>. Volte e remova o cliente do pacote anterior, ou clique em <strong>"Criar mesmo assim"</strong> se a duplicata for intencional.
          </div>
        </div>
      `;
    }

    const confirmLabel = dupWarnings.length > 0 ? 'Criar mesmo assim' : 'Confirmar pacote';
    const confirmClass = dupWarnings.length > 0 ? 'btn-confirm-warn' : 'btn-confirm';

    root.innerHTML = `
      <h3 style="margin-top:0;">Revisar pacote</h3>
      ${dupHtml}
      <div style="display:flex;gap:1rem;align-items:flex-start;margin-bottom:1rem;">
        ${state.product.full_url ? `<img src="${state.product.full_url}" alt="" style="max-width:180px;">` : ''}
        <div>
          <div style="font-weight:600;font-size:1.1rem;">${escapeHtml(p.product.name)}</div>
          <div>Preço/peça: R$ ${p.product.unit_price.toFixed(2)}</div>
          <div>Subtotal (24 peças): R$ ${p.subtotal.toFixed(2)}</div>
          <div>Comissão ${p.commission_percent}%: R$ ${p.commission_amount.toFixed(2)}</div>
          <div style="font-weight:700;margin-top:0.4rem;">Total: R$ ${p.total_final.toFixed(2)}</div>
        </div>
      </div>
      <h4>Clientes</h4>
      <ul style="list-style:none;padding:0;margin:0;">${votesHtml}</ul>
      <div style="display:flex;gap:0.5rem;justify-content:flex-end;margin-top:1rem;">
        <button type="button" class="btn-modal btn-cancel" id="adhoc-preview-back">Voltar</button>
        <button type="button" class="btn-modal ${confirmClass}" id="adhoc-confirm" data-force="${dupWarnings.length > 0 ? '1' : '0'}">${confirmLabel}</button>
      </div>
    `;
    document.getElementById('adhoc-preview-back').addEventListener('click', renderVotesStep);
    document.getElementById('adhoc-confirm').addEventListener('click', confirmAdhoc);
  }

  async function confirmAdhoc() {
    const btn = document.getElementById('adhoc-confirm');
    if (!btn) return;
    const force = btn.getAttribute('data-force') === '1';
    const originalText = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Confirmando…';
    const payload = {
      product: {
        name: state.product.name,
        unit_price: state.product.unit_price,
        image: { drive_file_id: state.product.drive_file_id },
      },
      votes: state.votes.map((v) => ({
        phone: (v.phone || '').replace(/\D/g, ''),
        qty: v.qty,
        customer_id: v.customer_id,
        name: v.name,
      })),
      force,
    };
    try {
      const resp = await fetch('/api/packages/adhoc/confirm', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (resp.status === 409) {
        // Duplicatas detectadas no servidor (estado mudou entre preview e confirm).
        // Recarrega preview para mostrar o aviso amarelo atualizado.
        btn.disabled = false;
        btn.textContent = originalText;
        await renderPreviewStep();
        return;
      }
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${resp.status}`);
      }
      // Sucesso: fecha modal e recarrega pra atualizar listagem
      window.location.reload();
    } catch (err) {
      btn.disabled = false;
      btn.textContent = originalText;
      alert(`Falha ao confirmar: ${err.message}`);
    }
  }

  // Expõe funções globalmente pra facilitar debug — não é necessário em prod
  window.__adhoc = { state, renderProductStep, renderVotesStep, renderPreviewStep, closeModal };
})();
