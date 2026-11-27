"""Vendas: valores em centavos e transacoes controladas pelo chamador."""
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

MAX_CENTAVOS = 100_000_000_000
MAX_QUANTIDADE = 1_000_000


def criar_tabelas_vendas(c):
    c.execute('''CREATE TABLE IF NOT EXISTS vendas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pedido_id INTEGER UNIQUE REFERENCES pedidos(id),
        token TEXT UNIQUE,
        origem TEXT NOT NULL CHECK(origem IN ('balcao','pedido','assistente')),
        cliente TEXT NOT NULL, criado_em TEXT NOT NULL, criado_por TEXT NOT NULL
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS venda_itens (
        venda_id INTEGER NOT NULL REFERENCES vendas(id), produto_id TEXT NOT NULL,
        nome TEXT NOT NULL, quantidade INTEGER NOT NULL CHECK(quantidade > 0),
        preco_centavos INTEGER NOT NULL CHECK(preco_centavos >= 0),
        custo_centavos INTEGER CHECK(custo_centavos >= 0),
        PRIMARY KEY(venda_id, produto_id)
    )''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_vendas_data ON vendas(criado_em)')


def em_centavos(valor):
    try:
        d = Decimal(str(valor))
        if not d.is_finite() or d < 0 or d > Decimal(MAX_CENTAVOS) / 100:
            raise ValueError('Valor monetário fora do limite permitido.')
        return int((d * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    except (InvalidOperation, TypeError):
        raise ValueError('Valor monetário inválido.') from None


def _gravar(c, cliente, usuario, origem, linhas, pedido_id=None, token=None):
    if not linhas:
        raise ValueError('Venda sem itens.')
    c.execute('''INSERT INTO vendas(pedido_id,token,origem,cliente,criado_em,criado_por)
        VALUES (?,?,?,?,?,?)''', (pedido_id, token, origem, cliente,
        datetime.now().strftime('%Y-%m-%d %H:%M'), usuario))
    numero = c.lastrowid
    c.executemany('INSERT INTO venda_itens VALUES (?,?,?,?,?,?)',
                  [(numero, *linha) for linha in linhas])
    return numero


def registrar_balcao(c, cliente, itens, usuario, token, movimentar):
    """Itens: (id, quantidade, preço mostrado em centavos). Preço real vem do banco."""
    cliente = cliente.strip() or 'Consumidor balcão'
    if len(cliente) > 120 or not itens or len(itens) > 50:
        raise ValueError('Informe de 1 a 50 itens e um cliente de até 120 caracteres.')
    if not isinstance(token, str) or not 20 <= len(token) <= 100:
        raise ValueError('Formulário inválido. Abra a tela de vendas novamente.')
    anterior = c.execute('SELECT id FROM vendas WHERE token=?', (token,)).fetchone()
    if anterior:
        raise ValueError('Esta venda já foi registrada. Confira o histórico.')
    agrupados = {}
    for pid, qtd, preco_visto in itens:
        if not pid or type(qtd) is not int or not 1 <= qtd <= MAX_QUANTIDADE:
            raise ValueError('Produto ou quantidade inválida.')
        if type(preco_visto) is not int or not 0 <= preco_visto <= MAX_CENTAVOS:
            raise ValueError('Preço inválido. Recarregue a página.')
        anterior = agrupados.get(pid, (0, preco_visto))
        if anterior[1] != preco_visto or anterior[0] + qtd > MAX_QUANTIDADE:
            raise ValueError('Itens repetidos com preço ou quantidade inválida.')
        agrupados[pid] = (anterior[0] + qtd, preco_visto)
    linhas = []
    for pid, (qtd, preco_visto) in agrupados.items():
        p = c.execute('SELECT nome,preco,custo FROM produtos WHERE id=?', (pid,)).fetchone()
        if not p:
            raise ValueError('Produto não encontrado.')
        nome, preco, custo = p
        preco = em_centavos(preco)
        if preco != preco_visto:
            raise ValueError(f'O preço de {nome} mudou. Revise a venda antes de confirmar.')
        linhas.append((pid, nome, qtd, preco, None if custo is None else em_centavos(custo)))
    for pid, _, qtd, _, _ in linhas:
        movimentar(c, pid, 'saida', qtd, usuario)
    return _gravar(c, cliente, usuario, 'balcao', linhas, token=token)


def registrar_pedido_finalizado(c, pedido_id, usuario):
    """Registra somente o financeiro; mudar_status já efetua a baixa."""
    p = c.execute('SELECT cliente,status FROM pedidos WHERE id=?', (pedido_id,)).fetchone()
    if not p or p[1] != 'finalizado':
        raise ValueError('Pedido ainda não finalizado.')
    itens = c.execute('''SELECT i.produto_id,i.nome,i.quantidade,i.preco_centavos,p.custo
        FROM pedido_itens i JOIN produtos p ON p.id=i.produto_id WHERE i.pedido_id=?''',
        (pedido_id,)).fetchall()
    if len(itens) != c.execute('SELECT COUNT(*) FROM pedido_itens WHERE pedido_id=?', (pedido_id,)).fetchone()[0]:
        raise ValueError('Produto do pedido não encontrado.')
    linhas = [(pid, nome, qtd, preco, None if custo is None else em_centavos(custo))
              for pid, nome, qtd, preco, custo in itens]
    ia = c.execute('SELECT 1 FROM assistente_confirmacoes WHERE pedido_id=?', (pedido_id,)).fetchone()
    return _gravar(c, p[0], usuario, 'assistente' if ia else 'pedido', linhas, pedido_id=pedido_id)


def intervalo_periodo(periodo, hoje):
    if periodo == 'semana':
        inicio = hoje - timedelta(days=hoje.weekday())
        fim = inicio + timedelta(days=7)
    elif periodo == 'mes':
        inicio = hoje.replace(day=1)
        fim = (inicio.replace(day=28) + timedelta(days=4)).replace(day=1)
    else:
        inicio, fim = hoje, hoje + timedelta(days=1)
    return inicio.isoformat(), fim.isoformat()


def consultar_vendas(c, inicio, fim, admin=False):
    """Resumo de todo o período; histórico limitado às 100 últimas vendas."""
    vendas = {}
    linhas = c.execute('''SELECT v.id,v.origem,v.cliente,v.criado_em,v.criado_por,v.pedido_id,
        i.nome,i.quantidade,i.preco_centavos,i.custo_centavos
        FROM vendas v JOIN venda_itens i ON v.id=i.venda_id
        WHERE v.criado_em>=? AND v.criado_em<? ORDER BY v.id DESC,i.nome''', (inicio, fim))
    for vid, origem, cliente, data, usuario, pid, nome, qtd, preco, custo in linhas:
        v = vendas.setdefault(vid, dict(id=vid, origem=origem, cliente=cliente, data=data,
            usuario=usuario, pedido_id=pid, total=0, custo=0, sem_custo=0, itens=[]))
        v['total'] += qtd * preco
        if custo is None:
            v['sem_custo'] += 1
        else:
            v['custo'] += qtd * custo
        item = dict(nome=nome, quantidade=qtd, preco=preco, subtotal=qtd*preco)
        if admin:
            item['custo'] = custo
        if len(vendas) <= 100:
            v['itens'].append(item)
    lista = list(vendas.values())
    resumo = dict(total=sum(v['total'] for v in lista), quantidade=len(lista))
    if admin:
        faltam = sum(v['sem_custo'] > 0 for v in lista)
        custo = None if faltam else sum(v['custo'] for v in lista)
        lucro = None if custo is None else resumo['total'] - custo
        resumo.update(custo=custo, lucro=lucro, sem_custo=faltam,
            margem=None if lucro is None or not resumo['total'] else lucro / resumo['total'] * 100)
    for v in lista:
        if admin:
            if v['sem_custo']:
                v['custo'] = None
            v['lucro'] = None if v['custo'] is None else v['total'] - v['custo']
        else:
            v.pop('custo'); v.pop('sem_custo')
    return resumo, lista[:100]
