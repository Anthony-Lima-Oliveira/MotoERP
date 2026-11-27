"""Segurança básica do MotoERP: chave da sessão, usuários com senha em hash e limite de tentativas."""
import os
import secrets
import time
from pathlib import Path
from werkzeug.security import generate_password_hash, check_password_hash

PASTA = Path(__file__).resolve().parent
ARQUIVO_CHAVE = PASTA / "secret_key.txt"
TIPOS = ("admin", "operador")
TAMANHO_MINIMO_SENHA = 8

# Quantas senhas erradas seguidas até bloquear, e por quanto tempo (em segundos).
MAX_TENTATIVAS = 5
BLOQUEIO_SEGUNDOS = 300

_falhas = {}  # (ip, usuário) -> [quantidade, momento da última falha]


# ---------------- CHAVE DA SESSÃO ----------------
def chave_secreta():
    """
    Chave que assina o cookie de login. Vem da variável MOTOERP_SECRET_KEY
    ou, se ela não existir, de um arquivo secret_key.txt criado na primeira vez
    (esse arquivo não vai para o Git).
    """
    da_variavel = os.environ.get("MOTOERP_SECRET_KEY", "").strip()
    if da_variavel:
        return da_variavel
    if ARQUIVO_CHAVE.exists():
        chave = ARQUIVO_CHAVE.read_text(encoding="utf-8").strip()
        if chave:
            return chave
    chave = secrets.token_hex(32)
    ARQUIVO_CHAVE.write_text(chave, encoding="utf-8")
    return chave


# ---------------- USUÁRIOS ----------------
def criar_tabela_usuarios(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            usuario    TEXT PRIMARY KEY,
            senha_hash TEXT NOT NULL,
            tipo       TEXT NOT NULL CHECK (tipo IN ('admin', 'operador'))
        )
    """)
    conn.commit()


def validar_senha(senha):
    if len(senha) < TAMANHO_MINIMO_SENHA:
        raise ValueError(f"A senha precisa ter pelo menos {TAMANHO_MINIMO_SENHA} caracteres.")


def salvar_usuario(conn, usuario, senha, tipo):
    """Cria o usuário ou troca a senha/tipo dele."""
    usuario = usuario.strip()
    if not usuario or len(usuario) > 40:
        raise ValueError("Nome de usuário inválido.")
    if tipo not in TIPOS:
        raise ValueError("O tipo precisa ser admin ou operador.")
    validar_senha(senha)
    conn.execute(
        """INSERT INTO usuarios (usuario, senha_hash, tipo) VALUES (?, ?, ?)
           ON CONFLICT(usuario) DO UPDATE SET senha_hash=excluded.senha_hash, tipo=excluded.tipo""",
        (usuario, generate_password_hash(senha), tipo),
    )
    conn.commit()


def garantir_usuarios(conn):
    """
    Na primeira execução (tabela vazia) cria 'admin' e 'operador' com senhas
    aleatórias e mostra no terminal UMA vez. Para trocar: py gerenciar_usuarios.py senha admin
    """
    criar_tabela_usuarios(conn)
    if conn.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0]:
        return
    senhas = {}
    for usuario in ("admin", "operador"):
        senhas[usuario] = secrets.token_urlsafe(9)
        salvar_usuario(conn, usuario, senhas[usuario], usuario)
    print("=" * 60)
    print(" PRIMEIRO ACESSO: usuários criados. Anote as senhas agora,")
    print(" elas não serão mostradas de novo.")
    for usuario, senha in senhas.items():
        print(f"   usuário: {usuario:<10} senha: {senha}")
    print(" Para trocar uma senha:  py gerenciar_usuarios.py senha admin")
    print("=" * 60)


# ---------------- LOGIN ----------------
_HASH_FALSO = generate_password_hash("senha-que-ninguem-usa")


def segundos_de_bloqueio(ip, usuario):
    """Quantos segundos faltam para poder tentar de novo (0 = liberado)."""
    registro = _falhas.get((ip, usuario.lower()))
    if not registro:
        return 0
    quantidade, ultima = registro
    if quantidade < MAX_TENTATIVAS:
        return 0
    restante = int(BLOQUEIO_SEGUNDOS - (time.time() - ultima))
    if restante <= 0:
        _falhas.pop((ip, usuario.lower()), None)
        return 0
    return restante


def conferir_login(conn, ip, usuario, senha):
    """Devolve o tipo do usuário ('admin'/'operador') se a senha estiver certa; senão None."""
    chave = (ip, usuario.lower())
    linha = conn.execute("SELECT senha_hash, tipo FROM usuarios WHERE usuario=?", (usuario,)).fetchone()
    # Confere o hash mesmo se o usuário não existir, para não revelar quais existem pelo tempo de resposta.
    valido = check_password_hash(linha[0] if linha else _HASH_FALSO, senha)
    if linha and valido:
        _falhas.pop(chave, None)
        return linha[1]
    quantidade = _falhas.get(chave, [0, 0])[0] + 1
    _falhas[chave] = [quantidade, time.time()]
    return None
