import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

# Bancos de dados temporários na memória do PC
fila_veiculos = []
historico_veiculos = [] 

class ServidorFilaHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global fila_veiculos, historico_veiculos
        
        diretorio_atual = os.path.dirname(os.path.abspath(__file__))
        
        # 1. Rota principal (Página Web)
        if self.path == '/' or self.path == '/index.html':
            self.send_response(200)
            self.send_header('Content-type', 'text/html; charset=utf-8')
            self.end_headers()
            
            caminho_index = os.path.join(diretorio_atual, 'index.html')
            try:
                with open(caminho_index, 'r', encoding='utf-8') as f:
                    self.wfile.write(f.read().encode('utf-8'))
            except FileNotFoundError:
                self.wfile.write(b"Erro: Arquivo index.html nao encontrado!")
                
        # 2. ROTA INTELIGENTE PARA IMAGENS (Ignora maiusculas/minusculas)
        elif self.path.lower().endswith('.png') or self.path.lower().endswith('.jpg'):
            nome_arquivo_pedido = self.path.lstrip('/')
            encontrou = False
            
            # Procura o arquivo na pasta ignorando letras maiúsculas/minúsculas
            for arquivo_na_pasta in os.listdir(diretorio_atual):
                if arquivo_na_pasta.lower() == nome_arquivo_pedido.lower():
                    caminho_imagem = os.path.join(diretorio_atual, arquivo_na_pasta)
                    try:
                        with open(caminho_imagem, 'rb') as f:
                            self.send_response(200)
                            if arquivo_na_pasta.lower().endswith('.png'):
                                self.send_header('Content-type', 'image/png')
                            else:
                                self.send_header('Content-type', 'image/jpeg')
                            self.end_headers()
                            self.wfile.write(f.read())
                            encontrou = True
                            break
                    except Exception as e:
                        pass
            
            if not encontrou:
                self.send_response(404)
                self.end_headers()
        
        # 3. Rota da Fila da Garagem
        elif self.path == '/api/fila':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(fila_veiculos).encode('utf-8'))

        # 4. Rota do Histórico/LOG
        elif self.path == '/api/historico':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(historico_veiculos).encode('utf-8'))
            
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        global fila_veiculos, historico_veiculos
        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length)
        dados = json.loads(post_data.decode('utf-8'))

        if self.path == '/api/adicionar':
            for carro in fila_veiculos:
                if carro["prisma"] == dados["prisma"]:
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b"DUPLICADO")
                    return

            novo_carro = {
                "id": dados["id"],
                "prisma": dados["prisma"],
                "hora": dados["hora"]
            }
            fila_veiculos.append(novo_carro)
            historico_veiculos.insert(0, novo_carro)
            historico_veiculos = historico_veiculos[:20] 
            
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")

        elif self.path == '/api/baixa':
            fila_veiculos = [c for c in fila_veiculos if c["id"] != dados["id"]]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")

def run():
    server_address = ('', 8080)
    httpd = HTTPServer(server_address, ServidorFilaHandler)
    print("====================================================")
    print(" SERVIDOR DO VALET EM EXECUCAO (Porta 8080)         ")
    print("====================================================")
    httpd.serve_forever()

if __name__ == '__main__':
    run()