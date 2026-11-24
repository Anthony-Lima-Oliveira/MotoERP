class Usuario:
    def __init__(self, nome, senha):
        self.nome = nome
        self.senha = senha

    def tipo(self):
        return "usuario"


class Administrador(Usuario):
    def tipo(self):
        return "admin"


class Operador(Usuario):
    def tipo(self):
        return "operador"