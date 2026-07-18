import json
import os
import sys
import time
import mimetypes
import base64
import csv
import io
import traceback
from http.server import BaseHTTPRequestHandler, HTTPServer

# ======================================================================
# ARMAZENAMENTO PERSISTENTE (Google Cloud Storage)
# ======================================================================
# PROBLEMA ORIGINAL: os dados eram salvos apenas em /tmp/dados_valet.json.
# No Google App Engine (Standard, instancia F1), a pasta /tmp e um disco
# EFEMERO e local da instancia. Quando o App Engine recicla a instancia
# (por ociosidade, por um novo deploy, por falta de memoria, por
# manutencao do proprio Google) uma instancia NOVA sobe do zero e o /tmp
# vem vazio. Como nao ha "min_instances" fixo, isso acontece com
# frequencia -> e o motivo dos dados "sumirem do nada".
#
# SOLUCAO: os dados agora sao gravados em um bucket do Google Cloud
# Storage (o bucket padrao do App Engine, "SEU-PROJETO.appspot.com",
# que ja existe e ja tem permissao). O /tmp continua sendo usado como
# cache local de leitura rapida, mas a fonte da verdade e o GCS.
# ======================================================================

try:
    from google.cloud import storage as gcs_storage
    GCS_DISPONIVEL = True
except ImportError:
    GCS_DISPONIVEL = False

NOME_ARQUIVO = 'dados_valet.json'
CAMINHO_LOCAL_CACHE = '/tmp/' + NOME_ARQUIVO
NOME_BUCKET_ENV = os.environ.get('BUCKET_DADOS', '').strip()

_cliente_gcs = None
_bucket_gcs = None
_ultima_data_backup = None  # controla o backup diario (1x por dia)


def log(msg):
    """Log simples que aparece no Cloud Logging do App Engine."""
    print(msg, file=sys.stderr)
    sys.stderr.flush()


def obter_bucket():
    """Conecta (uma vez) ao bucket do Cloud Storage. Retorna None se indisponivel."""
    global _cliente_gcs, _bucket_gcs
    if not GCS_DISPONIVEL:
        return None
    if _bucket_gcs is not None:
        return _bucket_gcs
    try:
        nome_bucket = NOME_BUCKET_ENV
        if not nome_bucket:
            project_id = os.environ.get('GOOGLE_CLOUD_PROJECT') or \
                         os.environ.get('GAE_APPLICATION', '').split('~')[-1]
            if project_id:
                nome_bucket = project_id + '.appspot.com'
        if not nome_bucket:
            log("[AVISO] Nao foi possivel determinar o bucket do Cloud Storage.")
            return None
        _cliente_gcs = gcs_storage.Client()
        _bucket_gcs = _cliente_gcs.bucket(nome_bucket)
        log("[INFO] Conectado ao bucket: " + nome_bucket)
        return _bucket_gcs
    except Exception:
        log("[ERRO] Falha ao conectar ao Cloud Storage:")
        log(traceback.format_exc())
        return None


def _estrutura_padrao():
    return {"fila": [], "historico": [], "colaboradores": [], "atalhos": [], "logs_acesso": []}


# Bancos de dados em memoria
fila_veiculos = []
historico_veiculos = []
colaboradores = []
atalhos = []
logs_acesso = []


def carregar_dados():
    global fila_veiculos, historico_veiculos, colaboradores, atalhos, logs_acesso
    dados = None

    bucket = obter_bucket()
    if bucket:
        try:
            blob = bucket.blob(NOME_ARQUIVO)
            if blob.exists():
                conteudo = blob.download_as_text(encoding='utf-8')
                dados = json.loads(conteudo)
                log("[INFO] Dados carregados do Cloud Storage.")
        except Exception:
            log("[ERRO] Falha ao ler dados do Cloud Storage:")
            log(traceback.format_exc())

    if dados is None and os.path.exists(CAMINHO_LOCAL_CACHE):
        try:
            with open(CAMINHO_LOCAL_CACHE, 'r', encoding='utf-8') as f:
                dados = json.load(f)
                log("[INFO] Dados carregados do cache local (fallback, sem Cloud Storage).")
        except Exception:
            log("[ERRO] Falha ao ler cache local:")
            log(traceback.format_exc())

    if dados is None:
        dados = _estrutura_padrao()
        log("[INFO] Nenhum dado anterior encontrado. Iniciando com listas vazias.")

    fila_veiculos = dados.get("fila", [])
    historico_veiculos = dados.get("historico", [])
    colaboradores = dados.get("colaboradores", [])
    atalhos = dados.get("atalhos", [])
    logs_acesso = dados.get("logs_acesso", [])


