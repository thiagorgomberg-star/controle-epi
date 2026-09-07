import uuid
from pathlib import Path

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for

from .auth import roles_required
from .db import get_db, query_db, execute_db

bp = Blueprint("configuracoes", __name__, url_prefix="/configuracoes")


def _config():
    cfg = query_db("SELECT * FROM configuracoes WHERE id = 1", one=True)
    if cfg is None:
        execute_db("INSERT INTO configuracoes (id, empresa_nome) VALUES (1, '')")
        cfg = query_db("SELECT * FROM configuracoes WHERE id = 1", one=True)
    return cfg


@bp.route("/", methods=["GET", "POST"])
@roles_required("admin")
def editar():
    cfg = _config()

    if request.method == "POST":
        empresa_nome = request.form.get("empresa_nome", "").strip()
        responsavel_sesmt = request.form.get("responsavel_sesmt", "").strip()

        logo_path = cfg["logo_path"]
        arquivo = request.files.get("logo")
        if arquivo and arquivo.filename:
            ext = arquivo.filename.rsplit(".", 1)[-1].lower()
            if ext in {"png", "jpg", "jpeg", "webp"}:
                nome = f"logo/{uuid.uuid4().hex}.{ext}"
                arquivo.save(Path(current_app.config["UPLOAD_FOLDER"]) / nome)
                logo_path = nome
            else:
                flash("Logo precisa ser PNG, JPG ou WEBP.", "erro")

        execute_db(
            """UPDATE configuracoes SET empresa_nome=?, responsavel_sesmt=?, logo_path=?,
                                          atualizado_em=datetime('now') WHERE id=1""",
            (empresa_nome, responsavel_sesmt, logo_path),
        )
        flash("Configuração salva.", "sucesso")
        return redirect(url_for("configuracoes.editar"))

    return render_template("admin/configuracoes.html", cfg=cfg)


@bp.route("/zerar", methods=["POST"])
@roles_required("admin")
def zerar_dados():
    confirmacao = request.form.get("confirmacao", "")
    if confirmacao != "RESET":
        flash('Digite exatamente "RESET" para confirmar.', "erro")
        return redirect(url_for("configuracoes.editar"))

    db = get_db()
    for tabela in ("aceites", "entregas", "estoque_movimentacoes", "epis", "ca_cache", "sync_log"):
        db.execute(f"DELETE FROM {tabela}")
    db.commit()
    flash("Dados de EPIs, estoque e entregas foram apagados. Colaboradores foram mantidos.", "sucesso")
    return redirect(url_for("configuracoes.editar"))
