from flask import Flask, render_template, request, redirect, session, flash, url_for
from functools import wraps
import math
import json
from models.conversa import estado_novo, linhas_carrinho, responder
import secrets
import time
from models.assistente import catalogo, interpretar, centavos, ErroAssistente
from models.pedido import reservado, criar_pedido, mudar_status, TRANSICOES
import sqlite3
import unicodedata
from database import criar_tabelas, conectar
from datetime import datetime

app = Flask(__name__)
app.secret_key = "erp123"

criar_tabelas()

# Estoque mínimo sugerido ao cadastrar um produto novo.
# (Cada produto tem o seu próprio mínimo, que dá para mudar ao editar.)
ESTOQUE_MINIMO_PADRAO = 5

# Quantas movimentações a tela de histórico mostra no máximo.
LIMITE_HISTORICO = 300

# Colunas do produto, sempre nesta ordem:
#   0 id | 1 nome | 2 preço de venda | 3 quantidade | 4 custo | 5 estoque mínimo | 6 categoria
COLUNAS_PRODUTO = "id, nome, preco, quantidade, custo, estoque_minimo, categoria"

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


@app.template_filter("margem")
def margem(preco, custo):
    """Margem de lucro sobre o preço de venda: (venda - custo) / venda. Ex.: 32%"""
    try:
        if custo is None or float(preco) <= 0:
            return "—"
        return f"{(float(preco) - float(custo)) / float(preco) * 100:.0f}%"
    except (TypeError, ValueError):
        return "—"


def sem_acento(texto):
    """'Óleo Pastilha' -> 'oleo pastilha' (para busca e ordenação)."""
    texto = unicodedata.normalize("NFD", str(texto or ""))
    return "".join(c for c in texto if not unicodedata.combining(c)).lower()


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


# ---------------- REGRAS DO ESTOQUE ----------------
class ErroEstoque(Exception):
    """Erro de regra do estoque, com uma mensagem pronta para mostrar na tela."""


def aplicar_movimentacao(cursor, produto_id, tipo, quantidade, usuario):
    """
    Único lugar do sistema que muda a quantidade de um produto.
    Atualiza o estoque e grava a movimentação no histórico.
    Devolve (nome, novo_estoque). Não faz commit: quem chama decide.
    Telas futuras (como vendas) devem usar esta função.
    """
    if tipo not in ("entrada", "saida"):
        raise ErroEstoque("Tipo de movimentação inválido.")

    if quantidade <= 0:
        raise ErroEstoque("A quantidade precisa ser maior que zero.")

    cursor.execute("SELECT nome, quantidade FROM produtos WHERE id=?", (produto_id,))
    produto = cursor.fetchone()

    if not produto:
        raise ErroEstoque("Produto não encontrado.")

    nome, estoque_atual = produto

    if tipo == "entrada":
        novo_estoque = estoque_atual + quantidade
    else:
        disponivel = estoque_atual - reservado(cursor, produto_id)
        if quantidade > disponivel:
            raise ErroEstoque(f"Só há {disponivel} un. de {nome} disponíveis (descontadas as reservas).")
        novo_estoque = estoque_atual - quantidade

    cursor.execute(
        "UPDATE produtos SET quantidade=? WHERE id=?",
        (novo_estoque, produto_id)
    )

    cursor.execute("""
        INSERT INTO movimentacoes (produto_id, tipo, quantidade, data, usuario)
        VALUES (?, ?, ?, ?, ?)
    """, (produto_id, tipo, quantidade, datetime.now().strftime("%Y-%m-%d %H:%M"), usuario))

    return nome, novo_estoque


def _numero(texto, nome_campo, inteiro=False):
    """Converte texto em número (aceita vírgula). Levanta ValueError com mensagem clara."""
    try:
        valor = int(texto) if inteiro else float(str(texto).replace(",", "."))
    except (TypeError, ValueError):
        raise ValueError(f"{nome_campo} precisa ser um número.")
    if not inteiro and not math.isfinite(valor):
        raise ValueError(f"{nome_campo} precisa ser um número.")
    if valor < 0:
        raise ValueError(f"{nome_campo} não pode ser negativo.")
    return valor


