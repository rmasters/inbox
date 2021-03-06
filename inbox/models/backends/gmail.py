from datetime import datetime, timedelta

from sqlalchemy import Column, Integer, String, ForeignKey
from sqlalchemy import event

from inbox.models.vault import vault
from inbox.models.backends.imap import ImapAccount
from inbox.oauth import new_token, validate_token
from inbox.basicauth import AuthError

from inbox.log import get_logger
log = get_logger()

PROVIDER = 'gmail'
IMAP_HOST = 'imap.gmail.com'

__volatile_tokens__ = {}


class GmailAccount(ImapAccount):
    id = Column(Integer, ForeignKey(ImapAccount.id, ondelete='CASCADE'),
                primary_key=True)

    __mapper_args__ = {'polymorphic_identity': 'gmailaccount'}

    refresh_token_id = Column(Integer())  # Secret
    # STOPSHIP(emfree) store these either as secrets or as properties of the
    # developer app.
    client_id = Column(String(256))
    client_secret = Column(String(256))
    scope = Column(String(512))
    access_type = Column(String(64))
    family_name = Column(String(256))
    given_name = Column(String(256))
    name = Column(String(256))
    gender = Column(String(16))
    g_id = Column(String(32))  # `id`
    g_id_token = Column(String(1024))  # `id_token`
    g_user_id = Column(String(32))  # `user_id`
    link = Column(String(256))
    locale = Column(String(8))
    picture = Column(String(1024))
    home_domain = Column(String(256))

    @property
    def refresh_token(self):
        return vault.get(self.refresh_token_id)

    @refresh_token.setter
    def refresh_token(self, value):
        self.refresh_token_id = vault.put(value)

    @property
    def access_token(self):
        if self.id in __volatile_tokens__:
            tok, expires = __volatile_tokens__[self.id]
            if datetime.utcnow() > expires:
                # Remove access token from pool,  return new one
                del __volatile_tokens__[self.id]
                return self.access_token
            else:
                return tok
        else:
            # first time getting access token, or perhaps it expired?
            tok, expires = new_token(self.refresh_token,
                                     self.client_id,
                                     self.client_secret)

            validate_token(tok)
            self.set_access_token(tok, expires)
            return tok

    def renew_access_token(self):
        del __volatile_tokens__[self.id]
        return self.access_token

    def verify(self):
        if self.id in __volatile_tokens__:
            tok, expires = __volatile_tokens__[self.id]

            if datetime.utcnow() > expires:
                del __volatile_tokens__[self.id]
                return self.verify()
            else:
                try:
                    return validate_token(tok)
                except AuthError:
                    del __volatile_tokens__[self.id]
                    raise

        else:
            tok, expires = new_token(self.refresh_token)
            valid = validate_token(tok)
            self.set_access_token(tok, expires)
            return valid

    def set_access_token(self, tok, expires_in):
        # Subtract 10 seconds as it takes _some_ time to propagate between
        # google's servers and this code (much less than 10 seconds, but
        # 10 should be safe)
        expires = datetime.utcnow() + timedelta(seconds=expires_in - 10)
        if datetime.utcnow() > expires:
            log.error("Error setting expired access_token for {}"
                      .format(self.id))
            return

        __volatile_tokens__[self.id] = tok, expires

    @property
    def sender_name(self):
        return self.name or ''

    @property
    def provider(self):
        return PROVIDER


@event.listens_for(GmailAccount, 'after_update')
def _after_gmailaccount_update(mapper, connection, target):
    """ Hook to cascade delete the refresh_token as it may be remote (and usual
    ORM mechanisms don't apply)."""
    if target.deleted_at:
        vault.remove(target.refresh_token_id)
