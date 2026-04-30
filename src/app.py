"""Flask Web 主程序 — 仪表盘 + JSON API + 登录。"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import (
    Flask, jsonify, redirect, render_template, request,
    session, url_for, abort,
)

from . import auth, config as cfgmod, db, scheduler


log = logging.getLogger("p4d-monitor.app")


def create_app() -> Flask:
    cfg = cfgmod.load()

    app = Flask(
        __name__,
        template_folder=str(Path(__file__).resolve().parent.parent / "templates"),
        static_folder=str(Path(__file__).resolve().parent.parent / "static"),
    )

    # Session 密钥
    app.secret_key = (
        cfg["dashboard"].get("secret_key")
        or os.environ.get("P4D_MONITOR_SECRET_KEY")
        or os.urandom(32).hex()
    )
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["PERMANENT_SESSION_LIFETIME"] = 60 * 60 * 24 * 7  # 7 天

    # 启动调度器(只在主进程,避免 reload 时双倍)
    if os.environ.get("P4D_MONITOR_DISABLE_SCHEDULER") != "1":
        scheduler.start(cfg)

    # ---- 路由 ----

    @app.route("/login", methods=["GET", "POST"])
    def login() -> Any:
        cfg_now = cfgmod.load()
        dash = cfg_now["dashboard"]

        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")

            if (
                username == dash.get("username")
                and auth.verify_password(password, dash.get("password_hash", ""))
            ):
                session["logged_in"] = True
                session["user"] = username
                session.permanent = True
                next_url = request.args.get("next") or url_for("dashboard")
                return redirect(next_url)

            return render_template("login.html", error="用户名或密码错误"), 401

        if session.get("logged_in"):
            return redirect(url_for("dashboard"))
        return render_template("login.html")

    @app.route("/logout")
    def logout() -> Any:
        session.clear()
        return redirect(url_for("login"))

    @app.route("/")
    @auth.login_required
    def dashboard() -> Any:
        cfg_now = cfgmod.load()
        servers = cfg_now.get("servers", [])
        latest = scheduler.get_latest_probes()

        # 没探测过的从 DB 拿
        for srv in servers:
            if srv["name"] not in latest:
                hist = db.latest_probe(srv["name"])
                if hist:
                    latest[srv["name"]] = hist

        return render_template(
            "dashboard.html",
            servers=servers,
            probes=latest,
            now=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        )

    @app.route("/api/servers")
    @auth.login_required
    def api_servers() -> Any:
        latest = scheduler.get_latest_probes()
        cfg_now = cfgmod.load()
        for srv in cfg_now.get("servers", []):
            if srv["name"] not in latest:
                hist = db.latest_probe(srv["name"])
                if hist:
                    latest[srv["name"]] = hist
        return jsonify(latest)

    @app.route("/api/server/<name>/history")
    @auth.login_required
    def api_history(name: str) -> Any:
        hours = int(request.args.get("hours", "24"))
        history = db.history_probes(name, hours=hours)
        return jsonify(history)

    @app.route("/api/server/<name>/recheck", methods=["POST"])
    @auth.login_required
    def api_recheck(name: str) -> Any:
        cfg_now = cfgmod.load()
        srv = next((s for s in cfg_now.get("servers", []) if s["name"] == name), None)
        if not srv:
            return jsonify({"error": "Server not found"}), 404

        scheduler._probe_one_server(cfg_now, srv)
        latest = scheduler.get_latest_probes().get(name)
        return jsonify(latest or {"error": "Probe failed"})

    @app.route("/alerts")
    @auth.login_required
    def alerts() -> Any:
        recent = db.recent_alerts(limit=100)
        return render_template("alerts.html", alerts=recent)

    @app.route("/api/alerts")
    @auth.login_required
    def api_alerts() -> Any:
        return jsonify(db.recent_alerts(limit=int(request.args.get("limit", 50))))

    @app.route("/server/<name>")
    @auth.login_required
    def server_detail(name: str) -> Any:
        cfg_now = cfgmod.load()
        srv = next((s for s in cfg_now.get("servers", []) if s["name"] == name), None)
        if not srv:
            abort(404)

        latest = scheduler.get_latest_probes().get(name) or db.latest_probe(name)
        history = db.history_probes(name, hours=24)
        return render_template(
            "server_detail.html",
            server=srv,
            probe=latest,
            history=history,
        )

    @app.route("/healthz")
    def healthz() -> Any:
        """K8s/Docker 风格健康检查端点(无需登录)。"""
        return jsonify({"status": "ok", "time": datetime.now(timezone.utc).isoformat()})

    return app


# WSGI 入口(systemd / gunicorn 用)
app = None  # 延迟初始化


def get_app() -> Flask:
    global app
    if app is None:
        app = create_app()
    return app


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    cfg = cfgmod.load()
    dash = cfg["dashboard"]
    flask_app = get_app()
    flask_app.run(
        host=dash.get("host", "0.0.0.0"),
        port=dash.get("port", 8080),
        debug=False,
    )
