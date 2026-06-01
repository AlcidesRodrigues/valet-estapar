import json
import os
import mimetypes
import base64
from http.server import BaseHTTPRequestHandler, HTTPServer

# Bancos de dados temporários
fila_veiculos = []
historico_veiculos = [] 

# ========================================================
# 🔒 CONFIGURAÇÃO DE ACESSO (USUÁRIO E SENHA)
# ========================================================
USUARIO = "estapar"
SENHA = "123"
# ========================================================

# Prepara a chave de segurança
CHAVE_AUTH = base64.b64encode(f"{USUARIO}:{SENHA}".encode('utf-8')).decode('utf-8')
AUTH_ESPERADO = f"Basic {CHAVE_AUTH}"

class ServidorFilaHandler(BaseHTTPRequestHandler):
    
    # Função que cria o cadeado na porta do servidor
    def verificar_autenticacao(self):
        auth_header = self.headers.get('Authorization')
        if auth_header == AUTH_ESPERADO:
            return True
        else:
            # Trava a requisição e pede a senha
            self.send_response(401)
            self.send_header('WWW-Authenticate', 'Basic realm="Acesso Restrito - Valet"')
            self.end_headers()
            self.wfile.write(b"Acesso Negado. Digite o usuario e senha corretos.")
            return False

    def do_GET(self):
        # Bate na porta: tem a senha? Se não tiver, bloqueia aqui mesmo!
        if not self.verificar_autenticacao():
            return

        global fila_veiculos, historico_veiculos
        diretorio_atual = os.path.dirname(os.path.abspath(__file__))
        
        if self.path == '/api/fila':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(fila_veiculos).encode('utf-8'))
            return
            
        elif self.path == '/api/historico':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(historico_veiculos).encode('utf-8'))
            return

        caminho_relativo = self.path.lstrip('/')
        if caminho_relativo == '' or caminho_relativo == 'index.html':
            caminho_relativo = 'index.html'
            
        caminho_absoluto = os.path.join(diretorio_atual, caminho_relativo)
        
        if os.path.isfile(caminho_absoluto):
            try:
                with open(caminho_absoluto, 'rb') as f:
                    self.send_response(200)
                    tipo_mime, _ = mimetypes.guess_type(caminho_absoluto)
                    if tipo_mime:
                        self.send_header('Content-type', tipo_mime)
                    self.end_headers()
                    self.wfile.write(f.read())
                return
            except Exception as e:
                print(f"Erro ao carregar arquivo: {e}")
        
        self.send_response(404)
        self.end_headers()
        self.wfile.write(b"404 - Nao encontrado")

    def do_POST(self):
        # API também protegida por senha!
        if not self.verificar_autenticacao():
            return

        global fila_veiculos, historico_veiculos
        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length)
        dados = json.loads(post_data.decode('utf-8'))

        if self.path == '/api/adicionar':
            prisma_solicitado = str(dados["prisma"]).strip()
            
            for carro in fila_veiculos:
                if str(carro["prisma"]).strip() == prisma_solicitado:
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b"DUPLICADO")
                    return

            novo_carro = {
                "id": dados["id"],
                "prisma": prisma_solicitado,
                "hora": dados["hora"]
            }
            fila_veiculos.append(novo_carro)
            historico_veiculos.insert(0, novo_carro)
            historico_veiculos = historico_veiculos[:20] 
            
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")
            print(f"[CAIXA] Identificador {prisma_solicitado} solicitado as {dados['hora']}.")

        elif self.path == '/api/baixa':
            fila_veiculos = [c for c in fila_veiculos if c["id"] != dados["id"]]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")
            print(f"[GARAGEM] Veiculo baixado.")

def run():
    porta = int(os.environ.get('PORT', 8080))
    server_address = ('0.0.0.0', porta) 
    httpd = HTTPServer(server_address, ServidorFilaHandler)
    print(f"SERVIDOR DO VALET RODANDO NA NUVEM NA PORTA {porta}")
    httpd.serve_forever()

if __name__ == '__main__':
    run()
