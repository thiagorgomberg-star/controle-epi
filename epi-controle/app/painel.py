from datetime import datetime, timedelta

from flask import Blueprint, redirect, render_template, url_for

from .auth import get_current_user, login_required, roles_required
from .db import query_db

bp = Blueprint("painel", __name__)


@bp.route("/")
def index_redirect():
    user = get_current_user()
    if user is None:
        return redirect(url_for("auth.login"))
    if user["papel"] in ("admin", "almoxarife"):
        return redirect(url_for("painel.admin_dashboard"))
    return redirect(url_for("painel.meus_epis"))


@bp.route("/painel")
@login_required
def meus_epis():
    user = get_current_user()
    hoje = datetime.now().strftime("%Y-%m-%d")

    pendentes = query_db(
        """SELECT en.*, e.nome AS epi_nome FROM entregas en
           JOIN epis e ON e.id = en.epi_id
           WHERE en.colaborador_id = ? AND en.status = 'pendente'
           ORDER BY en.id DESC""",
        (user["id"],),
    )
    historico = query_db(
        """SELECT en.*, e.nome AS epi_nome, e.foto_arquivo_id AS epi_foto_arquivo_id, a.aceito_em
           FROM entregas en
           JOIN epis e ON e.id = en.epi_id
           LEFT JOIN aceites a ON a.entrega_id = en.id
           WHERE en.colaborador_id = ?
           ORDER BY en.id DESC""",
        (user["id"],),
    )

    epis_vencidos = sum(1 for h in historico if h["data_troca_prevista"] and h["data_troca_prevista"] < hoje)
    ca_vencidos = sum(1 for h in historico if h["ca_validade"] and h["ca_validade"] < hoje)

    return render_template(
        "colaborador/meus_epis.html",
        pendentes=pendentes, historico=historico,
        epis_vencidos=epis_vencidos, ca_vencidos=ca_vencidos, hoje=hoje,
    )


@bp.route("/admin")
@roles_required("admin", "almoxarife")
def admin_dashboard():
    hoje = datetime.now().strftime("%Y-%m-%d")

    aceites_pendentes = query_db(
        "SELECT COUNT(*) AS c FROM entregas WHERE status = 'pendente'", one=True
    )["c"]
    epis_vencidos = query_db(
        "SELECT COUNT(*) AS c FROM entregas WHERE status='aceito' AND data_troca_prevista < ?",
        (hoje,), one=True,
    )["c"]
    ca_vencidos = query_db(
        "SELECT COUNT(*) AS c FROM epis WHERE ativo=1 AND ca_validade IS NOT NULL AND ca_validade != '' AND ca_validade < ?",
        (hoje,), one=True,
    )["c"]
    estoque_baixo = query_db(
        """SELECT COUNT(*) AS c FROM epi_tamanhos et JOIN epis e ON e.id = et.epi_id
           WHERE et.ativo=1 AND e.ativo=1 AND et.estoque_atual <= et.estoque_minimo""",
        one=True,
    )["c"]

    return render_template(
        "admin/dashboard.html",
        aceites_pendentes=aceites_pendentes, epis_vencidos=epis_vencidos,
        ca_vencidos=ca_vencidos, estoque_baixo=estoque_baixo,
    )
