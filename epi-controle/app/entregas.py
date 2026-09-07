import base64
from datetime import datetime, timedelta

from flask import (
    Blueprint, flash, redirect, render_template, request, url_for
)

from .auth import roles_required, login_required, get_current_user
from .db import get_db, query_db, execute_db, salvar_arquivo

bp = Blueprint("entregas", __name__, url_prefix="/entregas")

# Estados de preparação da entrega no almoxarifado, antes de o colaborador
# poder retirar e assinar o aceite. "aceito" e "recusado" são estados finais,
# fora deste fluxo (por isso não entram nesta lista).
STATUS_PREPARACAO = ("em_separacao", "em_compra", "pronto_retirada")
STATUS_ROTULOS = {
    "em_separacao": "Em separação",
    "em_compra": "Em compra",
    "pronto_retirada": "Pronto p/ retirada",
}


@bp.route("/")
@roles_required("admin", "almoxarife")
def listar():
    entregas = query_db(
        """SELECT en.*, e.nome AS epi_nome, u.nome AS colaborador_nome,
                  u.matricula AS colaborador_matricula, a.aceito_em
           FROM entregas en
           JOIN epis e ON e.id = en.epi_id
           JOIN usuarios u ON u.id = en.colaborador_id
           LEFT JOIN aceites a ON a.entrega_id = en.id
           ORDER BY en.id DESC LIMIT 100"""
    )
    colaboradores = query_db(
        "SELECT id, nome, matricula FROM usuarios WHERE ativo = 1 ORDER BY nome"
    )
    tamanhos = query_db(
        """SELECT et.id, et.tamanho, et.estoque_atual, e.id AS epi_id, e.nome, e.ca_numero, e.ca_validade
           FROM epi_tamanhos et JOIN epis e ON e.id = et.epi_id
           WHERE et.ativo = 1 AND e.ativo = 1
           ORDER BY e.nome, et.tamanho"""
    )
    return render_template(
        "admin/entregas.html", entregas=entregas, colaboradores=colaboradores, tamanhos=tamanhos,
        hoje=datetime.now().strftime("%Y-%m-%d"),
    )


@bp.route("/nova", methods=["POST"])
@roles_required("admin", "almoxarife")
def nova():
    epi_tamanho_id = request.form.get("epi_tamanho_id", type=int)
    colaborador_id = request.form.get("colaborador_id", type=int)
    quantidade = request.form.get("quantidade", type=int) or 1
    motivo = request.form.get("motivo", "Entrega inicial").strip()
    data_entrega = request.form.get("data_entrega") or datetime.now().strftime("%Y-%m-%d")
    observacoes = request.form.get("observacoes", "").strip()

    variante = query_db(
        """SELECT et.*, e.nome AS epi_nome, e.ca_numero, e.ca_validade, e.vida_util_dias
           FROM epi_tamanhos et JOIN epis e ON e.id = et.epi_id WHERE et.id = ?""",
        (epi_tamanho_id,), one=True,
    )
    colaborador = query_db("SELECT * FROM usuarios WHERE id = ?", (colaborador_id,), one=True)

    if variante is None or colaborador is None:
        flash("Selecione o colaborador e o EPI/tamanho.", "erro")
        return redirect(url_for("entregas.listar"))

    if quantidade > variante["estoque_atual"]:
        flash(
            f"Saldo insuficiente em estoque para {variante['epi_nome']} "
            f"(tam. {variante['tamanho']}, disponível: {variante['estoque_atual']}).",
            "erro",
        )
        return redirect(url_for("entregas.listar"))

    try:
        data_troca = (
            datetime.strptime(data_entrega, "%Y-%m-%d") + timedelta(days=variante["vida_util_dias"])
        ).strftime("%Y-%m-%d")
    except ValueError:
        data_troca = None

    db = get_db()
    entrega_id = execute_db(
        """INSERT INTO entregas (epi_id, epi_tamanho_id, colaborador_id, direcionado_por, quantidade, tamanho,
                                  motivo, data_entrega, data_troca_prevista, ca_numero,
                                  ca_validade, observacoes, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'em_separacao')""",
        (variante["epi_id"], epi_tamanho_id, colaborador_id, get_current_user()["id"], quantidade,
         variante["tamanho"], motivo, data_entrega, data_troca,
         variante["ca_numero"], variante["ca_validade"], observacoes),
    )
    db.execute("UPDATE epi_tamanhos SET estoque_atual = estoque_atual - ? WHERE id = ?", (quantidade, epi_tamanho_id))
    db.execute(
        """INSERT INTO estoque_movimentacoes (epi_id, epi_tamanho_id, tipo, quantidade, motivo, usuario_id)
           VALUES (?, ?, 'saida', ?, ?, ?)""",
        (variante["epi_id"], epi_tamanho_id, quantidade, f"Entrega para {colaborador['nome']}", get_current_user()["id"]),
    )
    db.commit()

    flash(f"EPI direcionado para {colaborador['nome']}. Enviado para o almoxarifado separar.", "sucesso")
    return redirect(url_for("entregas.listar"))


