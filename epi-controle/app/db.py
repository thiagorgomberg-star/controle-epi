"""Camada de acesso ao banco de dados (SQLite puro, sem ORM)."""
import sqlite3
from pathlib import Path

import click
from flask import current_app, g


def get_db():
    """Retorna a conexão SQLite da requisição atual (cria se não existir)."""
    if "db" not in g:
        g.db = sqlite3.connect(
            current_app.config["DATABASE"],
            detect_types=sqlite3.PARSE_DECLTYPES,
        )
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
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
    cur = db.execute(query, args)
    db.commit()
    return cur.lastrowid


def init_app(app):
    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)
