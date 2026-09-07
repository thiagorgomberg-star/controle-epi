from flask import (
    Blueprint, flash, redirect, render_template, request, url_for
)

from .auth import roles_required, get_current_user
from .db import query_db, execute_db, salvar_arquivo

bp = Blueprint("epis", __name__, url_prefix="/epis")

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


def _tamanhos_do_epi(epi_id):
    return query_db(
        "SELECT * FROM epi_tamanhos WHERE epi_id = ? ORDER BY ativo DESC, tamanho", (epi_id,)
    )


def _parse_linhas_tamanho(form):
    """Lê as linhas de tamanho/estoque do formulário de cadastro/edição de EPI
    e devolve uma lista de dicts:
    {"tamanho_id": int|None, "tamanho": str, "estoque_inicial": int, "estoque_minimo": int}

    Uma linha existente (edição) chega com `tamanho_id` preenchido (o app só
    atualiza o rótulo do tamanho e o estoque mínimo dela — o saldo atual só
    muda pela tela de Estoque). Uma linha nova chega com `tamanho_id` vazio e
    vira uma variante nova, com o estoque inicial informado. Linhas em branco
    (sem tamanho e sem id) são ignoradas, para permitir linhas extras vazias
    no formulário dinâmico sem gerar erro."""
    ids = form.getlist("tamanho_id")
    nomes = form.getlist("tamanho")
    iniciais = form.getlist("estoque_inicial")
    minimos = form.getlist("estoque_minimo")

    linhas = []
    for i in range(len(nomes)):
        tid = ids[i].strip() if i < len(ids) else ""
        nome = nomes[i].strip()
        inicial = iniciais[i].strip() if i < len(iniciais) else ""
        minimo = minimos[i].strip() if i < len(minimos) else ""
        if not nome and not tid:
            continue
        linhas.append({
            "tamanho_id": int(tid) if tid else None,
            "tamanho": nome,
            "estoque_inicial": int(inicial) if inicial else 0,
            "estoque_minimo": int(minimo) if minimo else 0,
        })
    return linhas


def _validar_linhas_tamanho(linhas):
    """Devolve uma mensagem de erro (ou None se estiver tudo certo)."""
    if any(not l["tamanho"] for l in linhas):
        return "Informe o tamanho em todas as linhas adicionadas (ou remova as linhas em branco antes de salvar)."
    vistos = set()
    for l in linhas:
        chave = l["tamanho"].lower()
        if chave in vistos:
            return f'Tamanho "{l["tamanho"]}" repetido — use um nome diferente para cada linha.'
        vistos.add(chave)
    return None


@bp.route("/")
@roles_required("admin", "almoxarife")
def listar():
    # Administradores também veem os EPIs removidos (inativos), com opção de
    # reativar caso a remoção tenha sido engano. Almoxarifes veem só os ativos,
    # que é o que importa no dia a dia de estoque/entregas.
    if get_current_user()["papel"] == "admin":
        epis = query_db("SELECT * FROM epis ORDER BY ativo DESC, nome")
    else:
        epis = query_db("SELECT * FROM epis WHERE ativo = 1 ORDER BY nome")

    tamanhos_por_epi = {}
    for t in query_db("SELECT * FROM epi_tamanhos WHERE ativo = 1 ORDER BY tamanho"):
        tamanhos_por_epi.setdefault(t["epi_id"], []).append(t)
    for epi in epis:
        epi["tamanhos"] = tamanhos_por_epi.get(epi["id"], [])

    return render_template("admin/epis.html", epis=epis)


