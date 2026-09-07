from flask import Blueprint, flash, redirect, render_template, request, url_for

from .auth import roles_required, get_current_user
from .db import get_db, query_db, execute_db

bp = Blueprint("estoque", __name__, url_prefix="/estoque")


@bp.route("/")
@roles_required("admin", "almoxarife")
def listar():
    tamanhos = query_db(
        """SELECT et.*, e.nome AS epi_nome, e.fabricante
           FROM epi_tamanhos et
           JOIN epis e ON e.id = et.epi_id
           WHERE et.ativo = 1 AND e.ativo = 1
           ORDER BY (et.estoque_atual <= et.estoque_minimo) DESC, e.nome, et.tamanho"""
    )
    movimentacoes = query_db(
        """SELECT m.*, e.nome AS epi_nome, et.tamanho AS tamanho, u.nome AS usuario_nome
           FROM estoque_movimentacoes m
           JOIN epis e ON e.id = m.epi_id
           LEFT JOIN epi_tamanhos et ON et.id = m.epi_tamanho_id
           LEFT JOIN usuarios u ON u.id = m.usuario_id
           ORDER BY m.id DESC LIMIT 50"""
    )
    return render_template("admin/estoque.html", tamanhos=tamanhos, movimentacoes=movimentacoes)


@bp.route("/movimentar", methods=["POST"])
@roles_required("admin", "almoxarife")
def movimentar():
    epi_tamanho_id = request.form.get("epi_tamanho_id", type=int)
    tipo = request.form.get("tipo")
    quantidade = request.form.get("quantidade", type=int)
    motivo = request.form.get("motivo", "").strip()

    if not epi_tamanho_id or tipo not in ("entrada", "saida", "ajuste") or not quantidade or quantidade <= 0:
        flash("Preencha item, tipo e uma quantidade válida.", "erro")
        return redirect(url_for("estoque.listar"))

    variante = query_db(
        """SELECT et.*, e.nome AS epi_nome FROM epi_tamanhos et
           JOIN epis e ON e.id = et.epi_id WHERE et.id = ?""",
        (epi_tamanho_id,), one=True,
    )
    if variante is None:
        flash("Item não encontrado.", "erro")
        return redirect(url_for("estoque.listar"))

    if tipo == "saida" and quantidade > variante["estoque_atual"]:
        flash(f"Saldo insuficiente: há apenas {variante['estoque_atual']} em estoque.", "erro")
        return redirect(url_for("estoque.listar"))

    delta = quantidade if tipo == "entrada" else -quantidade
    db = get_db()
    db.execute("UPDATE epi_tamanhos SET estoque_atual = estoque_atual + ? WHERE id = ?", (delta, epi_tamanho_id))
    db.execute(
        """INSERT INTO estoque_movimentacoes (epi_id, epi_tamanho_id, tipo, quantidade, motivo, usuario_id)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (variante["epi_id"], epi_tamanho_id, tipo, quantidade, motivo, get_current_user()["id"]),
    )
    db.commit()
    flash("Movimentação registrada.", "sucesso")
    return redirect(url_for("estoque.listar"))
