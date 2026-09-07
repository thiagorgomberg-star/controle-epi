"""Camada de acesso ao banco de dados (PostgreSQL puro, sem ORM).

O aplicativo inteiro escreve as consultas usando `?` como marcador de
parâmetro (estilo SQLite, mais simples de ler). Este módulo traduz isso
automaticamente para o `%s` que o driver do PostgreSQL (psycopg) espera,
então nenhum outro arquivo precisa se preocupar com essa diferença.
"""
import re
from pathlib import Path

import click
import psycopg
from psycopg.rows import dict_row
from flask import current_app, g

_PLACEHOLDER_RE = re.compile(r"\?")


def _qmark_para_pyformat(query):
    # Nossas consultas nunca têm "?" dentro de literais de texto, então uma
    # substituição direta é suficiente.
    return _PLACEHOLDER_RE.sub("%s", query)


class _CursorWrapper:
    """Envolve um cursor do psycopg para aceitar `?` como marcador."""

    def __init__(self, cur):
        self._cur = cur

    def execute(self, query, args=()):
        self._cur.execute(_qmark_para_pyformat(query), args)
        return self

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    @property
    def rowcount(self):
        return self._cur.rowcount


class _ConnWrapper:
    """Envolve a conexão do psycopg para se comportar como sqlite3.Connection
    (métodos .execute()/.executescript() diretos na conexão, marcador `?`)."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, query, args=()):
        cur = self._conn.cursor()
        cur.execute(_qmark_para_pyformat(query), args)
        return _CursorWrapper(cur)

    def executescript(self, sql):
        # Executa cada instrução (separada por ";") em uma chamada própria,
        # em vez de depender do driver aceitar múltiplos comandos em uma só
        # chamada — mais previsível entre versões do psycopg.
        with self._conn.cursor() as cur:
            for instrucao in sql.split(";"):
                instrucao = instrucao.strip()
                if instrucao:
                    cur.execute(instrucao)

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()


def get_db():
    """Retorna a conexão PostgreSQL da requisição atual (cria se não existir)."""
    if "db" not in g:
        conn = psycopg.connect(
            current_app.config["DATABASE_URL"],
            row_factory=dict_row,
            autocommit=False,
        )
        g.db = _ConnWrapper(conn)
    return g.db


def close_db(e=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    """Cria as tabelas a partir do schema.sql (idempotente)."""
    db = get_db()
    schema_path = Path(__file__).parent / "schema.sql"
    with open(schema_path, "r", encoding="utf-8") as f:
        db.executescript(f.read())
    db.commit()


@click.command("init-db")
def init_db_command():
    """Comando de CLI: flask --app app init-db"""
    init_db()
    click.echo("Banco de dados inicializado.")


def query_db(query, args=(), one=False):
    db = get_db()
    cur = db.execute(query, args)
    rv = cur.fetchall()
    return (rv[0] if rv else None) if one else rv


def execute_db(query, args=()):
    db = get_db()
    q = query.strip()
    eh_insert = q[:6].upper() == "INSERT" and "RETURNING" not in q.upper()
    if eh_insert:
        query = q + " RETURNING id"
    cur = db.execute(query, args)
    db.commit()
    if eh_insert:
        row = cur.fetchone()
        return row["id"] if row else None
    return None


def salvar_arquivo(conteudo, content_type):
    """Grava bytes (foto, assinatura, logo) na tabela `arquivos` e retorna o id
    criado. Usado no lugar de salvar arquivos no disco, já que o disco do
    Render não é permanente no plano gratuito."""
    return execute_db(
        "INSERT INTO arquivos (conteudo, content_type) VALUES (?, ?)",
        (conteudo, content_type),
    )


def init_app(app):
    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)
