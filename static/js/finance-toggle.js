(function () {
    let financeOpen = false;
    let financeGroupOpen = false;

    function setGroupOpen(open) {
        financeGroupOpen = open;
        document.getElementById('fin-group')?.classList.toggle('open', open);
    }

    function openFinance(view) {
        financeOpen = true;
        window._financeOpen = true;
        document.getElementById('packages-area')?.classList.add('retracted');
        document.getElementById('section-finance')?.classList.add('active');
        // Acordeon: abrir financeiro fecha Comercial/Estoque/Logística/Clientes.
        window._railCollapseGroups?.();
        window._clientesClose?.();
        setGroupOpen(true);
        if (view) window.financeSetView?.(view);
        window.financeRefresh?.();
    }

    function closeFinance() {
        financeOpen = false;
        window._financeOpen = false;
        document.getElementById('section-finance')?.classList.remove('active');
        setGroupOpen(false);
        // Só devolve a packages-area se nenhuma outra section ocupou o slot.
        // Sem isso, abrir Financeiro a partir de Clientes (que dispara
        // closeFinance/closeClientes recíproco) acabava removendo retracted
        // recém-adicionado, sobrepondo packages-area com a section nova.
        if (!window._clientesOpen) {
            document.getElementById('packages-area')?.classList.remove('retracted');
        }
    }

    // Compat: handlers externos (dashboard_v2.js) chamam toggleFinanceView()
    // pra fechar o financeiro ao abrir outro grupo.
    window.toggleFinanceView = function (view) {
        if (view) { openFinance(view); return; }
        if (financeOpen) closeFinance(); else openFinance();
    };

    document.addEventListener('DOMContentLoaded', function () {
        // Header do dropdown: abre/fecha o grupo + abre a view default (receivable)
        document.getElementById('fin-group-header')?.addEventListener('click', function () {
            if (financeOpen) closeFinance();
            else openFinance('receivable');
        });

        // Sub-itens "A receber" / "Pagos"
        document.querySelectorAll('#fin-group .rail-step[data-fin-view]').forEach((step) => {
            step.addEventListener('click', function (e) {
                e.stopPropagation();  // evita disparar o toggle do header
                const view = step.dataset.finView;
                document.querySelectorAll('#fin-group .rail-step').forEach((s) =>
                    s.classList.toggle('active', s === step));
                openFinance(view);
            });
        });
    });
})();
