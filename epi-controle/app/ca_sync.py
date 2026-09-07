"""
Sincronização com a base oficial de Certificados de Aprovação (CA) do
Ministério do Trabalho e Emprego (sistema CAEPI).

Fonte oficial: ftp://ftp.mtps.gov.br/portal/fiscalizacao/seguranca-e-saude-no-trabalho/caepi/
Arquivo: tgg_export_caepi.zip — apesar do nome, o site já serviu esse link
tanto como um zip de verdade quanto como o arquivo de dados puro, sem
compactação nenhuma (um .txt delimitado por '|', com cabeçalho começando em
"#NRRegistroCA...", encoding latin-1, quebra de linha \\r\\n). Por isso este
módulo detecta o formato pela assinatura dos bytes baixados em vez de supor
que é sempre um zip — ver _baixar_uma_tentativa() e sincronizar_base_ca().

Este módulo baixa o arquivo, interpreta as colunas e grava tudo em uma tabela
local (ca_cache) para que a busca por número de CA no cadastro de EPI seja
instantânea e não dependa de nenhum serviço externo no dia a dia.

IMPORTANTE: o layout exato das colunas do arquivo público pode mudar com o
tempo. Se a sincronização começar a trazer campos vazios, confira o mapeamento
em COLUNAS_CANDIDATAS abaixo e ajuste os nomes de acordo com o cabeçalho real
do arquivo baixado (rode `flask --app app inspecionar-ca <arquivo>` para ver
as colunas encontradas).
"""
import csv
import os
import tempfile
import zipfile
from datetime import datetime

import click
import requests

from .db import get_db, execute_db, query_db

URL_ZIP = "https://www.gov.br/trabalho-e-emprego/pt-br/assuntos/inspecao-do-trabalho/seguranca-e-saude-no-trabalho/equipamentos-de-protecao-individual-epi/tgg_export_caepi.zip/@@download/file"
URL_ZIP_FTP = "ftp://ftp.mtps.gov.br/portal/fiscalizacao/seguranca-e-saude-no-trabalho/caepi/tgg_export_caepi.zip"

# Nomes possíveis de cada coluna no arquivo oficial (varia conforme a versão
# publicada). O importador usa o primeiro nome da lista que encontrar no
# cabeçalho, ignorando maiúsculas/acentos.
COLUNAS_CANDIDATAS = {
    "numero_ca": ["nrregistrocaepi", "nrregistroca", "nrca", "numeroca", "registrocaepi", "ca"],
    "situacao": ["dscsituacaoregistrocaepi", "situacao", "dscsituacao"],
    "validade": ["dtvalidade", "datavalidade", "dtvalidadecaepi"],
    "fabricante_cnpj": ["nrcnpj", "cnpj"],
    "fabricante_nome": ["nomerazaosocialfabricante", "fabricante", "razaosocial"],
    "equipamento_nome": ["nomeequipamento", "equipamento"],
    "descricao": ["descricaoequipamento", "descricao"],
}


def _normaliza(texto):
    import unicodedata
    texto = unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode()
    return "".join(ch for ch in texto.lower() if ch.isalnum())


def _mapear_colunas(colunas):
    normalizadas = {_normaliza(c): c for c in colunas}
    mapeamento = {}
    for campo, candidatos in COLUNAS_CANDIDATAS.items():
        for candidato in candidatos:
            if candidato in normalizadas:
                mapeamento[campo] = normalizadas[candidato]
                break
    return mapeamento


# Muitos sites de governo (atrás de WAF/CDN) bloqueiam ou devolvem uma página
# de erro/"acesso negado" (com HTTP 200!) quando o pedido não parece vir de um
# navegador de verdade — o User-Agent padrão da lib requests ("python-requests/…")
# costuma ser filtrado. Por isso simulamos um navegador comum aqui.
#
# O cabeçalho Accept abaixo imita o que um navegador de verdade manda ao abrir
# esse link diretamente (não um pedido "eu quero especificamente um zip") —
# um Accept forçando "application/zip, application/octet-stream" chegou a ser
# usado aqui e é suspeito de ter feito o site (ou um WAF/CDN na frente dele)
# devolver uma resposta diferente da que um navegador comum recebe, que veio
# truncada/corrompida como zip. Testado manualmente: baixando esse mesmo link
# num navegador comum, o arquivo chega como texto puro (sem compactação
# nenhuma), então normalizamos o Accept para não sinalizar essa preferência.
_HEADERS_NAVEGADOR = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
        "image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
}