def ler_dados_produto(form, cadastrando):
    """
    Lê e valida os campos do formulário de produto.
    Devolve um dicionário. Levanta ValueError com a mensagem do problema.
    """
    nome = form.get("nome", "").strip()
    if not nome:
        raise ValueError("Preencha o nome do produto.")

    categoria = form.get("categoria", "").strip()[:40] or None

    preco = _numero(form.get("preco", "").strip(), "O preço de venda")

    custo_txt = form.get("custo", "").strip()
    if custo_txt:
        custo = _numero(custo_txt, "O custo")
    elif cadastrando:
        raise ValueError("Informe o custo do produto.")
    else:
        custo = None

    minimo = _numero(form.get("estoque_minimo", "").strip(), "O estoque mínimo", inteiro=True)

    dados = {
        "nome": nome,
        "categoria": categoria,
        "preco": preco,
        "custo": custo,
        "estoque_minimo": minimo,
    }

    if cadastrando:
        dados["quantidade"] = _numero(form.get("quantidade", "").strip(), "A quantidade", inteiro=True)

    return dados


def avisar_se_preco_abaixo_do_custo(dados):
    if dados["custo"] is not None and dados["preco"] < dados["custo"]:
        flash(
            f"Atenção: o preço de venda ({real(dados['preco'])}) está abaixo do custo ({real(dados['custo'])}).",
            "alerta"
        )


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

    cursor.execute("SELECT COUNT(*) FROM produtos WHERE quantidade <= estoque_minimo")
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
def chave_id(produto):
    """Ordena IDs numéricos como números (2 antes de 10) e o resto como texto."""
    texto = str(produto[0])
    return (0, int(texto), "") if texto.isdigit() else (1, 0, sem_acento(texto))


ORDENS = {
    "id": chave_id,
    "nome": lambda p: sem_acento(p[1]),
    "preco": lambda p: p[2],
    "quantidade": lambda p: p[3],
}


@app.route("/produtos")
@login_required
def produtos():
    busca = request.args.get("q", "").strip()
    categoria = request.args.get("categoria", "")
    so_baixo = request.args.get("baixo") == "1"
    ordem = request.args.get("ordem", "nome")
    if ordem not in ORDENS:
        ordem = "nome"
    direcao = "desc" if request.args.get("dir") == "desc" else "asc"

    conn = conectar()
    cursor = conn.cursor()
    cursor.execute(f"SELECT {COLUNAS_PRODUTO} FROM produtos")
    todos = cursor.fetchall()
    conn.close()

    categorias = sorted({p[6] for p in todos if p[6]}, key=sem_acento)

    lista = todos
    if busca:
        termo = sem_acento(busca)
        lista = [p for p in lista if termo in sem_acento(f"{p[1]} {p[0]} {p[6] or ''}")]
    if categoria == "__sem":
        lista = [p for p in lista if not p[6]]
    elif categoria:
        lista = [p for p in lista if p[6] == categoria]
    if so_baixo:
        lista = [p for p in lista if p[3] <= p[5]]

    lista = sorted(lista, key=ORDENS[ordem], reverse=(direcao == "desc"))

    return render_template(
        "produtos.html",
        produtos=lista,
        total=len(todos),
        categorias=categorias,
        busca=busca,
        categoria=categoria,
        so_baixo=so_baixo,
        ordem=ordem,
        direcao=direcao,
        tipo=session["tipo"],
        minimo_padrao=ESTOQUE_MINIMO_PADRAO
    )


@app.route("/add_produto", methods=["POST"])
@login_required
def add_produto():
    id = request.form.get("id", "").strip()

    if not id:
        flash("Preencha o ID do produto.", "erro")
        return redirect("/produtos")

    try:
        dados = ler_dados_produto(request.form, cadastrando=True)
    except ValueError as erro:
        flash(str(erro), "erro")
        return redirect("/produtos")

    conn = conectar()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            INSERT INTO produtos (id, nome, preco, quantidade, custo, estoque_minimo, categoria)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (id, dados["nome"], dados["preco"], dados["quantidade"],
              dados["custo"], dados["estoque_minimo"], dados["categoria"]))
        conn.commit()
        flash("Produto adicionado com sucesso.", "ok")
        avisar_se_preco_abaixo_do_custo(dados)
    except sqlite3.IntegrityError:
        flash(f"Já existe um produto com o ID {id}.", "erro")
    finally:
        conn.close()

    return redirect("/produtos")


