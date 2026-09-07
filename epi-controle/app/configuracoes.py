from datetime import datetime

from flask import Blueprint, flash, redirect, render_template, request, url_for

from .auth import roles_required
from .db import get_db, query_db, execute_db, salvar_arquivo

bp = Blueprint("configuracoes", __name__, url_prefix="/configuracoes")

_MIME_POR_EXTENSAO = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}


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

        logo_arquivo_id = cfg["logo_arquivo_id"]
        arquivo = request.files.get("logo")
        if arquivo and arquivo.filename:
            ext = arquivo.filename.rsplit(".", 1)[-1].lower()
            if ext in _MIME_POR_EXTENSAO:
                logo_arquivo_id = salvar_arquivo(arquivo.read(), _MIME_POR_EXTENSAO[ext])
            else:
                flash("Logo precisa ser PNG, JPG ou WEBP.", "erro")

        execute_db(
            """UPDATE configuracoes SET empresa_nome=?, responsavel_sesmt=?, logo_arquivo_id=?,
                                          atualizado_em=? WHERE id=1""",
            (empresa_nome, responsavel_sesmt, logo_arquivo_id,
             datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
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