def _fazer_backup_diario(bucket, conteudo_atual):
    """Antes de sobrescrever, guarda uma copia do dia em backups/ (1x por dia)."""
    global _ultima_data_backup
    hoje = time.strftime('%Y-%m-%d')
    if _ultima_data_backup == hoje:
        return
    try:
        blob_backup = bucket.blob("backups/dados_valet_" + hoje + ".json")
        if not blob_backup.exists():
            blob_original = bucket.blob(NOME_ARQUIVO)
            if blob_original.exists():
                bucket.copy_blob(blob_original, bucket, "backups/dados_valet_" + hoje + ".json")
        _ultima_data_backup = hoje
    except Exception:
        log("[AVISO] Falha ao criar backup diario (nao impede o funcionamento):")
        log(traceback.format_exc())


def salvar_dados():
    conteudo = json.dumps({
        "fila": fila_veiculos,
        "historico": historico_veiculos,
        "colaboradores": colaboradores,
        "atalhos": atalhos,
        "logs_acesso": logs_acesso[-500:]
    }, ensure_ascii=False)

    # cache local (rapido, mas nao confiavel entre reinicios de instancia)
    try:
        with open(CAMINHO_LOCAL_CACHE, 'w', encoding='utf-8') as f:
            f.write(conteudo)
    except Exception:
        log("[ERRO] Falha ao gravar cache local:")
        log(traceback.format_exc())

    # fonte da verdade: Cloud Storage
    bucket = obter_bucket()
    if bucket:
        try:
            _fazer_backup_diario(bucket, conteudo)
            blob = bucket.blob(NOME_ARQUIVO)
            blob.upload_from_string(conteudo, content_type='application/json; charset=utf-8')
        except Exception:
            log("[ERRO CRITICO] Falha ao gravar dados no Cloud Storage - "
                "os dados podem ser perdidos se a instancia reiniciar:")
            log(traceback.format_exc())
    else:
        log("[AVISO] Cloud Storage indisponivel - salvando apenas no cache local "
            "(NAO e persistente entre reinicios de instancia).")


def registrar_log(usuario, acao):
    entrada = {
        "usuario": usuario or "desconhecido",
        "acao": acao,
        "timestamp": int(time.time() * 1000),
        "data_hora": time.strftime('%d/%m/%Y %H:%M:%S')
    }
    logs_acesso.append(entrada)
    del logs_acesso[:-500]  # mantem so os ultimos 500 registros


# ========================================================
# CONFIGURACAO DE ACESSOS
# ========================================================
USUARIOS = {
    "estapar": "123",
    "caixa": "123",
    "caixa1": "123",
    "operador": "123",
    "operador2": "123"
}
# ========================================================