@app.route("/produtos/<id>/editar", methods=["GET", "POST"])
@admin_required
def editar_produto(id):
    conn = conectar()
    cursor = conn.cursor()
    cursor.execute(f"SELECT {COLUNAS_PRODUTO} FROM produtos WHERE id=?", (id,))
    produto = cursor.fetchone()

    if not produto:
        conn.close()
        flash("Produto não encontrado.", "erro")
        return redirect("/produtos")

    if request.method == "POST":
        try:
            dados = ler_dados_produto(request.form, cadastrando=False)
        except ValueError as erro:
            cursor.execute("SELECT DISTINCT categoria FROM produtos WHERE categoria IS NOT NULL")
            categorias = sorted((c[0] for c in cursor.fetchall()), key=sem_acento)
            conn.close()
            flash(str(erro), "erro")
            # mostra o formulário de novo, com o que a pessoa já tinha digitado
            return render_template("editar_produto.html", p=produto, form=request.form, categorias=categorias)

        cursor.execute("""
            UPDATE produtos
            SET nome=?, categoria=?, custo=?, preco=?, estoque_minimo=?
            WHERE id=?
        """, (dados["nome"], dados["categoria"], dados["custo"],
              dados["preco"], dados["estoque_minimo"], id))
        conn.commit()
        conn.close()

        flash(f"Produto {dados['nome']} atualizado.", "ok")
        avisar_se_preco_abaixo_do_custo(dados)
        return redirect("/produtos")

    cursor.execute("SELECT DISTINCT categoria FROM produtos WHERE categoria IS NOT NULL")
    categorias = sorted((c[0] for c in cursor.fetchall()), key=sem_acento)
    conn.close()

    return render_template("editar_produto.html", p=produto, form={}, categorias=categorias)


@app.route("/delete_produto/<id>", methods=["POST"])
@admin_required
def delete_produto(id):
    conn = conectar()
    cursor = conn.cursor()

    cursor.execute("BEGIN IMMEDIATE")
    if reservado(cursor, id):
        conn.rollback()
        conn.close()
        flash("Cancele ou finalize os pedidos ativos antes de excluir o produto.", "erro")
        return redirect("/produtos")
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

        conn = conectar()
        cursor = conn.cursor()

        try:
            cursor.execute("BEGIN IMMEDIATE")
            nome, novo_estoque = aplicar_movimentacao(
                cursor, produto_id, tipo, quantidade, session["user"]
            )
            conn.commit()
        except ErroEstoque as erro:
            conn.rollback()
            conn.close()
            flash(str(erro), "erro")
            return voltar_para_movimentar(produto_id, tipo)

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

    cursor.execute("SELECT id, nome, quantidade, estoque_minimo FROM produtos")
    lista_produtos = sorted(cursor.fetchall(), key=lambda p: sem_acento(p[1]))

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
        recentes=recentes
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

    cursor.execute("SELECT id, nome FROM produtos")
    lista_produtos = sorted(cursor.fetchall(), key=lambda p: sem_acento(p[1]))

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
    cursor.execute(f"SELECT {COLUNAS_PRODUTO} FROM produtos")
    produtos = sorted(cursor.fetchall(), key=lambda p: sem_acento(p[1]))
    conn.close()

    total_itens = sum(p[3] for p in produtos)
    valor_venda = sum(p[2] * p[3] for p in produtos)

    # custo e lucro só contam produtos que têm o custo informado
    com_custo = [p for p in produtos if p[4] is not None]
    valor_custo = sum(p[4] * p[3] for p in com_custo)
    lucro_potencial = sum((p[2] - p[4]) * p[3] for p in com_custo)
    sem_custo = len(produtos) - len(com_custo)

    return render_template(
        "relatorio_estoque.html",
        produtos=produtos,
        total_itens=total_itens,
        valor_venda=valor_venda,
        valor_custo=valor_custo,
        lucro_potencial=lucro_potencial,
        sem_custo=sem_custo
    )



