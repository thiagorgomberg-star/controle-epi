import os
import uuid
from pathlib import Path

from flask import (
    Blueprint, current_app, flash, redirect, render_template, request, url_for
)
from werkzeug.utils import secure_filename

from .auth import roles_required, get_current_user
from .db import query_db, execute_db, get_db

bp = Blueprint("epis", __name__, url_prefix="/epis")

EXTENSOES_IMAGEM = {"png", "jpg", "jpeg", "webp"}


def _extensao_valida(nome_arquivo):
    return "." in nome_arquivo and nome_arquivo.rsplit(".", 1)[1].lower() in EXTENSOES_IMAGEM


def _salvar_foto(arquivo, subpasta):
    if not arquivo or arquivo.filename == "":
        return None
    if not _extensao_valida(arquivo.filename):
        raise ValueError("Formato de imagem não suportado. Use PNG, JPG ou WEBP.")
    ext = arquivo.filename.rsplit(".", 1)[1].lower()
    nome = f"{uuid.uuid4().hex}.{ext}"
    destino = Path(current_app.config["UPLOAD_FOLDER"]) / subpasta / nome
    arquivo.save(destino)
    return f"{subpasta}/{nome}"


@bp.route("/")
@roles_required("admin", "almoxarife")
def listar():
    epis = query_db("SELECT * FROM epis WHERE ativo = 1 ORDER BY nome")
    return render_template("admin/epis.html", epis=epis)


@bp.route("/novo", methods=["GET", "POST"])
@roles_required("admin", "almoxarife")
def novo():
    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        descricao = request.form.get("descricao", "").strip()
        fabricante = request.form.get("fabricante", "").strip()
        tamanho = request.form.get("tamanho", "").strip()
        ca_numero = request.form.get("ca_numero", "").strip()
        ca_validade = request.form.get("ca_validade", "").strip()
        vida_util = request.form.get("vida_util_dias", "180").strip() or "180"
        estoque_inicial = request.form.get("estoque_inicial", "0").strip() or "0"
        estoque_minimo = request.form.get("estoque_minimo", "0").strip() or "0"

        erro = None
        if not nome:
            erro = "Informe o nome do EPI."
        elif not ca_numero:
            erro = "Informe o número do CA."

        foto_path = None
        if erro is None:
            try:
                foto_path = _salvar_foto(request.files.get("foto"), "epis")
            except ValueError as e:
                erro = str(e)

        if erro is None:
            epi_id = execute_db(
                """INSERT INTO epis (nome, descricao, fabricante, tamanho, ca_numero,
                                      ca_validade, vida_util_dias, foto_path, estoque_atual,
                                      estoque_minimo, criado_por)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (nome, descricao, fabricante, tamanho, ca_numero, ca_validade,
                 int(vida_util), foto_path, int(estoque_inicial), int(estoque_minimo),
                 get_current_user()["id"]),
            )
            if int(estoque_inicial) > 0:
                execute_db(
                    """INSERT INTO estoque_movimentacoes (epi_id, tipo, quantidade, motivo)
                       VALUES (?, 'entrada', ?, 'Estoque inicial no cadastro do EPI')""",
                    (epi_id, int(estoque_inicial)),
                )
            flash("EPI cadastrado com sucesso.", "sucesso")
            return redirect(url_for("epis.listar"))

        flash(erro, "erro")

    return render_template("admin/epi_form.html", epi=None)


@bp.route("/<int:epi_id>/editar", methods=["GET", "POST"])
@roles_required("admin", "almoxarife")
def editar(epi_id):
    epi = query_db("SELECT * FROM epis WHERE id = ?", (epi_id,), one=True)
    if epi is None:
        flash("EPI não encontrado.", "erro")
        return redirect(url_for("epis.listar"))

    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        descricao = request.form.get("descricao", "").strip()
        fabricante = request.form.get("fabricante", "").strip()
        tamanho = request.form.get("tamanho", "").strip()
        ca_numero = request.form.get("ca_numero", "").strip()
        ca_validade = request.form.get("ca_validade", "").strip()
        vida_util = request.form.get("vida_util_dias", "180").strip() or "180"
        estoque_minimo = request.form.get("estoque_minimo", "0").strip() or "0"

        foto_path = epi["foto_path"]
        try:
            nova_foto = _salvar_foto(request.files.get("foto"), "epis")
            if nova_foto:
                foto_path = nova_foto
        except ValueError as e:
            flash(str(e), "erro")
            return render_template("admin/epi_form.html", epi=epi)

        execute_db(
            """UPDATE epis SET nome=?, descricao=?, fabricante=?, tamanho=?, ca_numero=?,
                                ca_validade=?, vida_util_dias=?, estoque_minimo=?, foto_path=?
               WHERE id=?""",
            (nome, descricao, fabricante, tamanho, ca_numero, ca_validade,
             int(vida_util), int(estoque_minimo), foto_path, epi_id),
        )
        flash("EPI atualizado.", "sucesso")
        return redirect(url_for("epis.listar"))

    return render_template("admin/epi_form.html", epi=epi)


@bp.route("/<int:epi_id>/desativar", methods=["POST"])
@roles_required("admin")
def desativar(epi_id):
    execute_db("UPDATE epis SET ativo = 0 WHERE id = ?", (epi_id,))
    flash("EPI removido da lista de cadastrados.", "sucesso")
    return redirect(url_for("epis.listar"))
