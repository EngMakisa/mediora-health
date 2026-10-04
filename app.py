import os

from flask import Flask, render_template, request, redirect, url_for, session, send_from_directory
import sqlite3
from datetime import datetime, timedelta
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash


# ============================================================
# MEDIORA
# Smart Medication Adherence & Prescription Tracking System
# ============================================================

app = Flask(__name__)

app.secret_key = os.environ.get("SECRET_KEY", "mediora-secret-key-2026") 
@app.route("/service-worker.js")
def service_worker():
    return send_from_directory(".", "service-worker.js")

app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = False


# ============================================================
# DATABASE CONNECTION
# ============================================================

def get_database_connection():

    connection = sqlite3.connect(
        "database/medtrack.db"
    )

    connection.row_factory = sqlite3.Row

    return connection


# ============================================================
# USER FUNCTIONS
# ============================================================

def get_current_user():

    return session.get("user_id")


def get_current_user_details():

    user_id = get_current_user()

    if not user_id:
        return None

    connection = get_database_connection()
    cursor = connection.cursor()

    cursor.execute("""
        SELECT
            id,
            full_name,
            email
        FROM users
        WHERE id = ?
    """, (user_id,))

    user = cursor.fetchone()

    connection.close()

    return user


def login_required(function):

    @wraps(function)
    def decorated_function(*args, **kwargs):

        if not get_current_user():

            return redirect(
                url_for("home")
            )

        return function(*args, **kwargs)

    return decorated_function


# ============================================================
# CREATE TODAY'S DOSES
# ============================================================

