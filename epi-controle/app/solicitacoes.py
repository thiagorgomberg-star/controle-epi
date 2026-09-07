from datetime import datetime, timedelta

from flask import (
    Blueprint, flash, redirect, render_template, request, url_for
)

from .auth import roles_required, login_required, get_current_user
from .db import get_db, query_db, execute_db

bp = Blueprint("solicitacoes", __name__, url_prefix="/solicitacoes")


@bp.route("/nova", methods=["POST"])
@login_required
def nova():
    """O colaborador pede a troca ou reposição de um EPI que já existe no
    estoque. Fica pendente até o gestor SESMT (admin) aprovar ou recusar —
    só depois disso vira uma entrega de verdade, que segue para o
    almoxarifado separar."""
    user = get_current_user()
    epi_tamanho_id = request.form.get("epi_tamanho_id", type=int)
    tipo = request.form.get("tipo", "").strip()
    quantidade = request.form.get("quantidade", type=int) or 1
    motivo = request.form.get("motivo", "").strip()

    variante = query_db(
        """SELECT et.*, e.nome AS epi_nome FROM epi_tamanhos et
           JOIN epis e ON e.id = et.epi_id
           WHERE et.id = ? AND et.ativo = 1 AND e.ativo = 1""",
        (epi_tamanho_id,), one=True,
    )

    if variante is None or tipo not in ("troca", "reposicao"):
        flash("Selecione o EPI/tamanho e o tipo de solicitação antes de enviar.", "erro")
        return redirect(url_for("painel.meus_epis"))

    if quantidade < 1:
        quantidade = 1

    execute_db(
        """INSERT INTO solicitacoes (colaborador_id, epi_tamanho_id, tipo, quantidade, motivo)
           VALUES (?, ?, ?, ?, ?)""",
        (user["id"], epi_tamanho_id, tipo, quantidade, motivo),
    )
    flash(
        f"Solicitação enviada para o SESMT aprovar ({variante['epi_nome']} — tam. {variante['tamanho']}).",
        "sucesso",
    )
    return redirect(url_for("painel.meus_epis"))


@bp.route("/")
@roles_required("admin")
def listar():
    """Fila de solicitações de troca/reposição para o gestor SESMT decidir."""
    pendentes = query_db(
        """SELECT s.*, u.nome AS colaborador_nome, u.matricula AS colaborador_matricula,
                  et.tamanho, et.estoque_atual, e.nome AS epi_nome
           FROM solicitacoes s
           JOIN usuarios u ON u.id = s.colaborador_id
           JOIN epi_tamanhos et ON et.id = s.epi_tamanho_id
           JOIN epis e ON e.id = et.epi_id
           WHERE s.status = 'pendente_aprovacao'
           ORDER BY s.id"""
    )
    historico = query_db(
        """SELECT s.*, u.nome AS colaborador_nome, et.tamanho, e.nome AS epi_nome,
                  ap.nome AS aprovado_por_nome
           FROM solicitacoes s
           JOIN usuarios u ON u.id = s.colaborador_id
           JOIN epi_tamanhos et ON et.id = s.epi_tamanho_id
           JOIN epis e ON e.id = et.epi_id
           LEFT JOIN usuarios ap ON ap.id = s.aprovado_por
           WHERE s.status != 'pendente_aprovacao'
           ORDER BY s.id DESC LIMIT 50"""
    )
    return render_template("admin/solicitacoes.html", pendentes=pendentes, historico=historico)


