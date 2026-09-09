from datetime import datetime, timedelta

from flask import Blueprint, flash, redirect, render_template, request, url_for

from .auth import get_current_user, login_required, roles_required
from .db import execute_db, query_db, salvar_arquivo

bp = Blueprint("painel", __name__)

EXTENSOES_IMAGEM = {"png", "jpg", "jpeg", "webp"}
_MIME_POR_EXTENSAO = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}
TIPOS_SANGUINEOS = ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"]


def _extensao_valida(nome_arquivo):
    return "." in nome_arquivo and nome_arquivo.rsplit(".", 1)[1].lower() in EXTENSOES_IMAGEM


def _salvar_foto_perfil(arquivo):
    """Salva a foto de perfil enviada como um registro na tabela `arquivos`
    (banco de dados) e retorna o id criado — mesmo padrão usado para fotos de
    EPIs, ferramentas, assinaturas etc., já que o disco do Render free tier
    não é permanente."""
    if not arquivo or arquivo.filename == "":
        return None
    if not _extensao_valida(arquivo.filename):
        raise ValueError("Formato de imagem não suportado. Use PNG, JPG ou WEBP.")
    ext = arquivo.filename.rsplit(".", 1)[1].lower()
    conteudo = arquivo.read()
    return salvar_arquivo(conteudo, _MIME_POR_EXTENSAO[ext])


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


@bp.route("/painel/ferramentas")
@login_required
def minhas_ferramentas():
    user = get_current_user()
    hoje = datetime.now().strftime("%Y-%m-%d")

    pendentes = query_db(
        """SELECT l.*, f.nome AS ferramenta_nome FROM liberacoes_ferramenta l
           JOIN ferramentas f ON f.id = l.ferramenta_id
           WHERE l.colaborador_id = ? AND l.status = 'pronto_retirada'
           ORDER BY l.id DESC""",
        (user["id"],),
    )
    em_andamento = query_db(
        """SELECT l.*, f.nome AS ferramenta_nome FROM liberacoes_ferramenta l
           JOIN ferramentas f ON f.id = l.ferramenta_id
           WHERE l.colaborador_id = ? AND l.status IN ('em_separacao', 'em_compra')
           ORDER BY l.id DESC""",
        (user["id"],),
    )
    comigo = query_db(
        """SELECT l.*, f.nome AS ferramenta_nome, f.fabricante, f.patrimonio
           FROM liberacoes_ferramenta l JOIN ferramentas f ON f.id = l.ferramenta_id
           WHERE l.colaborador_id = ? AND l.status = 'aceito'
           ORDER BY l.id DESC""",
        (user["id"],),
    )
    historico = query_db(
        """SELECT l.*, f.nome AS ferramenta_nome FROM liberacoes_ferramenta l
           JOIN ferramentas f ON f.id = l.ferramenta_id
           WHERE l.colaborador_id = ?
           ORDER BY l.id DESC""",
        (user["id"],),
    )

    devolucoes_vencidas = sum(
        1 for c in comigo
        if c["tipo"] == "emprestimo" and c["data_prevista_devolucao"] and c["data_prevista_devolucao"] < hoje
    )

    return render_template(
        "colaborador/ferramentas.html",
        pendentes=pendentes, em_andamento=em_andamento, comigo=comigo, historico=historico,
        devolucoes_vencidas=devolucoes_vencidas, hoje=hoje,
    )


@bp.route("/painel/perfil", methods=["GET", "POST"])
@login_required
def perfil():
    user = get_current_user()

    if request.method == "POST":
        cpf = request.form.get("cpf", "").strip()
        rg = request.form.get("rg", "").strip()
        tipo_sanguineo = request.form.get("tipo_sanguineo", "").strip().upper()
        data_nascimento = request.form.get("data_nascimento", "").strip()
        data_admissao = request.form.get("data_admissao", "").strip()
        telefone = request.form.get("telefone", "").strip()

        erro = None
        if tipo_sanguineo and tipo_sanguineo not in TIPOS_SANGUINEOS:
            erro = "Tipo sanguíneo inválido."

        foto_perfil_arquivo_id = user["foto_perfil_arquivo_id"]
        if erro is None:
            try:
                nova_foto = _salvar_foto_perfil(request.files.get("foto"))
                if nova_foto:
                    foto_perfil_arquivo_id = nova_foto
            except ValueError as e:
                erro = str(e)

        if erro is not None:
            flash(erro, "erro")
            return redirect(url_for("painel.perfil"))

        execute_db(
            """UPDATE usuarios SET cpf=?, rg=?, tipo_sanguineo=?, data_nascimento=?,
                                    data_admissao=?, telefone=?, foto_perfil_arquivo_id=?
               WHERE id=?""",
            (cpf, rg, tipo_sanguineo, data_nascimento, data_admissao, telefone,
             foto_perfil_arquivo_id, user["id"]),
        )
        flash("Perfil atualizado com sucesso.", "sucesso")
        return redirect(url_for("painel.perfil"))

    usuario = query_db("SELECT * FROM usuarios WHERE id = ?", (user["id"],), one=True)
    return render_template("colaborador/perfil.html", usuario=usuario, tipos_sanguineos=TIPOS_SANGUINEOS)


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
    ferramental_pendentes = query_db(
        "SELECT COUNT(*) AS c FROM liberacoes_ferramenta WHERE status = 'pronto_retirada'", one=True
    )["c"]
    ferramental_devolucao_vencida = query_db(
        """SELECT COUNT(*) AS c FROM liberacoes_ferramenta
           WHERE status = 'aceito' AND tipo = 'emprestimo'
             AND data_prevista_devolucao IS NOT NULL AND data_prevista_devolucao < ?""",
        (hoje,), one=True,
    )["c"]
    ferramental_estoque_baixo = query_db(
        "SELECT COUNT(*) AS c FROM ferramentas WHERE ativo=1 AND estoque_atual <= estoque_minimo",
        one=True,
    )["c"]

    return render_template(
        "admin/dashboard.html",
        aceites_pendentes=aceites_pendentes, epis_vencidos=epis_vencidos,
        ca_vencidos=ca_vencidos, estoque_baixo=estoque_baixo,
        solicitacoes_pendentes=solicitacoes_pendentes,
        ferramental_pendentes=ferramental_pendentes,
        ferramental_devolucao_vencida=ferramental_devolucao_vencida,
        ferramental_estoque_baixo=ferramental_estoque_baixo,
    )
