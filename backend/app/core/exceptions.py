class AppError(Exception):
    """An expected outcome the client is supposed to act on, not a fault.

    Carries the status code its handler in main.py should return, which keeps the service layer
    free of FastAPI imports. Contrast NL2SQLError below: those all mean "something broke" and map
    to a 500.
    """

    status_code = 400
    detail = "request failed"

    def __init__(self, detail: str | None = None):
        self.detail = detail or type(self).detail
        super().__init__(self.detail)


class NL2SQLError(Exception):
    pass


class ConnectionNotFoundError(AppError):
    status_code = 404
    # returned identically whether the id is unknown or owned by someone else — distinguishing the
    # two would let a signed-in user probe for other people's connection ids
    detail = "no such connection"


class ConnectionNameTakenError(AppError):
    status_code = 409
    detail = "you already have a connection with this name"


class ChatNotFoundError(AppError):
    status_code = 404
    # same reasoning as ConnectionNotFoundError: identical whether the chat is unknown or someone
    # else's, so the id space can't be probed
    detail = "no such chat"


class NoActiveConnectionError(AppError):
    # 409 rather than 400: the request is well-formed, the account just isn't pointed at a database
    # yet. A distinct code so the frontend can prompt "pick a connection" instead of showing an error.
    status_code = 409
    detail = "no active database connection — save or activate one first"


class ConnectionUnreachableError(AppError):
    # the credentials or host the user supplied don't work — their input, not our fault, so not a 500
    status_code = 400
    detail = "could not connect to the database"


class InvalidAnnotationError(AppError):
    status_code = 400
    detail = "annotation does not match the database schema"


class UnsafeSQLError(NL2SQLError):
    pass


class DestructiveSQLError(UnsafeSQLError):
    pass


class SQLExecutionError(NL2SQLError):
    pass


class AuthError(AppError):
    status_code = 401
    detail = "authentication failed"


class InvalidCredentialsError(AuthError):
    status_code = 401
    # identical whether the email is unknown or the password is wrong — anything more specific
    # turns the login form into a tool for discovering who has an account
    detail = "invalid email or password"


class NotAuthenticatedError(AuthError):
    status_code = 401
    detail = "not authenticated"


class EmailAlreadyRegisteredError(AuthError):
    status_code = 409
    detail = "an account with this email already exists"


class AccountDisabledError(AuthError):
    status_code = 403
    detail = "this account has been disabled"


class OAuthNotConfiguredError(AuthError):
    status_code = 503
    detail = "Google sign-in is not configured on this server"


class OAuthFailedError(AuthError):
    status_code = 400
    detail = "Google sign-in failed"