@bp.route("/<int:entrega_id>/status", methods=["POST"])
@roles_required("admin", "almoxarife")
def atualizar_status(entrega_id):
    """O almoxarife avança o status conforme faz a conferência/compra do
    material, até marcar "pronto para retirada" — só a partir daí o
    colaborador consegue ver a pendência e assinar o aceite."""
    novo_status = request.form.get("novo_status", "")
    entrega = query_db("SELECT * FROM entregas WHERE id = ?", (entrega_id,), one=True)

    if entrega is None:
        flash("Entrega não encontrada.", "erro")
    elif entrega["status"] not in STATUS_PREPARACAO:
        flash("Esta entrega já foi finalizada (aceita ou recusada) e não pode mais mudar de status.", "erro")
    elif novo_status not in STATUS_PREPARACAO:
        flash("Status inválido.", "erro")
    else:
        execute_db("UPDATE entregas SET status = ? WHERE id = ?", (novo_status, entrega_id))
        flash(f"Status atualizado para \"{STATUS_ROTULOS[novo_status]}\".", "sucesso")

    return redirect(url_for("entregas.listar"))


@bp.route("/<int:entrega_id>/aceitar", methods=["GET", "POST"])
@login_required
def aceitar(entrega_id):
    user = get_current_user()
    entrega = query_db(
        """SELECT en.*, e.nome AS epi_nome, e.descricao AS epi_descricao, e.foto_arquivo_id AS epi_foto_arquivo_id
           FROM entregas en JOIN epis e ON e.id = en.epi_id
           WHERE en.id = ?""",
        (entrega_id,), one=True,
    )

    if entrega is None:
        flash("Entrega não encontrada.", "erro")
        return redirect(url_for("painel.index_redirect"))

    if entrega["colaborador_id"] != user["id"]:
        # Só o próprio colaborador pode ver e assinar o aceite dele — inclusive o
        # admin não pode assinar em nome de outra pessoa, isso invalidaria o termo.
        flash("Esta entrega não pertence ao seu usuário.", "erro")
        return redirect(url_for("painel.index_redirect"))

    if entrega["status"] == "aceito":
        flash("Este EPI já foi aceito anteriormente.", "info")
        return redirect(url_for("painel.index_redirect"))

    if entrega["status"] in ("em_separacao", "em_compra"):
        flash(
            "Este EPI ainda está em preparação no almoxarifado. "
            "Você poderá assinar o aceite assim que estiver pronto para retirada.",
            "info",
        )
        return redirect(url_for("painel.index_redirect"))

    if request.method == "POST":
        assinatura_b64 = request.form.get("assinatura_dados", "")
        foto_b64 = request.form.get("foto_dados", "")

        if not assinatura_b64 or "," not in assinatura_b64:
            flash("É necessário assinar antes de confirmar o aceite.", "erro")
            return render_template("colaborador/aceite.html", entrega=entrega)

        if not foto_b64 or "," not in foto_b64:
            flash("É necessário tirar uma foto no momento do aceite.", "erro")
            return render_template("colaborador/aceite.html", entrega=entrega)

        assinatura_arquivo_id = salvar_arquivo(
            base64.b64decode(assinatura_b64.split(",", 1)[1]), "image/png"
        )
        foto_arquivo_id = salvar_arquivo(
            base64.b64decode(foto_b64.split(",", 1)[1]), "image/png"
        )

        db = get_db()
        db.execute(
            """INSERT INTO aceites (entrega_id, assinatura_arquivo_id, foto_arquivo_id, ip_address, user_agent)
               VALUES (?, ?, ?, ?, ?)""",
            (entrega_id, assinatura_arquivo_id, foto_arquivo_id, request.remote_addr,
             request.headers.get("User-Agent", "")[:255]),
        )
        db.execute("UPDATE entregas SET status = 'aceito' WHERE id = ?", (entrega_id,))
        db.commit()

        flash("Aceite registrado com sucesso. Obrigado!", "sucesso")
        return redirect(url_for("relatorios.ficha_entrega", entrega_id=entrega_id))

    return render_template("colaborador/aceite.html", entrega=entrega)
