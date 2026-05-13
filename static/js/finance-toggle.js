(function () {
    let financeOpen = false;

    window.toggleFinanceView = function () {
        financeOpen = !financeOpen;
        window._financeOpen = financeOpen;

        const pkgsArea   = document.getElementById('packages-area');
        const finSection = document.getElementById('section-finance');
        const filterBar  = document.getElementById('filter-bar');

        if (financeOpen) {
            pkgsArea?.classList.add('retracted');
            finSection?.classList.add('active');
            if (filterBar) { filterBar.style.opacity = '0.25'; filterBar.style.pointerEvents = 'none'; }
            window.financeRefresh?.();
        } else {
            pkgsArea?.classList.remove('retracted');
            finSection?.classList.remove('active');
            if (filterBar) { filterBar.style.opacity = ''; filterBar.style.pointerEvents = ''; }
        }
    };
})();
