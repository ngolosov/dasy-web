from os.path import join
from zipfile import ZipFile
from pathlib import Path
from tempfile import mkdtemp
from shutil import rmtree
from flask import Flask, flash, redirect, render_template, request, session
from werkzeug.exceptions import default_exceptions, HTTPException, InternalServerError
from werkzeug.security import check_password_hash, generate_password_hash
from helpers import error_message, login_required, file_basename
from dasymetric import list_fields, dasymetric_map, is_polygon, export_as_shp
from sqlite3 import connect, Row, IntegrityError
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
# Configure application
app = Flask(__name__)

# Ensure templates are auto-reloaded
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.secret_key = "super secret key"
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///test.db'


# custom filter for file names
app.jinja_env.filters["basename"] = file_basename

db = SQLAlchemy(app)

# Ensure responses aren't cached
@app.after_request
def after_request(response):
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Expires"] = 0
    response.headers["Pragma"] = "no-cache"
    return response

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(), unique=True, nullable=False)
    hash = db.Column(db.String(), nullable=False)

    def __repr__(self):
        return '<User %r>' % self.username




class History(db.Model):
    row_id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(), nullable=False)
    temp_dir = db.Column(db.String(), nullable=False)
    census_shp = db.Column(db.String(), nullable=False)
    footprint_shp = db.Column(db.String(), nullable=False)
    method = db.Column(db.String(), nullable=False)
    map_json = db.Column(db.String())
    is_shared = db.Column(db.Integer, nullable=False)
    map_caption = db.Column(db.Integer, nullable=False)
    date = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


    def __repr__(self):
        return '<History %r>' % self.row_id
"""
print(db.session.add(User(username="ddd", hash="ddd")))
result = db.session.query(User).filter_by(username='dd').first()
result.hash = "vv"

db.session.commit()
"""



@app.route("/")
@login_required
def index():
    return render_template("index.html")


@app.route("/uploader", methods=['POST'])
@login_required
def uploader():
    # TODO add check to check if there are any numeric fields in the uploaded shapefiles
    """ This route handles upload of the files, unpacking the archives and """
    allowed_extensions = ['zip']

    def allowed_file(filename):
        return '.' in filename and filename.rsplit('.', 1)[1].lower() in allowed_extensions

    user_id = session["user_id"]
    dasymetric_method = request.form.get("dasymetric_method")
    map_caption = request.form.get("map_caption")
    census = request.files['census']
    footprints = request.files['footprints']
    # checking if the file ends with .zip
    if allowed_file(census.filename) and allowed_file(footprints.filename) and map_caption and (
            dasymetric_method in ('2D', '3D')):
        # creating the temporary directory
        tmp_dir_name = mkdtemp()
        # creating path to the zip archives and saving them
        census_path = join(tmp_dir_name, 'census.zip')
        footprints_path = join(tmp_dir_name, 'footprints.zip')
        census.save(census_path)
        footprints.save(footprints_path)
        # unpacking the zip files
        with ZipFile(census_path) as zip_ref:
            census_dir = join(tmp_dir_name, "census")
            zip_ref.extractall(census_dir)
        with ZipFile(footprints_path) as zip_ref:
            footprints_dir = join(tmp_dir_name, "footprints")
            zip_ref.extractall(footprints_dir)
        # searching the shp files in the unpacked folders
        census_shp_list = list(Path(census_dir).glob('**/*.shp'))
        footprints_shp_list = list(Path(footprints_dir).glob('**/*.shp'))
        # checking if we have exactly one shape file in each archive
        if len(census_shp_list) != 1 or len(footprints_shp_list) != 1:
            # trying to clean temporary directory, if failed, just pass
            try:
                rmtree(tmp_dir_name)
            except:
                pass
            return error_message('No shp files or more then one shp file in the archive', 200)
        # checking if our files are polygons
        elif not all((is_polygon(footprints_shp_list[0]), is_polygon(census_shp_list[0]))):
            try:
                rmtree(tmp_dir_name)
            except:
                pass
            return error_message('Uploaded files are not polygon SHP', 200)
        else:
            flash("Files saved")
            footprint_fields = list_fields(footprints_shp_list[0])
            census_fields = list_fields(census_shp_list[0])
            row = History(user_id=user_id, temp_dir=tmp_dir_name, census_shp=str(census_shp_list[0]),
                    footprint_shp=str(footprints_shp_list[0]), method=dasymetric_method,
                    map_caption=map_caption, is_shared=0)
            db.session.flush()
            db.session.add(row)
            db.session.commit()
            if dasymetric_method == '2D':
                return render_template("fields.html", census_fields=census_fields, history_id=row.row_id)
            else:
                return render_template("fields.html", census_fields=census_fields,
                                       footprint_fields=footprint_fields, history_id=row.row_id)
    else:
        return error_message('Input parameters are not valid.', 200)


