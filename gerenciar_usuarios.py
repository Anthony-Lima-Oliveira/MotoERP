"""
Gerencia os usuários do MotoERP pelo terminal.

  py gerenciar_usuarios.py listar
  py gerenciar_usuarios.py senha admin          (troca a senha)
  py gerenciar_usuarios.py criar maria operador (cria usuário; tipo: admin ou operador)
"""
import sys
from getpass import getpass
from database import conectar, criar_tabelas
import seguranca


def pedir_senha():
    senha = getpass("Nova senha (não aparece enquanto digita): ")
    if senha != getpass("Repita a senha: "):
        raise ValueError("As senhas não são iguais.")
    return senha


def main(args):
    criar_tabelas()
    conn = conectar()
    seguranca.criar_tabela_usuarios(conn)
    try:
        if args[:1] == ["listar"]:
            for usuario, tipo in conn.execute("SELECT usuario, tipo FROM usuarios ORDER BY usuario"):
                print(f"{usuario}  ({tipo})")
        elif len(args) == 2 and args[0] == "senha":
            existente = conn.execute("SELECT tipo FROM usuarios WHERE usuario=?", (args[1],)).fetchone()
            if not existente:
                raise ValueError(f"Usuário '{args[1]}' não existe. Use: listar")
            seguranca.salvar_usuario(conn, args[1], pedir_senha(), existente[0])
            print("Senha trocada.")
        elif len(args) == 3 and args[0] == "criar":
            if conn.execute("SELECT 1 FROM usuarios WHERE usuario=?", (args[1],)).fetchone():
                raise ValueError(f"O usuário '{args[1]}' já existe. Para mudar a senha: senha {args[1]}")
            seguranca.salvar_usuario(conn, args[1], pedir_senha(), args[2])
            print("Usuário criado.")
        else:
            print(__doc__)
    except ValueError as erro:
        print(f"Erro: {erro}")
        return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
