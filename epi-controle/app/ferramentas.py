from flask import (
    Blueprint, flash, redirect, render_template, request, url_for
)

from .auth import roles_required, get_current_user
from .db import get_db, query_db, execute_db, salvar_arquivo

bp = Blueprint("ferramentas", __name__, url_prefix="/ferramentas")

EXTENSOES_IMAGEM = {"png", "jpg", "jpeg", "webp"}
_MIME_POR_EXTENSAO = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}


def _extensao_valida(nome_arquivo):
    return "." in nome_arquivo and nome_arquivo.rsplit(".", 1)[1].lower() in EXTENSOES_IMAGEM


def _salvar_foto(arquivo):
    """Salva a foto enviada como um registro na tabela `arquivos` (banco de
    dados) e retorna o id criado, em vez de gravar no disco — o disco do
    Render free tier não é permanente."""
    if not arquivo or arquivo.filename == "":
        return None
    if not _extensao_valida(arquivo.filename):
        raise ValueError("Formato de imagem não suportado. Use PNG, JPG ou WEBP.")
    ext = arquivo.filename.rsplit(".", 1)[1].lower()
    conteudo = arquivo.read()
    return salvar_arquivo(conteudo, _MIME_POR_EXTENSAO[ext])


@bp.route("/")
@roles_required("admin", "almoxarife")
def listar():
    # Administradores também veem as ferramentas removidas (inativas), com
    # opção de reativar caso a remoção tenha sido engano. Almoxarifes veem só
    # as ativas, que é o que importa no dia a dia de estoque/liberações.
    if get_current_user()["papel"] == "admin":
        ferramentas = query_db("SELECT * FROM ferramentas ORDER BY ativo DESC, nome")
    else:
        ferramentas = query_db("SELECT * FROM ferramentas WHERE ativo = 1 ORDER BY nome")

    movimentacoes = query_db(
        """SELECT m.*, f.nome AS ferramenta_nome, u.nome AS usuario_nome
           FROM ferramenta_movimentacoes m
           JOIN ferramentas f ON f.id = m.ferramenta_id
           LEFT JOIN usuarios u ON u.id = m.usuario_id
           ORDER BY m.id DESC LIMIT 50"""
    )
    return render_template("admin/ferramentas.html", ferramentas=ferramentas, movimentacoes=movimentacoes)


