from flask import Flask, render_template, request, redirect, session, flash, url_for
from functools import wraps
import sqlite3
from database import criar_tabelas, conectar
from datetime import datetime

app = Flask(__name__)
app.secret_key = "erp123"

criar_tabelas()

# Produto com quantidade igual ou menor que isso aparece como "Estoque baixo".
LIMITE_ESTOQUE_BAIXO = 5

# Quantas movimentações a tela de histórico mostra no máximo.
LIMITE_HISTORICO = 300

# ---------------- USUÁRIOS ----------------
usuarios = {
    "admin": {"senha": "123", "tipo": "admin"},
    "operador": {"senha": "123", "tipo": "operador"}
}


# ---------------- FORMATAÇÃO ----------------
@app.template_filter("real")
def real(valor):
    """1234.5 -> R$ 1.234,50"""
    try:
        valor = float(valor)
    except (TypeError, ValueError):
        valor = 0.0
    texto = f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return "R$ " + texto


@app.template_filter("data_br")
def data_br(valor):
    """2026-06-11 19:08 -> 11/06/2026 19:08"""
    try:
        return datetime.strptime(valor, "%Y-%m-%d %H:%M").strftime("%d/%m/%Y %H:%M")
    except (TypeError, ValueError):
        return valor or ""


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
    conn = conectar()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT COUNT(*), COALESCE(SUM(quantidade), 0), COALESCE(SUM(preco * quantidade), 0)
        FROM produtos
    """)
    qtd_produtos, total_itens, valor_total = cursor.fetchone()

    cursor.execute(
        "SELECT COUNT(*) FROM produtos WHERE quantidade <= ?",
        (LIMITE_ESTOQUE_BAIXO,)
    )
    qtd_baixo = cursor.fetchone()[0]

    cursor.execute("""
        SELECT m.data, m.produto_id, p.nome, m.tipo, m.quantidade
        FROM movimentacoes m
        LEFT JOIN produtos p ON p.id = m.produto_id
        ORDER BY m.id DESC
        LIMIT 5
    """)
    recentes = cursor.fetchall()

    conn.close()

    return render_template(
        "home.html",
        qtd_produtos=qtd_produtos,
        total_itens=total_itens,
        valor_total=valor_total,
        qtd_baixo=qtd_baixo,
        recentes=recentes
    )


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

    return render_template(
        "produtos.html",
        produtos=dados,
        tipo=session["tipo"],
        limite=LIMITE_ESTOQUE_BAIXO
    )


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


# ---------------- MOVIMENTAR ----------------
def voltar_para_movimentar(produto_id="", tipo=""):
    """Volta para a tela Movimentar mantendo o que a pessoa já tinha escolhido."""
    return redirect(url_for("movimentar", produto=produto_id, tipo=tipo))


@app.route("/movimentar", methods=["GET", "POST"])
@login_required
def movimentar():
    if request.method == "POST":
        produto_id = request.form.get("produto_id", "")
        tipo = request.form.get("tipo", "")

        if tipo not in ("entrada", "saida"):
            flash("Escolha se foi uma entrada ou uma saída.", "erro")
            return voltar_para_movimentar(produto_id)

        if not produto_id:
            flash("Escolha o produto.", "erro")
            return voltar_para_movimentar(tipo=tipo)

        try:
            quantidade = int(request.form.get("quantidade", ""))
        except ValueError:
            flash("Quantidade inválida.", "erro")
            return voltar_para_movimentar(produto_id, tipo)

        if quantidade <= 0:
            flash("A quantidade precisa ser maior que zero.", "erro")
            return voltar_para_movimentar(produto_id, tipo)

        conn = conectar()
        cursor = conn.cursor()

        cursor.execute("SELECT nome, quantidade FROM produtos WHERE id=?", (produto_id,))
        produto = cursor.fetchone()

        if not produto:
            conn.close()
            flash("Produto não encontrado.", "erro")
            return voltar_para_movimentar(tipo=tipo)

        nome, estoque_atual = produto

        if tipo == "entrada":
            novo_estoque = estoque_atual + quantidade
        else:
            if quantidade > estoque_atual:
                conn.close()
                flash(f"Só há {estoque_atual} un. de {nome} em estoque.", "erro")
                return voltar_para_movimentar(produto_id, tipo)
            novo_estoque = estoque_atual - quantidade

        data = datetime.now().strftime("%Y-%m-%d %H:%M")

        cursor.execute(
            "UPDATE produtos SET quantidade=? WHERE id=?",
            (novo_estoque, produto_id)
        )

        cursor.execute("""
            INSERT INTO movimentacoes (produto_id, tipo, quantidade, data, usuario)
            VALUES (?, ?, ?, ?, ?)
        """, (produto_id, tipo, quantidade, data, session["user"]))

        conn.commit()
        conn.close()

        palavra = "Entrada" if tipo == "entrada" else "Saída"
        flash(f"{palavra} registrada: {quantidade} un. de {nome}. Estoque agora: {novo_estoque}.", "ok")
        return redirect("/movimentar")

    # ---- GET: mostra a tela ----
    produto_sel = request.args.get("produto", "")
    tipo_sel = request.args.get("tipo", "")
    if tipo_sel not in ("entrada", "saida"):
        tipo_sel = ""

    conn = conectar()
    cursor = conn.cursor()

    cursor.execute("SELECT id, nome, quantidade FROM produtos ORDER BY nome")
    lista_produtos = cursor.fetchall()

    cursor.execute("""
        SELECT m.data, m.produto_id, p.nome, m.tipo, m.quantidade
        FROM movimentacoes m
        LEFT JOIN produtos p ON p.id = m.produto_id
        ORDER BY m.id DESC
        LIMIT 5
    """)
    recentes = cursor.fetchall()

    conn.close()

    return render_template(
        "movimentar.html",
        lista_produtos=lista_produtos,
        produto_sel=produto_sel,
        tipo_sel=tipo_sel,
        recentes=recentes,
        limite=LIMITE_ESTOQUE_BAIXO
    )


# ---------------- HISTÓRICO ----------------
@app.route("/historico")
@login_required
def historico():
    produto = request.args.get("produto", "")
    tipo = request.args.get("tipo", "")
    if tipo not in ("entrada", "saida"):
        tipo = ""

    sql = """
        SELECT m.data, m.produto_id, p.nome, m.tipo, m.quantidade, m.usuario
        FROM movimentacoes m
        LEFT JOIN produtos p ON p.id = m.produto_id
    """
    filtros = []
    params = []

    if produto:
        filtros.append("m.produto_id = ?")
        params.append(produto)
    if tipo:
        filtros.append("m.tipo = ?")
        params.append(tipo)

    if filtros:
        sql += " WHERE " + " AND ".join(filtros)

    sql += " ORDER BY m.id DESC LIMIT ?"
    params.append(LIMITE_HISTORICO)

    conn = conectar()
    cursor = conn.cursor()

    cursor.execute(sql, params)
    movimentacoes = cursor.fetchall()

    cursor.execute("SELECT id, nome FROM produtos ORDER BY nome")
    lista_produtos = cursor.fetchall()

    conn.close()

    return render_template(
        "historico.html",
        movimentacoes=movimentacoes,
        lista_produtos=lista_produtos,
        produto_filtro=produto,
        tipo_filtro=tipo,
        limite=LIMITE_HISTORICO
    )


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