@app.route("/create_map", methods=['POST'])
@login_required
def create_map():
    # TODO remove the database record if map creation failed
    # getting user_id and form fields
    user_id = session["user_id"]
    pop_field = request.form.get("census_fields")
    bldg_height_field = request.form.get("footprint_fields")
    history_id = request.form.get("history_id")
    if None in (pop_field, history_id):
        return error_message('Some of the input parameters are empty', 403)
    # getting row from database to retrieve paths to the files, submitted on the previous step
    history_row = db.session.query(History).filter_by(user_id=user_id, row_id=history_id).first()

    census_shp_location = history_row.census_shp
    footprints_shp_location = history_row.footprint_shp
    tmp_dir_name = history_row.temp_dir
    # setting 3D method to true or false
    method = history_row.method
    if method == '3D':
        method_3d = True
    else:
        method_3d = False

    # getting the result from the dasymetric mapping function
    json = dasymetric_map(census_shp_location, footprints_shp_location, pop_field, bldg_height_field, method_3d)

    # removing temporary directory with shp files
    try:
        rmtree(tmp_dir_name)
    except:
        pass
    # if the function reported success, storing the result in the database
    if json[1]:
        history_row.map_json = json[1]
        history_row.user_id = user_id
        history_row.row_id = history_id
        db.session.commit()
        return render_template("map.html", json=json[1])
    else:
        return error_message('Error during creating the map', 200)


@app.route("/history")
@login_required
def history():
    """ route to retrieve history and allow user to share and view created maps"""
    user_id = session["user_id"]
    names = ["Map name", "Census file", "Footprints file", "Date", "Is shared?", "Link to the map", "Remove map"]

    history_table = db.session.query(History).filter(History.map_json.isnot(None)).all()
    listo = [[i.row_id, i.map_caption, i.census_shp, i.footprint_shp, i.date, i.is_shared] for i in history_table]

    return render_template("history.html", names=names, tbody=listo)


@app.route("/view_map")
@login_required
def view_map():
    """ route to view maps which are belong to the user """
    user_id = session["user_id"]
    row_id = request.args.get('map_id')
    try:
        row_id = int(row_id)
        if row_id < 1:
            return error_message("Specified input parameters are not valid, map_id should be positive")
    except ValueError:
        return error_message("Specified input parameters are not valid, map_id should be integer")
    history_table = db.execute(
        "SELECT row_id, map_caption, map_json, is_shared FROM history WHERE user_id = ? AND row_id= ?",
        [user_id, row_id]).fetchall()
    if not history_table:
        return error_message('Map with specified ID was not found, or does not belong to the current user')
    caption = history_table[0]['map_caption']
    json = history_table[0]['map_json']
    is_shared = history_table[0]['is_shared']
    map_id = history_table[0]['row_id']
    return render_template("map.html", caption=caption, json=json, is_shared=is_shared, map_id=map_id)


@app.route("/share_map")
@login_required
def share_map():
    """ route to share / unshare maps from the history screen,
     it basically just setting the corresponding field to 1 or 0 """
    user_id = session["user_id"]
    row_id = request.args.get('map_id')
    action = request.args.get('shared')
    try:
        row_id = int(row_id)
        action = int(action)
        if row_id < 1 or (action not in range(0, 2)):
            return error_message("Specified input parameters are not valid, not in range")
    except ValueError:
        return error_message("Specified input parameters are not valid, not integer")
    result = db.execute(
        "UPDATE history SET is_shared =? WHERE user_id =? AND row_id =?",
        [action, user_id, row_id]).rowcount
    if not result:
        return error_message("Error during sharing the map")

    return redirect("/history")


@app.route("/public_map")
def public_map():
    """ route to view shared maps, it checks if the map is shared and serving it outside. Route is available without
    logging in"""
    row_id = request.args.get('map_id')
    history_table = db.execute(
        "SELECT row_id, map_json, is_shared FROM history WHERE is_shared = 1 AND row_id=?",
        [row_id]).fetchall()

    if not history_table:
        return error_message('Map with specified ID was not found, or does not belong to the current user')
    json = history_table[0]['map_json']
    return render_template("public_map.html", json=json)