@bp.route("/novo", methods=["GET", "POST"])
@roles_required("admin", "almoxarife")
def novo():
    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        descricao = request.form.get("descricao", "").strip()
        fabricante = request.form.get("fabricante", "").strip()
        patrimonio = request.form.get("patrimonio", "").strip()
        estoque_inicial = request.form.get("estoque_inicial", "0").strip() or "0"
        estoque_minimo = request.form.get("estoque_minimo", "0").strip() or "0"

        erro = None
        if not nome:
            erro = "Informe o nome da ferramenta."

        foto_arquivo_id = None
        if erro is None:
            try:
                foto_arquivo_id = _salvar_foto(request.files.get("foto"))
            except ValueError as e:
                erro = str(e)

        if erro is None:
            ferramenta_id = execute_db(
                """INSERT INTO ferramentas (nome, descricao, fabricante, patrimonio,
                                             foto_arquivo_id, estoque_atual, estoque_minimo, criado_por)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (nome, descricao, fabricante, patrimonio, foto_arquivo_id,
                 int(estoque_inicial), int(estoque_minimo), get_current_user()["id"]),
            )
            if int(estoque_inicial) > 0:
                execute_db(
                    """INSERT INTO ferramenta_movimentacoes (ferramenta_id, tipo, quantidade, motivo, usuario_id)
                       VALUES (?, 'entrada', ?, 'Estoque inicial no cadastro da ferramenta', ?)""",
                    (ferramenta_id, int(estoque_inicial), get_current_user()["id"]),
                )
            flash("Ferramenta cadastrada com sucesso.", "sucesso")
            return redirect(url_for("ferramentas.listar"))

        flash(erro, "erro")

    return render_template("admin/ferramenta_form.html", ferramenta=None)


@bp.route("/<int:ferramenta_id>/editar", methods=["GET", "POST"])
@roles_required("admin", "almoxarife")
def editar(ferramenta_id):
    ferramenta = query_db("SELECT * FROM ferramentas WHERE id = ?", (ferramenta_id,), one=True)
    if ferramenta is None:
        flash("Ferramenta não encontrada.", "erro")
        return redirect(url_for("ferramentas.listar"))

    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        descricao = request.form.get("descricao", "").strip()
        fabricante = request.form.get("fabricante", "").strip()
        patrimonio = request.form.get("patrimonio", "").strip()
        estoque_minimo = request.form.get("estoque_minimo", "0").strip() or "0"

        erro = None
        if not nome:
            erro = "Informe o nome da ferramenta."

        foto_arquivo_id = ferramenta["foto_arquivo_id"]
        if erro is None:
            try:
                nova_foto = _salvar_foto(request.files.get("foto"))
                if nova_foto:
                    foto_arquivo_id = nova_foto
            except ValueError as e:
                erro = str(e)

        if erro is not None:
            flash(erro, "erro")
            return render_template("admin/ferramenta_form.html", ferramenta=ferramenta)

        execute_db(
            """UPDATE ferramentas SET nome=?, descricao=?, fabricante=?, patrimonio=?,
                                       estoque_minimo=?, foto_arquivo_id=?
               WHERE id=?""",
            (nome, descricao, fabricante, patrimonio, int(estoque_minimo), foto_arquivo_id, ferramenta_id),
        )
        flash("Ferramenta atualizada.", "sucesso")
        return redirect(url_for("ferramentas.listar"))

    return render_template("admin/ferramenta_form.html", ferramenta=ferramenta)


@bp.route("/<int:ferramenta_id>/desativar", methods=["POST"])
@roles_required("admin")
def desativar(ferramenta_id):
    execute_db("UPDATE ferramentas SET ativo = 0 WHERE id = ?", (ferramenta_id,))
    flash("Ferramenta removida da lista de cadastradas.", "sucesso")
    return redirect(url_for("ferramentas.listar"))


@bp.route("/<int:ferramenta_id>/reativar", methods=["POST"])
@roles_required("admin")
def reativar(ferramenta_id):
    execute_db("UPDATE ferramentas SET ativo = 1 WHERE id = ?", (ferramenta_id,))
    flash("Ferramenta reativada.", "sucesso")
    return redirect(url_for("ferramentas.listar"))


@bp.route("/movimentar", methods=["POST"])
@roles_required("admin", "almoxarife")
def movimentar():
    ferramenta_id = request.form.get("ferramenta_id", type=int)
    tipo = request.form.get("tipo")
    quantidade = request.form.get("quantidade", type=int)
    motivo = request.form.get("motivo", "").strip()

    if not ferramenta_id or tipo not in ("entrada", "saida", "ajuste") or not quantidade or quantidade <= 0:
        flash("Preencha item, tipo e uma quantidade válida.", "erro")
        return redirect(url_for("ferramentas.listar"))

    ferramenta = query_db("SELECT * FROM ferramentas WHERE id = ?", (ferramenta_id,), one=True)
    if ferramenta is None:
        flash("Ferramenta não encontrada.", "erro")
        return redirect(url_for("ferramentas.listar"))

    if tipo == "saida" and quantidade > ferramenta["estoque_atual"]:
        flash(f"Saldo insuficiente: há apenas {ferramenta['estoque_atual']} em estoque.", "erro")
        return redirect(url_for("ferramentas.listar"))

    delta = quantidade if tipo == "entrada" else -quantidade
    db = get_db()
    db.execute("UPDATE ferramentas SET estoque_atual = estoque_atual + ? WHERE id = ?", (delta, ferramenta_id))
    db.execute(
        """INSERT INTO ferramenta_movimentacoes (ferramenta_id, tipo, quantidade, motivo, usuario_id)
           VALUES (?, ?, ?, ?, ?)""",
        (ferramenta_id, tipo, quantidade, motivo, get_current_user()["id"]),
    )
    db.commit()
    flash("Movimentação registrada.", "sucesso")
    return redirect(url_for("ferramentas.listar"))
