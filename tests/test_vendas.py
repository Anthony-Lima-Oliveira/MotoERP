"""Testes com banco temporário. Rodar: py -m unittest discover -s tests -v."""
import contextlib
import io
import os
import sys
import tempfile
import unittest
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from database import criar_tabelas, conectar
from models.pedido import criar_pedido, mudar_status, reservado
from models.venda import registrar_balcao, consultar_vendas, intervalo_periodo, em_centavos

# Importar o app inicia o banco. Faça isso somente dentro de uma pasta de teste.
# Em ambientes sem Flask, o fallback permite testar o modelo usando a função
# de estoque original extraída da árvore sintática; os testes HTTP são pulados.
try:
    import flask
    TEM_FLASK = True
except ImportError:
    TEM_FLASK = False


def estoque_original():
    import ast
    arvore = ast.parse((ROOT / 'app.py').read_text(encoding='utf-8'))
    nomes = {'ErroEstoque', 'aplicar_movimentacao'}
    parte = ast.Module(body=[n for n in arvore.body if getattr(n, 'name', None) in nomes], type_ignores=[])
    escopo = {'datetime': datetime, 'reservado': reservado}
    exec(compile(parte, str(ROOT / 'app.py'), 'exec'), escopo)
    return escopo['ErroEstoque'], escopo['aplicar_movimentacao']


