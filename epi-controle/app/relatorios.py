import io
from pathlib import Path

from flask import Blueprint, current_app, flash, redirect, render_template, request, send_file, url_for
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, HRFlowable
)

from .auth import roles_required, login_required, get_current_user
from .db import query_db

bp = Blueprint("relatorios", __name__, url_prefix="/relatorios")

DECLARACAO_NR6 = (
    "Declaro, para os devidos fins, que recebi o(s) Equipamento(s) de Proteção Individual (EPI) "
    "acima descrito(s) em perfeito estado de conservação e funcionamento. Comprometo-me a usá-lo(s) "
    "corretamente durante o exercício das minhas atividades, conforme treinamento e orientações "
    "recebidas, a zelar por sua guarda e conservação, e a comunicar ao SESMT qualquer alteração que "
    "o(s) torne impróprio(s) para uso, nos termos da Norma Regulamentadora NR-6."
)


def _config():
    return query_db("SELECT * FROM configuracoes WHERE id = 1", one=True)


def _caminho_upload(rel_path):
    if not rel_path:
        return None
    caminho = Path(current_app.config["UPLOAD_FOLDER"]) / rel_path
    return str(caminho) if caminho.exists() else None


def _gerar_pdf_entrega(entrega):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        topMargin=18 * mm, bottomMargin=18 * mm, leftMargin=18 * mm, rightMargin=18 * mm,
    )
    estilos = getSampleStyleSheet()
    titulo = ParagraphStyle("Titulo", parent=estilos["Title"], fontSize=14, spaceAfter=2)
    subtitulo = ParagraphStyle("Sub", parent=estilos["Normal"], textColor=colors.grey, fontSize=9)
    rotulo = ParagraphStyle("Rotulo", parent=estilos["Normal"], fontSize=9, textColor=colors.grey)
    valor = ParagraphStyle("Valor", parent=estilos["Normal"], fontSize=11)
    secao = ParagraphStyle("Secao", parent=estilos["Heading3"], fontSize=11, spaceBefore=10, spaceAfter=4)

    config = _config()
    elementos = []

    cabecalho = []
    logo_path = _caminho_upload(config["logo_path"]) if config else None
    if logo_path:
        try:
            cabecalho.append(Image(logo_path, width=30 * mm, height=30 * mm, kind="proportional"))
        except Exception:  # noqa: BLE001
            pass

    empresa_nome = (config["empresa_nome"] if config and config["empresa_nome"] else "Controle de EPI")
    texto_cabecalho = [Paragraph(empresa_nome, titulo), Paragraph("Termo de Entrega e Aceite de EPI — NR-6", subtitulo)]
    if logo_path:
        tabela_topo = Table([[cabecalho[0], texto_cabecalho]], colWidths=[35 * mm, None])
        tabela_topo.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
        elementos.append(tabela_topo)
    else:
        elementos.extend(texto_cabecalho)

    elementos.append(Spacer(1, 4))
    elementos.append(HRFlowable(width="100%", color=colors.HexColor("#cccccc")))
    elementos.append(Spacer(1, 8))

    def linha(rotulo_txt, valor_txt):
        return [Paragraph(rotulo_txt, rotulo), Paragraph(str(valor_txt or "—"), valor)]

    elementos.append(Paragraph("Colaborador", secao))
    dados_colab = Table(
        [
            linha("Nome", entrega["colaborador_nome"]),
            linha("Matrícula", entrega["colaborador_matricula"]),
            linha("Cargo / Setor", f"{entrega['colaborador_cargo'] or '—'} / {entrega['colaborador_setor'] or '—'}"),
        ],
        colWidths=[35 * mm, None],
    )
    dados_colab.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    elementos.append(dados_colab)

    elementos.append(Paragraph("Equipamento entregue", secao))
    dados_epi = Table(
        [
            linha("EPI", entrega["epi_nome"]),
            linha("Descrição", entrega["epi_descricao"]),
            linha("CA (Certificado de Aprovação)", entrega["ca_numero"]),
            linha("Validade do CA", entrega["ca_validade"]),
            linha("Tamanho / Quantidade", f"{entrega['tamanho'] or '—'} / {entrega['quantidade']}"),
            linha("Motivo da entrega", entrega["motivo"]),
            linha("Data da entrega", entrega["data_entrega"]),
            linha("Previsão de troca", entrega["data_troca_prevista"]),
        ],
        colWidths=[45 * mm, None],
    )
    dados_epi.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    elementos.append(dados_epi)

    elementos.append(Paragraph("Declaração do colaborador", secao))
    elementos.append(Paragraph(DECLARACAO_NR6, valor))
    elementos.append(Spacer(1, 10))

    assinatura_path = _caminho_upload(entrega["assinatura_path"]) if entrega["assinatura_path"] else None
    foto_path = _caminho_upload(entrega["foto_path"]) if entrega["foto_path"] else None

    celulas_evidencia = []
    legendas = []
    if assinatura_path:
        celulas_evidencia.append(Image(assinatura_path, width=70 * mm, height=35 * mm, kind="proportional"))
        legendas.append(Paragraph("Assinatura do colaborador", rotulo))
    if foto_path:
        celulas_evidencia.append(Image(foto_path, width=40 * mm, height=40 * mm, kind="proportional"))
        legendas.append(Paragraph("Foto no momento do aceite", rotulo))

    if celulas_evidencia:
        tabela_evidencias = Table([celulas_evidencia, legendas], colWidths=[80 * mm] * len(celulas_evidencia))
        tabela_evidencias.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
        elementos.append(tabela_evidencias)
    else:
        elementos.append(Paragraph("Aceite ainda não registrado.", valor))

    elementos.append(Spacer(1, 8))
    rodape_txt = f"Aceito em {entrega['aceito_em']}" if entrega["aceito_em"] else "Aguardando aceite do colaborador"
    elementos.append(Paragraph(rodape_txt, subtitulo))
    elementos.append(Paragraph(f"Documento gerado eletronicamente pelo sistema de Controle de EPI — entrega #{entrega['id']}.", subtitulo))

    doc.build(elementos)
    buffer.seek(0)
    return buffer


def _buscar_entrega_completa(entrega_id):
    return query_db(
        """SELECT en.*, e.nome AS epi_nome, e.descricao AS epi_descricao,
                  u.nome AS colaborador_nome, u.matricula AS colaborador_matricula,
                  u.cargo AS colaborador_cargo, u.setor AS colaborador_setor,
                  a.assinatura_path, a.foto_path, a.aceito_em
           FROM entregas en
           JOIN epis e ON e.id = en.epi_id
           JOIN usuarios u ON u.id = en.colaborador_id
           LEFT JOIN aceites a ON a.entrega_id = en.id
           WHERE en.id = ?""",
        (entrega_id,), one=True,
    )


@bp.route("/entrega/<int:entrega_id>.pdf")
@login_required
def ficha_entrega(entrega_id):
    entrega = _buscar_entrega_completa(entrega_id)
    user = get_current_user()
    if entrega is None:
        flash("Entrega não encontrada.", "erro")
        return redirect(url_for("painel.index_redirect"))
    if user["papel"] not in ("admin", "almoxarife") and entrega["colaborador_id"] != user["id"]:
        flash("Você não tem acesso a esta ficha.", "erro")
        return redirect(url_for("painel.index_redirect"))

    pdf = _gerar_pdf_entrega(entrega)
    return send_file(
        pdf, mimetype="application/pdf", as_attachment=False,
        download_name=f"entrega-epi-{entrega_id}.pdf",
    )
