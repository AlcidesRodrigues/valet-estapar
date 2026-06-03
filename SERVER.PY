import json
import os
import mimetypes
import base64
from http.server import BaseHTTPRequestHandler, HTTPServer

# Bancos de dados temporários
fila_veiculos = []
historico_veiculos = [] 

# ========================================================
# 🔒 CONFIGURAÇÃO DE ACESSOS (NÍVEIS DE USUÁRIO)
# ========================================================
USUARIOS = {
    "estapar": "123",   # Acesso Total
    "caixa": "123",     # Acesso Caixa + Solicitações
    "operador": "123",  # Acesso só Solicitações
    "operador2": "123"  # Acesso só Painel Cliente
}
# ========================================================

class ServidorFilaHandler(BaseHTTPRequestHandler):
    
    def log_message(self, format, *args):
        pass

    # Função que descobre quem está acessando agora
    def get_usuario_autenticado(self):
        auth_header = self.headers.get('Authorization')
        if auth_header and auth_header.startswith('Basic '):
            try:
                encoded_credentials = auth_header.split(' ')[1]
                decoded_credentials = base64.b64decode(encoded_credentials).decode('utf-8')
                username, _ = decoded_credentials.split(':', 1)
                return username.lower()
            except:
                return None
        return None

    # Função que verifica se o login e a senha batem com a nossa lista
    def verificar_autenticacao(self):
        auth_header = self.headers.get('Authorization')
        if auth_header and auth_header.startswith('Basic '):
            try:
                encoded_credentials = auth_header.split(' ')[1]
                decoded_credentials = base64.b64decode(encoded_credentials).decode('utf-8')
                username, password = decoded_credentials.split(':', 1)
                username = username.lower()
                
                if username in USUARIOS and USUARIOS[username] == password:
                    return True
            except:
                pass

        self.send_response(401)
        self.send_header('WWW-Authenticate', 'Basic realm="Acesso Restrito"')
        self.end_headers()
        self.wfile.write(b"Acesso Negado.")
        return False

    def do_HEAD(self):
        if self.path == '/api/ping':
            self.send_response(200)
            self.end_headers()
            return
        self.send_response(404)
        self.end_headers()

    def do_GET(self):
        global fila_veiculos, historico_veiculos
        diretorio_atual = os.path.dirname(os.path.abspath(__file__))
        
        # ROTA PÚBLICA PARA O ROBÔ
        if self.path == '/api/ping':
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b"PONG")
            return

        # CADEADO DE SEGURANÇA
        if not self.verificar_autenticacao():
            return
            
        # NOVA ROTA: PERGUNTA AO SERVIDOR QUEM SOU EU?
        if self.path == '/api/perfil':
            usuario = self.get_usuario_autenticado()
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"usuario": usuario}).encode('utf-8'))
            return
        
        # ROTA DE DADOS
        if self.path == '/api/dados':
            tempos = [c.get("tempo_espera_ms", 0) for c in historico_veiculos if "tempo_espera_ms" in c]
            tempo_medio = sum(tempos) / len(tempos) if tempos else 600000
            
            resposta = {
                "fila": fila_veiculos,
                "historico": historico_veiculos,
                "tempo_medio": tempo_medio
            }
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(resposta).encode('utf-8'))
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
        if not self.verificar_autenticacao():
            return

        global fila_veiculos, historico_veiculos
        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length)
        dados = json.loads(post_data.decode('utf-8'))

        if self.path == '/api/adicionar':
            prisma_solicitado = str(dados.get("prisma", "")).strip()
            
            for carro in fila_veiculos:
                if str(carro.get("prisma", "")).strip() == prisma_solicitado:
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b"DUPLICADO")
                    return

            novo_carro = {
                "id": dados["id"],
                "placa": str(dados.get("placa", "")).strip().upper(),
                "modelo": str(dados.get("modelo", "")).strip().upper(),
                "prisma": prisma_solicitado,
                "hora_solicitacao": dados.get("hora_solicitacao", "")
            }
            fila_veiculos.append(novo_carro)
            
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")

        elif self.path == '/api/baixa':
            carro_id = dados.get("id")
            hora_baixa = dados.get("hora_baixa")
            timestamp_baixa = dados.get("timestamp_baixa", 0)
            
            carro_alvo = next((c for c in fila_veiculos if c["id"] == carro_id), None)
            
            if carro_alvo:
                carro_alvo["hora_baixa"] = hora_baixa
                carro_alvo["tempo_espera_ms"] = timestamp_baixa - carro_alvo["id"]
                
                historico_veiculos.insert(0, carro_alvo)
                historico_veiculos = historico_veiculos[:20] 
                
                fila_veiculos = [c for c in fila_veiculos if c["id"] != carro_id]

            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")

def run():
    porta = int(os.environ.get('PORT', 8080))
    server_address = ('0.0.0.0', porta) 
    httpd = HTTPServer(server_address, ServidorFilaHandler)
    print(f"SERVIDOR INICIADO COM SUCESSO NA PORTA {porta}")
    httpd.serve_forever()

if __name__ == '__main__':
    run()
