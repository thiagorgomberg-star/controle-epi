import functools
import re

from flask import (
    Blueprint, current_app, flash, g, redirect, render_template, request, session, url_for
)
from werkzeug.security import check_password_hash, generate_password_hash

from .db import get_db, query_db, execute_db

bp = Blueprint("auth", __name__, url_prefix="/auth")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def get_current_user():
    if "user_id" not in session:
        return None
    if "_user_cache" not in g:
        g._user_cache = query_db(
            "SELECT * FROM usuarios WHERE id = ? AND ativo = 1",
            (session["user_id"],),
            one=True,
        )
    return g._user_cache


def login_required(view):
    @functools.wraps(view)
    def wrapped(**kwargs):
        if get_current_user() is None:
            session.clear()
            flash("Faça login para continuar.", "erro")
            return redirect(url_for("auth.login"))
        return view(**kwargs)
    return wrapped


def roles_required(*papeis):
    def decorator(view):
        @functools.wraps(view)
        @login_required
        def wrapped(**kwargs):
            user = get_current_user()
            if user["papel"] not in papeis:
                flash("Você não tem permissão para acessar essa página.", "erro")
                return redirect(url_for("painel.index_redirect"))
            return view(**kwargs)
        return wrapped
    return decorator


def is_admin_almoxarife(user):
    return user is not None and user["papel"] in ("admin", "almoxarife")


@bp.route("/login", methods=("GET", "POST"))
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        senha = request.form.get("senha", "")

        erro = None
        user = query_db("SELECT * FROM usuarios WHERE email = ?", (email,), one=True)

        if user is None or not check_password_hash(user["senha_hash"], senha):
            erro = "E-mail ou senha inválidos."
        elif not user["ativo"]:
            erro = "Este acesso está desativado. Fale com o SESMT."

        if erro is None:
            session.clear()
            session["user_id"] = user["id"]
            session.permanent = True

            # Importação local para evitar import circular (ca_sync importa
            # roles_required deste módulo). Dispara, no máximo 1x por semana
            # civil, a sincronização automática da base de CA em segundo
            # plano — não atrasa este login.
            from .ca_sync import verificar_e_disparar_sincronizacao_semanal
            verificar_e_disparar_sincronizacao_semanal(current_app._get_current_object())

            return redirect(url_for("painel.index_redirect"))

        flash(erro, "erro")

    return render_template("auth/login.html")


@bp.route("/cadastro", methods=("GET", "POST"))
def cadastro():
    total_usuarios = query_db("SELECT COUNT(*) AS c FROM usuarios", one=True)["c"]
    admin_ativo_existe = query_db(
        "SELECT COUNT(*) AS c FROM usuarios WHERE papel = 'admin' AND ativo = 1", one=True
    )["c"] > 0

    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        email = request.form.get("email", "").strip().lower()
        matricula = request.form.get("matricula", "").strip() or None
        cargo = request.form.get("cargo", "").strip()
        setor = request.form.get("setor", "").strip()
        senha = request.form.get("senha", "")
        senha_confirma = request.form.get("senha_confirma", "")

        existente = query_db("SELECT * FROM usuarios WHERE email = ?", (email,), one=True)
        # Se não existe nenhum administrador ativo no sistema (por exemplo, o
        # único admin ficou com o acesso desativado por engano), permitimos que
        # o dono desse e-mail recupere o acesso recadastrando a senha aqui,
        # em vez de bloquear com "e-mail já cadastrado".
        recuperacao = existente is not None and not admin_ativo_existe

        erro = None
        if not nome:
            erro = "Informe o nome completo."
        elif not email or not EMAIL_RE.match(email):
            erro = "Informe um e-mail válido."
        elif len(senha) < 6:
            erro = "A senha precisa ter pelo menos 6 caracteres."
        elif senha != senha_confirma:
            erro = "As senhas não coincidem."
        elif existente and not recuperacao:
            erro = "Já existe um acesso com este e-mail."

        if erro is None:
            if recuperacao:
                execute_db(
                    """UPDATE usuarios SET senha_hash = ?, nome = ?, cargo = ?, setor = ?,
                                            papel = 'admin', ativo = 1
                       WHERE id = ?""",
                    (generate_password_hash(senha), nome, cargo, setor, existente["id"]),
                )
                user_id = existente["id"]
                flash(
                    "Acesso recuperado com sucesso. Como não havia administrador ativo no "
                    "sistema, esta conta voltou a ser a administradora do SESMT.",
                    "sucesso",
                )
            else:
                papel = "admin" if total_usuarios == 0 else "colaborador"
                user_id = execute_db(
                    """INSERT INTO usuarios (nome, email, senha_hash, matricula, cargo, setor, papel)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (nome, email, generate_password_hash(senha), matricula, cargo, setor, papel),
                )
                if papel == "admin":
                    flash("Conta criada com sucesso! Como primeiro acesso, você é o administrador do SESMT.", "sucesso")
            session.clear()
            session["user_id"] = user_id
            session.permanent = True
            return redirect(url_for("painel.index_redirect"))

        flash(erro, "erro")

    return render_template("auth/cadastro.html", primeiro_acesso=(total_usuarios == 0))


@bp.route("/sair")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
