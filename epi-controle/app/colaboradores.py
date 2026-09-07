import secrets

from flask import Blueprint, flash, redirect, render_template, request, url_for
from werkzeug.security import generate_password_hash

from .auth import roles_required, get_current_user
from .db import query_db, execute_db, get_db

bp = Blueprint("colaboradores", __name__, url_prefix="/colaboradores")

PAPEIS_VALIDOS = {"colaborador", "almoxarife", "admin"}


@bp.route("/")
@roles_required("admin")
def listar():
    colaboradores = query_db("SELECT * FROM usuarios ORDER BY nome")
    return render_template("admin/colaboradores.html", colaboradores=colaboradores)


@bp.route("/novo", methods=["POST"])
@roles_required("admin")
def novo():
    nome = request.form.get("nome", "").strip()
    email = request.form.get("email", "").strip().lower()
    matricula = request.form.get("matricula", "").strip() or None
    cargo = request.form.get("cargo", "").strip()
    setor = request.form.get("setor", "").strip()
    papel = request.form.get("papel", "colaborador")

    if not nome or not email or papel not in PAPEIS_VALIDOS:
        flash("Preencha nome, e-mail e um nível de acesso válido.", "erro")
        return redirect(url_for("colaboradores.listar"))

    if query_db("SELECT id FROM usuarios WHERE email = ?", (email,), one=True):
        flash("Já existe um colaborador com este e-mail.", "erro")
        return redirect(url_for("colaboradores.listar"))

    senha_provisoria = secrets.token_urlsafe(6)
    execute_db(
        """INSERT INTO usuarios (nome, email, senha_hash, matricula, cargo, setor, papel)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (nome, email, generate_password_hash(senha_provisoria), matricula, cargo, setor, papel),
    )
    flash(
        f"Colaborador cadastrado. Senha provisória: {senha_provisoria} "
        "(repasse com segurança — ele pode trocar depois de entrar).",
        "sucesso",
    )
    return redirect(url_for("colaboradores.listar"))


@bp.route("/<int:usuario_id>/papel", methods=["POST"])
@roles_required("admin")
def alterar_papel(usuario_id):
    papel = request.form.get("papel")
    if papel not in PAPEIS_VALIDOS:
        flash("Nível de acesso inválido.", "erro")
    else:
        execute_db("UPDATE usuarios SET papel = ? WHERE id = ?", (papel, usuario_id))
        flash("Nível de acesso atualizado.", "sucesso")
    return redirect(url_for("colaboradores.listar"))


@bp.route("/<int:usuario_id>/status", methods=["POST"])
@roles_required("admin")
def alternar_status(usuario_id):
    usuario = query_db("SELECT * FROM usuarios WHERE id = ?", (usuario_id,), one=True)
    if usuario is None:
        flash("Colaborador não encontrado.", "erro")
        return redirect(url_for("colaboradores.listar"))

    atual = get_current_user()
    if atual and usuario["id"] == atual["id"]:
        # Evita que um administrador se desative por engano e fique trancado
        # para fora do próprio sistema.
        flash("Você não pode desativar o seu próprio acesso. Peça para outro administrador fazer isso.", "erro")
        return redirect(url_for("colaboradores.listar"))

    if usuario["ativo"] and usuario["papel"] == "admin":
        outros_admins_ativos = query_db(
            "SELECT COUNT(*) AS c FROM usuarios WHERE papel = 'admin' AND ativo = 1 AND id != ?",
            (usuario_id,), one=True,
        )["c"]
        if outros_admins_ativos == 0:
            flash("Não é possível desativar o único administrador ativo do sistema.", "erro")
            return redirect(url_for("colaboradores.listar"))

    execute_db("UPDATE usuarios SET ativo = ? WHERE id = ?", (0 if usuario["ativo"] else 1, usuario_id))
    flash("Situação do colaborador atualizada.", "sucesso")
    return redirect(url_for("colaboradores.listar"))


@bp.route("/importar", methods=["POST"])
@roles_required("admin")
def importar():
    arquivo = request.files.get("planilha")
    if not arquivo or arquivo.filename == "":
        flash("Selecione um arquivo .xlsx ou .csv para importar.", "erro")
        return redirect(url_for("colaboradores.listar"))

    import pandas as pd

    try:
        if arquivo.filename.lower().endswith(".csv"):
            df = pd.read_csv(arquivo, dtype=str).fillna("")
        else:
            df = pd.read_excel(arquivo, dtype=str).fillna("")
    except Exception as e:  # noqa: BLE001
        flash(f"Não consegui ler a planilha: {e}", "erro")
        return redirect(url_for("colaboradores.listar"))

    df.columns = [str(c).strip().lower() for c in df.columns]
    obrigatorias = {"nome", "email"}
    if not obrigatorias.issubset(set(df.columns)):
        flash("A planilha precisa ter, no mínimo, as colunas: nome, email.", "erro")
        return redirect(url_for("colaboradores.listar"))

    db = get_db()
    importados = 0
    ignorados = 0
    for _, row in df.iterrows():
        nome = str(row.get("nome", "")).strip()
        email = str(row.get("email", "")).strip().lower()
        if not nome or not email:
            ignorados += 1
            continue
        existente = db.execute("SELECT id FROM usuarios WHERE email = ?", (email,)).fetchone()
        if existente:
            ignorados += 1
            continue
        papel = str(row.get("nivel", "colaborador")).strip().lower()
        if papel not in PAPEIS_VALIDOS:
            papel = "colaborador"
        senha_provisoria = secrets.token_urlsafe(6)
        db.execute(
            """INSERT INTO usuarios (nome, email, senha_hash, matricula, cpf, cargo, setor,
                                      telefone, papel)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                nome, email, generate_password_hash(senha_provisoria),
                str(row.get("matricula", "")).strip() or None,
                str(row.get("cpf", "")).strip(),
                str(row.get("cargo", "")).strip(),
                str(row.get("setor", "")).strip(),
                str(row.get("telefone", "")).strip(),
                papel,
            ),
        )
        importados += 1
    db.commit()
    flash(f"Importação concluída: {importados} colaborador(es) adicionados, {ignorados} ignorado(s).", "sucesso")
    return redirect(url_for("colaboradores.listar"))
