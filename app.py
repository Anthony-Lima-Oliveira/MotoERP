from flask import Flask, render_template, request, redirect, session, flash
from functools import wraps
import sqlite3
from database import criar_tabelas, conectar
from datetime import datetime

app = Flask(__name__)
app.secret_key = "erp123"

criar_tabelas()

# ---------------- USUÁRIOS ----------------
usuarios = {
    "admin": {"senha": "123", "tipo": "admin"},
    "operador": {"senha": "123", "tipo": "operador"}
}


# ---------------- PROTEÇÃO DAS ROTAS ----------------
def login_required(f):
    """Só deixa entrar quem fez login."""
    @wraps(f)
    def decorada(*args, **kwargs):
        if "user" not in session:
            return redirect("/")
        return f(*args, **kwargs)
    return decorada


def admin_required(f):
    """Só deixa entrar o administrador."""
    @wraps(f)
    def decorada(*args, **kwargs):
        if "user" not in session:
            return redirect("/")
        if session.get("tipo") != "admin":
            flash("Apenas o administrador pode fazer isso.", "erro")
            return redirect("/produtos")
        return f(*args, **kwargs)
    return decorada


# ---------------- LOGIN ----------------
@app.route("/", methods=["GET", "POST"])
def login():
    erro = None

    if request.method == "POST":
        user = request.form.get("usuario", "").strip()
        senha = request.form.get("senha", "")

        if user in usuarios and usuarios[user]["senha"] == senha:
            session["user"] = user
            session["tipo"] = usuarios[user]["tipo"]
            return redirect("/home")

        erro = "Usuário ou senha incorretos."

    return render_template("login.html", erro=erro)


@app.route("/home")
@login_required
def home():
    return render_template("home.html", user=session["user"], tipo=session["tipo"])


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")


# ---------------- PRODUTOS ----------------
@app.route("/produtos")
@login_required
def produtos():
    conn = conectar()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM produtos")
    dados = cursor.fetchall()
    conn.close()

    return render_template("produtos.html", produtos=dados, tipo=session["tipo"])


@app.route("/add_produto", methods=["POST"])
@login_required
def add_produto():
    id = request.form.get("id", "").strip()
    nome = request.form.get("nome", "").strip()
    preco = request.form.get("preco", "").strip()
    quantidade = request.form.get("quantidade", "").strip()

    if not id or not nome or not preco or not quantidade:
        flash("Preencha todos os campos!", "erro")
        return redirect("/produtos")

    try:
        preco = float(preco)
        quantidade = int(quantidade)
    except ValueError:
        flash("Preço e quantidade precisam ser números.", "erro")
        return redirect("/produtos")

    if preco < 0 or quantidade < 0:
        flash("Preço e quantidade não podem ser negativos.", "erro")
        return redirect("/produtos")

    conn = conectar()
    cursor = conn.cursor()

    try:
        cursor.execute(
            "INSERT INTO produtos (id, nome, preco, quantidade) VALUES (?, ?, ?, ?)",
            (id, nome, preco, quantidade)
        )
        conn.commit()
        flash("Produto adicionado com sucesso.", "ok")
    except sqlite3.IntegrityError:
        flash(f"Já existe um produto com o ID {id}.", "erro")
    finally:
        conn.close()

    return redirect("/produtos")


@app.route("/delete_produto/<id>", methods=["POST"])
@admin_required
def delete_produto(id):
    conn = conectar()
    cursor = conn.cursor()

    cursor.execute("DELETE FROM produtos WHERE id=?", (id,))

    conn.commit()
    conn.close()

    flash("Produto excluído.", "ok")
    return redirect("/produtos")


@app.route("/movimentar_produto", methods=["POST"])
@login_required
def movimentar_produto():
    produto_id = request.form.get("produto_id", "")
    tipo = request.form.get("tipo", "")

    try:
        quantidade = int(request.form.get("quantidade", ""))
    except ValueError:
        flash("Quantidade inválida.", "erro")
        return redirect("/produtos")

    if tipo not in ("entrada", "saida"):
        flash("Tipo de movimentação inválido.", "erro")
        return redirect("/produtos")

    if quantidade <= 0:
        flash("A quantidade precisa ser maior que zero.", "erro")
        return redirect("/produtos")

    data = datetime.now().strftime("%Y-%m-%d %H:%M")

    conn = conectar()
    cursor = conn.cursor()

    cursor.execute("SELECT quantidade FROM produtos WHERE id=?", (produto_id,))
    produto = cursor.fetchone()

    if not produto:
        conn.close()
        flash("Produto não encontrado.", "erro")
        return redirect("/produtos")

    estoque_atual = produto[0]

    if tipo == "entrada":
        novo_estoque = estoque_atual + quantidade
    else:
        if quantidade > estoque_atual:
            conn.close()
            flash("Quantidade de saída maior que o estoque atual.", "erro")
            return redirect("/produtos")
        novo_estoque = estoque_atual - quantidade

    cursor.execute(
        "UPDATE produtos SET quantidade=? WHERE id=?",
        (novo_estoque, produto_id)
    )

    cursor.execute("""
        INSERT INTO movimentacoes (produto_id, tipo, quantidade, data)
        VALUES (?, ?, ?, ?)
    """, (produto_id, tipo, quantidade, data))

    conn.commit()
    conn.close()

    flash("Movimentação registrada.", "ok")
    return redirect("/produtos")


# ---------------- RELATÓRIO ----------------
@app.route("/relatorio_estoque")
@login_required
def relatorio_estoque():
    conn = conectar()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM produtos")
    produtos = cursor.fetchall()

    cursor.execute("SELECT SUM(quantidade) FROM produtos")
    total_itens = cursor.fetchone()[0] or 0

    cursor.execute("SELECT SUM(preco * quantidade) FROM produtos")
    valor_total = cursor.fetchone()[0] or 0

    conn.close()

    return render_template(
        "relatorio_estoque.html",
        produtos=produtos,
        total_itens=total_itens,
        valor_total=valor_total
    )


if __name__ == "__main__":
    app.run(debug=True)