class ServidorFilaHandler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        pass

    def get_usuario_autenticado(self):
        auth_header = self.headers.get('Authorization')
        if auth_header and auth_header.startswith('Basic '):
            try:
                encoded_credentials = auth_header.split(' ')[1]
                decoded_credentials = base64.b64decode(encoded_credentials).decode('utf-8')
                username, _ = decoded_credentials.split(':', 1)
                return username.lower()
            except Exception:
                return None
        return None

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
            except Exception:
                pass

        self.send_response(401)
        self.send_header('WWW-Authenticate', 'Basic realm="Acesso Restrito"')
        self.end_headers()
        self.wfile.write(b"Acesso Negado.")
        return False

    def _responder_json(self, dados, status=200):
        self.send_response(status)
        self.send_header('Content-type', 'application/json; charset=utf-8')
        self.end_headers()
        self.wfile.write(json.dumps(dados, ensure_ascii=False).encode('utf-8'))

    def do_GET(self):
        global fila_veiculos, historico_veiculos, colaboradores, atalhos, logs_acesso
        diretorio_atual = os.path.dirname(os.path.abspath(__file__))

        if self.path == '/api/ping':
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b"PONG")
            return

        if not self.verificar_autenticacao():
            return

        if self.path == '/api/perfil':
            usuario = self.get_usuario_autenticado()
            self._responder_json({"usuario": usuario})
            return

        if self.path == '/api/logs':
            usuario = self.get_usuario_autenticado()
            if usuario != 'estapar':
                self.send_response(403)
                self.end_headers()
                self.wfile.write(b"Acesso negado")
                return
            self._responder_json(list(reversed(logs_acesso)))
            return

        if self.path == '/api/relatorio':
            try:
                self.send_response(200)
                self.send_header('Content-type', 'text/csv; charset=utf-8')
                self.send_header('Content-Disposition', 'attachment; filename="relatorio_diario_valet.csv"')
                self.end_headers()

                output = io.StringIO()
                writer = csv.writer(output, delimiter=';')
                writer.writerow(['Prisma', 'Placa', 'Modelo', 'Manobrista', 'Hora Solicitado',
                                  'Hora Entregue (Baixa)', 'Tempo Total de Espera'])

                for c in historico_veiculos:
                    tempo_min = round(c.get("tempo_espera_ms", 0) / 60000)
                    writer.writerow([
                        c.get("prisma", ""),
                        c.get("placa", ""),
                        c.get("modelo", ""),
                        c.get("manobrista", "N/A"),
                        c.get("hora_solicitacao", ""),
                        c.get("hora_baixa", ""),
                        f"{tempo_min} min"
                    ])

                self.wfile.write(output.getvalue().encode('utf-8'))
            except Exception:
                log("[ERRO] Falha ao gerar relatorio:")
                log(traceback.format_exc())
            return

        if self.path == '/api/dados':
            tempos = [c.get("tempo_espera_ms", 0) for c in historico_veiculos if "tempo_espera_ms" in c]
            tempo_medio = sum(tempos) / len(tempos) if tempos else 600000

            resposta = {
                "fila": fila_veiculos,
                "historico": historico_veiculos,
                "colaboradores": colaboradores,
                "atalhos": atalhos,
                "tempo_medio": tempo_medio
            }
            self._responder_json(resposta)
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
            except Exception:
                log("[ERRO] Falha ao servir arquivo estatico " + caminho_absoluto + ":")
                log(traceback.format_exc())

        self.send_response(404)
        self.end_headers()
        self.wfile.write(b"404 - Nao encontrado")

    def do_POST(self):
        if not self.verificar_autenticacao():
            return

        global fila_veiculos, historico_veiculos, colaboradores, atalhos, logs_acesso
        usuario = self.get_usuario_autenticado()

        try:
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length) if content_length else b'{}'
            dados = json.loads(post_data.decode('utf-8'))
        except Exception:
            log("[ERRO] Corpo da requisicao POST invalido em " + self.path + ":")
            log(traceback.format_exc())
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Requisicao invalida")
            return

        try:
            if self.path == '/api/adicionar':
                novo_carro = {
                    "id": dados["id"],
                    "placa": str(dados.get("placa", "")).strip().upper(),
                    "modelo": str(dados.get("modelo", "")).strip().upper(),
                    "prisma": str(dados.get("prisma", "")).strip(),
                    "hora_solicitacao": dados.get("hora_solicitacao", ""),
                    "status": "solicitado",
                    "manobrista": ""
                }
                fila_veiculos.append(novo_carro)
                registrar_log(usuario, "Solicitou veiculo - Prisma " + novo_carro["prisma"])
                salvar_dados()

            elif self.path == '/api/atribuir':
                carro_id = dados.get("id")
                manobrista = dados.get("manobrista")
                carro_alvo = next((c for c in fila_veiculos if c["id"] == carro_id), None)

                if carro_alvo:
                    carro_alvo["status"] = "em_manobra"
                    carro_alvo["manobrista"] = manobrista
                    registrar_log(usuario, "Atribuiu manobrista '" + str(manobrista) +
                                  "' - Prisma " + str(carro_alvo.get("prisma")))
                    salvar_dados()

            elif self.path == '/api/baixa':
                carro_id = dados.get("id")
                hora_baixa = dados.get("hora_baixa")
                timestamp_baixa = dados.get("timestamp_baixa", 0)

                carro_alvo = next((c for c in fila_veiculos if c["id"] == carro_id), None)

                if carro_alvo:
                    carro_alvo["hora_baixa"] = hora_baixa
                    carro_alvo["tempo_espera_ms"] = timestamp_baixa - carro_alvo["id"]
                    carro_alvo["status"] = "entregue"
                    historico_veiculos.insert(0, carro_alvo)
                    fila_veiculos = [c for c in fila_veiculos if c["id"] != carro_id]
                    registrar_log(usuario, "Deu baixa - Prisma " + str(carro_alvo.get("prisma")))
                    salvar_dados()

            elif self.path == '/api/colaborador/adicionar':
                novo_colab = {
                    "id": dados.get("id"),
                    "nome": dados.get("nome"),
                    "matricula": dados.get("matricula"),
                    "entrada": dados.get("entrada"),
                    "saida": dados.get("saida"),
                    "escala": dados.get("escala")
                }
                colaboradores.append(novo_colab)
                registrar_log(usuario, "Cadastrou colaborador: " + str(novo_colab["nome"]))
                salvar_dados()

            elif self.path == '/api/colaborador/editar':
                colab_id = int(dados.get("id"))
                for c in colaboradores:
                    if c["id"] == colab_id:
                        c["nome"] = dados.get("nome")
                        c["matricula"] = dados.get("matricula")
                        c["entrada"] = dados.get("entrada")
                        c["saida"] = dados.get("saida")
                        c["escala"] = dados.get("escala")
                        break
                registrar_log(usuario, "Editou colaborador ID " + str(colab_id))
                salvar_dados()

            elif self.path == '/api/colaborador/excluir':
                colab_id = int(dados.get("id"))
                colaboradores = [c for c in colaboradores if c["id"] != colab_id]
                registrar_log(usuario, "Excluiu colaborador ID " + str(colab_id))
                salvar_dados()

            elif self.path == '/api/atalho/adicionar':
                novo_atalho = {
                    "id": dados.get("id"),
                    "nome": str(dados.get("nome", "")).strip().upper()
                }
                atalhos.append(novo_atalho)
                registrar_log(usuario, "Criou atalho: " + novo_atalho["nome"])
                salvar_dados()

            elif self.path == '/api/atalho/editar':
                atalho_id = int(dados.get("id"))
                for a in atalhos:
                    if a["id"] == atalho_id:
                        a["nome"] = str(dados.get("nome", "")).strip().upper()
                        break
                registrar_log(usuario, "Editou atalho ID " + str(atalho_id))
                salvar_dados()

            elif self.path == '/api/atalho/excluir':
                atalho_id = int(dados.get("id"))
                atalhos = [a for a in atalhos if a["id"] != atalho_id]
                registrar_log(usuario, "Excluiu atalho ID " + str(atalho_id))
                salvar_dados()

            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")

        except Exception:
            log("[ERRO] Falha ao processar POST " + self.path + ":")
            log(traceback.format_exc())
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"Erro interno")


def run():
    carregar_dados()
    porta = int(os.environ.get('PORT', 8080))
    server_address = ('0.0.0.0', porta)
    httpd = HTTPServer(server_address, ServidorFilaHandler)
    log(f"SERVIDOR INICIADO NA PORTA {porta} (Cloud Storage disponivel: {GCS_DISPONIVEL})")
    httpd.serve_forever()


if __name__ == '__main__':
    run()
