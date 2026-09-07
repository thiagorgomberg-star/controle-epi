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

    # "pronto_retirada" é o único status em que o colaborador já pode ir ao
    # almoxarifado retirar o item e assinar o aceite. 'pendente' fica aqui só
    # por segurança/compatibilidade com qualquer entrega antiga que a
    # migração de status não tenha alcançado.
    pendentes = query_db(
        """SELECT en.*, e.nome AS epi_nome FROM entregas en
           JOIN epis e ON e.id = en.epi_id
           WHERE en.colaborador_id = ? AND en.status IN ('pronto_retirada', 'pendente')
           ORDER BY en.id DESC""",
        (user["id"],),
    )
    em_andamento = query_db(
        """SELECT en.*, e.nome AS epi_nome FROM entregas en
           JOIN epis e ON e.id = en.epi_id
           WHERE en.colaborador_id = ? AND en.status IN ('em_separacao', 'em_compra')
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
    minhas_solicitacoes = query_db(
        """SELECT s.*, et.tamanho, e.nome AS epi_nome
           FROM solicitacoes s
           JOIN epi_tamanhos et ON et.id = s.epi_tamanho_id
           JOIN epis e ON e.id = et.epi_id
           WHERE s.colaborador_id = ?
           ORDER BY s.id DESC LIMIT 20""",
        (user["id"],),
    )
    tamanhos_disponiveis = query_db(
        """SELECT et.id, et.tamanho, et.estoque_atual, e.nome
           FROM epi_tamanhos et JOIN epis e ON e.id = et.epi_id
           WHERE et.ativo = 1 AND e.ativo = 1
           ORDER BY e.nome, et.tamanho"""
    )

    epis_vencidos = sum(1 for h in historico if h["data_troca_prevista"] and h["data_troca_prevista"] < hoje)
    ca_vencidos = sum(1 for h in historico if h["ca_validade"] and h["ca_validade"] < hoje)

    return render_template(
        "colaborador/meus_epis.html",
        pendentes=pendentes, em_andamento=em_andamento, historico=historico,
        minhas_solicitacoes=minhas_solicitacoes, tamanhos_disponiveis=tamanhos_disponiveis,
        epis_vencidos=epis_vencidos, ca_vencidos=ca_vencidos, hoje=hoje,
    )


@bp.route("/admin")
@roles_required("admin", "almoxarife")
def admin_dashboard():
    hoje = datetime.now().strftime("%Y-%m-%d")

    aceites_pendentes = query_db(
        "SELECT COUNT(*) AS c FROM entregas WHERE status IN ('pronto_retirada', 'pendente')", one=True
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
    solicitacoes_pendentes = query_db(
        "SELECT COUNT(*) AS c FROM solicitacoes WHERE status = 'pendente_aprovacao'", one=True
    )["c"]

    return render_template(
        "admin/dashboard.html",
        aceites_pendentes=aceites_pendentes, epis_vencidos=epis_vencidos,
        ca_vencidos=ca_vencidos, estoque_baixo=estoque_baixo,
        solicitacoes_pendentes=solicitacoes_pendentes,
    )
