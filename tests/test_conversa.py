import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import unittest, sqlite3
from unittest.mock import patch
from models.conversa import estado_novo, responder, linhas_carrinho
from models.assistente import catalogo, ErroAssistente
from models.pedido import criar_tabelas_pedidos, criar_pedido
from jinja2 import Environment, FileSystemLoader, select_autoescape

class ConversaTest(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:')
        self.db.execute('CREATE TABLE produtos(id TEXT PRIMARY KEY,nome TEXT,preco REAL,quantidade INTEGER,categoria TEXT)')
        self.db.executemany('INSERT INTO produtos VALUES (?,?,?,?,?)',[('5','Pastilha de Freio TESTE',35,7,'freio'),('4','Capacete LS2',500,6,'capacetes')])
        criar_tabelas_pedidos(self.db)
        self.db.commit();self.prod=catalogo(self.db);self.estado=estado_novo()
    def tearDown(self): self.db.close()
    def resposta(self,intencao,itens,msg='mensagem'):
        self.estado,apto=responder(msg,self.prod,self.estado,lambda *_:{'intencao':intencao,'itens':[dict(id=i,quantidade=q) for i,q in itens]})
        return apto
    def test_contexto_e_carrinho(self):
        self.assertFalse(self.resposta('consulta',[('5',0)],'Tem pastilha?'))
        with patch('models.assistente.chamar_ollama',return_value={'intencao':'pedido','itens':[{'id':'5','quantidade':2}]}) as m:
            self.estado,apto=responder('Quero duas',self.prod,self.estado)
            self.assertTrue(apto);self.assertEqual(m.call_args.args[2]['foco'],['5'])
        self.assertTrue(self.resposta('pedido',[('5',2),('4',1)],'E um capacete'))
        self.assertEqual(sum(i['subtotal_centavos'] for i in linhas_carrinho(self.estado,self.prod)),57000)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM pedidos').fetchone()[0],0)
    def test_sem_estoque(self):
        self.assertFalse(self.resposta('pedido',[('5',50)]))
        self.assertIn('7 disponíveis',self.estado['historico'][-1]['texto'])
    def test_consulta_sem_alterar_carrinho(self):
        self.assertTrue(self.resposta('pedido',[('5',2)]))
        self.assertFalse(self.resposta('consulta',[('5',0)],'Por 1 real?'))
        self.assertEqual(self.estado['carrinho'],[['5',2]])
        self.assertIn('R$ 35,00',self.estado['historico'][-1]['texto'])
    def test_desconhecido(self):
        self.assertFalse(self.resposta('esclarecer',[],'Pneu de caminhão?'))
        self.assertEqual(self.estado['carrinho'],[])
        with self.assertRaises(ErroAssistente):self.resposta('pedido',[('999',1)])
    def test_reserva_revalidada(self):
        self.assertTrue(self.resposta('pedido',[('5',7)]))
        self.db.execute('BEGIN IMMEDIATE');criar_pedido(self.db.cursor(),'teste',[('5',2)],'admin');self.db.commit()
        self.db.execute('BEGIN IMMEDIATE')
        with self.assertRaises(ValueError):criar_pedido(self.db.cursor(),'teste',[('5',7)],'admin')
        self.db.rollback();self.assertEqual(self.db.execute('SELECT COUNT(*) FROM pedidos').fetchone()[0],1)
    def test_html(self):
        self.assertTrue(self.resposta('pedido',[('5',2)],'<script>alert(1)</script>'))
        env=Environment(loader=FileSystemLoader(str(Path(__file__).resolve().parents[1]/'templates')),autoescape=select_autoescape())
        env.filters['real']=lambda x:f'R$ {x:.2f}'
        env.globals.update(url_for=lambda *a,**k:'#',session={'user':'admin','tipo':'admin'},request=type('R',(),{'path':'/assistente'})(),get_flashed_messages=lambda **k:[])
        html=env.get_template('assistente.html').render(csrf='csrf',versao=1,historico=self.estado['historico'],linhas=linhas_carrinho(self.estado,self.prod),total=7000,proposta='token')
        self.assertIn('Confirmar e reservar pedido',html);self.assertIn('&lt;script&gt;',html)
        html=env.get_template('assistente.html').render(csrf='csrf',versao=0,historico=[],linhas=[],total=0,proposta=None)
        self.assertIn('Nenhum item no carrinho',html);self.assertNotIn('Confirmar e reservar pedido',html)
if __name__=='__main__':unittest.main()
