"""Flask-WebUI: Statusseite mit wählbaren Statistiken.

Die Seite lädt ihre Daten über same-origin-Endpunkte (/ui-api/…),
damit sie ohne CORS-Probleme von jedem Gerät im Netz erreichbar ist.
"""

from flask import Flask, jsonify, render_template, request

from datastore import DataStore


def create_app(store: DataStore) -> Flask:
    app = Flask(__name__)

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/ui-api/state")
    def state():
        return jsonify(store.snapshot())

    @app.get("/ui-api/history")
    def history():
        return jsonify(
            store.history_series(
                fields=request.args.get("fields"),
                minutes=int(request.args["minutes"]) if request.args.get("minutes") else None,
                max_points=int(request.args["max_points"]) if request.args.get("max_points") else None,
            )
        )

    return app


def run(store: DataStore, host: str, port: int) -> None:
    create_app(store).run(host=host, port=port, threaded=True, use_reloader=False)
