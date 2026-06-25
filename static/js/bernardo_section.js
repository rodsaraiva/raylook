// Sessão Bernardo integrada ao dashboard /. View-toggling espelha enquetes.js.
// Render delegado ao módulo compartilhado BernardoCards.
(function () {
  const SESSION = "Bernardo";
  const state = { open: false };

  function openBernardo() {
    state.open = true;
    window._bernardoOpen = true;
    document.getElementById("packages-area")?.classList.add("retracted");
    document.getElementById("section-bernardo")?.classList.add("active");
    document.getElementById("section-enquetes")?.classList.remove("active");
    document.getElementById("section-finance")?.classList.remove("active");
    document.getElementById("section-clientes")?.classList.remove("active");
    document.getElementById("enquetes-group")?.classList.remove("open");
    document.getElementById("fin-group")?.classList.remove("open");
    document.getElementById("clientes-group")?.classList.remove("open");
    window._enquetesOpen = false;
    window._financeOpen = false;
    window._clientesOpen = false;
    window._railCollapseGroups?.();
    document.querySelector('[data-group="bernardo"]')?.classList.add("open");
    window.BernardoCards?.render(document.getElementById("bernardo-cards"), SESSION);
  }

  function closeBernardo() {
    state.open = false;
    window._bernardoOpen = false;
    document.getElementById("section-bernardo")?.classList.remove("active");
    document.querySelector('[data-group="bernardo"]')?.classList.remove("open");
    if (!window._financeOpen && !window._clientesOpen && !window._enquetesOpen) {
      document.getElementById("packages-area")?.classList.remove("retracted");
    }
  }

  function toggleBernardo() {
    if (state.open) closeBernardo(); else openBernardo();
  }

  window._bernardoClose = closeBernardo;
  window._bernardoToggle = toggleBernardo;
})();