class VendasTest(unittest.TestCase):
    def setUp(self):
        self.pasta = tempfile.TemporaryDirectory()
        self.cwd = os.getcwd()
        os.chdir(self.pasta.name)
        self.addCleanup(self.pasta.cleanup)
        self.addCleanup(os.chdir, self.cwd)
        criar_tabelas()
        self.db = conectar()
        self.addCleanup(self.db.close)
        self.db.executemany('INSERT INTO produtos(id,nome,preco,quantidade,custo) VALUES (?,?,?,?,?)',
            [('a','Pastilha',35,10,20),('b','Capacete',500,1,320),('c','Produto sem custo',10,4,None)])
        self.db.commit()
        self.erro, self.mov = estoque_original()

    def venda(self, itens, token='a'*32):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            n = registrar_balcao(self.db.cursor(), '', itens, 'admin', token, self.mov)
            self.db.commit()
            return n
        except Exception:
            self.db.rollback()
            raise

    def resumo(self, admin=True):
        return consultar_vendas(self.db.cursor(), *intervalo_periodo('hoje', date.today()), admin)

    def estoque(self, pid='a'):
        return self.db.execute('SELECT quantidade FROM produtos WHERE id=?',(pid,)).fetchone()[0]

    def reserva(self, qtd=3):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            return criar_pedido(self.db.cursor(), 'Cliente', [('a',qtd)], 'admin')

    def status(self, pid, destino):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            mudar_status(self.db.cursor(), pid, destino, 'admin', self.mov)

    def test_balcao_e_lucro_historico(self):
        self.venda([('a',2,3500),('b',1,50000)])
        self.assertEqual((self.estoque(),self.estoque('b')),(8,0))
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM movimentacoes').fetchone()[0],2)
        self.db.execute('UPDATE produtos SET custo=99,preco=100 WHERE id="a"'); self.db.commit()
        resumo, lista = self.resumo()
        self.assertEqual((resumo['total'],resumo['custo'],resumo['lucro']),(57000,36000,21000))
        self.assertEqual(lista[0]['origem'],'balcao')

    def test_reserva_protegida_e_rollback(self):
        self.reserva(8)
        with self.assertRaises(self.erro): self.venda([('b',1,50000),('a',3,3500)])
        self.assertEqual((self.estoque(),self.estoque('b')),(10,1))
        self.assertEqual(self.resumo()[0]['quantidade'],0)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM movimentacoes').fetchone()[0],0)

    def test_token_repetido(self):
        self.venda([('a',1,3500)])
        with self.assertRaises(ValueError): self.venda([('a',1,3500)])
        self.assertEqual(self.estoque(),9)
        self.assertEqual(self.resumo()[0]['quantidade'],1)

    def test_preco_adulterado_ou_desatualizado(self):
        with self.assertRaises(ValueError): self.venda([('a',2,100)])
        self.assertEqual(self.estoque(),10)

    def test_itens_repetidos(self):
        self.venda([('a',2,3500),('a',3,3500)])
        self.assertEqual(self.estoque(),5)
        self.assertEqual(self.resumo()[0]['total'],17500)

    def test_finalizacao_registra_sem_baixa_duplicada(self):
        p = self.reserva()
        self.db.execute('UPDATE produtos SET preco=50,custo=25 WHERE id="a"'); self.db.commit()
        self.status(p,'separando'); self.status(p,'pronto'); self.status(p,'finalizado')
        self.assertEqual(self.estoque(),7)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM movimentacoes').fetchone()[0],1)
        resumo, lista = self.resumo()
        self.assertEqual((resumo['total'],resumo['lucro']),(10500,3000))
        self.assertEqual(lista[0]['pedido_id'],p)
        with self.assertRaises(ValueError): self.status(p,'finalizado')
        self.assertEqual(self.estoque(),7)
        self.assertEqual(self.resumo()[0]['quantidade'],1)

    def test_ia_origem_e_cancelamento(self):
        p = self.reserva(2)
        self.db.execute('INSERT INTO assistente_confirmacoes VALUES (?,?)',('t',p));self.db.commit()
        self.status(p,'separando');self.status(p,'pronto');self.status(p,'finalizado')
        self.assertEqual(self.resumo()[1][0]['origem'],'assistente')
        p2 = self.reserva(3);self.status(p2,'cancelado')
        self.assertEqual(self.estoque(),8)
        self.assertEqual(self.resumo()[0]['quantidade'],1)

    def test_sem_custo_e_operador(self):
        self.venda([('c',1,1000)])
        resumo, lista = self.resumo()
        self.assertIsNone(resumo['lucro']);self.assertIsNone(lista[0]['lucro'])
        self.assertEqual(resumo['sem_custo'],1)
        resumo, lista = self.resumo(False)
        for campo in ('custo','lucro','margem','sem_custo'):
            self.assertNotIn(campo,resumo);self.assertNotIn(campo,lista[0])
        self.assertNotIn('custo',lista[0]['itens'][0])

    def test_migracao_idempotente_e_periodos(self):
        self.venda([('a',1,3500)])
        criar_tabelas(); self.assertEqual(self.resumo()[0]['quantidade'],1)
        self.assertEqual(intervalo_periodo('mes',date(2026,12,31)),('2026-12-01','2027-01-01'))
        self.assertEqual(intervalo_periodo('semana',date(2026,11,26)),('2026-11-23','2026-11-30'))
        self.assertEqual(em_centavos('10.005'),1001)
        for v in ('NaN','-1','Infinity'):
            with self.assertRaises(ValueError): em_centavos(v)

    def test_pedido_antigo_nao_importado_e_saida_nao_e_venda(self):
        p = self.reserva(2)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            self.db.execute("UPDATE pedidos SET status='finalizado' WHERE id=?", (p,))
            self.mov(self.db.cursor(), 'a', 'saida', 2, 'admin')
        criar_tabelas()
        self.assertEqual(self.resumo()[0]['quantidade'],0)
        self.assertEqual(self.estoque(),8)

    def test_falha_financeira_reverte_finalizacao(self):
        p = self.reserva(2)
        self.status(p,'separando'); self.status(p,'pronto')
        self.db.execute('UPDATE produtos SET custo=-1 WHERE id="a"'); self.db.commit()
        with self.assertRaises(ValueError): self.status(p,'finalizado')
        self.assertEqual(self.estoque(),10)
        self.assertEqual(reservado(self.db.cursor(),'a'),2)
        self.assertEqual(self.db.execute('SELECT status FROM pedidos WHERE id=?',(p,)).fetchone()[0],'pronto')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM movimentacoes').fetchone()[0],0)
        self.assertEqual(self.resumo()[0]['quantidade'],0)

    def test_concorrencia_duas_vendas(self):
        barreira = threading.Barrier(2)
        def tentar(token):
            db=conectar()
            try:
                barreira.wait(timeout=5)
                db.execute('BEGIN IMMEDIATE')
                registrar_balcao(db.cursor(),'', [('a',7,3500)], 'admin', token, self.mov)
                db.commit();return True
            except self.erro:
                db.rollback();return False
            finally: db.close()
        with ThreadPoolExecutor(max_workers=2) as ex:
            resultados=list(ex.map(tentar,['x'*32,'y'*32]))
        self.assertEqual(sorted(resultados),[False,True]);self.assertEqual(self.estoque(),3)
        self.assertEqual(self.resumo()[0]['quantidade'],1)

    @unittest.skipUnless(TEM_FLASK, 'Flask indisponível: teste HTTP não executado')
    def test_rotas_flask(self):
        import importlib
        from unittest.mock import patch
        with patch.dict(os.environ, {'MOTOERP_SECRET_KEY':'teste-local-nao-usar-em-producao'}), contextlib.redirect_stdout(io.StringIO()):
            app = importlib.import_module('app').app
        app.config['TESTING']=True
        client=app.test_client()
        self.assertEqual(client.get('/vendas').status_code,302)
        with client.session_transaction() as sess:
            sess.update(user='admin',tipo='admin',csrf='teste-csrf')
        dados={'produto_id':'a','quantidade':'2','preco_visto':'3500','venda_token':'h'*32,'csrf':'errado'}
        client.post('/vendas',data=dados);self.assertEqual(self.estoque(),10)
        dados['csrf']='teste-csrf'
        self.assertEqual(client.post('/vendas',data=dados).status_code,302)
        self.assertEqual(self.estoque(),8)
        html=client.get('/vendas').get_data(as_text=True)
        self.assertIn('Lucro bruto',html);self.assertIn('R$ 30,00',html)
        with client.session_transaction() as sess: sess['tipo']='operador'
        html=client.get('/vendas').get_data(as_text=True)
        self.assertNotIn('Lucro bruto',html);self.assertNotIn('R$ 30,00',html)


if __name__ == '__main__':
    unittest.main()
