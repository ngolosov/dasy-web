from flask import redirect, render_template, session
from functools import wraps
from os.path import basename


def error_message(message, code=400):
    return render_template("error.html", top=code, bottom=message), code


def login_required(f):
    """
    Decorate routes to require login.

    http://flask.pocoo.org/docs/1.0/patterns/viewdecorators/
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get("user_id") is None:
            return redirect("/login")
        return f(*args, **kwargs)
    return decorated_function


def file_basename(value):
    return basename(value)