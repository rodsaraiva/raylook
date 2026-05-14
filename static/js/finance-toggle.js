(function () {
    let financeOpen = false;

    window.toggleFinanceView = function () {
        financeOpen = !financeOpen;
        window._financeOpen = financeOpen;

        const pkgsArea   = document.getElementById('packages-area');
        const finSection = document.getElementById('section-finance');
        const finBlock   = document.getElementById('fin-block');

        if (financeOpen) {
            pkgsArea?.classList.add('retracted');
            finSection?.classList.add('active');
            finBlock?.classList.add('active');
            // Acordeon: abrir financeiro fecha Comercial/Estoque.
            window._railCollapseGroups?.();
            window.financeRefresh?.();
        } else {
            pkgsArea?.classList.remove('retracted');
            finSection?.classList.remove('active');
            finBlock?.classList.remove('active');
        }
    };

    document.addEventListener('DOMContentLoaded', function () {
        document.getElementById('fin-block')?.addEventListener('click', function () {
            window.toggleFinanceView();
        });

        // Clicar no rail colapsado (título "Fluxo") fecha o financeiro
        document.getElementById('rail')?.addEventListener('click', function (e) {
            if (window._financeOpen && !e.target.closest('.rail-step')) {
                window.toggleFinanceView();
            }
        });
    });
})();
