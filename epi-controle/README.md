# Controle de EPI

Sistema web para controle de entrega de Equipamentos de Proteção Individual (EPI):
cadastro de EPIs com CA (com busca automática na base oficial do Ministério do
Trabalho), controle de estoque, direcionamento de entregas, aceite do
colaborador com assinatura digital e foto, e emissão do termo de entrega em PDF.

## Tecnologia

Python (Flask) + SQLite, sem frameworks de front-end — HTML/CSS/JS escritos à
mão. Fácil de rodar e de hospedar em qualquer provedor com suporte a Python.

## Como rodar localmente

Pré-requisitos: Python 3.10 ou mais recente.

```bash
cd epi-controle
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python run.py
```

Acesse http://localhost:5000 no navegador. O **primeiro cadastro** feito em
"Criar acesso" vira automaticamente o administrador do SESMT.

O banco de dados (SQLite) é criado automaticamente em `instance/epi.sqlite3`
na primeira execução — não precisa configurar nada.

## Estrutura do projeto

```
app/
  __init__.py        Fábrica da aplicação Flask
  db.py               Acesso ao banco (SQLite puro)
  schema.sql          Estrutura das tabelas
  auth.py             Login, cadastro, papéis de acesso
  epis.py             Cadastro de EPIs
  estoque.py          Movimentações de estoque
  entregas.py         Direcionar entrega e fluxo de aceite (assinatura + foto)
  colaboradores.py    Cadastro de colaboradores e importação por planilha
  ca_sync.py          Sincronização com a base oficial de CA
  relatorios.py        Geração do PDF de entrega
  configuracoes.py    Dados da empresa, logo, zerar dados
  templates/          Páginas (Jinja2)
  static/             CSS e JavaScript (assinatura, câmera, busca de CA)
run.py                 Ponto de entrada para desenvolvimento
requirements.txt
```

## Busca automática de CA

O cadastro de EPI busca o número do CA numa tabela local (`ca_cache`), que é
alimentada pela base oficial do Ministério do Trabalho (sistema CAEPI). Isso
deixa a busca instantânea e não depende de nenhum site do governo estar no ar
no momento em que você cadastra um EPI.

Para popular/atualizar essa base, rode:

```bash
flask --app app sync-ca
```

Isso baixa a base pública do governo e grava/atualiza os registros locais.
**Rode esse comando manualmente uma vez após o primeiro deploy**, e depois
agende-o para rodar automaticamente (recomendo 1x por dia) — veja "Publicar
em produção" abaixo.

Se um dia a busca parar de trazer os dados corretos, é provável que o
Ministério do Trabalho tenha mudado o layout do arquivo público. Nesse caso,
abra `app/ca_sync.py` e ajuste o dicionário `COLUNAS_CANDIDATAS` no topo do
arquivo com os novos nomes de coluna.

## Publicar em produção (passo a passo, sem precisar programar)

Recomendo o **Render** (render.com) — tem plano gratuito e sobe projetos
Python em poucos cliques.

1. Crie uma conta gratuita em https://render.com (pode entrar com GitHub).
2. Suba esta pasta para um repositório no GitHub (o Render publica a partir
   de um repositório Git — se você nunca usou o GitHub, é só criar uma conta
   gratuita em github.com, criar um repositório novo e arrastar os arquivos
   pela interface web, sem precisar usar linha de comando).
3. No Render, clique em **New > Web Service**, conecte o repositório.
4. Configure:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn run:app`
   - **Environment Variable:** `SECRET_KEY` = qualquer texto longo e aleatório
     (isso protege as sessões de login — não deixe o valor padrão do código).
5. Clique em **Create Web Service**. Em poucos minutos o sistema estará no ar
   com uma URL própria (ex.: `https://seu-sistema.onrender.com`).
6. **Importante — disco persistente:** no plano gratuito do Render, os
   arquivos gravados (banco SQLite, fotos, assinaturas, PDFs) podem ser
   apagados a cada novo deploy. Para guardar tudo permanentemente, adicione
   um **Disk** (Render > seu serviço > Disks > Add Disk) montado em `/data`,
   e configure a variável de ambiente `DATABASE_PATH=/data/epi.sqlite3` e
   ajuste `UPLOAD_FOLDER` da mesma forma (ou me chame que eu ajusto o código
   para usar essas variáveis automaticamente).
7. **Agendar a sincronização do CA:** no Render, crie um **Cron Job** separado
   apontando para o mesmo repositório, com comando
   `flask --app app sync-ca`, agendado para rodar 1x por dia.

Alternativas igualmente simples: Railway (railway.app) ou PythonAnywhere
(pythonanywhere.com), ambos com planos gratuitos e passos parecidos.

## Segurança antes de usar com dados reais

- Troque `SECRET_KEY` por um valor aleatório e único (nunca use o valor de
  desenvolvimento do código).
- As senhas dos colaboradores nunca são armazenadas em texto puro (usamos
  hash do Werkzeug).
- Fotos e assinaturas ficam salvas em `instance/uploads/` — em produção,
  garanta que esse diretório está no disco persistente (ver item 6 acima).
- Depois dos primeiros testes, use o botão **Zerar dados do sistema** (em
  Configuração) para limpar os dados de teste antes de cadastrar dados reais.

## O que fazer se quiser evoluir o sistema depois

Funcionalidades que existiam na versão anterior (Lovable) e não entraram
nesta primeira versão, mas dá para adicionar depois: fluxo de devolução de
EPI, módulo de treinamentos/NR com alerta de vencimento, e aviso por
WhatsApp. É só pedir.
