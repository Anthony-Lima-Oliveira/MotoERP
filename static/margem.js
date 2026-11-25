// Mostra o lucro e a margem enquanto a pessoa digita o custo e o preço de venda.
(function () {
    document.querySelectorAll('[data-calc-margem]').forEach(function (form) {
        var custo = form.querySelector('[name=custo]');
        var preco = form.querySelector('[name=preco]');
        var saida = form.querySelector('[data-margem-saida]');
        if (!custo || !preco || !saida) return;

        function reais(valor) {
            return Math.abs(valor).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
        }

        function atualizar() {
            var c = parseFloat(String(custo.value).replace(',', '.'));
            var p = parseFloat(String(preco.value).replace(',', '.'));

            if (!isFinite(c) || !isFinite(p) || p <= 0) {
                saida.textContent = '';
                saida.className = saida.className.replace(/\s*(erro|ok)\b/g, '');
                return;
            }

            var lucro = p - c;
            var margem = Math.round((lucro / p) * 100);
            var base = saida.className.replace(/\s*(erro|ok)\b/g, '');

            if (lucro < 0) {
                saida.textContent = 'Prejuízo de ' + reais(lucro) + ' por unidade: o preço de venda está abaixo do custo.';
                saida.className = base + ' erro';
            } else {
                saida.textContent = 'Lucro de ' + reais(lucro) + ' por unidade (' + margem + '% de margem).';
                saida.className = base + ' ok';
            }
        }

        custo.addEventListener('input', atualizar);
        preco.addEventListener('input', atualizar);
        atualizar();
    });
})();