@bp.route("/<int:solicitacao_id>/aprovar", methods=["POST"])
@roles_required("admin")
def aprovar(solicitacao_id):
    """Aprovar cria a entrega de verdade (mesmo fluxo de separação/compra/
    retirada de qualquer outra entrega) e já debita do estoque, exatamente
    como uma entrega direcionada manualmente pelo admin."""
    solicitacao = query_db("SELECT * FROM solicitacoes WHERE id = ?", (solicitacao_id,), one=True)
    if solicitacao is None or solicitacao["status"] != "pendente_aprovacao":
        flash("Solicitação não encontrada ou já foi decidida.", "erro")
        return redirect(url_for("solicitacoes.listar"))

    variante = query_db(
        """SELECT et.*, e.nome AS epi_nome, e.ca_numero, e.ca_validade, e.vida_util_dias
           FROM epi_tamanhos et JOIN epis e ON e.id = et.epi_id WHERE et.id = ?""",
        (solicitacao["epi_tamanho_id"],), one=True,
    )
    colaborador = query_db("SELECT * FROM usuarios WHERE id = ?", (solicitacao["colaborador_id"],), one=True)

    if variante is None or colaborador is None:
        flash("Não foi possível aprovar: EPI/tamanho ou colaborador não encontrado.", "erro")
        return redirect(url_for("solicitacoes.listar"))

    if solicitacao["quantidade"] > variante["estoque_atual"]:
        flash(
            f"Saldo insuficiente em estoque para aprovar ({variante['epi_nome']} tam. "
            f"{variante['tamanho']}, disponível: {variante['estoque_atual']}). Reponha o estoque antes de aprovar.",
            "erro",
        )
        return redirect(url_for("solicitacoes.listar"))

    hoje = datetime.now().strftime("%Y-%m-%d")
    try:
        data_troca = (datetime.now() + timedelta(days=variante["vida_util_dias"])).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        data_troca = None

    motivo_entrega = (
        "Troca solicitada pelo colaborador" if solicitacao["tipo"] == "troca"
        else "Reposição solicitada pelo colaborador"
    )
    if solicitacao["motivo"]:
        motivo_entrega += f" — {solicitacao['motivo']}"

    db = get_db()
    entrega_id = execute_db(
        """INSERT INTO entregas (epi_id, epi_tamanho_id, colaborador_id, direcionado_por, quantidade, tamanho,
                                  motivo, data_entrega, data_troca_prevista, ca_numero, ca_validade, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'em_separacao')""",
        (variante["epi_id"], solicitacao["epi_tamanho_id"], solicitacao["colaborador_id"],
         get_current_user()["id"], solicitacao["quantidade"], variante["tamanho"],
         motivo_entrega, hoje, data_troca, variante["ca_numero"], variante["ca_validade"]),
    )
    db.execute(
        "UPDATE epi_tamanhos SET estoque_atual = estoque_atual - ? WHERE id = ?",
        (solicitacao["quantidade"], solicitacao["epi_tamanho_id"]),
    )
    db.execute(
        """INSERT INTO estoque_movimentacoes (epi_id, epi_tamanho_id, tipo, quantidade, motivo, usuario_id)
           VALUES (?, ?, 'saida', ?, ?, ?)""",
        (variante["epi_id"], solicitacao["epi_tamanho_id"], solicitacao["quantidade"],
         f"Aprovação de solicitação para {colaborador['nome']}", get_current_user()["id"]),
    )
    db.execute(
        """UPDATE solicitacoes SET status = 'aprovada', aprovado_por = ?, aprovado_em = ?, entrega_id = ?
           WHERE id = ?""",
        (get_current_user()["id"], datetime.now().strftime("%Y-%m-%d %H:%M:%S"), entrega_id, solicitacao_id),
    )
    db.commit()

    flash(f"Solicitação aprovada e enviada para o almoxarifado separar ({colaborador['nome']}).", "sucesso")
    return redirect(url_for("solicitacoes.listar"))


@bp.route("/<int:solicitacao_id>/recusar", methods=["POST"])
@roles_required("admin")
def recusar(solicitacao_id):
    observacao = request.form.get("observacao", "").strip()
    execute_db(
        """UPDATE solicitacoes SET status = 'recusada', aprovado_por = ?, aprovado_em = ?, observacoes_aprovacao = ?
           WHERE id = ? AND status = 'pendente_aprovacao'""",
        (get_current_user()["id"], datetime.now().strftime("%Y-%m-%d %H:%M:%S"), observacao, solicitacao_id),
    )
    flash("Solicitação recusada.", "sucesso")
    return redirect(url_for("solicitacoes.listar"))