# ---------------- PEDIDOS ----------------
@app.route("/pedidos", methods=["GET", "POST"])
@login_required
def pedidos():
    token = session.setdefault("pedido_csrf", secrets.token_urlsafe(32))
    conn = conectar()
    try:
        c = conn.cursor()
        if request.method == "POST":
            if not secrets.compare_digest(token.encode(), request.form.get("csrf", "").encode()):
                return "Requisição inválida", 403
            try:
                c.execute("BEGIN IMMEDIATE")
                if request.form.get("acao") == "criar":
                    ids = request.form.getlist("produto_id")
                    qs = request.form.getlist("quantidade")
                    if len(ids) != len(qs):
                        raise ValueError("Itens inválidos.")
                    itens = [(pid, int(q)) for pid,q in zip(ids,qs) if pid or q]
                    numero = criar_pedido(c, request.form.get("cliente", ""), itens, session["user"])
                    mensagem = f"Pedido #{numero} reservado."
                else:
                    mudar_status(c, int(request.form.get("pedido_id", "")), request.form.get("acao", ""), session["user"], aplicar_movimentacao)
                    mensagem = "Pedido atualizado."
                conn.commit()
                flash(mensagem, "ok")
            except (ValueError, ErroEstoque, sqlite3.Error) as erro:
                conn.rollback()
                flash(str(erro) if not isinstance(erro, sqlite3.Error) else "Não foi possível gravar o pedido. Tente novamente.", "erro")
            return redirect(url_for("pedidos"))
        produtos = [(pid, nome, preco, qtd-reservado(c,pid)) for pid,nome,preco,qtd in c.execute("SELECT id,nome,preco,quantidade FROM produtos ORDER BY nome").fetchall()]
        lista = c.execute("SELECT id,cliente,status,criado_em,criado_por FROM pedidos ORDER BY CASE WHEN status IN ('reservado','separando','pronto') THEN 0 ELSE 1 END, id DESC").fetchall()
        itens = {p[0]: c.execute("SELECT nome,quantidade,preco_centavos FROM pedido_itens WHERE pedido_id=?", (p[0],)).fetchall() for p in lista}
        return render_template("pedidos.html", produtos=produtos, pedidos=lista, itens=itens, transicoes=TRANSICOES, csrf=token)
    finally:
        conn.close()