def create_todays_doses(user_id):

    connection = get_database_connection()
    cursor = connection.cursor()

    today = datetime.now().strftime(
        "%Y-%m-%d"
    )

    cursor.execute("""
        SELECT
            id,
            time_to_take,
            frequency,
            start_date,
            end_date
        FROM medications
        WHERE user_id = ?
    """, (user_id,))

    medications = cursor.fetchall()

    for medication in medications:

        medication_id = medication["id"]

        time_to_take = medication["time_to_take"]
        frequency = medication["frequency"]
        start_date = medication["start_date"]
        end_date = medication["end_date"]

        if start_date and today < start_date:
            continue

        if end_date and today > end_date:
            continue

        if not time_to_take:
            continue

        if frequency == "As needed":
            continue

        if frequency == "Once daily":
            number_of_doses = 1

        elif frequency == "Twice daily":
            number_of_doses = 2

        elif frequency == "Three times daily":
            number_of_doses = 3

        elif frequency == "Four times daily":
            number_of_doses = 4

        else:
            number_of_doses = 1

        try:

            starting_time = datetime.strptime(
                time_to_take,
                "%H:%M"
            )

        except ValueError:

            continue

        dose_times = []

        for dose_number in range(number_of_doses):

            minutes_to_add = (
                dose_number *
                (24 * 60 // number_of_doses)
            )

            dose_time = (
                starting_time +
                timedelta(
                    minutes=minutes_to_add
                )
            ).strftime("%H:%M")

            dose_times.append(dose_time)

        for dose_time in dose_times:

            cursor.execute("""
                SELECT
                    id
                FROM dose_records
                WHERE medication_id = ?
                AND user_id = ?
                AND scheduled_date = ?
                AND scheduled_time = ?
            """, (
                medication_id,
                user_id,
                today,
                dose_time
            ))

            existing_dose = cursor.fetchone()

            if not existing_dose:

                cursor.execute("""
                    INSERT INTO dose_records
                    (
                        user_id,
                        medication_id,
                        scheduled_date,
                        scheduled_time,
                        status
                    )
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    user_id,
                    medication_id,
                    today,
                    dose_time,
                    "Pending"
                ))

    connection.commit()
    connection.close()


# ============================================================
# RESET EXPIRED SNOOZES
# ============================================================

def reset_expired_snoozes(user_id):

    connection = get_database_connection()
    cursor = connection.cursor()

    now = datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    cursor.execute("""
        UPDATE dose_records
        SET
            status = 'Pending',
            snoozed_until = NULL
        WHERE user_id = ?
        AND status = 'Snoozed'
        AND snoozed_until IS NOT NULL
        AND snoozed_until <= ?
    """, (
        user_id,
        now
    ))

    connection.commit()
    connection.close()


# ============================================================
# GET TODAY'S DOSES
# ============================================================

def get_todays_doses(user_id):

    connection = get_database_connection()
    cursor = connection.cursor()

    today = datetime.now().strftime(
        "%Y-%m-%d"
    )

    cursor.execute("""
        SELECT
            d.id AS dose_id,
            d.medication_id,
            d.scheduled_date,
            d.scheduled_time,
            d.status,
            d.taken_at,
            d.snoozed_until,
            m.medication_name,
            m.dosage,
            m.instructions,
            m.frequency
        FROM dose_records d
        INNER JOIN medications m
            ON d.medication_id = m.id
        WHERE d.user_id = ?
        AND d.scheduled_date = ?
        ORDER BY
            d.scheduled_time ASC,
            m.medication_name ASC
    """, (
        user_id,
        today
    ))

    rows = cursor.fetchall()

    connection.close()

    now = datetime.now()

    doses = []

    for row in rows:

        dose = dict(row)

        scheduled_datetime = datetime.strptime(
            f"{dose['scheduled_date']} {dose['scheduled_time']}",
            "%Y-%m-%d %H:%M"
        )

        # ----------------------------------------------------
        # DEFAULT STATE
        # ----------------------------------------------------

        dose["is_due"] = False
        dose["is_overdue"] = False
        dose["is_upcoming"] = False
        dose["is_snoozed"] = False

        # ----------------------------------------------------
        # TAKEN / SKIPPED
        # These doses are completed and are never due.
        # ----------------------------------------------------

        if dose["status"] in ["Taken", "Skipped"]:

            dose["is_due"] = False
            dose["is_overdue"] = False

        # ----------------------------------------------------
        # SNOOZED
        # A snoozed dose is only due again after
        # snoozed_until has expired.
        # ----------------------------------------------------

        elif dose["status"] == "Snoozed":

            dose["is_snoozed"] = True

            if dose["snoozed_until"]:

                try:

                    snoozed_until = datetime.strptime(
                        dose["snoozed_until"],
                        "%Y-%m-%d %H:%M:%S"
                    )

                    if now >= snoozed_until:

                        dose["is_due"] = True
                        dose["is_snoozed"] = False
                        dose["is_overdue"] = True

                except ValueError:

                    dose["is_due"] = False

        # ----------------------------------------------------
        # PENDING
        # Before scheduled time = Upcoming
        # At/after scheduled time = Due
        # ----------------------------------------------------

        elif dose["status"] == "Pending":

            if now >= scheduled_datetime:

                dose["is_due"] = True
                dose["is_overdue"] = True

            else:

                dose["is_upcoming"] = True

        doses.append(dose)

    return doses


# ============================================================
# ADHERENCE DATA
# ============================================================

def get_adherence_data(user_id, days=0):

    connection = get_database_connection()
    cursor = connection.cursor()

    if days > 0:

        start_date = (
            datetime.now()
            - timedelta(days=days - 1)
        ).strftime("%Y-%m-%d")

        cursor.execute("""
            SELECT
                status
            FROM dose_records
            WHERE user_id = ?
            AND scheduled_date >= ?
        """, (
            user_id,
            start_date
        ))

    else:

        cursor.execute("""
            SELECT
                status
            FROM dose_records
            WHERE user_id = ?
        """, (user_id,))

    records = cursor.fetchall()

    connection.close()

    total_doses = len(records)

    taken_doses = 0
    skipped_doses = 0
    pending_doses = 0
    snoozed_doses = 0

    for record in records:

        status = record["status"]

        if status == "Taken":
            taken_doses += 1

        elif status == "Skipped":
            skipped_doses += 1

        elif status == "Pending":
            pending_doses += 1

        elif status == "Snoozed":
            snoozed_doses += 1

    if total_doses > 0:

        adherence_percentage = round(
            (taken_doses / total_doses) * 100,
            1
        )

    else:

        adherence_percentage = 0

    return {
        "total_doses": total_doses,
        "taken_doses": taken_doses,
        "skipped_doses": skipped_doses,
        "pending_doses": pending_doses,
        "snoozed_doses": snoozed_doses,
        "adherence_percentage": adherence_percentage
    }


# ============================================================
# HOME
# ============================================================

@app.route("/")
def home():

    if get_current_user():

        return redirect(
            url_for("dashboard")
        )

    return render_template(
        "login.html"
    )


# ============================================================
# REGISTER
# ============================================================

@app.route(
    "/register",
    methods=["GET", "POST"]
)
def register():

    if request.method == "POST":

        full_name = request.form.get(
            "full_name",
            ""
        ).strip()

        email = request.form.get(
            "email",
            ""
        ).strip().lower()

        password = request.form.get(
            "password",
            ""
        )

        confirm_password = request.form.get(
            "confirm_password",
            ""
        )

        if not full_name or not email or not password:

            return (
                "Please fill in all required fields."
            )

        if len(password) < 8:

            return (
                "Password must be at least "
                "8 characters long."
            )

        if password != confirm_password:

            return "Passwords do not match."

        hashed_password = generate_password_hash(
            password
        )

        connection = get_database_connection()
        cursor = connection.cursor()

        try:

            cursor.execute("""
                INSERT INTO users
                (
                    full_name,
                    email,
                    password
                )
                VALUES (?, ?, ?)
            """, (
                full_name,
                email,
                hashed_password
            ))

            connection.commit()

        except sqlite3.IntegrityError:

            connection.close()

            return (
                "An account with this email "
                "already exists."
            )

        connection.close()

        return redirect(
            url_for("home")
        )

    return render_template(
        "register.html"
    )


# ============================================================
# LOGIN
# ============================================================

@app.route(
    "/login",
    methods=["GET", "POST"]
)
def login():

    if request.method == "GET":

        if get_current_user():

            return redirect(
                url_for("dashboard")
            )

        return render_template(
            "login.html"
        )

    email = request.form.get(
        "email",
        ""
    ).strip().lower()

    password = request.form.get(
        "password",
        ""
    )

    if not email or not password:

        return (
            "Please enter your email and password."
        )

    connection = get_database_connection()
    cursor = connection.cursor()

    cursor.execute("""
        SELECT
            id,
            password
        FROM users
        WHERE email = ?
    """, (email,))

    user = cursor.fetchone()

    connection.close()

    if user and check_password_hash(
        user["password"],
        password
    ):

        session.clear()

        session["user_id"] = user["id"]
        session["user_name"] = None

        connection = get_database_connection()
        cursor = connection.cursor()

        cursor.execute("""
            SELECT full_name
            FROM users
            WHERE id = ?
        """, (user["id"],))

        user_details = cursor.fetchone()

        connection.close()

        if user_details:
            session["user_name"] = user_details["full_name"]

        return redirect(
            url_for("dashboard")
        )

    return "Invalid email or password."


# ============================================================
# LOGOUT
# ============================================================

@app.route("/logout")
def logout():

    session.clear()

    return redirect(
        url_for("home")
    )


# ============================================================
# DASHBOARD
# ============================================================

@app.route("/dashboard")
@login_required
def dashboard():

    user_id = get_current_user()

    user = get_current_user_details()

    if not user:

        session.clear()

        return redirect(
            url_for("home")
        )

    create_todays_doses(user_id)

    reset_expired_snoozes(user_id)

    doses = get_todays_doses(user_id)

    total_doses = len(doses)

    completed_doses = sum(
        1
        for dose in doses
        if dose["status"] == "Taken"
    )

    pending_doses = sum(
        1
        for dose in doses
        if dose["status"] == "Pending"
    )

    skipped_doses = sum(
        1
        for dose in doses
        if dose["status"] == "Skipped"
    )

    snoozed_doses = sum(
        1
        for dose in doses
        if dose["status"] == "Snoozed"
    )

    due_doses = sum(
        1
        for dose in doses
        if dose["is_due"]
    )

    return render_template(
        "dashboard.html",
        user=user,
        doses=doses,
        total_doses=total_doses,
        completed_doses=completed_doses,
        pending_doses=pending_doses,
        skipped_doses=skipped_doses,
        snoozed_doses=snoozed_doses,
        due_doses=due_doses
    )


# ============================================================
# ADD MEDICATION
# ============================================================

@app.route(
    "/add-medication",
    methods=["GET", "POST"]
)
@login_required
def add_medication():

    user_id = get_current_user()

    if request.method == "POST":

        medication_name = request.form.get(
            "medication_name",
            ""
        ).strip()

        dosage = request.form.get(
            "dosage",
            ""
        ).strip()

        instructions = request.form.get(
            "instructions",
            ""
        ).strip()

        time_to_take = request.form.get(
            "time_to_take",
            ""
        ).strip()

        frequency = request.form.get(
            "frequency",
            ""
        ).strip()

        start_date = request.form.get(
            "start_date",
            ""
        ).strip()

        end_date = request.form.get(
            "end_date",
            ""
        ).strip()

        if not medication_name:

            return (
                "Medication name is required."
            )

        connection = get_database_connection()
        cursor = connection.cursor()

        cursor.execute("""
            INSERT INTO medications
            (
                user_id,
                medication_name,
                dosage,
                instructions,
                start_date,
                end_date,
                time_to_take,
                frequency
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id,
            medication_name,
            dosage,
            instructions,
            start_date,
            end_date,
            time_to_take,
            frequency
        ))

        connection.commit()

        connection.close()

        return redirect(
            url_for("medications")
        )

    user = get_current_user_details()

    return render_template(
        "add_medication.html",
        user=user
    )


# ============================================================
# MEDICATIONS
# ============================================================

@app.route("/medications")
@login_required
def medications():

    user_id = get_current_user()

    connection = get_database_connection()
    cursor = connection.cursor()

    cursor.execute("""
        SELECT
            id,
            medication_name,
            dosage,
            instructions,
            start_date,
            end_date,
            time_to_take,
            frequency
        FROM medications
        WHERE user_id = ?
        ORDER BY id DESC
    """, (user_id,))

    medications = cursor.fetchall()

    connection.close()

    return render_template(
        "medications.html",
        medications=medications
    )


# ============================================================
# EDIT MEDICATION
# ============================================================

@app.route(
    "/edit-medication/<int:medication_id>",
    methods=["GET", "POST"]
)
@login_required
def edit_medication(medication_id):

    user_id = get_current_user()

    connection = get_database_connection()
    cursor = connection.cursor()

    cursor.execute("""
        SELECT
            id,
            medication_name,
            dosage,
            instructions,
            start_date,
            end_date,
            time_to_take,
            frequency
        FROM medications
        WHERE id = ?
        AND user_id = ?
    """, (
        medication_id,
        user_id
    ))

    medication = cursor.fetchone()

    if medication is None:

        connection.close()

        return (
            "Medication not found.",
            404
        )

    if request.method == "POST":

        medication_name = request.form.get(
            "medication_name",
            ""
        ).strip()

        dosage = request.form.get(
            "dosage",
            ""
        ).strip()

        instructions = request.form.get(
            "instructions",
            ""
        ).strip()

        time_to_take = request.form.get(
            "time_to_take",
            ""
        ).strip()

        frequency = request.form.get(
            "frequency",
            ""
        ).strip()

        start_date = request.form.get(
            "start_date",
            ""
        ).strip()

        end_date = request.form.get(
            "end_date",
            ""
        ).strip()

        if not medication_name:

            connection.close()

            return (
                "Medication name is required."
            )

        cursor.execute("""
            UPDATE medications
            SET
                medication_name = ?,
                dosage = ?,
                instructions = ?,
                start_date = ?,
                end_date = ?,
                time_to_take = ?,
                frequency = ?
            WHERE id = ?
            AND user_id = ?
        """, (
            medication_name,
            dosage,
            instructions,
            start_date,
            end_date,
            time_to_take,
            frequency,
            medication_id,
            user_id
        ))

        cursor.execute("""
            DELETE FROM dose_records
            WHERE medication_id = ?
            AND user_id = ?
            AND scheduled_date = ?
            AND status = 'Pending'
        """, (
            medication_id,
            user_id,
            datetime.now().strftime(
                "%Y-%m-%d"
            )
        ))

        connection.commit()

        connection.close()

        return redirect(
            url_for("medications")
        )

    connection.close()

    return render_template(
        "edit_medication.html",
        medication=medication
    )


# ============================================================
# DELETE MEDICATION
# ============================================================

@app.route(
    "/delete-medication/<int:medication_id>",
    methods=["GET", "POST"]
)
@login_required
def delete_medication(medication_id):

    user_id = get_current_user()

    connection = get_database_connection()
    cursor = connection.cursor()

    cursor.execute("""
        SELECT
            id,
            medication_name,
            dosage,
            instructions,
            start_date,
            end_date,
            time_to_take,
            frequency
        FROM medications
        WHERE id = ?
        AND user_id = ?
    """, (
        medication_id,
        user_id
    ))

    medication = cursor.fetchone()

    if medication is None:

        connection.close()

        return (
            "Medication not found.",
            404
        )

    if request.method == "POST":

        cursor.execute("""
            DELETE FROM dose_records
            WHERE medication_id = ?
            AND user_id = ?
        """, (
            medication_id,
            user_id
        ))

        cursor.execute("""
            DELETE FROM medications
            WHERE id = ?
            AND user_id = ?
        """, (
            medication_id,
            user_id
        ))

        connection.commit()

        connection.close()

        return redirect(
            url_for("medications")
        )

    connection.close()

    return render_template(
        "delete_medication.html",
        medication=medication
    )


# ============================================================
# ADD PRESCRIPTION
# ============================================================

@app.route(
    "/add-prescription",
    methods=["GET", "POST"]
)
@login_required
def add_prescription():

    user_id = get_current_user()

    if request.method == "POST":

        prescription_name = request.form.get(
            "prescription_name",
            ""
        ).strip()

        doctor_name = request.form.get(
            "doctor_name",
            ""
        ).strip()

        issue_date = request.form.get(
            "issue_date",
            ""
        ).strip()

        expiry_date = request.form.get(
            "expiry_date",
            ""
        ).strip()

        refill_date = request.form.get(
            "refill_date",
            ""
        ).strip()

        notes = request.form.get(
            "notes",
            ""
        ).strip()

        connection = get_database_connection()
        cursor = connection.cursor()

        cursor.execute("""
            INSERT INTO prescriptions
            (
                user_id,
                prescription_name,
                doctor_name,
                issue_date,
                expiry_date,
                refill_date,
                notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id,
            prescription_name,
            doctor_name,
            issue_date,
            expiry_date,
            refill_date,
            notes
        ))

        connection.commit()

        connection.close()

        return redirect(
            url_for("prescriptions")
        )

    return render_template(
        "add_prescription.html"
    )


# ============================================================
# PRESCRIPTIONS
# ============================================================

@app.route("/prescriptions")
@login_required
def prescriptions():

    user_id = get_current_user()

    connection = get_database_connection()
    cursor = connection.cursor()

    cursor.execute("""
        SELECT
            id,
            prescription_name,
            doctor_name,
            issue_date,
            expiry_date,
            refill_date,
            notes
        FROM prescriptions
        WHERE user_id = ?
        ORDER BY id DESC
    """, (user_id,))

    prescriptions = cursor.fetchall()

    connection.close()

    return render_template(
        "prescriptions.html",
        prescriptions=prescriptions
    )


# ============================================================
# ADHERENCE
# ============================================================

@app.route("/adherence")
@login_required
def adherence():

    user_id = get_current_user()

    create_todays_doses(user_id)

    reset_expired_snoozes(user_id)

    period = request.args.get(
        "period",
        "7"
    )

    allowed_periods = [
        "7",
        "30",
        "90",
        "all"
    ]

    if period not in allowed_periods:

        period = "7"

    if period == "all":

        days = 0

    else:

        days = int(period)

    adherence_data = get_adherence_data(
        user_id,
        days
    )

    return render_template(
        "adherence.html",
        adherence=adherence_data,
        selected_period=period
    )


# ============================================================
# UPDATE DOSE
# ============================================================

@app.route(
    "/dose/<int:dose_id>/<status>",
    methods=["POST"]
)
@login_required
def update_dose(dose_id, status):

    user_id = get_current_user()

    allowed_statuses = [
        "Taken",
        "Skipped",
        "Snoozed"
    ]

    if status not in allowed_statuses:

        return "Invalid dose status."

    connection = get_database_connection()
    cursor = connection.cursor()

    cursor.execute("""
        SELECT
            id,
            status
        FROM dose_records
        WHERE id = ?
        AND user_id = ?
    """, (
        dose_id,
        user_id
    ))

    dose = cursor.fetchone()

    if dose is None:

        connection.close()

        return (
            "Dose record not found.",
            404
        )

    current_status = dose["status"]

    if status == "Taken":

        taken_at = datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        cursor.execute("""
            UPDATE dose_records
            SET
                status = ?,
                taken_at = ?,
                snoozed_until = NULL
            WHERE id = ?
            AND user_id = ?
        """, (
            "Taken",
            taken_at,
            dose_id,
            user_id
        ))

    elif status == "Skipped":

        cursor.execute("""
            UPDATE dose_records
            SET
                status = ?,
                taken_at = NULL,
                snoozed_until = NULL
            WHERE id = ?
            AND user_id = ?
        """, (
            "Skipped",
            dose_id,
            user_id
        ))

    elif status == "Snoozed":

        if current_status in [
            "Taken",
            "Skipped"
        ]:

            connection.close()

            return redirect(
                url_for("dashboard")
            )

        snoozed_until = (
            datetime.now()
            + timedelta(minutes=30)
        ).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        cursor.execute("""
            UPDATE dose_records
            SET
                status = ?,
                snoozed_until = ?,
                taken_at = NULL
            WHERE id = ?
            AND user_id = ?
        """, (
            "Snoozed",
            snoozed_until,
            dose_id,
            user_id
        ))

    connection.commit()

    connection.close()

    return redirect(
        url_for("dashboard")
    )


# ============================================================
# START MEDIORA
# ============================================================

if __name__ == "__main__":

    app.run(
        debug=True
    )