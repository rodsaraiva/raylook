// Filtros (Todos/Pendentes/Pagos) do portal de pedidos.
// Em arquivo próprio (não inline) porque a CSP bloqueia scripts inline, e
// carregado sempre — inclusive no preview read-only, que não puxa portal-sheet.js.
document.querySelectorAll('.filter-chip').forEach(function (chip) {
    chip.addEventListener('click', function () {
        document.querySelectorAll('.filter-chip').forEach(function (c) {
            c.classList.remove('active');
        });
        chip.classList.add('active');
        var filter = chip.dataset.filter;
        document.querySelectorAll('.order-card').forEach(function (card) {
            card.style.display =
                (filter === 'all' || card.dataset.status === filter) ? '' : 'none';
        });
    });
});