# ---------------- ASSISTENTE IA (TESTE LOCAL) ----------------
@app.route('/assistente', methods=['GET','POST'])
@login_required
def assistente():
    token=session.setdefault('assistente_csrf',secrets.token_urlsafe(32))
    usuario=session['user']
    cid=session.setdefault('assistente_conversa',secrets.token_urlsafe(24))
    conn=conectar()
    try:
        conn.execute('INSERT OR IGNORE INTO assistente_conversas VALUES (?,?,0,?)',(cid,usuario,json.dumps(estado_novo())))
        conn.commit()
        row=conn.execute('SELECT versao,estado FROM assistente_conversas WHERE id=? AND usuario=?',(cid,usuario)).fetchone()
        if not row:
            session.pop('assistente_conversa',None)
            session.pop('assistente_proposta',None)
            return redirect(url_for('assistente'))
        versao, bruto=row; estado=json.loads(bruto)
        conn.execute('BEGIN')
        try:
            produtos=catalogo(conn.cursor())
        except ErroAssistente as erro:
            produtos={}; flash(str(erro),'erro')
        conn.rollback()
    finally:
        conn.close()
    if request.method=='POST':
        if not secrets.compare_digest(token.encode(),request.form.get('csrf','').encode()):
            return 'Requisição inválida',403
        acao=request.form.get('acao')
        if request.form.get('versao')!=str(versao):
            flash('A conversa mudou. Confira o estado atual antes de continuar.','erro')
            return redirect(url_for('assistente'))
        if acao in ('analisar','reiniciar'):
            session.pop('assistente_proposta',None)
            try:
                novo, apto = (estado_novo(),False) if acao=='reiniciar' else responder(request.form.get('mensagem','').strip(),produtos,estado)
                conn=conectar()
                try:
                    cur=conn.execute('UPDATE assistente_conversas SET versao=versao+1,estado=? WHERE id=? AND usuario=? AND versao=?',(json.dumps(novo,ensure_ascii=False),cid,usuario,versao))
                    if cur.rowcount!=1:
                        raise ErroAssistente('A conversa mudou durante a análise. Tente novamente.')
                    conn.commit()
                finally:
                    conn.close()
                if apto:
                    linhas=linhas_carrinho(novo,produtos)
                    session['assistente_proposta']=dict(token=secrets.token_urlsafe(24),expira=int(time.time())+600,user=usuario,cid=cid,versao=versao+1,itens=[[i['id'],i['quantidade'],i['preco_centavos'],i['nome']] for i in linhas])
            except (ErroAssistente,sqlite3.Error) as erro:
                flash(str(erro) if isinstance(erro,ErroAssistente) else 'Não foi possível consultar o estoque.','erro')
            return redirect(url_for('assistente'))
        elif acao=='confirmar':
            r=session.get('assistente_proposta')
            if not r or r.get('cid')!=cid or r.get('versao')!=versao or r['user']!=usuario or r['expira']<time.time() or not secrets.compare_digest(r['token'].encode(),request.form.get('proposta','').encode()) or request.form.get('conferido')!='sim':
                flash('Proposta inválida, expirada ou não conferida. Analise novamente.','erro')
                return redirect(url_for('assistente'))
            conn=conectar()
            try:
                c=conn.cursor(); c.execute('BEGIN IMMEDIATE')
                if c.execute('SELECT versao FROM assistente_conversas WHERE id=? AND usuario=?',(cid,usuario)).fetchone()!=(versao,):
                    raise ValueError('A conversa mudou. Analise novamente.')
                if c.execute('SELECT pedido_id FROM assistente_confirmacoes WHERE token=?',(r['token'],)).fetchone():
                    raise ValueError('Esta proposta já foi confirmada.')
                if [[pid,qtd] for pid,qtd,_,_ in r['itens']]!=estado['carrinho']:
                    raise ValueError('O carrinho mudou. Analise novamente.')
                for pid,qtd,preco,nome in r['itens']:
                    atual=c.execute('SELECT preco,nome FROM produtos WHERE id=?',(pid,)).fetchone()
                    if not atual or centavos(atual[0])!=preco or atual[1]!=nome:
                        raise ValueError('O catálogo ou preço mudou. Analise novamente.')
                numero=criar_pedido(c,request.form.get('cliente',''),[(pid,qtd) for pid,qtd,_,_ in r['itens']],usuario)
                c.execute('INSERT INTO assistente_confirmacoes(token,pedido_id) VALUES (?,?)',(r['token'],numero))
                estado['carrinho']=[]
                estado['historico']=(estado['historico']+[{'role':'assistente','texto':f'Pedido #{numero} reservado pelo funcionário.'}])[-20:]
                c.execute('UPDATE assistente_conversas SET versao=versao+1,estado=? WHERE id=? AND usuario=?',(json.dumps(estado,ensure_ascii=False),cid,usuario))
                conn.commit(); session.pop('assistente_proposta',None)
                flash(f'Pedido #{numero} reservado a partir da proposta da IA.','ok')
                return redirect(url_for('pedidos'))
            except (ValueError,sqlite3.Error) as erro:
                conn.rollback(); session.pop('assistente_proposta',None)
                flash(str(erro) if isinstance(erro,ValueError) else 'Não foi possível confirmar o pedido.','erro')
                return redirect(url_for('assistente'))
            finally:
                conn.close()
        else:
            return 'Ação inválida',400
    linhas=linhas_carrinho(estado,produtos)
    r=session.get('assistente_proposta') or {}
    proposta=r.get('token') if r.get('cid')==cid and r.get('versao')==versao and r.get('user')==usuario and r.get('expira',0)>=time.time() and linhas and all(i['quantidade']<=i['disponivel'] for i in linhas) and r.get('itens')==[[i['id'],i['quantidade'],i['preco_centavos'],i['nome']] for i in linhas] else None
    return render_template('assistente.html',csrf=token,versao=versao,historico=estado['historico'],linhas=linhas,total=sum(i['subtotal_centavos'] for i in linhas),proposta=proposta)


if __name__ == "__main__":
    app.run(debug=True)
