from flask import Flask, render_template, request, redirect, session
import sqlite3
from database import criar_tabelas
from datetime import datetime

app = Flask(__name__)
app.secret_key = "erp123"

criar_tabelas()

# ---------------- LOGIN ----------------
usuarios = {
    "admin": {"senha": "123", "tipo": "admin"},
    "operador": {"senha": "123", "tipo": "operador"}
}

@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = request.form["usuario"]
        senha = request.form["senha"]

        if user in usuarios and usuarios[user]["senha"] == senha:
            session["user"] = user
            session["tipo"] = usuarios[user]["tipo"]
            return redirect("/home")

    return render_template("login.html")

@app.route("/movimentar_produto", methods=["POST"])
def movimentar_produto():
    produto_id = request.form["produto_id"]
    tipo = request.form["tipo"]
    quantidade = int(request.form["quantidade"])
    data = datetime.now().strftime("%Y-%m-%d %H:%M")

    conn = sqlite3.connect("erp.db")
    cursor = conn.cursor()

    cursor.execute("SELECT quantidade FROM produtos WHERE id=?", (produto_id,))
    produto = cursor.fetchone()

    if produto:
        estoque_atual = produto[0]

        if tipo == "entrada":
            novo_estoque = estoque_atual + quantidade

        elif tipo == "saida":
            if quantidade > estoque_atual:
                conn.close()
                return "Erro: quantidade de saída maior que o estoque atual."

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
    return redirect("/produtos")

@app.route("/home")
def home():
    if "user" not in session:
        return redirect("/")
    return render_template("home.html", user=session["user"])


# ---------------- PRODUTOS ----------------
@app.route("/produtos")
def produtos():
    conn = sqlite3.connect("erp.db")
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM produtos")
    dados = cursor.fetchall()
    conn.close()

    return render_template("produtos.html", produtos=dados)


@app.route("/add_produto", methods=["POST"])
def add_produto():

    id = request.form["id"]
    nome = request.form["nome"]
    preco = request.form["preco"]
    quantidade = request.form["quantidade"]

    if not id or not nome or not preco or not quantidade:
        return """
        <script>
            alert('Preencha todos os campos!');
            window.location.href='/produtos';
        </script>
        """

    conn = sqlite3.connect("erp.db")
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO produtos VALUES (?, ?, ?, ?)
    """, (id, nome, preco, quantidade))

    conn.commit()
    conn.close()

    return redirect("/produtos")


@app.route("/delete_produto/<id>")
def delete_produto(id):
    conn = sqlite3.connect("erp.db")
    cursor = conn.cursor()

    cursor.execute("DELETE FROM produtos WHERE id=?", (id,))

    conn.commit()
    conn.close()

    return redirect("/produtos")

@app.route("/relatorio_estoque")
def relatorio_estoque():
    conn = sqlite3.connect("erp.db")
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

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")


if __name__ == "__main__":
    app.run(debug=True)