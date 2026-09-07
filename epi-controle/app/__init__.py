import os
from pathlib import Path

from flask import Flask

from . import db as db_module


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)

    instance_path = Path(app.instance_path)
    instance_path.mkdir(parents=True, exist_ok=True)

    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-secret-troque-em-producao"),
        DATABASE=os.environ.get("DATABASE_PATH", str(instance_path / "epi.sqlite3")),
        UPLOAD_FOLDER=str(instance_path / "uploads"),
        MAX_CONTENT_LENGTH=15 * 1024 * 1024,  # 15 MB por upload
    )

    if test_config:
        app.config.update(test_config)

    for sub in ("fotos", "assinaturas", "epis", "logo"):
        (Path(app.config["UPLOAD_FOLDER"]) / sub).mkdir(parents=True, exist_ok=True)

    db_module.init_app(app)

    # Garante que o banco exista (cria tabelas se necessário) sem apagar dados.
    with app.app_context():
        db_module.init_db()

    from . import auth
    from . import epis
    from . import estoque
    from . import entregas
    from . import colaboradores
    from . import ca_sync
    from . import relatorios
    from . import painel
    from . import configuracoes

    app.register_blueprint(auth.bp)
    app.register_blueprint(painel.bp)
    app.register_blueprint(epis.bp)
    app.register_blueprint(estoque.bp)
    app.register_blueprint(entregas.bp)
    app.register_blueprint(colaboradores.bp)
    app.register_blueprint(ca_sync.bp)
    app.register_blueprint(relatorios.bp)
    app.register_blueprint(configuracoes.bp)
    ca_sync.register_cli(app)

    from flask import send_from_directory

    @app.route("/uploads/<path:subpath>")
    def uploads(subpath):
        return send_from_directory(app.config["UPLOAD_FOLDER"], subpath)

    @app.context_processor
    def inject_globals():
        from .auth import get_current_user
        return {"current_user": get_current_user()}

    return app
