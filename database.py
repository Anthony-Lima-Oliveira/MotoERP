import sqlite3

def conectar():
    return sqlite3.connect("erp.db")

def criar_tabelas():
    conn = conectar()
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS produtos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL,
    preco REAL NOT NULL,
    quantidade INTEGER NOT NULL
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

    # Bancos criados antes do histórico não têm a coluna "usuario".
    # Esta parte adiciona a coluna sem apagar nada do que já existe.
    colunas = [c[1] for c in cursor.execute("PRAGMA table_info(movimentacoes)")]
    if "usuario" not in colunas:
        cursor.execute("ALTER TABLE movimentacoes ADD COLUMN usuario TEXT")

    conn.commit()
    conn.close()
