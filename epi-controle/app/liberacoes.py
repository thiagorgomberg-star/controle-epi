import base64
from datetime import datetime

from flask import (
    Blueprint, flash, redirect, render_template, request, url_for
)

from .auth import roles_required, login_required, get_current_user
from .db import get_db, query_db, execute_db, salvar_arquivo

bp = Blueprint("liberacoes", __name__, url_prefix="/liberacoes")

# Mesmos estados de preparação usados em entregas (separação/compra no
# almoxarifado, antes de o colaborador poder retirar e assinar o aceite).
STATUS_PREPARACAO = ("em_separacao", "em_compra", "pronto_retirada")
STATUS_ROTULOS = {
    "em_separacao": "Em separação",
    "em_compra": "Em compra",
    "pronto_retirada": "Pronto p/ retirada",
}


@bp.route("/")
@roles_required("admin", "almoxarife")
def listar():
    liberacoes = query_db(
        """SELECT l.*, f.nome AS ferramenta_nome, u.nome AS colaborador_nome,
                  u.matricula AS colaborador_matricula
           FROM liberacoes_ferramenta l
           JOIN ferramentas f ON f.id = l.ferramenta_id
           JOIN usuarios u ON u.id = l.colaborador_id
           ORDER BY l.id DESC LIMIT 100"""
    )
    colaboradores = query_db(
        "SELECT id, nome, matricula FROM usuarios WHERE ativo = 1 ORDER BY nome"
    )
    ferramentas = query_db(
        "SELECT id, nome, fabricante, patrimonio, estoque_atual FROM ferramentas WHERE ativo = 1 ORDER BY nome"
    )
    return render_template(
        "admin/liberacoes.html", liberacoes=liberacoes, colaboradores=colaboradores,
        ferramentas=ferramentas, hoje=datetime.now().strftime("%Y-%m-%d"),
    )


@bp.route("/nova", methods=["POST"])
@roles_required("admin", "almoxarife")
def nova():
    ferramenta_id = request.form.get("ferramenta_id", type=int)
    colaborador_id = request.form.get("colaborador_id", type=int)
    quantidade = request.form.get("quantidade", type=int) or 1
    tipo = request.form.get("tipo", "")
    motivo = request.form.get("motivo", "").strip()
    data_liberacao = request.form.get("data_liberacao") or datetime.now().strftime("%Y-%m-%d")
    data_prevista_devolucao = request.form.get("data_prevista_devolucao", "").strip() or None
    observacoes = request.form.get("observacoes", "").strip()

    ferramenta = query_db("SELECT * FROM ferramentas WHERE id = ?", (ferramenta_id,), one=True)
    colaborador = query_db("SELECT * FROM usuarios WHERE id = ?", (colaborador_id,), one=True)

    if ferramenta is None or colaborador is None:
        flash("Selecione o colaborador e a ferramenta.", "erro")
        return redirect(url_for("liberacoes.listar"))

    if tipo not in ("emprestimo", "fixo"):
        flash("Selecione o tipo de liberação (empréstimo ou fixa).", "erro")
        return redirect(url_for("liberacoes.listar"))

    # Item fixo fica definitivamente com o colaborador/equipe — não faz
    # sentido guardar uma previsão de devolução nesse caso.
    if tipo == "fixo":
        data_prevista_devolucao = None

    if quantidade > ferramenta["estoque_atual"]:
        flash(
            f"Saldo insuficiente em estoque para {ferramenta['nome']} "
            f"(disponível: {ferramenta['estoque_atual']}).",
            "erro",
        )
        return redirect(url_for("liberacoes.listar"))

    db = get_db()
    execute_db(
        """INSERT INTO liberacoes_ferramenta (ferramenta_id, colaborador_id, direcionado_por, quantidade,
                                                tipo, motivo, data_liberacao, data_prevista_devolucao,
                                                observacoes, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'em_separacao')""",
        (ferramenta_id, colaborador_id, get_current_user()["id"], quantidade,
         tipo, motivo, data_liberacao, data_prevista_devolucao, observacoes),
    )
    db.execute("UPDATE ferramentas SET estoque_atual = estoque_atual - ? WHERE id = ?", (quantidade, ferramenta_id))
    db.execute(
        """INSERT INTO ferramenta_movimentacoes (ferramenta_id, tipo, quantidade, motivo, usuario_id)
           VALUES (?, 'saida', ?, ?, ?)""",
        (ferramenta_id, quantidade, f"Liberação para {colaborador['nome']}", get_current_user()["id"]),
    )
    db.commit()

    flash(f"Ferramenta direcionada para {colaborador['nome']}. Enviada para o almoxarifado separar.", "sucesso")
    return redirect(url_for("liberacoes.listar"))


@bp.route("/<int:liberacao_id>/status", methods=["POST"])
@roles_required("admin", "almoxarife")
def atualizar_status(liberacao_id):
    """O almoxarife avança o status conforme faz a conferência/compra do
    material, até marcar "pronto para retirada" — só a partir daí o
    colaborador consegue ver a pendência e assinar o aceite."""
    novo_status = request.form.get("novo_status", "")
    liberacao = query_db("SELECT * FROM liberacoes_ferramenta WHERE id = ?", (liberacao_id,), one=True)

    if liberacao is None:
        flash("Liberação não encontrada.", "erro")
    elif liberacao["status"] not in STATUS_PREPARACAO:
        flash("Esta liberação já foi finalizada e não pode mais mudar de status.", "erro")
    elif novo_status not in STATUS_PREPARACAO:
        flash("Status inválido.", "erro")
    else:
        execute_db("UPDATE liberacoes_ferramenta SET status = ? WHERE id = ?", (novo_status, liberacao_id))
        flash(f"Status atualizado para \"{STATUS_ROTULOS[novo_status]}\".", "sucesso")

    return redirect(url_for("liberacoes.listar"))


