(function () {
    let financeOpen = false;

    window.toggleFinanceView = function () {
        financeOpen = !financeOpen;
        window._financeOpen = financeOpen;

        const pkgsArea   = document.getElementById('packages-area');
        const finSection = document.getElementById('section-finance');
        const finBlock   = document.getElementById('fin-block');
        const rail       = document.getElementById('rail');
        const filterBar  = document.getElementById('filter-bar');

        if (financeOpen) {
            pkgsArea?.classList.add('retracted');
            finSection?.classList.add('active');
            finBlock?.classList.add('active');
            rail?.classList.add('dimmed');
            if (filterBar) { filterBar.style.opacity = '0.25'; filterBar.style.pointerEvents = 'none'; }
            window.financeRefresh?.();
        } else {
            pkgsArea?.classList.remove('retracted');
            finSection?.classList.remove('active');
            finBlock?.classList.remove('active');
            rail?.classList.remove('dimmed');
            if (filterBar) { filterBar.style.opacity = ''; filterBar.style.pointerEvents = ''; }
        }
    };

    document.addEventListener('DOMContentLoaded', function () {
        document.getElementById('fin-block')?.addEventListener('click', function () {
            window.toggleFinanceView();
        });
    });
})();
