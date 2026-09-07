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
                resp = requests.get(url, timeout=20)
                resp.raise_for_status()
                return resp.content
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

        db = get_db()
        total = 0
        agora = datetime.now().isoformat(timespec="seconds")
        for _, row in df.iterrows():
            numero_ca = str(row.get(mapa.get("numero_ca"), "")).strip()
            if not numero_ca or numero_ca.lower() == "nan":
                continue
            db.execute(
                """INSERT INTO ca_cache
                       (numero_ca, situacao, validade, fabricante_cnpj, fabricante_nome,
                        equipamento_nome, descricao, atualizado_em)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(numero_ca) DO UPDATE SET
                       situacao=excluded.situacao,
                       validade=excluded.validade,
                       fabricante_cnpj=excluded.fabricante_cnpj,
                       fabricante_nome=excluded.fabricante_nome,
                       equipamento_nome=excluded.equipamento_nome,
                       descricao=excluded.descricao,
                       atualizado_em=excluded.atualizado_em""",
                (
                    numero_ca,
                    str(row.get(mapa.get("situacao"), "") or ""),
                    _normalizar_data(row.get(mapa.get("validade"), "")),
                    str(row.get(mapa.get("fabricante_cnpj"), "") or ""),
                    str(row.get(mapa.get("fabricante_nome"), "") or ""),
                    str(row.get(mapa.get("equipamento_nome"), "") or ""),
                    str(row.get(mapa.get("descricao"), "") or ""),
                    agora,
                ),
            )
            total += 1
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
    ok, mensagem, total = sincronizar_base_ca()
    flash(mensagem, "sucesso" if ok else "erro")
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