@app.route("/export_shp")
@login_required
def export_shp():
    """ route to view shared maps, it checks if the map is shared and serving it outside. Route is available without
    logging in"""
    user_id = session["user_id"]
    row_id = request.args.get('map_id')
    try:
        row_id = int(row_id)
        if row_id < 1:
            return error_message("Specified input parameters are not valid, not in range")
    except ValueError:
        return error_message("Specified input parameters are not valid, not integer")

    history_table = db.execute(
        "SELECT row_id, user_id, map_json FROM history WHERE user_id=? AND row_id=?",
        [user_id, row_id]).fetchall()

    if not history_table:
        return error_message('Map with specified ID was not found, or does not belong to the current user')
    json = history_table[0]['map_json']
    exported_file = export_as_shp(json)

    if exported_file:
        response = app.response_class(response=exported_file, status=200, mimetype='application/zip')
        return response
    else:
        return error_message("Error during exporting the shp file")


@app.route("/delete_row")
@login_required
def delete_row():
    """ route to remove rows from the history"""
    user_id = session["user_id"]
    row_id = request.args.get('map_id')
    try:
        row_id = int(row_id)
        if row_id < 1:
            return error_message("Specified input parameters are not valid, not in range")
    except ValueError:
        return error_message("Specified input parameters are not valid, not integer")

    history_table = db.execute(
        "DELETE FROM history WHERE user_id=? AND row_id=?", [user_id, row_id]).fetchall()

    if not history_table:
        return error_message('Map with specified ID was not found, or does not belong to the current user')

    return redirect("/history")


@app.route("/help")
def help_route():
    return render_template("help.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    """Log user in"""

    # Forget any user_id
    session.clear()

    # User reached route via POST (as by submitting a form via POST)
    if request.method == "POST":

        # Ensure username was submitted
        if not request.form.get("username"):
            flash("Please provide username")
            return render_template("login.html")

        # Ensure password was submitted
        elif not request.form.get("password"):
            flash("Please provide password")
            return render_template("login.html")

        # Query database for username
        rows = db.session.query(User).filter_by(username=request.form.get("username")).first()

        # Ensure username exists and password is correct
        if not rows or not check_password_hash(rows.hash, request.form.get("password")):
            flash("Invalid username and/or password")
            return render_template("login.html")

        # Remember which user has logged in
        session["user_id"] = rows.id

        # Redirect user to home page
        return redirect("/")

    # User reached route via GET (as by clicking a link or via redirect)
    else:
        return render_template("login.html")


@app.route("/logout")
def logout():
    """Log user out"""

    # Forget any user_id
    session.clear()

    # Redirect user to login form
    return redirect("/")


@app.route("/register", methods=["GET", "POST"])
def register():
    # TODO add password checks(password complexity and if the username is taken)
    """Register user"""
    if request.method == "POST":
        # Ensure username was submitted
        if not request.form.get("username"):
            flash("Please specify username")
            return render_template("register.html")

        # Ensure password was submitted
        elif not request.form.get("password"):
            flash("Please specify password")
            return render_template("register.html")

        # Ensure password matches the confirmation
        elif request.form.get("password") != request.form.get("confirmation"):
            flash("Password doesn't match confirmation")
            return render_template("register.html")

        try:
            db.session.add(User(username=request.form.get("username"), hash=generate_password_hash(request.form.get("password"))))
            db.session.commit()
        except:
            return error_message("Username exists...")

        flash("User successfully registered, now you may log in...")
        return redirect("/")

    else:
        return render_template("register.html")


@app.route("/change_password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        user_id = session["user_id"]
        current_password = request.form.get("current_password")
        new_password = request.form.get("password")
        # Ensure username was submitted
        if not current_password:
            flash("Current password is missing")
            return render_template("change_password.html")

        # Ensure password was submitted
        elif not new_password:
            flash("New password is missing")
            return render_template("change_password.html")

        # Ensure password matches the confirmation
        elif new_password != request.form.get("confirmation"):
            flash("Password doesn't match confirmation")
            return render_template("change_password.html")

        # getting current user' record from the database
        result = db.session.query(User).filter_by(id=user_id).first()

        # reject if the new password is same as the former password
        if check_password_hash(result.hash, new_password):
            flash("Please specify a new password")
            return render_template("change_password.html")
        # if current password match the database
        if check_password_hash(result.hash, current_password):
            result.hash = generate_password_hash(new_password)
            db.session.commit()
        else:
            flash("Current password is wrong")
            return render_template("change_password.html")

        flash("Password changed successfully")
        return redirect("/")

    else:
        return render_template("change_password.html")


def errorhandler(e):
    """Handle error"""
    if not isinstance(e, HTTPException):
        e = InternalServerError()
    return error_message(e.name, e.code)


# Listen for errors
for code in default_exceptions:
    app.errorhandler(code)(errorhandler)