def _baixar_uma_tentativa(destino):
    """Uma rodada tentando cada URL configurada (HTTPS, depois FTP).

    Baixa em streaming DIRETO PARA UM ARQUIVO EM DISCO (`destino`), em vez de
    acumular a resposta inteira na memória — esse arquivo tem quase 100MB, e
    testes locais mostraram que só carregá-lo como uma string Python (mesmo
    sem usar pandas) já passava de 700MB de pico de memória, o que estouraria
    os 512MB do plano gratuito do Render (o mesmo tipo de problema que já
    tinha derrubado o processo antes, com pandas). Devolve o tipo detectado
    ("zip" ou "dados") ou levanta o último erro encontrado.

    Esse link já serviu tanto um zip de verdade quanto o arquivo de dados
    puro (sem nenhuma compactação, apesar do nome "...zip" na URL) — por
    isso NÃO exigimos mais que a resposta comece com a assinatura de zip
    ("PK"). Quem decide se compacta ou não é o próprio site; aqui só
    detectamos o que veio e tratamos cada caso.
    """
    ultimo_erro = None
    for url in (URL_ZIP, URL_ZIP_FTP):
        try:
            content_length_esperado = None
            bytes_baixados = 0
            if url.startswith("ftp://"):
                import ftplib
                from urllib.parse import urlparse
                u = urlparse(url)
                with ftplib.FTP(u.hostname, timeout=20) as ftp:
                    ftp.login()
                    with open(destino, "wb") as f:
                        def _escrever(pedaco):
                            nonlocal bytes_baixados
                            f.write(pedaco)
                            bytes_baixados += len(pedaco)
                        ftp.retrbinary(f"RETR {u.path}", _escrever)
            else:
                resp = requests.get(
                    url, timeout=90, headers=_HEADERS_NAVEGADOR,
                    allow_redirects=True, stream=True,
                )
                resp.raise_for_status()
                content_length_esperado = resp.headers.get("Content-Length")
                with open(destino, "wb") as f:
                    for pedaco in resp.iter_content(chunk_size=256 * 1024):
                        if pedaco:
                            f.write(pedaco)
                            bytes_baixados += len(pedaco)

            with open(destino, "rb") as f:
                inicio_bytes = f.read(200)

            if not url.startswith("ftp://"):
                # Se o site bloquear/errar, o mais comum é devolver uma
                # página HTML (de erro, captcha, etc.) em vez do arquivo —
                # detectamos isso aqui para não mascarar o motivo real do
                # problema com um erro confuso de parsing mais adiante.
                inicio_normalizado = inicio_bytes.lstrip().lower()
                parece_html = (
                    inicio_normalizado.startswith(b"<!doctype") or inicio_normalizado.startswith(b"<html")
                    or inicio_normalizado.startswith(b"<?xml") or inicio_normalizado.startswith(b"<head")
                )
                if parece_html:
                    trecho = inicio_bytes.decode("utf-8", errors="replace").replace("\n", " ").strip()
                    raise RuntimeError(
                        f"O site devolveu HTTP {resp.status_code} mas o conteúdo parece ser uma "
                        f"página HTML (de bloqueio, erro ou captcha), não o arquivo da base. "
                        f"Início do conteúdo: {trecho!r}"
                    )

            # Um zip de verdade começa com "PK" — só faz sentido validar que
            # abre como zip nesse caso. Se NÃO começar com "PK", tratamos o
            # conteúdo como o próprio arquivo de dados, sem compactação (ver
            # docstring do módulo).
            if inicio_bytes[:2] == b"PK":
                # O começo "PK" não garante um arquivo íntegro — uma resposta
                # cortada no meio do download (comum em redes instáveis)
                # também pode começar com "PK" e mesmo assim não abrir como
                # zip. Testamos isso aqui, antes de considerar a tentativa
                # bem-sucedida — e, se falhar, registramos o tamanho baixado
                # vs. o esperado para confirmar se foi truncamento.
                try:
                    with zipfile.ZipFile(destino):
                        pass
                except zipfile.BadZipFile as e:
                    detalhe = f"{bytes_baixados} bytes baixados"
                    if content_length_esperado:
                        detalhe += f" de {content_length_esperado} esperados (Content-Length)"
                    raise RuntimeError(
                        f"Resposta começou com assinatura de zip válida, mas o arquivo não abriu "
                        f"({e}) — provável download incompleto/truncado ({detalhe})."
                    )
                return "zip"
            return "dados"
        except Exception as e:  # noqa: BLE001
            ultimo_erro = e
            continue
    raise RuntimeError(f"Não foi possível baixar a base do CAEPI: {ultimo_erro}")