@bp.route("/<int:liberacao_id>/aceitar", methods=["GET", "POST"])
@login_required
def aceitar(liberacao_id):
    user = get_current_user()
    liberacao = query_db(
        """SELECT l.*, f.nome AS ferramenta_nome, f.descricao AS ferramenta_descricao,
                  f.fabricante AS ferramenta_fabricante, f.patrimonio AS ferramenta_patrimonio,
                  f.foto_arquivo_id AS ferramenta_foto_arquivo_id
           FROM liberacoes_ferramenta l JOIN ferramentas f ON f.id = l.ferramenta_id
           WHERE l.id = ?""",
        (liberacao_id,), one=True,
    )

    if liberacao is None:
        flash("Liberação não encontrada.", "erro")
        return redirect(url_for("painel.index_redirect"))

    if liberacao["colaborador_id"] != user["id"]:
        # Só o próprio colaborador pode ver e assinar o aceite dele — inclusive o
        # admin não pode assinar em nome de outra pessoa, isso invalidaria o termo.
        flash("Esta liberação não pertence ao seu usuário.", "erro")
        return redirect(url_for("painel.index_redirect"))

    if liberacao["status"] in ("aceito", "devolvido"):
        flash("Esta ferramenta já foi aceita anteriormente.", "info")
        return redirect(url_for("painel.index_redirect"))

    if liberacao["status"] in ("em_separacao", "em_compra"):
        flash(
            "Esta ferramenta ainda está em preparação no almoxarifado. "
            "Você poderá assinar o aceite assim que estiver pronta para retirada.",
            "info",
        )
        return redirect(url_for("painel.index_redirect"))

    if request.method == "POST":
        assinatura_b64 = request.form.get("assinatura_dados", "")
        foto_b64 = request.form.get("foto_dados", "")

        if not assinatura_b64 or "," not in assinatura_b64:
            flash("É necessário assinar antes de confirmar o aceite.", "erro")
            return render_template("colaborador/aceite_ferramenta.html", liberacao=liberacao)

        if not foto_b64 or "," not in foto_b64:
            flash("É necessário tirar uma foto no momento do aceite.", "erro")
            return render_template("colaborador/aceite_ferramenta.html", liberacao=liberacao)

        assinatura_arquivo_id = salvar_arquivo(
            base64.b64decode(assinatura_b64.split(",", 1)[1]), "image/png"
        )
        foto_arquivo_id = salvar_arquivo(
            base64.b64decode(foto_b64.split(",", 1)[1]), "image/png"
        )

        execute_db(
            """UPDATE liberacoes_ferramenta
               SET status = 'aceito', assinatura_arquivo_id = ?, foto_arquivo_id = ?,
                   aceito_em = ?
               WHERE id = ?""",
            (assinatura_arquivo_id, foto_arquivo_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), liberacao_id),
        )

        flash("Aceite registrado com sucesso. Obrigado!", "sucesso")
        return redirect(url_for("painel.index_redirect"))

    return render_template("colaborador/aceite_ferramenta.html", liberacao=liberacao)


@bp.route("/<int:liberacao_id>/devolver", methods=["POST"])
@roles_required("admin", "almoxarife")
def devolver(liberacao_id):
    """Registra a devolução ao almoxarifado (repõe o estoque). Vale tanto para
    um empréstimo que venceu quanto para um item fixo que precisa voltar por
    algum motivo (ex.: colaborador saiu da empresa)."""
    observacao = request.form.get("observacao", "").strip()
    liberacao = query_db("SELECT * FROM liberacoes_ferramenta WHERE id = ?", (liberacao_id,), one=True)

    if liberacao is None:
        flash("Liberação não encontrada.", "erro")
        return redirect(url_for("liberacoes.listar"))

    if liberacao["status"] != "aceito":
        flash("Só é possível devolver uma ferramenta que já foi aceita pelo colaborador.", "erro")
        return redirect(url_for("liberacoes.listar"))

    db = get_db()
    db.execute(
        """UPDATE liberacoes_ferramenta
           SET status = 'devolvido', devolvido_em = ?, devolvido_para = ?, observacoes_devolucao = ?
           WHERE id = ?""",
        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), get_current_user()["id"], observacao, liberacao_id),
    )
    db.execute(
        "UPDATE ferramentas SET estoque_atual = estoque_atual + ? WHERE id = ?",
        (liberacao["quantidade"], liberacao["ferramenta_id"]),
    )
    db.execute(
        """INSERT INTO ferramenta_movimentacoes (ferramenta_id, tipo, quantidade, motivo, usuario_id)
           VALUES (?, 'entrada', ?, 'Devolução de liberação', ?)""",
        (liberacao["ferramenta_id"], liberacao["quantidade"], get_current_user()["id"]),
    )
    db.commit()

    flash("Devolução registrada e estoque atualizado.", "sucesso")
    return redirect(url_for("liberacoes.listar"))
