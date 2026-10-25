"""Authentication forms and shared input normalization."""

from datetime import datetime
from zoneinfo import ZoneInfo

from flask_wtf import FlaskForm
from wtforms import BooleanField, DateField, PasswordField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.fields.choices import SelectChoice
from wtforms.validators import DataRequired, EqualTo, InputRequired, Length, Regexp
from wtforms.validators import ValidationError


def trim_text(value):
    return value.strip() if isinstance(value, str) else value


def normalize_email(value):
    return value.strip().lower() if isinstance(value, str) else value


EMAIL_FORMAT = Regexp(
    r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$",
    message="Enter an email address in a valid format.",
)


class RegisterForm(FlaskForm):
    name = StringField(
        "Name",
        filters=[trim_text],
        validators=[DataRequired(), Length(min=2, max=100)],
    )
    email = StringField(
        "Email",
        filters=[normalize_email],
        validators=[DataRequired(), Length(max=254), EMAIL_FORMAT],
    )
    password = PasswordField(
        "Password",
        validators=[
            InputRequired(),
            Length(min=12, max=128),
            EqualTo("confirm_password", message="Passwords must match."),
        ],
    )
    confirm_password = PasswordField("Confirm password", validators=[InputRequired()])
    submit = SubmitField("Register")


class LoginForm(FlaskForm):
    email = StringField(
        "Email",
        filters=[normalize_email],
        validators=[DataRequired(), Length(max=254), EMAIL_FORMAT],
    )
    password = PasswordField("Password", validators=[InputRequired()])
    submit = SubmitField("Log in")


class LogoutForm(FlaskForm):
    submit = SubmitField("Log out")


ITEM_CATEGORIES = (
    "Electronics", "Documents", "Keys", "Bags", "Clothing", "Books", "Other"
)


class ItemForm(FlaskForm):
    report_type = SelectField(
        "Report type", choices=[SelectChoice("lost", "Lost"), SelectChoice("found", "Found")],
        validators=[DataRequired()],
    )
    title = StringField(
        "Title", filters=[trim_text], validators=[DataRequired(), Length(min=3, max=120)]
    )
    description = TextAreaField(
        "Description", filters=[trim_text],
        validators=[DataRequired(), Length(min=10, max=2000)],
    )
    category = SelectField(
        "Category", choices=[SelectChoice(value, value) for value in ITEM_CATEGORIES],
        validators=[DataRequired()],
    )
    location = StringField(
        "Campus location", filters=[trim_text],
        validators=[DataRequired(), Length(min=2, max=150)],
    )
    event_date = DateField("Date lost or found", format="%Y-%m-%d", validators=[DataRequired()])
    submit = SubmitField("Save report")

    def validate_event_date(self, field):
        if field.data and field.data > datetime.now(ZoneInfo("Asia/Kolkata")).date():
            raise ValidationError("Date cannot be later than today on campus.")


class ClaimForm(FlaskForm):
    proof_text = TextAreaField(
        "Evidence that this item is yours", filters=[trim_text],
        validators=[DataRequired(), Length(min=20, max=2000)],
    )
    submit = SubmitField("Submit claim")


class DecisionForm(FlaskForm):
    explanation = TextAreaField(
        "Decision explanation (visible to the claimant)", filters=[trim_text],
        validators=[DataRequired(), Length(min=3, max=500)],
    )


class ReturnForm(FlaskForm):
    confirm_handover = BooleanField(
        "I confirm the item was handed over", validators=[DataRequired()]
    )
