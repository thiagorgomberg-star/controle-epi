from flask import Blueprint, flash, redirect, render_template, request, url_for

from .auth import roles_required, get_current_user
from .db import get_db, query_db, execute_db

bp = Blueprint("estoque", __name__, url_prefix="/estoque")


@bp.route("/")
@roles_required("admin", "almoxarife")
def listar():
    epis = query_db(
        "SELECT * FROM epis WHERE ativo = 1 ORDER BY (estoque_atual <= estoque_minimo) DESC, nome"
    )
    movimentacoes = query_db(
        """SELECT m.*, e.nome AS epi_nome, u.nome AS usuario_nome
           FROM estoque_movimentacoes m
           JOIN epis e ON e.id = m.epi_id
           LEFT JOIN usuarios u ON u.id = m.usuario_id
           ORDER BY m.id DESC LIMIT 50"""
    )
    return render_template("admin/estoque.html", epis=epis, movimentacoes=movimentacoes)


@bp.route("/movimentar", methods=["POST"])
@roles_required("admin", "almoxarife")
def movimentar():
    epi_id = request.form.get("epi_id", type=int)
    tipo = request.form.get("tipo")
    quantidade = request.form.get("quantidade", type=int)
    motivo = request.form.get("motivo", "").strip()

    if not epi_id or tipo not in ("entrada", "saida", "ajuste") or not quantidade or quantidade <= 0:
        flash("Preencha item, tipo e uma quantidade válida.", "erro")
        return redirect(url_for("estoque.listar"))

    epi = query_db("SELECT * FROM epis WHERE id = ?", (epi_id,), one=True)
    if epi is None:
        flash("Item não encontrado.", "erro")
        return redirect(url_for("estoque.listar"))

    if tipo == "saida" and quantidade > epi["estoque_atual"]:
        flash(f"Saldo insuficiente: há apenas {epi['estoque_atual']} em estoque.", "erro")
        return redirect(url_for("estoque.listar"))

    delta = quantidade if tipo == "entrada" else -quantidade
    db = get_db()
    db.execute("UPDATE epis SET estoque_atual = estoque_atual + ? WHERE id = ?", (delta, epi_id))
    db.execute(
        """INSERT INTO estoque_movimentacoes (epi_id, tipo, quantidade, motivo, usuario_id)
           VALUES (?, ?, ?, ?, ?)""",
        (epi_id, tipo, quantidade, motivo, get_current_user()["id"]),
    )
    db.commit()
    flash("Movimentação registrada.", "sucesso")
    return redirect(url_for("estoque.listar"))
