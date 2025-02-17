from flask import Blueprint, request, abort, current_app, url_for
from werkzeug.http import dump_cookie
from apifairy import authenticate, body, response, other_responses

from api.app import db
from api.auth import basic_auth
from api.email import send_email
from api.models import User, Token
from api.schemas import TokenSchema, PasswordResetRequestSchema, \
    PasswordResetSchema, EmptySchema

tokens = Blueprint('tokens', __name__)
token_schema = TokenSchema()


def token_response(token):
    domain = request.host
    if domain.startswith('localhost') or \
            domain.startswith('127.0.0.1'):  # pragma: no cover
        domain = None
    cookie = dump_cookie(
        'refresh_token', token.refresh_token,
        domain=domain, path=url_for('tokens.new'),
        secure=not current_app.debug, httponly=True,
        samesite='none' if not current_app.debug else 'lax')
    return {
        'access_token': token.access_token,
        'refresh_token': token.refresh_token,
    }, 200, {'Set-Cookie': cookie}


@tokens.route('/tokens', methods=['POST'])
@authenticate(basic_auth)
@response(token_schema)
@other_responses({401: 'Invalid username or password'})
def new():
    """Create new access and refresh tokens

    The refresh token is also returned as a hardened cookie, in case the
    client is running in an insecure environment such as a web browser, and
    cannot securely store the token.
    """
    user = basic_auth.current_user()
    token = user.generate_auth_token()
    db.session.add(token)
    Token.clean()  # keep token table clean of old tokens
    db.session.commit()
    return token_response(token)


@tokens.route('/tokens', methods=['PUT'])
@body(token_schema)
@response(token_schema, description='Newly issued access and refresh tokens')
@other_responses({401: 'Invalid access or refresh token'})
def refresh(args):
    """Refresh an access token

    The client has the option to pass the refresh token in the body of the
    request or in a `refresh_token` cookie. The access token must be passed in
    the body of the request.
    """
    access_token = args['access_token']
    refresh_token = args.get('refresh_token', request.cookies.get(
        'refresh_token'))
    if not access_token or not refresh_token:
        abort(401)
    token = User.verify_refresh_token(refresh_token, access_token)
    if not token:
        abort(401)
    token.expire()
    new_token = token.user.generate_auth_token()
    db.session.add_all([token, new_token])
    db.session.commit()
    return token_response(new_token)


@tokens.route('/tokens/reset', methods=['POST'])
@body(PasswordResetRequestSchema)
@response(EmptySchema, status_code=204,
          description='Password reset email sent')
def reset(args):
    """Request a password reset token"""
    user = db.session.scalar(User.select().filter_by(email=args['email']))
    if user is not None:
        reset_token = user.generate_reset_token()
        reset_url = request.referrer.strip('/') + args['callback_url'] + \
            '?token=' + reset_token
        send_email(args['email'], 'Reset Your Password', 'reset',
                   token=reset_token, url=reset_url)
    return {}


@tokens.route('/tokens/reset', methods=['PUT'])
@body(PasswordResetSchema)
@response(EmptySchema, status_code=204,
          description='Password reset successful')
@other_responses({400: 'Invalid reset token'})
def password_reset(args):
    """Reset a user password"""
    user = User.verify_reset_token(args['token'])
    if user is None:
        abort(400)
    user.password = args['new_password']
    db.session.commit()
    return {}