def _baixar_arquivo_base(destino):
    """Baixa o arquivo oficial (zip ou dados puros) para `destino`, tentando
    de novo algumas vezes. Devolve o tipo detectado ("zip" ou "dados").

    Na prática, o download desse arquivo às vezes falha de forma
    intermitente — a rede cai no meio, ou o site bloqueia uma tentativa e
    libera a próxima. Tentar mais de uma vez, com uma pequena pausa entre as
    tentativas, evita que a sincronização inteira falhe por causa de um
    problema passageiro em vez de um problema real.
    """
    import time

    ultimo_erro = None
    for tentativa in range(3):
        try:
            return _baixar_uma_tentativa(destino)
        except Exception as e:  # noqa: BLE001
            ultimo_erro = e
            if tentativa < 2:
                time.sleep(3)
    raise RuntimeError(f"{ultimo_erro} (após 3 tentativas)")


def _detectar_dialeto(amostra_bytes):
    """Descobre o encoding, o separador e em que linha começa o cabeçalho,
    olhando uma amostra do começo do arquivo.

    Evita usar pandas aqui de propósito: a base oficial tem centenas de
    milhares de linhas, e ler o arquivo inteiro num DataFrame (ainda mais
    com engine="python") usa memória demais para o plano gratuito do Render
    (512MB) — foi exatamente isso que derrubou o processo por falta de
    memória. Olhar só uma amostra do começo é suficiente pra descobrir o
    formato.

    Olhamos várias linhas (não só a primeira) porque alguns arquivos do
    governo trazem uma linha de título/metadado antes do cabeçalho de
    verdade — se olhássemos só a linha 0, essa linha de título (sem vários
    separadores) faria a detecção falhar mesmo com um arquivo válido.

    Retorna (encoding, separador, índice da linha onde o cabeçalho começa).
    """
    linhas_originais = amostra_bytes.split(b"\n")
    indices_com_conteudo = [i for i, l in enumerate(linhas_originais) if l.strip()]
    if not indices_com_conteudo:
        raise RuntimeError("Arquivo da base CAEPI veio vazio ou sem linhas.")

    # Nomes de coluna que já sabemos reconhecer (ver COLUNAS_CANDIDATAS) —
    # usados abaixo para achar o cabeçalho por conteúdo, não só por contagem
    # de separadores.
    candidatos_conhecidos = {c for lista in COLUNAS_CANDIDATAS.values() for c in lista}
    # A coluna do número do CA é obrigatória (é a chave primária da tabela
    # local) — exigir que ELA especificamente apareça entre os acertos, além
    # de um mínimo maior de acertos no total, reduz bastante o risco de uma
    # linha de descrição em português livre (que pode ter palavras soltas
    # como "CNPJ" ou "situação" no meio do texto) ser confundida com o
    # cabeçalho de verdade só por coincidência.
    candidatos_numero_ca = set(COLUNAS_CANDIDATAS["numero_ca"])

    for encoding in ("latin-1", "utf-8", "cp1252"):
        try:
            linhas = [linhas_originais[i].decode(encoding) for i in indices_com_conteudo]
        except UnicodeDecodeError:
            continue

        # 1) Detecção "semântica": para cada linha da amostra (na ordem em
        #    que aparecem no arquivo) e cada separador candidato, olhamos se
        #    dividir a linha por esse separador produz colunas cujos nomes
        #    batem com os que já conhecemos. Isso é bem mais confiável do
        #    que só contar separadores: uma descrição de EPI em português
        #    livre pode ter várias vírgulas, tabs ou até ponto-e-vírgula só
        #    por coincidência de pontuação/formatação, o que já confundiu a
        #    detecção puramente estatística usada antes (linhas de dados no
        #    meio da amostra sendo escolhidas como se fossem o cabeçalho).
        #    Testar linha por linha, na ordem, garante que a primeira linha
        #    que realmente parecer um cabeçalho vença — normalmente a linha
        #    0 mesmo.
        for pos, indice_original in enumerate(indices_com_conteudo):
            linha = linhas[pos]
            for sep in (";", "\t", ",", "|"):
                partes = linha.split(sep)
                if len(partes) < 5:
                    continue
                normalizadas = {_normaliza(p) for p in partes}
                acertos = normalizadas & candidatos_conhecidos
                if len(acertos) >= 3 and (acertos & candidatos_numero_ca):
                    return encoding, sep, indice_original

        # 2) Vazio: se nada bateu com um nome de coluna conhecido (layout
        #    novo/desconhecido), caímos de volta na heurística estatística
        #    antiga — a linha de cabeçalho de verdade tem várias colunas
        #    (bastante separadores) e as linhas de dados logo depois
        #    costumam ter a MESMA contagem.
        for sep in (";", "\t", ",", "|"):
            contagens = [linha.count(sep) for linha in linhas]
            for pos, indice_original in enumerate(indices_com_conteudo):
                contagem = contagens[pos]
                if contagem <= 2:
                    continue
                seguintes = contagens[pos + 1:pos + 6] or contagens[pos + 1:]
                if seguintes and sum(1 for c in seguintes if c == contagem) >= max(1, len(seguintes) // 2):
                    return encoding, sep, indice_original

    primeira = linhas_originais[indices_com_conteudo[0]][:200].decode("latin-1", errors="replace")
    raise RuntimeError(
        "Não foi possível interpretar o formato do arquivo da base CAEPI. "
        f"Primeira linha não vazia: {primeira!r}"
    )


def _extrair_zip_para_arquivo(caminho_zip, destino):
    """Descompacta o zip oficial para `destino` e devolve o nome do arquivo
    extraído.

    O zip pode conter mais de um arquivo (por exemplo, um "leiame.txt"
    pequeno junto com o arquivo de dados de verdade) — por isso escolhemos o
    MAIOR arquivo do zip, e não simplesmente o primeiro da lista. Pegar só o
    primeiro foi, na prática, o que nos fez extrair um arquivo pequeno
    (não a base de dados) numa sincronização anterior.

    Lê o zip direto do arquivo em disco (não de bytes em memória) e grava a
    entrada escolhida em outro arquivo, em pedaços — evita carregar o
    arquivo de dados inteiro (pode passar de 100MB depois de descompactado)
    de uma vez na memória.
    """
    with zipfile.ZipFile(caminho_zip) as z:
        entradas = [info for info in z.infolist() if not info.is_dir() and info.file_size > 0]
        if not entradas:
            raise RuntimeError("Arquivo zip da base CAEPI veio vazio.")
        maior = max(entradas, key=lambda info: info.file_size)
        with z.open(maior) as origem, open(destino, "wb") as saida:
            for pedaco in iter(lambda: origem.read(256 * 1024), b""):
                saida.write(pedaco)
        return maior.filename


def _abrir_leitor_ca(caminho_arquivo, nome_arquivo="arquivo"):
    """Abre o arquivo de dados (já baixado e, se preciso, descompactado) e
    devolve (leitor, arquivo_aberto).

    O leitor é um csv.DictReader que lê o arquivo DIRETO DO DISCO, linha a
    linha, sem nunca carregar o conteúdo inteiro numa string Python de uma
    vez só — o arquivo oficial tem quase 100MB e mais de 96 mil linhas;
    carregá-lo inteiro (mesmo sem pandas) chegou a usar mais de 700MB de
    memória em teste local, o que estouraria os 512MB do plano gratuito do
    Render. Quem chama esta função é responsável por fechar `arquivo_aberto`
    quando terminar de iterar o leitor.
    """
    with open(caminho_arquivo, "rb") as f:
        amostra = f.read(256 * 1024)  # dá pra várias dezenas de linhas
    try:
        encoding, sep, indice_cabecalho = _detectar_dialeto(amostra)
    except RuntimeError as e:
        tamanho = os.path.getsize(caminho_arquivo)
        raise RuntimeError(f"{e} (arquivo: {nome_arquivo!r}, {tamanho} bytes)")

    # newline="" é a forma recomendada pela documentação do módulo csv para
    # abrir arquivos que serão lidos com csv.reader/DictReader — deixa o
    # próprio módulo csv reconhecer corretamente as quebras de linha
    # (inclusive \r\n) em vez de a gente cortar o arquivo manualmente.
    arquivo = open(caminho_arquivo, "r", encoding=encoding, errors="replace", newline="")
    for _ in range(indice_cabecalho):
        arquivo.readline()
    leitor = csv.DictReader(arquivo, delimiter=sep)
    if not leitor.fieldnames:
        arquivo.close()
        raise RuntimeError("Não foi possível interpretar o formato do arquivo da base CAEPI.")
    return leitor, arquivo


def _normalizar_data(texto):
    """Converte datas em formatos comuns do arquivo oficial (DD/MM/AAAA, etc.) para ISO (AAAA-MM-DD)."""
    texto = (texto or "").strip()
    if not texto or texto.lower() == "nan":
        return ""
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(texto[:10], fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return texto


_COLUNAS_CA_CACHE = (
    "numero_ca", "situacao", "validade", "fabricante_cnpj",
    "fabricante_nome", "equipamento_nome", "descricao", "atualizado_em",
)

_UPSERT_CA_CACHE_SQL = """
    ON CONFLICT(numero_ca) DO UPDATE SET
        situacao=excluded.situacao,
        validade=excluded.validade,
        fabricante_cnpj=excluded.fabricante_cnpj,
        fabricante_nome=excluded.fabricante_nome,
        equipamento_nome=excluded.equipamento_nome,
        descricao=excluded.descricao,
        atualizado_em=excluded.atualizado_em
"""

# O banco (Neon/PostgreSQL) fica em outra região/continente do servidor
# (Render nos EUA, banco no Brasil), então cada ida-e-volta de rede custa uns
# 150-300ms. Gravar linha por linha (uma consulta por CA) levaria dezenas de
# minutos com a base oficial toda — inserimos em lotes grandes para reduzir
# drasticamente o número de idas-e-voltas.
_TAMANHO_LOTE = 500


def sincronizar_base_ca():
    """Baixa a base oficial, interpreta e grava em ca_cache. Retorna (ok, mensagem, total).

    Processa o arquivo em streaming (linha a linha, gravando em lotes) em vez
    de carregar tudo em memória de uma vez — a base oficial tem centenas de
    milhares de linhas, e o plano gratuito do Render só tem 512MB de RAM.
    """
    try:
        with tempfile.TemporaryDirectory(prefix="caepi-") as pasta_temp:
            caminho_baixado = os.path.join(pasta_temp, "baixado")
            tipo = _baixar_arquivo_base(caminho_baixado)
            # O site já serviu esse link tanto compactado (zip) quanto como o
            # arquivo de dados puro (sem compactação nenhuma) — só
            # descompacta se o que veio realmente for um zip. Tudo em disco,
            # nunca o conteúdo inteiro na memória de uma vez (ver docstrings
            # acima).
            if tipo == "zip":
                caminho_dados = os.path.join(pasta_temp, "dados")
                nome_arquivo = _extrair_zip_para_arquivo(caminho_baixado, caminho_dados)
            else:
                caminho_dados = caminho_baixado
                nome_arquivo = "download direto (sem compactação)"

            leitor, arquivo_aberto = _abrir_leitor_ca(caminho_dados, nome_arquivo)
            try:
                mapa = _mapear_colunas(leitor.fieldnames)

                if "numero_ca" not in mapa:
                    raise RuntimeError(
                        "Não encontrei a coluna do número do CA no arquivo baixado. "
                        f"Colunas encontradas no cabeçalho detectado: {leitor.fieldnames!r}. "
                        "O layout oficial pode ter mudado — ajuste COLUNAS_CANDIDATAS em app/ca_sync.py."
                    )

                db = get_db()
                colunas_sql = "(" + ", ".join(_COLUNAS_CA_CACHE) + ")"
                agora = datetime.now().isoformat(timespec="seconds")
                total = 0
                # Dict só com as linhas do lote atual (no máximo
                # _TAMANHO_LOTE de cada vez) — se o mesmo CA aparecer duas
                # vezes dentro do MESMO lote, mantemos a última (o Postgres
                # não permite que um único INSERT atualize a mesma linha
                # duas vezes via ON CONFLICT). Repetições em lotes
                # diferentes não têm problema: o lote mais recente
                # simplesmente sobrescreve o valor gravado pelo lote
                # anterior.
                lote = {}

                def _gravar_lote():
                    nonlocal total
                    if not lote:
                        return
                    valores = list(lote.values())
                    marcadores = ", ".join(["(?, ?, ?, ?, ?, ?, ?, ?)"] * len(valores))
                    args_lote = [valor for linha in valores for valor in linha]
                    db.execute(
                        f"INSERT INTO ca_cache {colunas_sql} VALUES {marcadores} {_UPSERT_CA_CACHE_SQL}",
                        args_lote,
                    )
                    # Confirma a cada lote em vez de só no final: se algo
                    # der errado no meio (timeout, queda de conexão), o que
                    # já foi processado não se perde e a próxima
                    # sincronização continua de onde parou.
                    db.commit()
                    total += len(valores)
                    lote.clear()

                for row in leitor:
                    numero_ca = (row.get(mapa.get("numero_ca")) or "").strip()
                    if not numero_ca or numero_ca.lower() == "nan":
                        continue
                    lote[numero_ca] = (
                        numero_ca,
                        (row.get(mapa.get("situacao")) or "").strip(),
                        _normalizar_data(row.get(mapa.get("validade"))),
                        (row.get(mapa.get("fabricante_cnpj")) or "").strip(),
                        (row.get(mapa.get("fabricante_nome")) or "").strip(),
                        (row.get(mapa.get("equipamento_nome")) or "").strip(),
                        (row.get(mapa.get("descricao")) or "").strip(),
                        agora,
                    )
                    if len(lote) >= _TAMANHO_LOTE:
                        _gravar_lote()
                _gravar_lote()  # o restante (lote incompleto no final do arquivo)

                db.execute(
                    "INSERT INTO sync_log (status, total_registros, mensagem) VALUES (?, ?, ?)",
                    ("sucesso", total, f"{total} certificados importados/atualizados."),
                )
                db.commit()
            finally:
                # Fecha o arquivo aberto ANTES de sair do bloco `with` acima
                # (que apaga a pasta temporária) — evita depender da coleta
                # de lixo pra liberar o descritor de arquivo.
                arquivo_aberto.close()

        return True, f"{total} certificados sincronizados com sucesso.", total
    except Exception as e:  # noqa: BLE001
        db = get_db()
        db.execute(
            "INSERT INTO sync_log (status, total_registros, mensagem) VALUES (?, ?, ?)",
            ("erro", 0, str(e)),
        )
        db.commit()
        return False, str(e), 0


def buscar_ca(numero_ca):
    numero_ca = (numero_ca or "").strip()
    if not numero_ca:
        return None
    return query_db("SELECT * FROM ca_cache WHERE numero_ca = ?", (numero_ca,), one=True)


# --- Sincronização automática semanal (disparada pelo login) ---------------
# Em vez de depender de um agendador externo (cron) — que o plano gratuito do
# Render não oferece —, a cada login verificamos se a base já foi sincronizada
# nesta semana civil (segunda a domingo). Se não, a primeira pessoa a logar
# na semana dispara a sincronização em segundo plano (thread separada), sem
# atrasar o login dela.

def _semana_civil(momento):
    """(ano ISO, semana ISO) de um datetime — a semana civil começa na segunda-feira."""
    ano, semana, _ = momento.isocalendar()
    return (ano, semana)


def _sincronizacao_pendente_esta_semana():
    """True se ainda não tentamos sincronizar (sucesso, erro ou em andamento)
    nesta semana civil — ou se nunca sincronizamos."""
    ultimo = query_db("SELECT executado_em FROM sync_log ORDER BY id DESC LIMIT 1", one=True)
    if ultimo is None:
        return True
    try:
        quando = datetime.strptime(ultimo["executado_em"][:19], "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return True
    return _semana_civil(quando) != _semana_civil(datetime.now())


def _disparar_sincronizacao_em_segundo_plano(app, mensagem_inicial):
    """Grava um registro "em_andamento" e sobe uma thread para rodar a
    sincronização de verdade — sem segurar a requisição HTTP que a disparou.

    Isso é essencial no plano gratuito do Render: ele roda só 1 worker, e
    baixar/interpretar/gravar a base oficial (dezenas de milhares de linhas)
    pode levar mais de um minuto. Se isso acontecesse dentro da própria
    requisição, o único worker ficaria ocupado tempo demais, a plataforma
    acha que o serviço travou e reinicia o processo no meio da sincronização
    — foi exatamente isso que aconteceu quando o botão "Sincronizar agora"
    chamava sincronizar_base_ca() diretamente e esperava o resultado.
    """
    execute_db(
        "INSERT INTO sync_log (status, total_registros, mensagem) VALUES (?, ?, ?)",
        ("em_andamento", None, mensagem_inicial),
    )

    import threading

    def _tarefa():
        with app.app_context():
            sincronizar_base_ca()

    threading.Thread(target=_tarefa, daemon=True, name="sync-ca").start()


def verificar_e_disparar_sincronizacao_semanal(app):
    """Chamada a cada login bem-sucedido. Se a base de CA ainda não foi
    sincronizada nesta semana civil, dispara a sincronização em segundo
    plano — sem atrasar o login de quem a disparou.

    O registro "em_andamento" gravado aqui também serve de trava: um
    segundo login alguns segundos depois já vê a semana como "não pendente"
    e não dispara uma segunda sincronização em paralelo.
    """
    try:
        if not _sincronizacao_pendente_esta_semana():
            return
        _disparar_sincronizacao_em_segundo_plano(
            app, "Sincronização automática semanal iniciada em segundo plano."
        )
    except Exception:  # noqa: BLE001
        # Nunca deixamos um problema aqui atrapalhar o login de ninguém.
        return


# --- Rotas web -------------------------------------------------------------
from flask import Blueprint, jsonify, redirect, render_template, request, url_for, flash
from .auth import roles_required

bp = Blueprint("ca_sync", __name__, url_prefix="/ca")


@bp.route("/buscar")
@roles_required("admin", "almoxarife")
def buscar():
    numero = request.args.get("numero", "")
    registro = buscar_ca(numero)
    if registro is None:
        return jsonify({"encontrado": False})
    return jsonify({
        "encontrado": True,
        "situacao": registro["situacao"],
        "validade": registro["validade"],
        "fabricante_nome": registro["fabricante_nome"],
        "equipamento_nome": registro["equipamento_nome"],
        "descricao": registro["descricao"],
    })


@bp.route("/sincronizar", methods=["POST"])
@roles_required("admin")
def sincronizar():
    from flask import current_app
    _disparar_sincronizacao_em_segundo_plano(
        current_app._get_current_object(),
        "Sincronização manual iniciada em segundo plano.",
    )
    flash(
        "Sincronização iniciada em segundo plano — a base é grande e pode levar um ou dois "
        "minutos. Atualize esta página daqui a pouco para ver o resultado no histórico abaixo.",
        "sucesso",
    )
    return redirect(request.referrer or url_for("epis.listar"))


@bp.route("/status")
@roles_required("admin")
def status():
    logs = query_db("SELECT * FROM sync_log ORDER BY id DESC LIMIT 20")
    total_ca = query_db("SELECT COUNT(*) AS c FROM ca_cache", one=True)["c"]
    return render_template("admin/ca_status.html", logs=logs, total_ca=total_ca)


# --- CLI ---------------------------------------------------------------
@click.command("sync-ca")
def sync_ca_command():
    """Sincroniza a base oficial de CA (para rodar via cron em produção)."""
    from flask import current_app
    with current_app.app_context():
        ok, mensagem, total = sincronizar_base_ca()
        click.echo(("[OK] " if ok else "[ERRO] ") + mensagem)


def register_cli(app):
    app.cli.add_command(sync_ca_command)
