// Página standalone /bernardo — usa o render compartilhado (BernardoCards).
(function () {
  document.addEventListener("DOMContentLoaded", () => {
    window.BernardoCards.render(document.getElementById("bernardo-cards"), "Bernardo");
  });
})();
