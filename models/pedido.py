"""Pedidos locais: todas as escritas devem ocorrer em BEGIN IMMEDIATE."""
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from models.venda import registrar_pedido_finalizado

ATIVOS = ('reservado', 'separando', 'pronto')
TRANSICOES = {'reservado': ('separando', 'cancelado'), 'separando': ('pronto', 'cancelado'), 'pronto': ('finalizado', 'cancelado')}


def criar_tabelas_pedidos(c):
    c.execute('''CREATE TABLE IF NOT EXISTS pedidos (
        id INTEGER PRIMARY KEY AUTOINCREMENT, cliente TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('reservado','separando','pronto','finalizado','cancelado')),
        criado_em TEXT NOT NULL, criado_por TEXT NOT NULL,
        atualizado_em TEXT NOT NULL, atualizado_por TEXT NOT NULL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS pedido_itens (
        pedido_id INTEGER NOT NULL REFERENCES pedidos(id), produto_id TEXT NOT NULL,
        nome TEXT NOT NULL, quantidade INTEGER NOT NULL CHECK(quantidade > 0),
        preco_centavos INTEGER NOT NULL CHECK(preco_centavos >= 0),
        PRIMARY KEY(pedido_id, produto_id))''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_pedido_produto ON pedido_itens(produto_id)')


def reservado(c, produto_id):
    return c.execute('''SELECT COALESCE(SUM(i.quantidade),0) FROM pedido_itens i
        JOIN pedidos p ON p.id=i.pedido_id WHERE i.produto_id=?
        AND p.status IN ('reservado','separando','pronto')''', (produto_id,)).fetchone()[0]


def reservas_por_produto(c):
    """Devolve {produto_id: quantidade reservada} para todos os produtos de uma vez."""
    linhas = c.execute('''SELECT i.produto_id, SUM(i.quantidade) FROM pedido_itens i
        JOIN pedidos p ON p.id=i.pedido_id
        WHERE p.status IN ('reservado','separando','pronto') GROUP BY i.produto_id''').fetchall()
    return {pid: qtd for pid, qtd in linhas}


def criar_pedido(c, cliente, itens, usuario):
    cliente = cliente.strip()
    if not cliente or len(cliente) > 120 or not itens or len(itens) > 50:
        raise ValueError('Informe o cliente e de 1 a 50 produtos.')
    agrupados = {}
    for pid, qtd in itens:
        if not pid or type(qtd) is not int or not 1 <= qtd <= 1000000:
            raise ValueError('Produto ou quantidade inválida.')
        agrupados[pid] = agrupados.get(pid, 0) + qtd
    linhas = []
    for pid, qtd in agrupados.items():
        p = c.execute('SELECT nome, preco, quantidade FROM produtos WHERE id=?', (pid,)).fetchone()
        if not p:
            raise ValueError('Produto não encontrado.')
        nome, preco, fisico = p
        disponivel = fisico - reservado(c, pid)
        if qtd > disponivel:
            raise ValueError(f'Apenas {disponivel} un. disponíveis de {nome}.')
        centavos = int((Decimal(str(preco))*100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
        linhas.append((pid, nome, qtd, centavos))
    agora = datetime.now().strftime('%Y-%m-%d %H:%M')
    c.execute('INSERT INTO pedidos(cliente,status,criado_em,criado_por,atualizado_em,atualizado_por) VALUES (?,\'reservado\',?,?,?,?)', (cliente, agora, usuario, agora, usuario))
    pedido_id = c.lastrowid
    c.executemany('INSERT INTO pedido_itens VALUES (?,?,?,?,?)', [(pedido_id,*linha) for linha in linhas])
    return pedido_id


def mudar_status(c, pedido_id, destino, usuario, movimentar):
    p = c.execute('SELECT status FROM pedidos WHERE id=?', (pedido_id,)).fetchone()
    if not p or destino not in TRANSICOES.get(p[0], ()):
        raise ValueError('Pedido inexistente ou transição inválida.')
    # Libera a própria reserva antes da baixa, na mesma transação.
    c.execute('UPDATE pedidos SET status=?, atualizado_em=?, atualizado_por=? WHERE id=?', (destino, datetime.now().strftime('%Y-%m-%d %H:%M'), usuario, pedido_id))
    if destino == 'finalizado':
        itens = c.execute('SELECT produto_id, quantidade FROM pedido_itens WHERE pedido_id=?', (pedido_id,)).fetchall()
        if not itens:
            raise ValueError('Pedido sem itens.')
        for pid, qtd in itens:
            movimentar(c, pid, 'saida', qtd, usuario)
        registrar_pedido_finalizado(c, pedido_id, usuario)
