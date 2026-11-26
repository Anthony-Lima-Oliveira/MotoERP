import sqlite3
from models.pedido import criar_tabelas_pedidos

def conectar():
    return sqlite3.connect("erp.db")


def adicionar_coluna(cursor, tabela, coluna, definicao):
    """Adiciona uma coluna só se ela ainda não existir (não mexe nos dados)."""
    colunas = [c[1] for c in cursor.execute(f"PRAGMA table_info({tabela})")]
    if coluna not in colunas:
        cursor.execute(f"ALTER TABLE {tabela} ADD COLUMN {coluna} {definicao}")


def criar_tabelas():
    conn = conectar()
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS produtos (
    id TEXT PRIMARY KEY,
    nome TEXT NOT NULL,
    preco REAL NOT NULL,
    quantidade INTEGER NOT NULL,
    custo REAL,
    estoque_minimo INTEGER NOT NULL DEFAULT 5,
    categoria TEXT
    )
""")
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS movimentacoes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    produto_id TEXT NOT NULL,
    tipo TEXT NOT NULL,
    quantidade INTEGER NOT NULL,
    data TEXT NOT NULL,
    usuario TEXT,
    FOREIGN KEY (produto_id) REFERENCES produtos(id)
)
""")

    # Migrações: bancos criados em versões antigas ganham as colunas novas.
    # "preco" continua sendo o preço de VENDA. "custo" fica vazio (NULL)
    # nos produtos antigos até alguém informar.
    adicionar_coluna(cursor, "produtos", "custo", "REAL")
    adicionar_coluna(cursor, "produtos", "estoque_minimo", "INTEGER NOT NULL DEFAULT 5")
    adicionar_coluna(cursor, "produtos", "categoria", "TEXT")
    adicionar_coluna(cursor, "movimentacoes", "usuario", "TEXT")

    criar_tabelas_pedidos(cursor)
    cursor.execute("CREATE TABLE IF NOT EXISTS assistente_confirmacoes (token TEXT PRIMARY KEY, pedido_id INTEGER NOT NULL REFERENCES pedidos(id))")
    cursor.execute("CREATE TABLE IF NOT EXISTS assistente_conversas (id TEXT PRIMARY KEY, usuario TEXT NOT NULL, versao INTEGER NOT NULL, estado TEXT NOT NULL)")
    conn.commit()
    conn.close()
