(() => {
    const lista = document.getElementById('venda-itens');
    const modelo = lista.firstElementChild.cloneNode(true);
    const confirmar = document.getElementById('venda-confirmar');
    const aviso = document.getElementById('venda-alerta');
    const moeda = new Intl.NumberFormat('pt-BR', {style:'currency', currency:'BRL'});
    const form = document.getElementById('form-venda');
    function atualizar() {
        let total = 0n, valido = true, mensagem = '';
        const agrupados = new Map();
        for (const linha of lista.children) {
            const select = linha.querySelector('select');
            const opt = select.selectedOptions[0];
            const qtd = Number(linha.querySelector('[name=quantidade]').value);
            const preco = Number(opt?.dataset.preco);
            const escolhido = !!select.value && !opt.disabled;
            const correto = escolhido && Number.isSafeInteger(qtd) && qtd > 0 && qtd <= 1000000 && Number.isSafeInteger(preco) && preco >= 0;
            linha.querySelector('[name=preco_visto]').value = escolhido ? opt.dataset.preco : '';
            const subtotal = correto ? BigInt(qtd) * BigInt(preco) : 0n;
            linha.querySelector('[data-subtotal]').textContent = formatar(subtotal);
            total += subtotal;
            if (!correto) { valido = false; continue; }
            const acumulado = (agrupados.get(select.value) || 0) + qtd;
            agrupados.set(select.value, acumulado);
            if (acumulado > Number(opt.dataset.disponivel) || acumulado > 1000000) {
                valido = false; mensagem = 'A quantidade solicitada ultrapassa o estoque disponível ou o limite por produto.';
            }
        }
        document.getElementById('venda-total').textContent = formatar(total);
        aviso.textContent = mensagem; aviso.hidden = !mensagem;
        confirmar.disabled = !valido;
        for (const btn of lista.querySelectorAll('[data-remover]')) btn.disabled = lista.children.length === 1;
    }
    function formatar(c) {
        // BigInt evita arredondar os centavos de valores muito grandes.
        return moeda.formatToParts(c / 100n).map(p => p.type === 'fraction' ? String(c % 100n).padStart(2, '0') : p.value).join('');
    }
    document.getElementById('venda-adicionar').addEventListener('click', () => {
        if (lista.children.length < 50) lista.appendChild(modelo.cloneNode(true));
        atualizar();
    });
    lista.addEventListener('click', e => {
        if (e.target.matches('[data-remover]') && lista.children.length > 1) { e.target.closest('.venda-item').remove(); atualizar(); }
    });
    lista.addEventListener('input', atualizar);
    lista.addEventListener('change', atualizar);
    form.addEventListener('submit', e => { atualizar(); if (confirmar.disabled) e.preventDefault(); else confirmar.disabled = true; });
    atualizar();
})();
