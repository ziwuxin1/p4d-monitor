"""简单的用户名+密码 + session cookie 登录。"""
from __future__ import annotations

import functools
from typing import Any, Callable

import bcrypt
from flask import g, redirect, request, session, url_for


def verify_password(password: str, password_hash: str) -> bool:
    """bcrypt 校验。"""
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def make_password_hash(password: str) -> str:
    """生成 bcrypt hash(install.sh 用)。"""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def login_required(view: Callable) -> Callable:
    @functools.wraps(view)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        if not session.get("logged_in"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped
