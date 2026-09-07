"""
Sincronização com a base oficial de Certificados de Aprovação (CA) do
Ministério do Trabalho e Emprego (sistema CAEPI).

Fonte oficial: ftp://ftp.mtps.gov.br/portal/fiscalizacao/seguranca-e-saude-no-trabalho/caepi/
Arquivo: tgg_export_caepi.zip (contém um .txt/.csv delimitado por ';', encoding latin-1)

Este módulo baixa o arquivo, interpreta as colunas e grava tudo em uma tabela
local (ca_cache) para que a busca por número de CA no cadastro de EPI seja
instantânea e não dependa de nenhum serviço externo no dia a dia.

IMPORTANTE: o layout exato das colunas do arquivo público pode mudar com o
tempo. Se a sincronização começar a trazer campos vazios, confira o mapeamento
em COLUNAS_CANDIDATAS abaixo e ajuste os nomes de acordo com o cabeçalho real
do arquivo baixado (rode `flask --app app inspecionar-ca <arquivo>` para ver
as colunas encontradas).
"""
import io
import zipfile
from datetime import datetime

import click
import requests

from .db import get_db, execute_db, query_db

URL_ZIP = "https://www.gov.br/trabalho-e-emprego/pt-br/assuntos/inspecao-do-trabalho/seguranca-e-saude-no-trabalho/equipamentos-de-protecao-individual-epi/tgg_export_caepi.zip"
URL_ZIP_FTP = "ftp://ftp.mtps.gov.br/portal/fiscalizacao/seguranca-e-saude-no-trabalho/caepi/tgg_export_caepi.zip"

# Nomes possíveis de cada coluna no arquivo oficial (varia conforme a versão
# publicada). O importador usa o primeiro nome da lista que encontrar no
# cabeçalho, ignorando maiúsculas/acentos.
COLUNAS_CANDIDATAS = {
    "numero_ca": ["nrregistrocaepi", "nrca", "numeroca", "registrocaepi", "ca"],
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
_HEADERS_NAVEGADOR = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/zip, application/octet-stream, */*",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
}


def _baixar_zip_bytes():
    ultimo_erro = None
    for url in (URL_ZIP, URL_ZIP_FTP):
        try:
            if url.startswith("ftp://"):
                import ftplib
                from urllib.parse import urlparse
                u = urlparse(url)
                buf = io.BytesIO()
                with ftplib.FTP(u.hostname, timeout=20) as ftp:
                    ftp.login()
                    ftp.retrbinary(f"RETR {u.path}", buf.write)
                return buf.getvalue()
            else:
                resp = requests.get(url, timeout=30, headers=_HEADERS_NAVEGADOR, allow_redirects=True)
                resp.raise_for_status()
                conteudo = resp.content
                # Um zip de verdade começa com "PK". Se não começar, o site quase
                # sempre devolveu uma página de bloqueio/erro em vez do arquivo —
                # detectamos isso aqui para não mascarar o motivo real do erro.
                if not conteudo[:2] == b"PK":
                    tipo = resp.headers.get("Content-Type", "desconhecido")
                    trecho = conteudo[:200].decode("utf-8", errors="replace").replace("\n", " ").strip()
                    raise RuntimeError(
                        f"O site devolveu HTTP {resp.status_code} mas o conteúdo não é um "
                        f"arquivo zip (Content-Type: {tipo}). Início do conteúdo: {trecho!r}"
                    )
                return conteudo
        except Exception as e:  # noqa: BLE001
            ultimo_erro = e
            continue
    raise RuntimeError(f"Não foi possível baixar a base do CAEPI: {ultimo_erro}")


def _extrair_dataframe(zip_bytes):
    import pandas as pd

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        nomes = [n for n in z.namelist() if not n.endswith("/")]
        if not nomes:
            raise RuntimeError("Arquivo zip da base CAEPI veio vazio.")
        alvo = nomes[0]
        with z.open(alvo) as f:
            conteudo = f.read()

    for encoding in ("latin-1", "utf-8", "cp1252"):
        for sep in (";", "\t", ","):
            try:
                df = pd.read_csv(
                    io.BytesIO(conteudo), sep=sep, encoding=encoding,
                    dtype=str, engine="python", on_bad_lines="skip",
                )
                if df.shape[1] > 2:
                    return df
            except Exception:  # noqa: BLE001
                continue
    raise RuntimeError("Não foi possível interpretar o formato do arquivo da base CAEPI.")


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
    """Baixa a base oficial, interpreta e grava em ca_cache. Retorna (ok, mensagem, total)."""
    try:
        zip_bytes = _baixar_zip_bytes()
        df = _extrair_dataframe(zip_bytes)
        mapa = _mapear_colunas(df.columns)

        if "numero_ca" not in mapa:
            raise RuntimeError(
                "Não encontrei a coluna do número do CA no arquivo baixado. "
                "O layout oficial pode ter mudado — ajuste COLUNAS_CANDIDATAS em app/ca_sync.py."
            )

        agora = datetime.now().isoformat(timespec="seconds")
        # Usamos um dict (numero_ca -> linha) em vez de gravar direto: se o
        # arquivo oficial repetir o mesmo CA em mais de uma linha, mantemos
        # só a última — e evitamos que um único INSERT tente atualizar a
        # mesma linha duas vezes (o Postgres não permite isso em ON CONFLICT).
        registros = {}
        for _, row in df.iterrows():
            numero_ca = str(row.get(mapa.get("numero_ca"), "")).strip()
            if not numero_ca or numero_ca.lower() == "nan":
                continue
            registros[numero_ca] = (
                numero_ca,
                str(row.get(mapa.get("situacao"), "") or ""),
                _normalizar_data(row.get(mapa.get("validade"), "")),
                str(row.get(mapa.get("fabricante_cnpj"), "") or ""),
                str(row.get(mapa.get("fabricante_nome"), "") or ""),
                str(row.get(mapa.get("equipamento_nome"), "") or ""),
                str(row.get(mapa.get("descricao"), "") or ""),
                agora,
            )

        valores = list(registros.values())
        total = len(valores)

        db = get_db()
        colunas_sql = "(" + ", ".join(_COLUNAS_CA_CACHE) + ")"
        for inicio in range(0, total, _TAMANHO_LOTE):
            lote = valores[inicio:inicio + _TAMANHO_LOTE]
            marcadores = ", ".join(["(?, ?, ?, ?, ?, ?, ?, ?)"] * len(lote))
            args_lote = [valor for linha in lote for valor in linha]
            db.execute(
                f"INSERT INTO ca_cache {colunas_sql} VALUES {marcadores} {_UPSERT_CA_CACHE_SQL}",
                args_lote,
            )
            # Confirma a cada lote em vez de só no final: se algo der errado
            # no meio (timeout, queda de conexão), o que já foi processado
            # não se perde e a próxima sincronização continua de onde parou.
            db.commit()
        db.execute(
            "INSERT INTO sync_log (status, total_registros, mensagem) VALUES (?, ?, ?)",
            ("sucesso", total, f"{total} certificados importados/atualizados."),
        )
        db.commit()
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