@bp.route("/novo", methods=["GET", "POST"])
@roles_required("admin", "almoxarife")
def novo():
    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        descricao = request.form.get("descricao", "").strip()
        fabricante = request.form.get("fabricante", "").strip()
        ca_numero = request.form.get("ca_numero", "").strip()
        ca_validade = request.form.get("ca_validade", "").strip()
        vida_util = request.form.get("vida_util_dias", "180").strip() or "180"

        linhas = _parse_linhas_tamanho(request.form)
        if not linhas:
            linhas = [{"tamanho_id": None, "tamanho": "Único", "estoque_inicial": 0, "estoque_minimo": 0}]
        elif len(linhas) == 1 and not linhas[0]["tamanho"]:
            linhas[0]["tamanho"] = "Único"

        erro = None
        if not nome:
            erro = "Informe o nome do EPI."
        elif not ca_numero:
            erro = "Informe o número do CA."
        else:
            erro = _validar_linhas_tamanho(linhas)

        foto_arquivo_id = None
        if erro is None:
            try:
                foto_arquivo_id = _salvar_foto(request.files.get("foto"))
            except ValueError as e:
                erro = str(e)

        if erro is None:
            epi_id = execute_db(
                """INSERT INTO epis (nome, descricao, fabricante, ca_numero,
                                      ca_validade, vida_util_dias, foto_arquivo_id, criado_por)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (nome, descricao, fabricante, ca_numero, ca_validade,
                 int(vida_util), foto_arquivo_id, get_current_user()["id"]),
            )
            for linha in linhas:
                tamanho_id = execute_db(
                    """INSERT INTO epi_tamanhos (epi_id, tamanho, estoque_atual, estoque_minimo)
                       VALUES (?, ?, ?, ?)""",
                    (epi_id, linha["tamanho"], linha["estoque_inicial"], linha["estoque_minimo"]),
                )
                if linha["estoque_inicial"] > 0:
                    execute_db(
                        """INSERT INTO estoque_movimentacoes (epi_id, epi_tamanho_id, tipo, quantidade, motivo)
                           VALUES (?, ?, 'entrada', ?, 'Estoque inicial no cadastro do EPI')""",
                        (epi_id, tamanho_id, linha["estoque_inicial"]),
                    )
            flash("EPI cadastrado com sucesso.", "sucesso")
            return redirect(url_for("epis.listar"))

        flash(erro, "erro")

    return render_template("admin/epi_form.html", epi=None, tamanhos=[])


@bp.route("/<int:epi_id>/editar", methods=["GET", "POST"])
@roles_required("admin", "almoxarife")
def editar(epi_id):
    epi = query_db("SELECT * FROM epis WHERE id = ?", (epi_id,), one=True)
    if epi is None:
        flash("EPI não encontrado.", "erro")
        return redirect(url_for("epis.listar"))

    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        descricao = request.form.get("descricao", "").strip()
        fabricante = request.form.get("fabricante", "").strip()
        ca_numero = request.form.get("ca_numero", "").strip()
        ca_validade = request.form.get("ca_validade", "").strip()
        vida_util = request.form.get("vida_util_dias", "180").strip() or "180"

        linhas = _parse_linhas_tamanho(request.form)

        erro = None
        if not nome:
            erro = "Informe o nome do EPI."
        elif not ca_numero:
            erro = "Informe o número do CA."
        else:
            erro = _validar_linhas_tamanho(linhas)

        foto_arquivo_id = epi["foto_arquivo_id"]
        if erro is None:
            try:
                nova_foto = _salvar_foto(request.files.get("foto"))
                if nova_foto:
                    foto_arquivo_id = nova_foto
            except ValueError as e:
                erro = str(e)

        if erro is not None:
            flash(erro, "erro")
            return render_template("admin/epi_form.html", epi=epi, tamanhos=_tamanhos_do_epi(epi_id))

        execute_db(
            """UPDATE epis SET nome=?, descricao=?, fabricante=?, ca_numero=?,
                                ca_validade=?, vida_util_dias=?, foto_arquivo_id=?
               WHERE id=?""",
            (nome, descricao, fabricante, ca_numero, ca_validade,
             int(vida_util), foto_arquivo_id, epi_id),
        )

        for linha in linhas:
            if linha["tamanho_id"]:
                execute_db(
                    "UPDATE epi_tamanhos SET tamanho=?, estoque_minimo=? WHERE id=? AND epi_id=?",
                    (linha["tamanho"], linha["estoque_minimo"], linha["tamanho_id"], epi_id),
                )
            else:
                tamanho_id = execute_db(
                    """INSERT INTO epi_tamanhos (epi_id, tamanho, estoque_atual, estoque_minimo)
                       VALUES (?, ?, ?, ?)""",
                    (epi_id, linha["tamanho"], linha["estoque_inicial"], linha["estoque_minimo"]),
                )
                if linha["estoque_inicial"] > 0:
                    execute_db(
                        """INSERT INTO estoque_movimentacoes (epi_id, epi_tamanho_id, tipo, quantidade, motivo, usuario_id)
                           VALUES (?, ?, 'entrada', ?, 'Novo tamanho adicionado no cadastro do EPI', ?)""",
                        (epi_id, tamanho_id, linha["estoque_inicial"], get_current_user()["id"]),
                    )

        flash("EPI atualizado.", "sucesso")
        return redirect(url_for("epis.listar"))

    return render_template("admin/epi_form.html", epi=epi, tamanhos=_tamanhos_do_epi(epi_id))


@bp.route("/<int:epi_id>/desativar", methods=["POST"])
@roles_required("admin")
def desativar(epi_id):
    execute_db("UPDATE epis SET ativo = 0 WHERE id = ?", (epi_id,))
    flash("EPI removido da lista de cadastrados.", "sucesso")
    return redirect(url_for("epis.listar"))


@bp.route("/<int:epi_id>/reativar", methods=["POST"])
@roles_required("admin")
def reativar(epi_id):
    execute_db("UPDATE epis SET ativo = 1 WHERE id = ?", (epi_id,))
    flash("EPI reativado.", "sucesso")
    return redirect(url_for("epis.listar"))


@bp.route("/<int:epi_id>/tamanhos/<int:tamanho_id>/remover", methods=["POST"])
@roles_required("admin")
def tamanho_remover(epi_id, tamanho_id):
    execute_db("UPDATE epi_tamanhos SET ativo = 0 WHERE id = ? AND epi_id = ?", (tamanho_id, epi_id))
    flash("Tamanho removido do EPI. O histórico de estoque e entregas é mantido.", "sucesso")
    return redirect(url_for("epis.editar", epi_id=epi_id))


@bp.route("/<int:epi_id>/tamanhos/<int:tamanho_id>/reativar", methods=["POST"])
@roles_required("admin")
def tamanho_reativar(epi_id, tamanho_id):
    execute_db("UPDATE epi_tamanhos SET ativo = 1 WHERE id = ? AND epi_id = ?", (tamanho_id, epi_id))
    flash("Tamanho reativado.", "sucesso")
    return redirect(url_for("epis.editar", epi_id=epi_id))
