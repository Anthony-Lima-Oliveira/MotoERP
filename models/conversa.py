"""Contexto de atendimento local, sem executar operações de estoque."""
import json
import re
from models.assistente import interpretar, ErroAssistente


def estado_novo():
    return {'historico': [], 'carrinho': [], 'foco': []}


def linhas_carrinho(estado, produtos):
    linhas = []
    for pid, qtd in estado['carrinho']:
        p = produtos.get(pid)
        if p is None:
            linhas.append(dict(id=pid, nome='Produto removido do catálogo', quantidade=qtd, disponivel=0, preco_centavos=0, subtotal_centavos=0))
        else:
            linhas.append(dict(**p, quantidade=qtd, subtotal_centavos=qtd*p['preco_centavos']))
    return linhas


def reais(c):
    return 'R$ '+f'{c//100:,}'.replace(',','.')+f',{c%100:02d}'


def responder(mensagem, produtos, estado, modelo=None):
    if not mensagem or len(mensagem)>1000:
        raise ErroAssistente('Digite uma mensagem de 1 a 1000 caracteres.')
    # O histórico enviado ao modelo é pequeno; não contém comandos executáveis.
    contexto = {**estado, 'historico': estado['historico'][-8:]}
    resultado = interpretar(mensagem, produtos, modelo=modelo, contexto=contexto)
    itens = resultado['itens']; intencao = resultado['intencao']
    estado = json.loads(json.dumps(estado))
    if intencao == 'pedido':
        estado['carrinho'] = [[i['id'],i['quantidade']] for i in itens]
        estado['foco'] = [i['id'] for i in itens]
        falta = [i for i in itens if i['quantidade']>i['disponivel']]
        if falta:
            texto = 'Não consigo reservar tudo: '+ '; '.join(f"{i['nome']}: pediu {i['quantidade']}, temos {i['disponivel']} disponíveis" for i in falta)+'. Quer ajustar as quantidades?'
        else:
            texto = 'Montei seu carrinho: '+ '; '.join(f"{i['quantidade']} x {i['nome']} ({reais(i['preco_centavos'])} cada)" for i in itens)+f". Total: {reais(resultado['total_centavos'])}. Confira se está correto. A reserva depende de confirmação do funcionário."
    elif intencao == 'consulta':
        estado['foco'] = [i['id'] for i in itens]
        texto = 'No catálogo temos: '+ '; '.join(f"{i['nome']}: {reais(i['preco_centavos'])} cada, {i['disponivel']} disponíveis" for i in itens)+'. Estes são os preços cadastrados; não consigo conceder descontos. Quer comprar alguma quantidade?'
    else:
        if re.fullmatch(r'\s*(oi|ol[aá]|opa|bom dia|boa tarde|boa noite)[!.?\s]*',mensagem,re.I):
            texto = 'Olá! Qual peça ou acessório você procura?'
        else:
            texto = 'Não consegui identificar sua solicitação com segurança no catálogo. Pode informar o nome da peça, a moto/ano se necessário e a quantidade, caso queira comprar? Nenhuma reserva foi feita.'
    estado['historico'] = (estado['historico'] + [{'role':'cliente','texto':mensagem},{'role':'assistente','texto':texto}])[-20:]
    # Consultas e pedidos ambíguos não autorizam confirmação de um carrinho antigo.
    return estado, resultado['pode_reservar'] and intencao=='pedido'
